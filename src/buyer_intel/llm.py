"""Anthropic client 共用工具:單例 client、pause_turn 處理、文字抽取。"""

from __future__ import annotations

from anthropic import Anthropic

_client: Anthropic | None = None


def client() -> Anthropic:
    """單例 Anthropic client(自動讀取 ANTHROPIC_API_KEY 或 ant auth profile)。"""
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def text_of(response) -> str:
    """從回應中取出所有 text block 串接。"""
    return "\n".join(b.text for b in response.content if b.type == "text")


def create_with_server_tools(max_continuations: int = 5, **kwargs):
    """帶 server-side tool(如 web_search)的呼叫:處理 pause_turn 續跑。"""
    messages = list(kwargs.pop("messages"))
    response = client().messages.create(messages=messages, **kwargs)
    rounds = 0
    while response.stop_reason == "pause_turn" and rounds < max_continuations:
        messages.append({"role": "assistant", "content": response.content})
        response = client().messages.create(messages=messages, **kwargs)
        rounds += 1
    return response
