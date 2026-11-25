"""Evidence-based completion gate for agents executing an explicit workflow."""

from __future__ import annotations

import json

from pydantic import BaseModel

from drugpaper.agent.state import AgentState
from drugpaper.gateway.base import Message


class RequiredCallsValidator:
    """Require successful calls, without prescribing order, arguments or chemistry."""

    def __init__(self, required: set[str], *, any_of: list[set[str]] | None = None) -> None:
        self.required = frozenset(required)
        self.any_of = [frozenset(group) for group in any_of or []]

    def validate(self, answer: BaseModel, state: AgentState) -> list[str]:
        completed = set()
        for raw in state.get("messages", []):
            message = Message.model_validate(raw)
            if message.role != "tool" or not message.name:
                continue
            try:
                result = json.loads(message.text())
            except (ValueError, TypeError):
                continue
            if isinstance(result, dict) and result.get("ok") is True:
                completed.add(message.name)
        missing = sorted(self.required - completed)
        missing.extend(
            "至少一个图类处理调用：[" + ", ".join(sorted(group)) + "]"
            for group in self.any_of
            if not group & completed
        )
        return (
            [
                "工作尚未完成，缺少成功调用："
                + ", ".join(missing)
                + "。请根据已披露的卡片/参数与错误继续实际执行；已调用底层Tool不等于完成对应Skill。"
            ]
            if missing
            else []
        )
