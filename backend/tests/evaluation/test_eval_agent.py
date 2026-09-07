"""--agent 对照模式评测测试：mock agent_stream 跑 evaluate_agent / print_agent_report。

真实 --agent 需要真实 LLM（agent_stream 完整链路），本文件只做 mock：
1. 用假 SSE 生成器替身 agent_stream，验证 evaluate_agent 的指标口径与
   error 事件降级（回答为空 → 未命中 / ROUGE-L 0）；
2. 对照表输出格式；未配置 LLM 的 SystemExit 中文提示。
"""
import json
from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Conversation
from tests.evaluation.run_eval import (
    evaluate_agent,
    get_e2e_llm_or_exit,
    print_agent_report,
)
from tests.test_hybrid_search import QUERY

REFERENCE = "周末加班按 2 倍折算调休。"
QUERIES = [{"query": QUERY, "expect_keywords": ["火山"], "reference": REFERENCE}]


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def fake_agent_stream(answer: str | None):
    """agent_stream 替身：answer 为 None 时只发 error 事件（模拟生成失败降级）。"""

    async def stream(conv_id: int, question: str, model=None):
        assert conv_id > 0 and question
        yield _sse({"type": "stage", "stage": "generating"})
        if answer is None:
            yield _sse({"type": "error", "message": "生成失败，请稍后重试。"})
        else:
            yield _sse({"type": "delta", "text": answer})
        yield _sse({"type": "citations", "items": []})
        yield _sse({"type": "done", "followups": []})

    return stream


def test_evaluate_agent_with_mock_stream(eval_seed, monkeypatch):
    # agent 回答包含期望关键词且接近参考答案 → 命中 + 正 ROUGE-L
    import tests.evaluation.run_eval as run_eval_mod
    monkeypatch.setattr(run_eval_mod, "agent_stream",
                        fake_agent_stream("火山喷发时周末加班按 2 倍折算调休。"))
    flags, rouges, latencies = evaluate_agent(eval_seed, QUERIES)
    assert flags == [True]
    assert len(rouges) == 1 and 0.0 < rouges[0] <= 1.0
    assert latencies and latencies[0] > 0


def test_evaluate_agent_error_event_degrades(eval_seed, monkeypatch):
    # agent_stream 降级为 error 事件 → 回答为空：未命中、ROUGE-L 记 0，不抛异常
    import tests.evaluation.run_eval as run_eval_mod
    monkeypatch.setattr(run_eval_mod, "agent_stream", fake_agent_stream(None))
    flags, rouges, _ = evaluate_agent(eval_seed, QUERIES)
    assert flags == [False]
    assert rouges == [0.0]


def test_print_agent_report(capsys):
    print_agent_report(QUERIES, 3, [True], [0.8], [False], [0.0], [50.0])
    out = capsys.readouterr().out
    assert "rag" in out and "agent" in out
    assert "✓" in out and "✗" in out
    assert "关键词覆盖率" in out and "100%" in out and "0%" in out
    assert "平均 ROUGE-L F1:  rag = 0.800   agent = 0.000" in out
    assert "agent 平均端到端延迟: 50ms" in out


def test_get_e2e_llm_or_exit_agent_flag_without_llm(client):
    # 默认 autouse fixture 已摘除所有默认 provider → --agent 未配置 LLM 应 SystemExit
    with pytest.raises(SystemExit) as exc:
        get_e2e_llm_or_exit("--agent")
    assert "未配置 LLM" in str(exc.value) and "--agent" in str(exc.value)


def test_evaluate_agent_conversation_cleanup(eval_seed, monkeypatch):
    # 每条查询建临时会话，评测结束后不留孤儿行（delete_workspace 级联删除前的状态检查）
    import tests.evaluation.run_eval as run_eval_mod
    monkeypatch.setattr(run_eval_mod, "agent_stream",
                        fake_agent_stream("按 2 倍折算调休。"))
    evaluate_agent(eval_seed, QUERIES)
    with SessionLocal() as s:
        convs = s.query(Conversation).filter(
            Conversation.workspace_id == eval_seed).all()
        assert len(convs) == 1
        assert convs[0].title == "eval-agent"
        for c in convs:
            s.delete(c)
        s.commit()
