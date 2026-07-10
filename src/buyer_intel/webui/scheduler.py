"""背景寄送排程器:Web UI 開著就自動輪詢,到期的信一次寄 1 封。

- `buyer-intel serve` 啟動時開一條 daemon thread,每 POLL_SECONDS 檢查一次
- 可從 /outbox 頁暫停/恢復(狀態存 marker 檔,重啟後記得)
- 每輪日誌進 ring buffer,/outbox 頁即時顯示 —— 寄了什麼、為什麼沒寄,UI 全看得到
- gmail 後端時每輪順便偵測回覆(自動煞車);eml 乾跑模式則由人按「對方回信」

exportlab 用 Accio cron 每小時 polling + 一次寄 1 封來限速;這裡輪詢密
(60 秒)但寄送節奏由 scheduled_at 錯開(60-90 分鐘)+ warmup 每日上限控制,
到點就寄、不用等下一個整點,節奏一樣安全,延遲更小。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime

from ..config import DATA_DIR, SENDING_BACKEND

POLL_SECONDS = 60
_PAUSE_MARKER = DATA_DIR / ".dispatcher_paused"

_lock = threading.Lock()
_log: deque[str] = deque(maxlen=300)
_started = False
_last_run: str | None = None


def _stamp(line: str) -> str:
    return f"[{datetime.now():%m/%d %H:%M:%S}] {line}"


def log_lines() -> list[str]:
    with _lock:
        return list(_log)


def last_run() -> str | None:
    with _lock:
        return _last_run


def is_paused() -> bool:
    return _PAUSE_MARKER.exists()


def set_paused(paused: bool) -> None:
    if paused:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _PAUSE_MARKER.write_text(datetime.now().isoformat())
        _append("⏸ 自動寄送已暫停(佇列保留;恢復後從最早到期那封繼續)")
    else:
        _PAUSE_MARKER.unlink(missing_ok=True)
        _append("▶ 自動寄送已恢復")


def _append(line: str) -> None:
    with _lock:
        _log.append(_stamp(line))


_last_was_idle = False


def run_one_round(manual: bool = False) -> list[str]:
    """跑一輪派送(+ gmail 模式偵測回覆);手動觸發不受暫停影響。"""
    global _last_run, _last_was_idle
    from ..sending.dispatcher import run_once

    lines = run_once()
    if SENDING_BACKEND == "gmail":
        try:
            from ..sending.gmail import check_replies
            lines += check_replies()
        except Exception as exc:  # noqa: BLE001
            lines.append(f"⚠ 回覆偵測失敗:{exc}")

    # 閒置去重:連續空轉(全是 ⏸/⏲)只記第一次,避免每 60 秒洗版
    idle = all(l.startswith(("⏸", "⏲")) for l in lines)
    prefix = "(手動)" if manual else ""
    if manual or not (idle and _last_was_idle):
        for line in lines:
            _append(prefix + line)
    _last_was_idle = idle and not manual
    with _lock:
        _last_run = f"{datetime.now():%H:%M:%S}"
    return lines


def _loop() -> None:
    while True:
        try:
            if not is_paused():
                run_one_round()
        except Exception as exc:  # noqa: BLE001 — 排程器不能死
            _append(f"✘ 排程器異常:{exc}")
        time.sleep(POLL_SECONDS)


def start_background() -> None:
    """啟動背景輪詢(冪等;serve 啟動時呼叫)。"""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    _append(f"🚀 寄送排程器啟動(每 {POLL_SECONDS} 秒檢查;後端:{SENDING_BACKEND}"
            f"{';目前為暫停狀態' if is_paused() else ''})")
    threading.Thread(target=_loop, daemon=True).start()
