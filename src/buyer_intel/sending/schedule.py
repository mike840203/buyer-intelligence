"""寄送時間排程(純函式,不碰 DB/LLM,好測)。

規則(對應 exportlab 的工作日/時區/寄信時段設計,但簡化為美國單一國家):
- 工作日 = 週一到週五,且不是美國聯邦假日
- 寄信時段 = buyer 當地時間 SEND_WINDOW_START ~ SEND_WINDOW_END
- 三輪跟進:seq1=+0、seq2=+4、seq3=+6 工作日,都落在寄信時段起點
- 依 leads.csv 的州別換算 buyer 當地時區(查不到用 FALLBACK_TIMEZONE)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# 美國聯邦假日(2026-2027,含週末順延的 observed 日)。跨年後補下一年即可。
_US_HOLIDAYS_RAW = [
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26",
    "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25",
    "2027-12-24", "2027-12-31",
]
US_HOLIDAYS: set[date] = {date.fromisoformat(d) for d in _US_HOLIDAYS_RAW}
HOLIDAY_YEARS = {2026, 2027}

# 州別 → IANA 時區(美國本土四時區 + 阿拉斯加/夏威夷);查不到用 fallback。
STATE_TZ = {
    # Eastern
    "NY": "America/New_York", "NJ": "America/New_York", "CT": "America/New_York",
    "MA": "America/New_York", "PA": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "OH": "America/New_York", "MI": "America/New_York",
    "NC": "America/New_York", "SC": "America/New_York", "VA": "America/New_York",
    "MD": "America/New_York", "DC": "America/New_York", "ME": "America/New_York",
    "NH": "America/New_York", "VT": "America/New_York", "RI": "America/New_York",
    "DE": "America/New_York", "WV": "America/New_York", "IN": "America/New_York",
    "KY": "America/New_York",
    # Central
    "IL": "America/Chicago", "TX": "America/Chicago", "WI": "America/Chicago",
    "MN": "America/Chicago", "IA": "America/Chicago", "MO": "America/Chicago",
    "AR": "America/Chicago", "LA": "America/Chicago", "MS": "America/Chicago",
    "AL": "America/Chicago", "TN": "America/Chicago", "KS": "America/Chicago",
    "NE": "America/Chicago", "OK": "America/Chicago", "ND": "America/Chicago",
    "SD": "America/Chicago",
    # Mountain
    "CO": "America/Denver", "UT": "America/Denver", "NM": "America/Denver",
    "MT": "America/Denver", "WY": "America/Denver", "ID": "America/Denver",
    "AZ": "America/Phoenix",
    # Pacific
    "CA": "America/Los_Angeles", "WA": "America/Los_Angeles",
    "OR": "America/Los_Angeles", "NV": "America/Los_Angeles",
    # Non-contiguous
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
}


def buyer_tz(state: str | None, fallback: str) -> ZoneInfo:
    """州 → buyer 當地時區物件;查不到用 fallback。"""
    if state:
        iana = STATE_TZ.get(state.strip().upper())
        if iana:
            return ZoneInfo(iana)
    return ZoneInfo(fallback)


def parse_hhmm(hhmm: str) -> time:
    """'09:30' → time(9, 30)。"""
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def is_workday(d: date, holidays: set[date] = US_HOLIDAYS) -> bool:
    """週一到週五且非假日。"""
    return d.weekday() < 5 and d not in holidays


def next_workday_on_or_after(d: date, holidays: set[date] = US_HOLIDAYS) -> date:
    """d 當天或之後的第一個工作日。"""
    while not is_workday(d, holidays):
        d += timedelta(days=1)
    return d


def add_workdays(start: date, n: int, holidays: set[date] = US_HOLIDAYS) -> date:
    """從 start 起算,往後推 n 個工作日(n=0 → start 當天若是工作日則不動,否則順延)。"""
    cur = next_workday_on_or_after(start, holidays)
    added = 0
    while added < n:
        cur += timedelta(days=1)
        while not is_workday(cur, holidays):
            cur += timedelta(days=1)
        added += 1
    return cur


def at_window_start(d: date, tz: ZoneInfo, window_start: time) -> datetime:
    """某工作日的寄信時段起點(帶時區)。"""
    return datetime.combine(d, window_start, tzinfo=tz)


def first_send_datetime(
    now: datetime,
    tz: ZoneInfo,
    window_start: time,
    window_end: time,
    holidays: set[date] = US_HOLIDAYS,
) -> datetime:
    """seq1 的排定時間:buyer 當地「最近一個可寄時段起點」。

    - 今天是工作日且當地時間還沒過寄信時段結束 → 今天的 window_start
      (若此刻已過 window_start,時間會落在過去 = dispatcher 下輪即寄)
    - 否則順延到下一個工作日的 window_start
    """
    local = now.astimezone(tz)
    d = local.date()
    if not is_workday(d, holidays) or local.time() > window_end:
        d = add_workdays(d, 1, holidays) if is_workday(d, holidays) else \
            next_workday_on_or_after(d + timedelta(days=1), holidays)
    return at_window_start(d, tz, window_start)


def sequence_datetimes(
    seq1_at: datetime,
    offsets_workdays: tuple[int, ...],
    tz: ZoneInfo,
    window_start: time,
    holidays: set[date] = US_HOLIDAYS,
) -> list[datetime]:
    """由 seq1 時間 + 工作日 offset 算出整串(seq1/2/3)的排定時間。

    seq1 用傳入的 seq1_at(可能帶了 stagger);seq2/3 = seq1 當日 + offset 工作日
    的 window_start。
    """
    base_day = seq1_at.date()
    result = [seq1_at]
    for off in offsets_workdays[1:]:
        day = add_workdays(base_day, off, holidays)
        result.append(at_window_start(day, tz, window_start))
    return result
