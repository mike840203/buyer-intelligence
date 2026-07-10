"""公司 Profile 層:讓整套系統與「哪一家公司」解耦。

單一事實來源是 `company/<name>.toml`(預設 company/ankomn.toml,可用環境變數
COMPANY_PROFILE 指向別的檔)。outreach / footer / enrich / scoring / brief 全部
從這裡讀 —— 今天 Ankomn、明天塑膠代工,只換 toml,不改程式碼。

刻意不 import config,避免循環依賴(自行從 repo 根目錄推路徑)。
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE = _ROOT / "company" / "ankomn.toml"

# address 尚未填真實地址時的偵測前綴(footer 會據此顯示提醒,而非印出佔位字)
ADDRESS_PLACEHOLDER_PREFIX = "TODO"


class Sender(BaseModel):
    """寄件人身分——簽名檔與合規 footer 的來源。"""

    name: str = "The Team"
    title: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""

    @property
    def address_ready(self) -> bool:
        """地址是否已填真實值(CAN-SPAM footer 需要)。"""
        addr = self.address.strip()
        return bool(addr) and not addr.upper().startswith(ADDRESS_PLACEHOLDER_PREFIX)


class Campaign(BaseModel):
    """觸發場景。type=trade_show 才在信中帶攤位/預約連結。"""

    type: str = "general"  # trade_show | general
    name: str = ""
    detail: str = ""
    booth: str = ""
    booking_url: str = ""

    @property
    def is_trade_show(self) -> bool:
        return self.type == "trade_show" and bool(self.name)


class Targeting(BaseModel):
    """目標買家輪廓——背景研究與通路契合判斷引用。"""

    channel_priority: str = ""
    competitors: list[str] = Field(default_factory=list)
    # 品類對口職稱關鍵字(挑主收件人用);空值時 enrich 用內建通用清單
    category_keywords: list[str] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    """一家公司的完整對外開發身分。"""

    name: str
    description: str = ""
    value_proposition: str = ""
    industry: str = ""
    website: str = ""
    sender: Sender = Field(default_factory=Sender)
    campaign: Campaign = Field(default_factory=Campaign)
    targeting: Targeting = Field(default_factory=Targeting)

    @property
    def competitors_text(self) -> str:
        return ", ".join(self.targeting.competitors)


def _profile_path() -> Path:
    override = os.getenv("COMPANY_PROFILE")
    if override:
        p = Path(override)
        return p if p.is_absolute() else _ROOT / p
    return _DEFAULT_PROFILE


def load_company(path: str | Path | None = None) -> CompanyProfile:
    """讀取並驗證 profile。找不到檔時回傳最小可用預設(不讓系統整個掛掉)。"""
    target = Path(path) if path else _profile_path()
    if not target.exists():
        return CompanyProfile(name="(未設定公司 profile)")
    with target.open("rb") as f:
        data = tomllib.load(f)
    company = dict(data.get("company", {}))
    return CompanyProfile(
        **company,
        sender=Sender(**data.get("sender", {})),
        campaign=Campaign(**data.get("campaign", {})),
        targeting=Targeting(**data.get("targeting", {})),
    )


@lru_cache(maxsize=1)
def get_company() -> CompanyProfile:
    """快取版:整個行程共用一份 profile。"""
    return load_company()


def reload_company() -> CompanyProfile:
    """清快取重讀(改過 toml 後、或測試切換 profile 時用)。"""
    get_company.cache_clear()
    return get_company()
