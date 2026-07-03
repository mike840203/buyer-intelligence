"""名片 OCR:拍名片 → Haiku 抽取 → 結構化 CardContact。

展中流程:拍照 → 本模組抽取 → 比對既有名單(db.find_by_company_or_email)
→ 是預約客戶則掛會談紀錄,是新接觸則建新 lead。

影像以檔案路徑交給 llm 層:claude_code 後端用 Read 工具讀圖,
api 後端讀檔轉 base64 vision block。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..config import MODEL_FAST
from ..llm import complete_structured
from ..models import CardContact

_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_PROMPT = (
    "這是一張在貿易展收到的名片。抽取公司、姓名、職稱、email、"
    "電話、網站、城市、州別(美國州別縮寫)。看不清楚的欄位留空。"
)


def extract_card(image_bytes: bytes, media_type: str) -> CardContact:
    """從名片影像抽取聯絡資訊(Haiku:結構化抽取的甜蜜點)。"""
    suffix = _SUFFIXES.get(media_type, ".jpg")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(image_bytes)
        tmp_path = Path(f.name)
    try:
        contact = complete_structured(
            MODEL_FAST, _PROMPT, CardContact, image_path=tmp_path
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if contact is None:
        raise ValueError("名片抽取失敗,請重拍或改用手動輸入")
    return contact


def extract_card_file(path: str | Path) -> CardContact:
    path = Path(path)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise ValueError(f"不支援的影像格式:{path.suffix}")
    contact = complete_structured(MODEL_FAST, _PROMPT, CardContact, image_path=path)
    if contact is None:
        raise ValueError("名片抽取失敗,請重拍或改用手動輸入")
    return contact
