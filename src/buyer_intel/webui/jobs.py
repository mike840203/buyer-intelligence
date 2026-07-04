"""背景任務管理:pipeline / follow-up 這類跑數分鐘的工作,不能卡住網頁。

單一任務槽(單人系統,不需要佇列):啟動後網頁輪詢 snapshot() 顯示即時日誌。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "kind": None,        # pipeline / followup
    "log": [],
    "started_at": None,
    "finished_at": None,
}


def snapshot() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "kind": _state["kind"],
            "log": list(_state["log"]),
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
        }


def log(message: str) -> None:
    with _lock:
        _state["log"].append(f"[{datetime.now():%H:%M:%S}] {message}")


def start(kind: str, target: Callable[[Callable[[str], None]], None]) -> bool:
    """啟動背景任務;已有任務在跑則回 False。target 收到 log 回呼。"""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, kind=kind, log=[],
                      started_at=f"{datetime.now():%H:%M:%S}", finished_at=None)

    def runner() -> None:
        try:
            target(log)
            log("✅ 任務完成")
        except Exception as exc:  # noqa: BLE001 — 背景任務錯誤要進日誌不是消失
            log(f"✘ 任務失敗:{exc}")
        finally:
            with _lock:
                _state["running"] = False
                _state["finished_at"] = f"{datetime.now():%H:%M:%S}"

    threading.Thread(target=runner, daemon=True).start()
    return True
