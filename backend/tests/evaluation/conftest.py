"""评测测试共享 fixture：手工 seed hybrid 检索可稳定命中的最小语料。

原属 test_eval_e2e.py，--agent 模式测试（test_eval_agent.py）同样需要，
上移到 conftest 共享。
"""
import asyncio
from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from tests.test_hybrid_search import (
    OTHER_TEXTS,
    TARGET_TEXT,
    _make_chunk,
    _make_doc,
    _make_embedding,
)


@pytest.fixture
def eval_seed():
    fake = FakeEmbedding(dim=4)
    vectors = asyncio.run(fake.embed([TARGET_TEXT] + OTHER_TEXTS))
    with SessionLocal() as s:
        ws = Workspace(name=f"evale2e-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="",
                                 model="fake", is_default=True, params={"dim": 4})
        s.add_all([ws, emb_cfg])
        s.flush()
        docs = [_make_doc(s, ws.id, f"doc{i}.md") for i in range(3)]
        chunks = [_make_chunk(s, docs[0], 0, TARGET_TEXT)]
        for i, t in enumerate(OTHER_TEXTS):
            chunks.append(_make_chunk(s, docs[i % 3], (i % 3) + 1, t))
        for c, v in zip(chunks, vectors):
            _make_embedding(s, c, "fake", v)
        s.commit()
        yield ws.id
        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()
