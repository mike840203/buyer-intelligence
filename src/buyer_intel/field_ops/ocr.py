"""名片 OCR:拍名片 → Haiku vision 抽取 → 結構化 CardContact。

展中流程:拍照 → 本模組抽取 → 比對既有名單(db.find_by_company_or_email)
→ 是預約客戶則掛會談紀錄,是新接觸則建新 lead。
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..config import MODEL_FAST
from ..llm import client
from ..models import CardContact

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def extract_card(image_bytes: bytes, media_type: str) -> CardContact:
    """從名片影像抽取聯絡資訊(Haiku vision:結構化抽取的甜蜜點)。"""
    data = base64.standard_b64encode(image_bytes).decode()
    response = client().messages.parse(
        model=MODEL_FAST,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                },
                {
                    "type": "text",
                    "text": (
                        "這是一張在貿易展收到的名片。抽取公司、姓名、職稱、email、"
                        "電話、網站、城市、州別(美國州別縮寫)。看不清楚的欄位留空。"
                    ),
                },
            ],
        }],
        output_format=CardContact,
    )
    contact = response.parsed_output
    if contact is None:
        raise ValueError("名片抽取失敗,請重拍或改用手動輸入")
    return contact


def extract_card_file(path: str | Path) -> CardContact:
    path = Path(path)
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(f"不支援的影像格式:{path.suffix}")
    return extract_card(path.read_bytes(), media_type)
