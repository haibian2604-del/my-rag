"""oMLX rerank provider（Jina 兼容 /rerank 契约）。

契约：POST {base_url}/rerank，header Authorization: Bearer <key>（同 LLM），
body {"model","query","documents":[str],"top_n":int}；响应
{"results":[{"index":int,"relevance_score":float,...}]}，results 已按
relevance_score 降序。返回按 results 顺序解析出的原始文档下标列表。
"""
import httpx


class OMLXRerank:
    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[int]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/rerank",
                    json={"model": self.model, "query": query,
                          "documents": documents, "top_n": top_n},
                    headers=headers,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = (e.response.text or "")[:500]
            raise RuntimeError(f"rerank 请求失败：HTTP {e.response.status_code} {detail}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"rerank 请求失败：{e}") from e
        try:
            results = resp.json()["results"]
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"rerank 响应格式异常：{resp.text[:500]}") from e
        # results 已按 relevance_score 降序，直接按顺序取 index
        return [r["index"] for r in results]
