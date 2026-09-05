import asyncio
import logging

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import Document
from app.services.embedding_switch import reembed_all
from app.services.ingestion.pipeline import ingest_document

logger = logging.getLogger(__name__)


def run_ingestion_sync(document_id: int) -> None:
    asyncio.run(ingest_document(document_id))


def run_reembed_sync(target_model: str) -> None:
    asyncio.run(reembed_all(target_model))


def recover_interrupted() -> None:
    """启动时把 parsing/embedding 中断（及遗留 pending）的文档重跑。"""
    with SessionLocal() as s:
        stuck = s.execute(
            select(Document).where(Document.status.in_(["parsing", "embedding", "pending"]))
        ).scalars().all()
        ids = [d.id for d in stuck]
    if not ids:
        logger.info("启动恢复：无中断文档")
        return
    logger.info("启动恢复：重跑 %d 个中断文档 %s", len(ids), ids)
    for i in ids:
        try:
            run_ingestion_sync(i)
        except Exception as e:  # noqa: BLE001 — 状态已落库为 failed
            logger.warning("文档 %s 启动恢复重跑失败: %s", i, e)
