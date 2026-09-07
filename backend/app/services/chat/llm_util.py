"""LLM 非流式一次性生成的公共小工具：建议问题/追问/摘要等特性复用。

约定：生成类能力失败一律降级——llm_complete 失败/超时返回 None，
parse_json_list 解析失败返回空列表，绝不让调用方（接口/问答流）报错中断。
"""
import asyncio
import json

# 生成类辅助能力的统一超时（秒）
LLM_UTIL_TIMEOUT = 8.0


async def llm_complete(llm, messages: list[dict], timeout: float = LLM_UTIL_TIMEOUT) -> str | None:
    """非流式调用 LLM 生成一段文本；超时或任何异常返回 None（不抛出）。"""
    try:
        return await asyncio.wait_for(llm.complete(messages), timeout=timeout)
    except Exception:  # noqa: BLE001 — 生成失败降级，不影响主流程
        return None


def parse_json_list(text: str | None) -> list[str]:
    """从容错文本中解析字符串数组：优先整体解析，失败则提取首个 JSON 数组片段。"""
    if not text:
        return []
    raw = text.strip()
    candidates = [raw]
    # 常见容错：LLM 用 ```json 包裹，或数组前后混入说明文字
    if "```" in raw:
        for seg in raw.split("```"):
            seg = seg.removeprefix("json").strip()
            if seg.startswith("["):
                candidates.insert(0, seg)
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        candidates.insert(0, raw[start : end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            return [item.strip() for item in data if isinstance(item, str) and item.strip()]
    return []
