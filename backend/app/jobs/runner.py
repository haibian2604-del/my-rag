import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import Document
from app.services.ingestion.pipeline import ingest_document


def run_ingestion_sync(document_id: int) -> None:
    asyncio.run(ingest_document(document_id))


def recover_interrupted() -> None:
    """启动时把 parsing/embedding 中断（及遗留 pending）的文档重跑。"""
    with SessionLocal() as s:
        stuck = s.execute(
            select(Document).where(Document.status.in_(["parsing", "embedding", "pending"]))
        ).scalars().all()
        ids = [d.id for d in stuck]
    for i in ids:
        try:
            run_ingestion_sync(i)
        except Exception:  # noqa: BLE001, S110 — 状态已落库为 failed
            pass
