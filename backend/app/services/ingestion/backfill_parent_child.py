"""父子分块回填脚本：遍历 status=ready 的文档逐个重跑摄取管线。

旧文档的 chunk 无父子结构（全部 parent_id 为空），检索行为不受影响；
回填后子块被嵌入/建 FTS，命中时聚合返回父块全文上下文。

用法（backend 目录下）：

    uv run python -m app.services.ingestion.backfill_parent_child

逐文档调用 run_ingestion_sync 重跑（解析 → 父子切分 → 嵌入 → 入库）；
单个文档失败记录后继续，结尾打印成功/失败汇总。
"""
import logging

from sqlalchemy import select

from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Document

logger = logging.getLogger(__name__)


def backfill_parent_child() -> tuple[int, list[tuple[int, str]]]:
    """对全部 ready 文档重跑摄取，返回（成功数, [(文档 id, 失败原因), ...]）。"""
    with SessionLocal() as s:
        doc_ids = s.execute(
            select(Document.id).where(Document.status == "ready").order_by(Document.id)
        ).scalars().all()
    print(f"共 {len(doc_ids)} 个 ready 文档待回填父子分块")
    ok = 0
    failures: list[tuple[int, str]] = []
    for i, doc_id in enumerate(doc_ids, 1):
        try:
            run_ingestion_sync(doc_id)
            ok += 1
            print(f"  [{i}/{len(doc_ids)}] 文档 {doc_id} 回填成功")
        except Exception as e:  # noqa: BLE001 — 单文档失败不阻塞整体回填
            reason = str(e)[:500]
            failures.append((doc_id, reason))
            print(f"  [{i}/{len(doc_ids)}] 文档 {doc_id} 回填失败：{reason}")
            logger.warning("文档 %s 父子分块回填失败: %s", doc_id, e)
    print(f"回填汇总：成功 {ok}，失败 {len(failures)}，共 {len(doc_ids)}")
    return ok, failures


if __name__ == "__main__":
    backfill_parent_child()
