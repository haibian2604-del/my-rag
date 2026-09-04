import pytest

from app.providers.embedding.fake import FakeEmbedding
from app.providers.embedding.openai_compat import OpenAICompatEmbedding
from app.providers.llm.fake import FakeLLM
from app.providers.llm.openai_compat import OpenAICompatLLM


async def test_fake_llm_streams():
    llm = FakeLLM(reply="你好")
    assert [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])] == ["你好"]


async def test_fake_embedding_deterministic():
    emb = FakeEmbedding(dim=4)
    v1 = await emb.embed(["abc"])
    v2 = await emb.embed(["abc"])
    assert v1 == v2 and len(v1[0]) == 4


async def test_openai_compat_embedding_parses_response(httpx_mock):
    httpx_mock.add_response(json={"data": [{"index": 1, "embedding": [3.0]},
                                           {"index": 0, "embedding": [1.0]}]})
    emb = OpenAICompatEmbedding(base_url="http://x/v1", model="bge-m3")
    assert await emb.embed(["a", "b"]) == [[1.0], [3.0]]


async def test_openai_compat_llm_parses_sse(httpx_mock):
    body = (b'data: {"choices":[{"delta":{"content":"\\u4f60"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"\\u597d"}}]}\n\n'
            b"data: [DONE]\n\n")
    httpx_mock.add_response(content=body, headers={"content-type": "text/event-stream"})
    llm = OpenAICompatLLM(base_url="http://x/v1", model="qwen")
    assert [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])] == ["你", "好"]
