import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

logger = logging.getLogger(__name__)


def ensure_vector_index(dim: int) -> None:
    """幂等为指定维度创建 HNSW 表达式部分索引。

    chunk_embeddings.embedding 列无 typmod（多维度并存），pgvector 不能直接
    建 HNSW，必须用表达式部分索引：((embedding::vector(N)) vector_cosine_ops)
    WHERE dim = N（dim 为内部 int，内联安全）。索引名与表达式必须与检索侧
    的 cast 保持一致才能命中。

    索引缺失只影响性能：建索引失败仅记录告警，不阻塞调用方（如模型激活流程），
    沿用「检索永不因索引缺失失败」的降级原则。
    """
    dim = int(dim)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_ce_hnsw_{dim} ON chunk_embeddings "
                f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) "
                f"WHERE dim = {dim}"
            ))
    except Exception:
        logger.warning("为 dim=%s 创建 HNSW 索引失败（不影响激活/检索）", dim, exc_info=True)
