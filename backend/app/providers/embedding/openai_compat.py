import httpx


class OpenAICompatEmbedding:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, batch_size: int = 16,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.batch_size = batch_size  # 对本地 oMLX 小批次，避免与对话争资源
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        result: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i: i + self.batch_size]
                resp = await client.post(f"{self.base_url}/embeddings",
                                         json={"model": self.model, "input": batch}, headers=headers)
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                result.extend(d["embedding"] for d in data)
        return result
