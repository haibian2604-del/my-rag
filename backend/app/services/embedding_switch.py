"""嵌入并行重嵌引擎：按 target_model 多版本并存，进度写 app_config。

状态机：running → done/failed；running 时拒绝再次启动。不删其他 model_name 的向量。
"""
import logging
from copy import deepcopy

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.entities import AppConfig, Chunk, ChunkEmbedding, Document
from app.services.ingestion.pipeline import build_embedding_provider, get_default_provider

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


def read_state(s: Session) -> dict:
    row = s.get(AppConfig, "embedding_switch")
    return dict(row.value) if row else {"state": "idle"}


def write_state(s: Session, **fields) -> None:
    row = s.get(AppConfig, "embedding_switch")
    if row:
        row.value = {**row.value, **fields}
    else:
        s.add(AppConfig(key="embedding_switch", value=fields))


async def reembed_all(target_model: str) -> None:
    with SessionLocal() as s:
        state = read_state(s)
        # switch 端点启动前已把自身目标写为 running；仅拒绝其他目标的并发任务
        if state.get("state") == "running" and state.get("target_model") != target_model:
            raise RuntimeError("已有重嵌任务在运行")
        emb_cfg = get_default_provider(s, "embedding")
        cfg = deepcopy(emb_cfg)
        cfg.model = target_model  # provider 配置行本身不被修改
        chunk_ids = s.execute(
            select(Chunk.id).join(Document, Chunk.document_id == Document.id)
            .where(Document.status == "ready").order_by(Chunk.id)
        ).scalars().all()
        total = len(chunk_ids)
        # 先删 target_model 已有向量（幂等），绝不删其他 model_name
        s.execute(delete(ChunkEmbedding).where(
            ChunkEmbedding.model_name == target_model,
            ChunkEmbedding.chunk_id.in_(chunk_ids),
        ))
        write_state(s, state="running", target_model=target_model, total=total, done=0, error=None)
        s.commit()

    provider = build_embedding_provider(cfg)
    done = 0
    last_id = 0
    try:
        while True:
            with SessionLocal() as s:
                batch = s.execute(
                    select(Chunk).join(Document, Chunk.document_id == Document.id)
                    .where(Document.status == "ready", Chunk.id > last_id)
                    .order_by(Chunk.id).limit(BATCH_SIZE)
                ).scalars().all()
                if not batch:
                    break
                last_id = batch[-1].id
                vectors = await provider.embed([c.content for c in batch])
                s.add_all([
                    ChunkEmbedding(chunk_id=c.id, workspace_id=c.workspace_id,
                                   model_name=target_model, dim=len(v), embedding=v)
                    for c, v in zip(batch, vectors)
                ])
                done += len(batch)
                write_state(s, done=done)
                s.commit()
    except Exception as e:
        with SessionLocal() as s:
            write_state(s, state="failed", error=str(e)[:2000])
            s.commit()
        logger.error("重嵌失败: %s", e)
        raise
    with SessionLocal() as s:
        write_state(s, state="done", done=total)
        s.commit()
