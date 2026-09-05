import asyncio
import logging

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import Document
from app.services.embedding_switch import read_state, reembed_all, write_state
from app.services.ingestion.pipeline import ingest_document

logger = logging.getLogger(__name__)


def run_ingestion_sync(document_id: int) -> None:
    asyncio.run(ingest_document(document_id))


def run_reembed_sync(target_model: str) -> None:
    asyncio.run(reembed_all(target_model))


def _recover_embedding_switch() -> None:
    """进程重启会中断进程内 BackgroundTasks 里的重嵌任务，恢复状态机。"""
    with SessionLocal() as s:
        state = read_state(s)
        if state.get("state") == "running":
            write_state(s, state="failed", error="服务重启中断重嵌")
            s.commit()
            logger.warning("启动恢复：重嵌任务因服务重启标记为 failed")


def recover_interrupted() -> None:
    """启动时恢复重嵌状态机，并把 parsing/embedding 中断（及遗留 pending）的文档重跑。"""
    try:
        _recover_embedding_switch()
    except Exception as e:  # noqa: BLE001 — 恢复失败不阻塞文档重跑
        logger.warning("启动恢复 embedding_switch 状态失败: %s", e)
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
