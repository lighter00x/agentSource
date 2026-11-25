"""DbTracer：把 Agent 运行记录写入 `runs` 表（执行方案 S2.11），替换 P1 的 JSONL 作为权威记录。

- `run_start` 创建（或恢复时更新）顶层 Run（run_type=agent）；
- `model_call`、`tool_call`、`validation`、`budget_exhausted`、`cancelled` 作为子 Run（parent_run_id=顶层 run_id）；
- `run_end` 更新顶层 Run 的状态（stop_reason）、汇总指标与结束时间；
- 可同时写 JSONL（`jsonl` 参数），便于本地调试。

Tracer 协议的 `event()` 是同步的：事件先进入队列，由后台任务按顺序写库；`await flush()` 等待全部写完。
字符串负载只保存摘要（与 JsonlTracer 相同的规则），避免把论文全文和模型输出整段写进数据库。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from drugpaper.agent.interfaces import Tracer
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import RunRow, utcnow
from drugpaper.tracing.jsonl import summarize

COMPONENT_VERSION = "p2"
CHILD_TYPES = {
    "model_call": "model_call",
    "tool_call": "tool",
    "validation": "validation",
    "budget_exhausted": "event",
    "cancelled": "event",
}


class DbTracer:
    def __init__(self, db: Database, *, jsonl: Tracer | None = None, user_id: str = "default") -> None:
        self.db = db
        self.jsonl = jsonl
        self.user_id = user_id
        self._queue: asyncio.Queue[tuple[str, str, dict[str, Any]] | None] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._meta: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []

    # ------------------------------------------------------------ Tracer 协议

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        if self.jsonl is not None:
            self.jsonl.event(run_id, kind, payload)
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._worker = asyncio.get_running_loop().create_task(self._run())
        self._queue.put_nowait((run_id, kind, dict(payload)))

    async def flush(self) -> None:
        if self._queue is not None:
            await self._queue.join()

    async def aclose(self) -> None:
        if self._queue is not None and self._worker is not None:
            await self._queue.join()
            self._queue.put_nowait(None)
            await self._worker
            self._queue = None
            self._worker = None

    # ------------------------------------------------------------ 写库

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await self._write(*item)
            except Exception as e:  # noqa: BLE001 - 追踪失败不能影响 Agent 运行
                self.errors.append(f"{type(e).__name__}: {e}")
            finally:
                self._queue.task_done()

    async def _write(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        p = summarize(payload)
        async with self.db.session(self.user_id) as s:
            if kind == "run_start":
                self._meta[run_id] = {
                    "trace_id": payload.get("trace_id"),
                    "document_id": payload.get("document_id"),
                }
                row = await s.get(RunRow, run_id)
                if row is None:
                    s.add(
                        RunRow(
                            id=run_id,
                            user_id=self.user_id,
                            document_id=payload.get("document_id"),
                            run_type="agent",
                            component=str(payload.get("agent") or "agent"),
                            component_version=COMPONENT_VERSION,
                            inputs={"user_input": p.get("user_input"), "budget": p.get("budget")},
                            parent_run_id=payload.get("parent_run_id"),
                            trace_id=payload.get("trace_id"),
                            status="running",
                            metrics={},
                        )
                    )
                else:  # 从检查点恢复
                    row.status = "running"
                    row.metrics = {
                        **(row.metrics or {}),
                        "resumed": int((row.metrics or {}).get("resumed", 0)) + 1,
                    }
                return
            if kind == "run_end":
                row = await s.get(RunRow, run_id)
                if row is None:
                    return
                row.status = str(payload.get("stop_reason") or "unknown")
                row.metrics = {
                    **(row.metrics or {}),
                    **{k: p.get(k) for k in ("steps", "model_calls", "tool_calls", "usage", "elapsed_ms")},
                }
                row.outputs = {"warnings": p.get("warnings") or []}
                row.error = {"message": p["error"]} if p.get("error") else None
                row.ended_at = utcnow()
                return
            meta = self._meta.get(run_id, {})
            run_type = CHILD_TYPES.get(kind, "event")
            ok = payload.get("ok", True)
            child = RunRow(
                id=f"{run_id}.{kind[0]}{uuid.uuid4().hex[:8]}",
                user_id=self.user_id,
                document_id=meta.get("document_id"),
                run_type=run_type,
                component=str(payload.get("name") or payload.get("phase") or kind),
                component_version=COMPONENT_VERSION,
                parent_run_id=run_id,
                trace_id=meta.get("trace_id"),
                status="ok" if ok else "error",
                started_at=utcnow(),
                ended_at=utcnow(),
            )
            if kind == "model_call":
                child.model = {"model": p.get("model"), "task": p.get("task")}
                child.metrics = {
                    k: p.get(k)
                    for k in ("input_tokens", "output_tokens", "latency_ms")
                    if p.get(k) is not None
                }
                child.outputs = {"tool_calls": p.get("tool_calls") or [], "content": p.get("content")}
                child.inputs = {"structured": p.get("structured")}
                if not ok:
                    child.error = {"message": p.get("error")}
            elif kind == "tool_call":
                child.inputs = {"args": p.get("args"), "call_id": p.get("call_id")}
                child.outputs = {
                    "output": p.get("output"),
                    "refs": p.get("refs"),
                    "truncated": p.get("truncated"),
                }
                child.metrics = {"latency_ms": p.get("latency_ms")}
                if not ok:
                    child.error = {"message": p.get("error")}
            else:
                child.outputs = p
                if kind == "validation":
                    child.status = (
                        "ok" if payload.get("ok") else ("retry" if payload.get("retry") else "failed")
                    )
            s.add(child)
