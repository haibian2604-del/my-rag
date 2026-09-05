"""中文检索评测：recall@5 与平均检索延迟。

用法（backend 目录下，需已在设置页配置嵌入模型，推荐 oMLX 真实嵌入）：

    uv run python -m tests.evaluation.run_eval            # top_k=5
    uv run python -m tests.evaluation.run_eval --top-k 3
    uv run python -m tests.evaluation.run_eval --compare  # hybrid vs vector 对比

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


def evaluate_recall(ws_id: int, queries: list[dict], top_k: int,
                    hybrid: bool) -> tuple[list[bool], list[float]]:
    """对每条查询跑一次检索，返回（每条是否命中, 每条延迟 ms）。

    run_eval 主流程与 --compare 模式、冒烟测试共用。
    """
    hit_flags: list[bool] = []
    latencies: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        found = asyncio.run(search(ws_id, q["query"], top_k=top_k, hybrid=hybrid))
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        corpus = "\n".join(h["content"] for h in found)
        # expect_keywords 是同一事实的多种写法（如 "15天"/"15 天"），任一命中即算
        hit_flags.append(any(kw in corpus for kw in q["expect_keywords"]))
    return hit_flags, latencies


def truncate(text: str, width: int = 40) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def print_compare_report(queries: list[dict], top_k: int,
                         hybrid_flags: list[bool], hybrid_lat: list[float],
                         vector_flags: list[bool], vector_lat: list[float]) -> None:
    hr = sum(hybrid_flags) / len(queries)
    vr = sum(vector_flags) / len(queries)
    havg = sum(hybrid_lat) / len(hybrid_lat)
    vavg = sum(vector_lat) / len(vector_lat)
    print(f"{'查询':<42} hybrid  vector")
    for q, hf, vf in zip(queries, hybrid_flags, vector_flags):
        print(f"  {truncate(q['query'], 38):<40} "
              f"{'✓' if hf else '✗'}       {'✓' if vf else '✗'}")
    print(f"\nrecall@{top_k}:  hybrid = {sum(hybrid_flags)}/{len(queries)} = {hr:.0%}"
          f"   vector = {sum(vector_flags)}/{len(queries)} = {vr:.0%}")
    print(f"平均延迟: hybrid = {havg:.0f}ms   vector = {vavg:.0f}ms")
    verdict = "是" if hr >= vr else "否（需调 RRF k 常数或 FTS 权重后复测）"
    print(f"hybrid ≥ vector: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="中文检索评测：recall@k 与延迟")
    parser.add_argument("--top-k", type=int, default=5, help="每个查询检索的 chunk 数（默认 5）")
    parser.add_argument("--keep", action="store_true", help="保留评测工作区与文档")
    parser.add_argument("--no-hybrid", action="store_true", help="关闭混合检索（仅向量召回）")
    parser.add_argument("--compare", action="store_true",
                        help="对比模式：同一工作区分别跑 hybrid 与纯向量检索")
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

        if args.compare:
            print("跑 hybrid 检索…")
            hybrid_flags, hybrid_lat = evaluate_recall(ws_id, queries, args.top_k, True)
            print("跑纯向量检索…")
            vector_flags, vector_lat = evaluate_recall(ws_id, queries, args.top_k, False)
            print()
            print_compare_report(queries, args.top_k,
                                 hybrid_flags, hybrid_lat, vector_flags, vector_lat)
        else:
            hybrid = not args.no_hybrid
            flags, latencies = evaluate_recall(ws_id, queries, args.top_k, hybrid)
            for q, ok, dt in zip(queries, flags, latencies):
                mark = "✓" if ok else "✗"
                detail = "" if ok else "（关键词均未出现在 top_k 中）"
                print(f"  {mark} {q['query']}  {dt:6.0f}ms{detail}")
            hits = sum(flags)
            print(f"\nrecall@{args.top_k}: {hits}/{len(queries)} = {hits / len(queries):.0%}")
            print(f"平均检索延迟: {sum(latencies) / len(latencies):.0f}ms，"
                  f"最长 {max(latencies):.0f}ms")
    finally:
        if not args.keep:
            delete_workspace(ws_id)
            print("评测工作区已清理")


if __name__ == "__main__":
    main()
