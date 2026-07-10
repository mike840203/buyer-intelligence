"""Gmail API 寄送後端 + 回覆偵測(選用;SENDING_BACKEND=gmail 才會用到)。

用 REST + OAuth refresh token 直連(httpx,不加 Google SDK 依賴)。
需要三個環境變數(申請步驟見 docs/gmail.md):
    GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN

設計對應 exportlab 的兩條 failsafe:
- 寄出必須拿到 Gmail message id 才算成功(dispatcher 據此標 sent / failed)
- seq2/3 傳 threadId 接同一串(回信體驗像對話,不像轟炸)

回覆偵測(check_replies):對已寄出且有 thread_ref 的信,拉 thread 看有沒有
「不是我們寄的」新訊息 → 有就觸發回覆煞車(取消該 lead 剩餘跟進 + 推進階段)。
比 exportlab 的三層降級簡單:我們每封信都有 API 回傳的 threadId,直接查
thread 就是最可靠的 Layer(它的降級是為了補救沒存到 id 的情況)。
"""

from __future__ import annotations

import base64
import os
from email.message import EmailMessage

import httpx

from .dispatcher import SendResult

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"

_cached_token: dict = {}


def _creds() -> tuple[str, str, str]:
    cid = os.getenv("GMAIL_CLIENT_ID", "")
    secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    refresh = os.getenv("GMAIL_REFRESH_TOKEN", "")
    if not (cid and secret and refresh):
        raise RuntimeError(
            "Gmail 後端未設定:需要 GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / "
            "GMAIL_REFRESH_TOKEN(申請步驟見 docs/gmail.md),"
            "或把 SENDING_BACKEND 改回 eml 用乾跑模式。")
    return cid, secret, refresh


def _access_token() -> str:
    """refresh token 換 access token(簡單快取;過期 401 時清掉重換)。"""
    import time

    if _cached_token.get("token") and time.time() < _cached_token.get("expires", 0):
        return _cached_token["token"]
    cid, secret, refresh = _creds()
    resp = httpx.post(_TOKEN_URL, data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _cached_token["token"] = data["access_token"]
    _cached_token["expires"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return _cached_token["token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_access_token()}"}


def send_message(to: str, subject: str, body: str,
                 thread_ref: str | None = None) -> SendResult:
    """寄一封信;回傳 Gmail 的 message id + thread id。"""
    from .dispatcher import _list_unsubscribe_header

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    # Gmail/收件端據此顯示原生「取消訂閱」按鈕(mailto 變體,免 web endpoint)
    msg["List-Unsubscribe"] = _list_unsubscribe_header()
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(bytes(msg)).decode()

    payload: dict = {"raw": raw}
    if thread_ref:
        payload["threadId"] = thread_ref   # seq2/3 接 seq1 的 thread

    resp = httpx.post(f"{_API}/messages/send", json=payload,
                      headers=_headers(), timeout=60)
    if resp.status_code >= 400:
        return SendResult(ok=False, error=f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    mid, tid = data.get("id"), data.get("threadId")
    if not mid:
        return SendResult(ok=False, error="Gmail 回應沒有 message id")
    return SendResult(ok=True, message_id=mid, thread_ref=tid)


def thread_reply_info(thread_ref: str, our_message_ids: set[str]) -> tuple[bool, str]:
    """thread 裡「不是我們寄出的」訊息 = 對方回了。

    回傳 (有沒有回, 對方訊息的主旨+摘要合併文字)——後者供退訂關鍵字判斷。
    """
    resp = httpx.get(f"{_API}/threads/{thread_ref}",
                     params={"format": "metadata",
                             "metadataHeaders": ["From", "Subject"]},
                     headers=_headers(), timeout=30)
    resp.raise_for_status()
    texts: list[str] = []
    for m in resp.json().get("messages", []):
        if m.get("id") in our_message_ids:
            continue
        if "SENT" in m.get("labelIds", []):   # 我們補寄的也會進 thread,排除
            continue
        subject = next((h["value"] for h in m.get("payload", {}).get("headers", [])
                        if h.get("name", "").lower() == "subject"), "")
        texts.append(f"{subject} {m.get('snippet', '')}")
    return (bool(texts), " ".join(texts))


# 對方回信含這些字樣 = 要求退訂(對應 List-Unsubscribe 按鈕與 footer 指示)
_UNSUB_KEYWORDS = ("unsubscribe", "remove me", "opt out", "opt-out", "退訂")


def check_replies() -> list[str]:
    """掃所有已寄出的 thread,偵測回覆 → 觸發煞車(取消剩餘跟進 + 推進階段)。

    回信內容含退訂關鍵字 → 自動加入退訂名單 + 歸檔(對應 exportlab 的
    reply_keyword 機制)。由 dispatcher 輪詢迴圈定期呼叫(gmail 後端);
    eml 模式人工按「對方回信」/ /outbox 手動退訂即可。
    """
    from .. import actions, db

    log: list[str] = []
    sent = [q for q in db.list_queue(status="sent") if q.thread_ref]
    by_thread: dict[str, list] = {}
    for q in sent:
        by_thread.setdefault(q.thread_ref, []).append(q)

    for thread_ref, emails in by_thread.items():
        lead = db.get_lead(emails[0].lead_id)
        if lead is None:
            continue
        # 測試信的 lead 停在 new(不推進階段),也納入監控——讓 🧪 測試能驗證
        # 「回信/退訂 → 自動煞車」整個閉環;推進過的(followed_up/archived)不重複觸發
        allowed = ("contacted", "met_at_show") + (
            ("new",) if any(e.test for e in emails) else ())
        if lead.stage not in allowed:
            continue
        ours = {q.message_id for q in emails if q.message_id}
        try:
            replied, text = thread_reply_info(thread_ref, ours)
        except httpx.HTTPError as exc:
            log.append(f"⚠ 檢查 {lead.company} 回覆失敗:{exc}")
            continue
        if not replied:
            continue
        if any(k in text.lower() for k in _UNSUB_KEYWORDS):
            db.add_unsubscribe(lead.email or "", source="reply_keyword",
                               note=f"{lead.company} 回信要求退訂")
            actions.apply_track(lead, "dead", note="對方回覆 UNSUBSCRIBE,已退訂並歸檔")
            log.append(f"🚫 {lead.company} 要求退訂——已加入退訂名單、取消剩餘跟進、歸檔")
        else:
            actions.apply_track(lead, "replied", note="Gmail 偵測到 thread 回信")
            log.append(f"📩 {lead.company} 回信了!已取消剩餘跟進、推進為 followed_up")
    return log
