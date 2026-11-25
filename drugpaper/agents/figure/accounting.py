from __future__ import annotations

from pydantic import BaseModel, Field


class CallAccounting(BaseModel):
    exploration_calls: int = 0
    fixed_calls: int = 0
    exception_calls: int = 0
    remediation_calls: int = 0
    timings_ms: dict[str, int] = Field(default_factory=dict)
    by_task: dict[str, int] = Field(default_factory=dict)
    cached_steps: int = 0
    warnings: list[str] = Field(default_factory=list)

    def record(self, task: str, calls: int, elapsed_ms: int, category: str = "fixed") -> None:
        self.by_task[task] = self.by_task.get(task, 0) + calls
        self.timings_ms[task] = self.timings_ms.get(task, 0) + elapsed_ms
        if category == "fixed":
            self.fixed_calls += min(calls, 1)
            self.exception_calls += max(calls - 1, 0)
        elif category == "exploration":
            self.exploration_calls += calls
        elif category == "exception":
            self.exception_calls += calls
        else:
            self.remediation_calls += calls
        if self.exception_calls > 4 and "exception_call_limit" not in self.warnings:
            self.warnings.append("exception_call_limit")

    @property
    def total(self) -> int:
        return self.exploration_calls + self.fixed_calls + self.exception_calls + self.remediation_calls
