"""中文检索评测：recall@5 与平均检索延迟。

用法（backend 目录下，需已在设置页配置嵌入模型，推荐 oMLX 真实嵌入）：

    uv run python -m tests.evaluation.run_eval            # top_k=5
    uv run python -m tests.evaluation.run_eval --top-k 3

流程：创建临时评测工作区 → 摄取 sample_docs/ 下的样例文档 → 逐条跑
qa_set.jsonl 的查询 → 期望关键词（任一写法变体）出现在 top_k 任一 chunk 的
正文中即算命中 → 输出每条结果与 recall@k、平均延迟。
评测工作区默认用后即删（--keep 保留）。
"""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import Document, ProviderConfig, Workspace
from app.services.ingestion.pipeline import doc_file_path, ingest_document
from app.services.retrieval.search import search

EVAL_DIR = Path(__file__).parent
DOCS_DIR = EVAL_DIR / "sample_docs"


def load_queries() -> list[dict]:
    with open(EVAL_DIR / "qa_set.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in map(str.strip, f) if line]


def create_workspace() -> int:
    with SessionLocal() as s:
        ws = Workspace(name=f"eval-{int(time.time())}", description="检索评测临时工作区")
        s.add(ws)
        s.commit()
        return ws.id


def add_document(ws_id: int, path: Path) -> int:
    content = path.read_bytes()
    with SessionLocal() as s:
        doc = Document(
            workspace_id=ws_id, filename=path.name, source_type="upload",
            mime="text/markdown", size=len(content),
            checksum=hashlib.sha256(content).hexdigest(), status="pending",
        )
        s.add(doc)
        s.flush()
        dest = doc_file_path(doc)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        s.commit()
        return doc.id


def delete_workspace(ws_id: int) -> None:
    # 先记下磁盘文件路径，删除（级联清表）后再 unlink
    with SessionLocal() as s:
        paths = [doc_file_path(d) for d in s.execute(
            select(Document).where(Document.workspace_id == ws_id)).scalars()]
        ws = s.get(Workspace, ws_id)
        if ws:
            s.delete(ws)
        s.commit()
    for p in paths:
        p.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="中文检索评测：recall@k 与延迟")
    parser.add_argument("--top-k", type=int, default=5, help="每个查询检索的 chunk 数（默认 5）")
    parser.add_argument("--keep", action="store_true", help="保留评测工作区与文档")
    args = parser.parse_args()

    with SessionLocal() as s:
        cfg = s.execute(
            select(ProviderConfig).where(
                ProviderConfig.kind == "embedding", ProviderConfig.is_default.is_(True))
        ).scalar_one_or_none()
    if not cfg:
        raise SystemExit("未配置嵌入模型：请先在设置页配置嵌入模型后再评测")

    ws_id = create_workspace()
    try:
        print(f"评测工作区 #{ws_id}，摄取样例文档…")
        for doc_path in sorted(DOCS_DIR.glob("*.md")):
            doc_id = add_document(ws_id, doc_path)
            asyncio.run(ingest_document(doc_id))
            with SessionLocal() as s:
                status = s.get(Document, doc_id).status
            print(f"  {doc_path.name}: {status}")
            if status != "ready":
                raise SystemExit(f"{doc_path.name} 摄取失败，终止评测")

        queries = load_queries()
        print(f"\n共 {len(queries)} 条查询，top_k={args.top_k}\n")
        hits = 0
        latencies = []
        for q in queries:
            t0 = time.perf_counter()
            found = asyncio.run(search(ws_id, q["query"], top_k=args.top_k))
            dt = (time.perf_counter() - t0) * 1000
            latencies.append(dt)
            corpus = "\n".join(h["content"] for h in found)
            # expect_keywords 是同一事实的多种写法（如 "15天"/"15 天"），任一命中即算
            ok = any(kw in corpus for kw in q["expect_keywords"])
            hits += ok
            mark = "✓" if ok else "✗"
            detail = "" if ok else "（关键词均未出现在 top_k 中）"
            print(f"  {mark} {q['query']}  {dt:6.0f}ms{detail}")

        print(f"\nrecall@{args.top_k}: {hits}/{len(queries)} = {hits / len(queries):.0%}")
        print(f"平均检索延迟: {sum(latencies) / len(latencies):.0f}ms，"
              f"最长 {max(latencies):.0f}ms")
    finally:
        if not args.keep:
            delete_workspace(ws_id)
            print("评测工作区已清理")


if __name__ == "__main__":
    main()
