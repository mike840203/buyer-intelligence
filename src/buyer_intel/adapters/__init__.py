"""L1 資料擷取層:每個外部資料源封裝一個 adapter,實作統一介面。

Adapter Interface Pattern(架構報告設計原則 1):
任一資料源漲價、封鎖或關閉,換 adapter 不動核心邏輯。
"""

from .apollo import ApolloAdapter
from .base import BaseAdapter
from .iha import IHAAdapter
from .manual import ManualAdapter
from .places import PlacesAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "apollo": ApolloAdapter,
    "places": PlacesAdapter,
    "iha": IHAAdapter,
    "manual": ManualAdapter,
}

__all__ = ["BaseAdapter", "ApolloAdapter", "PlacesAdapter", "IHAAdapter",
           "ManualAdapter", "ADAPTERS"]
