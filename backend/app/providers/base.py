from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    async def stream_chat(self, messages: list[dict], **params) -> AsyncIterator[str]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
