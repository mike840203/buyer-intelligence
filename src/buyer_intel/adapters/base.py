"""Adapter 統一介面。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RawLead


class BaseAdapter(ABC):
    """所有資料源 adapter 的基底:fetch() 產出統一格式的 RawLead 列表。"""

    name: str = "base"

    @abstractmethod
    def fetch(self, **kwargs) -> list[RawLead]:
        """從資料源取得原始名單。參數依 adapter 而異(查詢字串、檔案路徑等)。"""
        raise NotImplementedError
