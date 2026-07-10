"""L6 送後引擎測試:排程數學、合規 footer、入佇列、dispatcher 限流與煞車。

全部走純規則 + 臨時 SQLite + eml 乾跑後端,不呼叫 LLM、不需要金鑰。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from buyer_intel import config, db
from buyer_intel.company import CompanyProfile, Sender
from buyer_intel.models import Lead
from buyer_intel.sending import dispatcher, schedule
from buyer_intel.sending.footer import compliance_footer, signature
from buyer_intel.sending.sequence import enqueue_for_lead

CHI = ZoneInfo("America/Chicago")
# 2026-06-08 是週一;06-12 週五;06-16 隔週二(該區間無美國聯邦假日)
MON = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)   # Chicago 09:00(時段前)


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """臨時 DB + 臨時 outbox + 固定的 L6 設定(不碰正式資料)。"""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(config, "SENDING_BACKEND", "eml")
    monkeypatch.setattr(config, "WARMUP_START_DATE", "")
    monkeypatch.setattr(config, "DAILY_LIMIT_WARMUP", 2)
    monkeypatch.setattr(config, "DAILY_LIMIT_NORMAL", 5)
    monkeypatch.setattr(config, "INTERVAL_MIN_MINUTES", 60)
    monkeypatch.setattr(config, "INTERVAL_MAX_MINUTES", 90)
    db.init_db()
    return db


def _lead(company="Acme Coffee", email="buyer@acme.com", state="IL", **kw) -> Lead:
    return db.save_lead(Lead(company=company, email=email, state=state, **kw))


def _drafts():
    return [
        "Subject: Quick note\n\nSeq1 body.",
        "Subject: Re: Quick note\n\nSeq2 value body.",
        "Subject: Re: Quick note\n\nSeq3 closing body.",
    ]


# ── schedule:工作日/假日/時段數學 ──

def test_workday_math_skips_weekend_and_holiday():
    assert schedule.is_workday(date(2026, 6, 8))            # 週一
    assert not schedule.is_workday(date(2026, 6, 6))        # 週六
    assert not schedule.is_workday(date(2026, 7, 3))        # 美國國慶(observed)
    # 7/2(四)+1 工作日:跳過 7/3 假日與週末 → 7/6(一)
    assert schedule.add_workdays(date(2026, 7, 2), 1) == date(2026, 7, 6)


def test_first_send_datetime_rolls_forward():
    ws, we = time(9, 30), time(16, 30)
    # 週一早上(時段前)→ 當天 09:30
    at = schedule.first_send_datetime(MON, CHI, ws, we)
    assert at == datetime(2026, 6, 8, 9, 30, tzinfo=CHI)
    # 週一 17:00(過了時段)→ 隔天 09:30
    late = datetime(2026, 6, 8, 22, 30, tzinfo=timezone.utc)   # Chicago 17:30
    assert schedule.first_send_datetime(late, CHI, ws, we).date() == date(2026, 6, 9)
    # 週六 → 下週一 09:30
    sat = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
    assert schedule.first_send_datetime(sat, CHI, ws, we).date() == date(2026, 6, 8)


def test_sequence_offsets_are_workdays():
    ws = time(9, 30)
    seq1 = datetime(2026, 6, 8, 9, 30, tzinfo=CHI)
    times = schedule.sequence_datetimes(seq1, (0, 4, 6), CHI, ws)
    assert [t.date() for t in times] == [
        date(2026, 6, 8), date(2026, 6, 12), date(2026, 6, 16)]  # 一、五、隔週二


def test_buyer_tz_mapping():
    assert str(schedule.buyer_tz("WA", "America/New_York")) == "America/Los_Angeles"
    assert str(schedule.buyer_tz(None, "America/New_York")) == "America/New_York"


# ── footer:CAN-SPAM 要件 ──

def _profile(address="123 Main St, Tainan, Taiwan"):
    return CompanyProfile(
        name="TestCo", website="https://test.co",
        sender=Sender(name="Team", email="hi@test.co", address=address))


def test_compliance_footer_has_required_elements():
    foot = compliance_footer(_profile())
    assert "UNSUBSCRIBE" in foot            # 退訂機制
    assert "123 Main St" in foot            # 實體地址
    assert "10 business days" in foot       # 處理時限


def test_footer_flags_placeholder_address():
    foot = compliance_footer(_profile(address="TODO: fill me"))
    assert "POSTAL ADDRESS NOT SET" in foot
    assert "TODO: fill me" not in foot      # 佔位字不能流進信裡


def test_signature_mode_has_no_legal_text():
    sig = signature(_profile())
    assert "UNSUBSCRIBE" not in sig and "TestCo" in sig


# ── sequence:入佇列 ──

def test_enqueue_three_rounds(tmp_env):
    lead = _lead()
    queued = enqueue_for_lead(lead, _drafts(), now=MON, interval_minutes=60)
    assert [q.sequence_no for q in queued] == [1, 2, 3]
    assert [q.scheduled_at.date() for q in queued] == [
        date(2026, 6, 8), date(2026, 6, 12), date(2026, 6, 16)]
    assert all("UNSUBSCRIBE" in q.body for q in queued)      # footer 附上了
    fresh = db.get_lead(lead.id)
    assert fresh.pending_draft is None                       # 草稿已清

def test_enqueue_staggers_same_day(tmp_env):
    enqueue_for_lead(_lead(), _drafts(), now=MON, interval_minutes=60)
    q2 = enqueue_for_lead(_lead("Beta LLC", "b@beta.com"), _drafts(),
                          now=MON, interval_minutes=60)
    # 第二家的 seq1 錯開 60 分鐘:09:30 → 10:30
    assert q2[0].scheduled_at.astimezone(CHI).time() == time(10, 30)


def test_enqueue_rejects_unsubscribed_and_duplicates(tmp_env):
    lead = _lead()
    db.add_unsubscribe("buyer@acme.com")
    with pytest.raises(ValueError, match="退訂"):
        enqueue_for_lead(lead, _drafts(), now=MON)
    lead2 = _lead("Beta LLC", "b@beta.com")
    enqueue_for_lead(lead2, _drafts(), now=MON, interval_minutes=60)
    with pytest.raises(ValueError, match="已有排程"):
        enqueue_for_lead(db.get_lead(lead2.id), _drafts(), now=MON)


def test_met_at_show_single_immediate(tmp_env):
    lead = _lead("Show Contact", "s@show.com", stage="met_at_show")
    queued = enqueue_for_lead(lead, _drafts(), now=MON)
    assert len(queued) == 1 and queued[0].scheduled_at == MON


# ── dispatcher:一次 1 封、限流、煞車、守門 ──

def _make_due(now, n=2):
    """兩家各三輪入佇列,並把 seq1 改成已到期。"""
    leads = []
    for i in range(n):
        lead = _lead(f"Co{i}", f"buyer@co{i}.com")
        enqueue_for_lead(lead, _drafts(), now=now, interval_minutes=60)
        leads.append(lead)
    for q in db.list_queue(status="ready"):
        if q.sequence_no == 1:
            q.scheduled_at = now - timedelta(minutes=5)
            db.save_queued(q)
    return leads


def test_dispatch_sends_one_per_run_and_respects_interval(tmp_env):
    _make_due(MON)
    log1 = dispatcher.run_once(now=MON)
    assert any("✅" in l for l in log1)
    assert sum(1 for q in db.list_queue(status="sent")) == 1   # 一次只寄 1 封
    # 同一時刻再跑:被節奏護欄擋下(距上一封 0 分鐘 < 60)
    log2 = dispatcher.run_once(now=MON)
    assert any("節奏護欄" in l for l in log2)
    # 61 分鐘後:第二封放行
    log3 = dispatcher.run_once(now=MON + timedelta(minutes=61))
    assert any("✅" in l for l in log3)
    assert sum(1 for q in db.list_queue(status="sent")) == 2


def test_dispatch_daily_warmup_limit(tmp_env):
    _make_due(MON, n=3)
    dispatcher.run_once(now=MON)
    dispatcher.run_once(now=MON + timedelta(minutes=61))
    log = dispatcher.run_once(now=MON + timedelta(minutes=122))
    assert any("上限 2/2" in l for l in log)                   # warmup 2 封/天
    assert sum(1 for q in db.list_queue(status="sent")) == 2


def test_dispatch_eml_writes_file_and_advances_lead(tmp_env):
    leads = _make_due(MON, n=1)
    dispatcher.run_once(now=MON)
    files = list((config.OUTBOX_DIR).glob("*.eml"))
    assert len(files) == 1 and "seq1" in files[0].name
    raw = files[0].read_text()
    assert "List-Unsubscribe:" in raw and "mailto:" in raw     # 原生退訂按鈕標頭
    fresh = db.get_lead(leads[0].id)
    assert fresh.stage == "contacted"
    assert fresh.next_action_due == date(2026, 6, 12)          # 下一封 seq2 的日期


def test_reply_brake_cancels_followups(tmp_env):
    from buyer_intel import actions

    leads = _make_due(MON, n=1)
    dispatcher.run_once(now=MON)                               # seq1 寄出
    actions.apply_track(db.get_lead(leads[0].id), "replied")
    statuses = {q.sequence_no: q.status for q in db.list_queue(lead_id=leads[0].id)}
    assert statuses[1] == "sent"
    assert statuses[2] == statuses[3] == "cancelled"           # 回信 → 剩餘跟進取消


def test_guard_cancels_unsubscribed_at_send_time(tmp_env):
    _make_due(MON, n=1)
    db.add_unsubscribe("buyer@co0.com")                        # 排程後才退訂
    log = dispatcher.run_once(now=MON)
    assert any("退訂" in l for l in log)
    assert not db.list_queue(status="sent")                    # 一封都沒寄
    cancelled = [q for q in db.list_queue() if q.status == "cancelled"]
    assert any(q.sequence_no == 1 for q in cancelled)


def test_followup_deferred_not_cancelled_when_seq1_unsent(tmp_env):
    """seq1 還沒寄出(失敗待重排)時,到期的 seq2 要「暫緩」保留 ready,不能永久取消。"""
    lead = _lead()
    enqueue_for_lead(lead, _drafts(), now=MON, interval_minutes=60)
    for q in db.list_queue(lead_id=lead.id):
        if q.sequence_no == 1:
            q.status = "failed"            # 模擬 seq1 寄送失敗
        else:
            q.scheduled_at = MON - timedelta(minutes=5)   # seq2/3 已到期
        db.save_queued(q)
    log = dispatcher.run_once(now=MON)
    assert any("暫緩" in l for l in log)
    statuses = {q.sequence_no: q.status for q in db.list_queue(lead_id=lead.id)}
    assert statuses[2] == statuses[3] == "ready"          # 保留,沒被殺


# ── 🧪 三輪測試序列(走覆核 → 核准 → 壓縮排程) ──

def _approve_test_sequence(to="me@example.com", now=MON):
    """模擬完整測試流程:建草稿進覆核 → 人核准整串 → 入佇列。"""
    from buyer_intel.sending.sequence import queue_test_review

    lead = queue_test_review(to)
    drafts = [lead.pending_draft] + lead.pending_followups
    return lead, enqueue_for_lead(db.get_lead(lead.id), drafts, now=now)


def test_test_sequence_goes_through_review_then_compressed(tmp_env):
    from buyer_intel.sending.sequence import queue_test_review

    lead = queue_test_review("me@example.com")
    # 覆核佇列裡長得跟真名單一樣:三封草稿、帶 Subject 行
    assert lead.pending_draft.startswith("Subject: TEST")
    assert len(lead.pending_followups) == 2
    assert lead.pending_followups[0].startswith("Subject: Re: ")

    _, queued = _approve_test_sequence()
    assert [q.sequence_no for q in queued] == [1, 2, 3]
    assert all(q.test for q in queued)                          # 全部標測試
    offsets = [(q.scheduled_at - MON).total_seconds() / 60 for q in queued]
    assert offsets == [0, 2, 4]                                 # 核准後才壓縮排程
    assert queued[1].subject.startswith("Re: ")                 # 接同串
    assert all("UNSUBSCRIBE" in q.body for q in queued)         # footer 附上
    assert db.get_lead(queued[0].lead_id).pending_draft is None  # 核准後草稿清空


def test_test_sequence_replaces_stale_and_rejects_unsubscribed(tmp_env):
    from buyer_intel.sending.sequence import queue_test_review

    _approve_test_sequence()
    _, q2 = _approve_test_sequence()                            # 重測第二輪
    ready = db.list_queue(status="ready")
    assert len(ready) == 3 and {q.id for q in ready} == {q.id for q in q2}
    assert sum(1 for q in db.list_queue() if q.status == "cancelled") == 3
    db.add_unsubscribe("me@example.com")
    with pytest.raises(ValueError, match="退訂"):
        queue_test_review("me@example.com")


def test_test_seq2_sends_despite_new_stage_and_skips_limits(tmp_env):
    """測試信的 seq2/3 不受「seq1 尚未寄出」暫緩與每日限流管制;不佔 warmup 額度。"""
    _approve_test_sequence()
    dispatcher.run_once(now=MON)                                # seq1
    dispatcher.run_once(now=MON + timedelta(minutes=2))         # seq2(lead 仍 stage=new)
    dispatcher.run_once(now=MON + timedelta(minutes=4))         # seq3
    sent = db.list_queue(status="sent")
    assert [q.sequence_no for q in sent] == [1, 2, 3]           # 三封全出門
    assert dispatcher.sent_today(MON + timedelta(minutes=5)) == 0  # 不佔 cold 額度


def test_warmup_limit_switches_after_warmup_weeks(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "WARMUP_START_DATE", "2026-06-01")
    assert dispatcher.daily_limit(date(2026, 6, 8))[0] == 2    # 第 2 週:warmup
    assert dispatcher.daily_limit(date(2026, 6, 20))[0] == 5   # 第 3 週:normal
