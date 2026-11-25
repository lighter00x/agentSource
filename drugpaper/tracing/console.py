"""ConsolePrinter：把 Tracer 事件实时打印为树状结构，便于调试。"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markup import escape


class ConsolePrinter:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True)
        self._step: dict[str, int] = {}

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        c = self.console
        p = payload
        if kind == "run_start":
            self._step[run_id] = 0
            c.print(f"[bold cyan]▶ run {escape(run_id)}[/] agent={escape(str(p.get('agent')))}")
            c.print(f"│  问题：{escape(str(p.get('user_input', '')))}")
        elif kind == "model_call":
            if p.get("phase") == "agent":
                self._step[run_id] = self._step.get(run_id, 0) + 1
            step = self._step.get(run_id, 0)
            if p.get("ok"):
                tools = ", ".join(p.get("tool_calls") or []) or "（无工具调用）"
                c.print(
                    f"├─ [bold]step {step}[/] model[{escape(str(p.get('phase')))}] "
                    f"{p.get('input_tokens', 0)}→{p.get('output_tokens', 0)} tok "
                    f"{p.get('latency_ms', 0)} ms  → {escape(tools)}"
                )
            else:
                c.print(
                    f"├─ [red]model[{escape(str(p.get('phase')))}] 失败：{escape(str(p.get('error')))}[/]"
                )
        elif kind == "tool_call":
            mark = "[green]✓[/]" if p.get("ok") else "[red]✗[/]"
            extra = " [yellow](truncated)[/]" if p.get("truncated") else ""
            err = f" {escape(str(p.get('error')))}" if p.get("error") else ""
            c.print(
                f"│  ├─ {mark} {escape(str(p.get('name')))}({escape(str(p.get('args')))}) "
                f"refs={p.get('refs', 0)} {p.get('latency_ms', 0)} ms{extra}{err}"
            )
        elif kind == "validation":
            if p.get("ok"):
                c.print("├─ [green]validation ok[/]")
            else:
                issues = "; ".join(p.get("issues") or [])
                tag = "retry" if p.get("retry") else "give up"
                c.print(f"├─ [yellow]validation {tag}[/]：{escape(issues)}")
        elif kind == "budget_exhausted":
            c.print(f"├─ [yellow]budget exhausted[/]：{escape(str(p.get('reason')))}")
        elif kind == "cancelled":
            c.print(f"└─ [red]cancelled[/]：{escape(str(p.get('reason')))}")
        elif kind == "run_end":
            c.print(
                f"└─ [bold]end[/] stop_reason={escape(str(p.get('stop_reason')))} "
                f"steps={p.get('steps')} model_calls={p.get('model_calls')} "
                f"tool_calls={p.get('tool_calls')} {p.get('elapsed_ms')} ms"
            )
            self._step.pop(run_id, None)
