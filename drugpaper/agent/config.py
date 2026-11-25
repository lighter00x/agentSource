"""AgentConfig：一个 Agent 与另一个 Agent 的全部差别（提示、工具集、校验、输出 Schema、预算、模型任务）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from drugpaper.agent.defaults import SimpleAssembler
from drugpaper.agent.interfaces import AnswerValidator, ContextAssembler, ToolsetProvider
from drugpaper.agent.state import AgentAnswer, Budget


@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    toolset: ToolsetProvider
    assembler: ContextAssembler | None = None  # 为 None 时使用 SimpleAssembler(system_prompt)
    validators: list[AnswerValidator] = field(default_factory=list)
    answer_model: type[BaseModel] = AgentAnswer
    budget: Budget = field(default_factory=Budget)
    task: str = "default"  # 模型任务名（P3 起用于路由）
    temperature: float = 0.0
    max_tokens: int = 2048
    model_timeout_s: float = 60
    max_validation_retries: int = 1

    def get_assembler(self) -> ContextAssembler:
        return self.assembler if self.assembler is not None else SimpleAssembler(self.system_prompt)
