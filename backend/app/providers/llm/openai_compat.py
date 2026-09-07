import json
from collections.abc import AsyncIterator

import httpx


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def complete(self, messages: list[dict], timeout: float | None = None, **params) -> str:
        """非流式一次性生成：整体等待响应，返回完整文本（供建议问题/摘要/追问等复用）。"""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model, "messages": messages, "stream": False, **params}
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"].get("content") or ""

    async def stream_chat(self, messages: list[dict], **params) -> AsyncIterator[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model, "messages": messages, "stream": True, **params}
        async with httpx.AsyncClient(timeout=self.timeout) as client, client.stream(
            "POST", f"{self.base_url}/chat/completions", json=payload, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    return
                delta = json.loads(data)["choices"][0].get("delta", {})
                if content := delta.get("content"):
                    yield content
