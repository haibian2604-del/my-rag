"""Agent 构建：OpenAIChatModel（OpenAI 兼容端点）+ 知识库/网页工具注册。"""
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.db import SessionLocal
from app.services.agent.deps import Deps
from app.services.agent.prompts import AGENT_SYSTEM_PROMPT
from app.services.agent.tools import kb_search_impl, read_url_impl
from app.services.ingestion.pipeline import default_provider_or_none
from app.services.providers_service import decrypt_api_key

# 未配置默认 LLM provider 时构造 agent 的兜底端点（模型仅在真实运行时才会被访问；
# 测试一律通过 agent.iter(model=TestModel()) 覆盖，不会触网）
_FALLBACK_BASE_URL = "http://localhost:8000/v1"
_FALLBACK_MODEL = "default"


def _llm_endpoint() -> tuple[str, str, str | None]:
    """读取默认 llm provider 配置，返回 (base_url, model, api_key)。

    未配置时用兜底端点（不抛异常），保证 agent 可构建（测试注入 TestModel 即可）。
    """
    with SessionLocal() as s:
        cfg = default_provider_or_none(s, "llm")
        if cfg is None:
            return _FALLBACK_BASE_URL, _FALLBACK_MODEL, None
        api_key: str | None
        if cfg.provider == "fake":
            # fake provider 无真实端点，同样走兜底（agent 模式不支持 fake LLM）
            return _FALLBACK_BASE_URL, _FALLBACK_MODEL, None
        api_key = decrypt_api_key(cfg)
        return cfg.base_url, cfg.model, api_key


def build_agent(workspace_id: int, max_turns: int = 6) -> Agent[Deps, str]:
    """构建带 kb_search / read_url 工具的 Agent（deps_type=Deps，输出纯文本）。"""
    base_url, model_name, api_key = _llm_endpoint()
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    model = OpenAIChatModel(model_name, provider=provider)
    agent: Agent[Deps, str] = Agent(
        model,
        deps_type=Deps,
        output_type=str,
        system_prompt=AGENT_SYSTEM_PROMPT,
    )

    @agent.tool
    async def kb_search(ctx: RunContext[Deps], query: str) -> str:
        """在用户知识库中检索与 query 最相关的资料片段，作为回答依据。"""
        return await kb_search_impl(ctx.deps, query)

    @agent.tool
    async def read_url(ctx: RunContext[Deps], url: str) -> str:
        """抓取并阅读指定网页的正文内容（最多 2000 字符）。仅限公网 http/https 地址。"""
        return await read_url_impl(url)

    return agent
