"""LLM 非流式/流式生成的公共小工具：建议问题/追问/摘要/问答流复用。

约定：非流式生成失败一律降级——llm_complete 失败/超时返回 None，
parse_json_list 解析失败返回空列表，绝不让调用方（接口/问答流）报错中断。
"""
import asyncio
import json

import httpx

# 生成类辅助能力的统一超时（秒）
LLM_UTIL_TIMEOUT = 8.0

# 流式生成护栏：首字 30s、流间空闲 60s 超时；等待期每 15s 产出一次心跳
STREAM_FIRST_TIMEOUT = 30.0
STREAM_IDLE_TIMEOUT = 60.0
STREAM_HEARTBEAT_INTERVAL = 15.0


async def llm_complete(llm, messages: list[dict], timeout: float = LLM_UTIL_TIMEOUT,
                       **params) -> str | None:
    """非流式调用 LLM 生成一段文本；超时或任何异常返回 None（不抛出）。"""
    try:
        return await asyncio.wait_for(llm.complete(messages, **params), timeout=timeout)
    except Exception:  # noqa: BLE001 — 生成失败降级，不影响主流程
        return None


async def guarded_llm_stream(llm, messages: list[dict], **params):
    """流式生成护栏：产出 delta 文本，等待期周期性产出 None（心跳）。

    首字超过 STREAM_FIRST_TIMEOUT、或两个 delta 间隔超过 STREAM_IDLE_TIMEOUT
    时抛 TimeoutError，由调用方转为 error 事件。delta 的生产放在独立 task，
    超时只打断等待、由 finally 取消 pump，避免取消半途的 __anext__ 损坏流。
    """
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    async def _pump():
        try:
            async for delta in llm.stream_chat(messages, **params):
                await queue.put(delta)
        except Exception as e:  # noqa: BLE001 — 异常经队列转交消费侧
            await queue.put(e)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(_pump())
    started = False
    try:
        while True:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + (STREAM_FIRST_TIMEOUT if not started else STREAM_IDLE_TIMEOUT)
            while True:
                wait = min(deadline - loop.time(), STREAM_HEARTBEAT_INTERVAL)
                if wait <= 0:
                    raise TimeoutError("LLM stream timeout")
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=wait)
                    break
                except TimeoutError:
                    yield None  # 心跳
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                raise item
            started = True
            yield item
    finally:
        task.cancel()


def friendly_error(e: Exception) -> str:
    """异常 → 面向用户的固定中文文案（细节只进日志，不透传）。"""
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "无法连接模型服务，请检查模型配置"
    if isinstance(e, TimeoutError):
        return "模型响应超时，请稍后重试"
    return "服务异常，请稍后重试"


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
