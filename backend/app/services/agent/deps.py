"""Agent 运行依赖定义（独立模块，避免 agent/tools 循环导入）。"""
from dataclasses import dataclass, field


@dataclass
class Deps:
    """Agent 运行依赖：工作区、引用收集、工具时间线（T2 持久化 trace）。"""

    workspace_id: int
    citations: list[dict] = field(default_factory=list)
    max_turns: int = 6
    trace: list[dict] = field(default_factory=list)
