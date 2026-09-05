"""历史 chunk 的 FTS 回填：批 500、id keyset 分批，只处理 fts IS NULL（幂等）。

用法（backend 目录下）：

    uv run python -m app.services.ingestion.backfill_fts
"""
from sqlalchemy import text

from app.core.db import SessionLocal
from app.services.ingestion.tokenize import tokenize_for_fts

BATCH_SIZE = 500


def backfill_fts(batch_size: int = BATCH_SIZE) -> int:
    """为 fts IS NULL 的 chunk 回填 tsvector，返回回填总数。"""
    total = 0
    last_id = 0
    while True:
        with SessionLocal() as s:
            rows = s.execute(
                text(
                    "SELECT id, content FROM chunks "
                    "WHERE fts IS NULL AND id > :last ORDER BY id LIMIT :batch"
                ),
                {"last": last_id, "batch": batch_size},
            ).all()
            for chunk_id, content in rows:
                s.execute(
                    text(
                        "UPDATE chunks SET fts = to_tsvector('simple', :tokens) "
                        "WHERE id = :id"
                    ),
                    {"tokens": tokenize_for_fts(content), "id": chunk_id},
                )
            s.commit()
        if not rows:
            break
        total += len(rows)
        last_id = rows[-1][0]
    return total


if __name__ == "__main__":
    n = backfill_fts()
    print(f"回填完成：{n} 条 chunk 的 fts 已写入")
