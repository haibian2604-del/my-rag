"""--e2e 端到端评测主流程测试：mock LLM 跑 evaluate_e2e / print_e2e_report。

复用 test_eval_compare 的 seed 思路（fixture 在 evaluation/conftest.py 共享）：
手工插入 Chunk + ChunkEmbedding（fake 向量）使 hybrid 检索稳定命中目标关键词；
LLM 用带固定回复的 fake 对象替代，不发起真实请求。
"""
import pytest

from tests.evaluation.run_eval import (
    evaluate_e2e,
    get_e2e_llm_or_exit,
    print_e2e_report,
)
from tests.test_hybrid_search import QUERY

REFERENCE = "周末加班按 2 倍折算调休。"
QUERIES = [{"query": QUERY, "expect_keywords": ["火山"], "reference": REFERENCE}]


class MockLLM:
    """固定回复的假 LLM：reply 为 None 时 complete 抛错（模拟生成失败降级）。"""

    def __init__(self, reply: str | None):
        self.reply = reply

    async def complete(self, messages, timeout=None, **params) -> str:
        if self.reply is None:
            raise RuntimeError("模拟 LLM 故障")
        return self.reply


def test_evaluate_e2e_with_mock_llm(eval_seed):
    # 回答包含期望关键词且接近参考答案 → 命中 + 正 ROUGE-L
    llm = MockLLM("火山喷发时周末加班按 2 倍折算调休。")
    flags, rouges, latencies = evaluate_e2e(llm, eval_seed, QUERIES, top_k=5)
    assert flags == [True]
    assert len(rouges) == 1 and 0.0 < rouges[0] <= 1.0
    assert latencies and latencies[0] > 0


def test_evaluate_e2e_llm_failure_degrades(eval_seed):
    # LLM 生成失败 → 空回答，关键词未命中、ROUGE-L 记 0，不抛异常
    llm = MockLLM(None)
    flags, rouges, _ = evaluate_e2e(llm, eval_seed, QUERIES, top_k=5)
    assert flags == [False]
    assert rouges == [0.0]


def test_evaluate_e2e_keyword_miss(eval_seed):
    # 回答不含任何期望关键词 → 未命中，但 ROUGE-L 仍可能为正
    llm = MockLLM("周末加班按两倍折算调休。")
    flags, rouges, _ = evaluate_e2e(llm, eval_seed, QUERIES, top_k=5)
    assert flags == [False]
    assert rouges[0] > 0.0  # 与参考答案仍有公共子序列


def test_print_e2e_report(capsys):
    print_e2e_report(QUERIES, 5, [True], [0.75], [12.0])
    out = capsys.readouterr().out
    assert "✓" in out
    assert "关键词覆盖率" in out and "100%" in out
    assert "平均 ROUGE-L F1: 0.750" in out


def test_get_e2e_llm_or_exit_without_llm(client):
    # 默认 autouse fixture 已摘除所有默认 provider → 未配置 LLM 应 SystemExit
    with pytest.raises(SystemExit) as exc:
        get_e2e_llm_or_exit()
    assert "未配置 LLM" in str(exc.value)
