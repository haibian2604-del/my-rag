"""HNSW 向量索引：ensure_vector_index 幂等建索引（表达式部分索引形态校验）。

chunk_embeddings.embedding 列无 typmod，索引必须是
((embedding::vector(N)) vector_cosine_ops) WHERE dim = N 的表达式部分索引，
且与检索侧 cast 完全一致才能命中。
"""
from sqlalchemy import text

from app.core.db import SessionLocal, ensure_vector_index


def test_ensure_vector_index_idempotent():
    ensure_vector_index(4)
    ensure_vector_index(4)  # 二次调用（IF NOT EXISTS）不应报错
    with SessionLocal() as s:
        ddl = s.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_ce_hnsw_4'")
        ).scalar_one()
    assert "USING hnsw" in ddl
    assert "vector_cosine_ops" in ddl
    assert "::vector(4)) vector_cosine_ops" in ddl  # pg 会规范化为 ((embedding)::vector(4))
    assert "dim = 4" in ddl  # pg 会规范化为 WHERE (dim = 4)
