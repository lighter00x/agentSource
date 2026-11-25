"""扩展点的 P1 默认实现。"""

from __future__ import annotations

from pydantic import BaseModel

from drugpaper.agent.state import AgentState, state_messages
from drugpaper.gateway.base import Message
from drugpaper.tools.protocol import Tool
from drugpaper.tools.registry import ToolRegistry


class StaticToolset:
    """固定工具列表（按名称从注册表取）。"""

    def __init__(self, registry: ToolRegistry, names: list[str]) -> None:
        missing = [n for n in names if n not in registry]
        if missing:
            raise KeyError(f"工具未注册：{', '.join(missing)}")
        self._tools = [registry.get(n) for n in names]

    def tools_for(self, state: AgentState) -> list[Tool]:
        return list(self._tools)


class SimpleAssembler:
    """系统提示 + 用户问题 + 历史消息。"""

    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    def build(self, state: AgentState) -> list[Message]:
        return [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=state["user_input"]),
            *state_messages(state),
        ]


class NoopValidator:
    def validate(self, answer: BaseModel, state: AgentState) -> list[str]:
        return []
