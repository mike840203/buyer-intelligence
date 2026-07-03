"""LLM 呼叫層:單一抽象,兩種後端可切換(LLM_BACKEND 環境變數)。

- claude_code(預設):透過 Claude Code CLI(claude -p,headless 模式)呼叫,
  走使用者的 Claude 訂閱額度,不需 API key 也不需 API 儲值。
  結構化輸出改以「JSON 指示 + 解析驗證」實作;web 搜尋用 CLI 的 WebSearch 工具;
  影像用 CLI 的 Read 工具讀檔。
- api:Anthropic SDK 直連(需組織有 API 額度),原生支援結構化輸出
  (messages.parse)、web_search server tool 與 vision,品質與速度最佳。

上層模組(enrich / scoring / outreach / ocr / brief)只用兩個函式:
    complete(model, prompt, ...)            → 純文字
    complete_structured(model, prompt, M)   → Pydantic 物件
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import CLAUDE_CLI, CLI_MODEL_MAP, LLM_BACKEND

T = TypeVar("T", bound=BaseModel)

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
}


# ────────────────────────── 公開介面 ──────────────────────────

def complete(
    model: str,
    prompt: str,
    *,
    max_tokens: int = 2048,
    web_search: bool = False,
    image_path: str | Path | None = None,
) -> str:
    """單次補全,回傳純文字。web_search / image_path 依後端各自實作。"""
    if LLM_BACKEND == "api":
        return _api_complete(model, prompt, max_tokens, web_search, image_path)
    return _cli_complete(model, prompt, web_search, image_path)


def complete_structured(
    model: str,
    prompt: str,
    output_model: type[T],
    *,
    image_path: str | Path | None = None,
) -> T | None:
    """結構化補全;解析失敗回 None(呼叫端自行保底,不中斷 pipeline)。"""
    if LLM_BACKEND == "api":
        return _api_structured(model, prompt, output_model, image_path)
    return _cli_structured(model, prompt, output_model, image_path)


# ────────────────────── claude_code 後端 ──────────────────────

def _cli_run(prompt: str, model: str, allowed_tools: list[str] | None = None) -> str:
    """執行 claude -p(headless):stdin 進 prompt,stdout 出回應。"""
    alias = CLI_MODEL_MAP.get(model, model)
    cmd = [CLAUDE_CLI, "-p", "--model", alias]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=600,  # web 搜尋可能跑數分鐘
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"找不到 claude CLI({CLAUDE_CLI})。請安裝 Claude Code,"
            "或以 CLAUDE_CLI 環境變數指定路徑,或改用 LLM_BACKEND=api。"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI 執行失敗(model={alias}):{result.stderr.strip()[:500]}"
        )
    return result.stdout.strip()


def _cli_complete(
    model: str, prompt: str, web_search: bool, image_path: str | Path | None
) -> str:
    tools: list[str] = []
    if web_search:
        tools.append("WebSearch")
    if image_path:
        tools.append("Read")
        prompt = f"請先用 Read 工具讀取影像檔 {Path(image_path).resolve()},再依指示作答。\n\n{prompt}"
    return _cli_run(prompt, model, tools or None)


def _cli_structured(
    model: str, prompt: str, output_model: type[T], image_path: str | Path | None
) -> T | None:
    schema = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
    full_prompt = (
        f"{prompt}\n\n"
        "回覆規則:只輸出一個符合以下 JSON Schema 的 JSON 物件,"
        "不要任何說明文字、不要 markdown 圍欄:\n"
        f"{schema}"
    )
    text = _cli_complete(model, full_prompt, web_search=False, image_path=image_path)
    try:
        return output_model.model_validate(_extract_json(text))
    except (ValueError, ValidationError):
        return None


def _extract_json(text: str) -> dict:
    """容錯解析:剝除 ``` 圍欄、擷取最外層 {...} 再 json.loads。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"回應中找不到 JSON 物件:{text[:200]}")
    return json.loads(text[start:end + 1])


# ────────────────────────── api 後端 ──────────────────────────

def _api_client():
    from anthropic import Anthropic  # 延遲載入:claude_code 後端不需要

    return Anthropic()


def _image_block(image_path: str | Path) -> dict:
    path = Path(image_path)
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(path.read_bytes()).decode(),
        },
    }


def _api_messages(prompt: str, image_path: str | Path | None) -> list[dict]:
    if image_path:
        return [{"role": "user", "content": [
            _image_block(image_path), {"type": "text", "text": prompt},
        ]}]
    return [{"role": "user", "content": prompt}]


def _api_complete(
    model: str, prompt: str, max_tokens: int,
    web_search: bool, image_path: str | Path | None,
) -> str:
    client = _api_client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _api_messages(prompt, image_path),
    }
    if web_search:
        kwargs["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
        ]
    response = client.messages.create(**kwargs)
    # server tool 迭代上限時 stop_reason 為 pause_turn:續跑至完成
    rounds = 0
    while response.stop_reason == "pause_turn" and rounds < 5:
        kwargs["messages"].append({"role": "assistant", "content": response.content})
        response = client.messages.create(**kwargs)
        rounds += 1
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _api_structured(
    model: str, prompt: str, output_model: type[T], image_path: str | Path | None
) -> T | None:
    response = _api_client().messages.parse(
        model=model,
        max_tokens=2048,
        messages=_api_messages(prompt, image_path),
        output_format=output_model,
    )
    return response.parsed_output
