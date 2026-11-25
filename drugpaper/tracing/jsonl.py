"""JsonlTracer：每个 run 写一份 `runs/<run_id>.jsonl`，每行一个事件（P2 换成 RunRecord 入库）。

每行包含 ts、run_id、trace_id、parent_run_id、kind 以及事件负载。
字符串只保留摘要（默认前 200 字符），以免日志过大、泄露论文全文或模型输出。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

SUMMARY_CHARS = 200
MAX_LIST_ITEMS = 50


def summarize(value: Any, limit: int = SUMMARY_CHARS) -> Any:
    """递归截断字符串与过长列表，保证事件体积可控。"""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): summarize(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        items = [summarize(v, limit) for v in list(value)[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"…(+{len(value) - MAX_LIST_ITEMS})")
        return items
    if value is None or isinstance(value, bool | int | float):
        return value
    return summarize(repr(value), limit)


class JsonlTracer:
    def __init__(self, runs_dir: Path | str = "runs") -> None:
        self.runs_dir = Path(runs_dir)
        self._meta: dict[str, dict[str, str | None]] = {}
        self._lock = threading.Lock()

    def path_for(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.jsonl"

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        if kind == "run_start":
            self._meta[run_id] = {
                "trace_id": payload.get("trace_id"),
                "parent_run_id": payload.get("parent_run_id"),
            }
        meta = self._meta.get(run_id, {"trace_id": None, "parent_run_id": None})
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "trace_id": meta["trace_id"],
            "parent_run_id": meta["parent_run_id"],
            "kind": kind,
        }
        # 保留字段不允许被负载覆盖
        record.update(summarize({k: v for k, v in payload.items() if k not in record}))
        line = orjson.dumps(record) + b"\n"
        with self._lock:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            with self.path_for(run_id).open("ab") as f:
                f.write(line)
        if kind == "run_end":
            self._meta.pop(run_id, None)


def read_events(path: Path | str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in Path(path).read_bytes().splitlines():
        if line.strip():
            out.append(orjson.loads(line))
    return out


class CompositeTracer:
    """把事件同时分发给多个 Tracer（例如 JSONL + 控制台）。"""

    def __init__(self, tracers: Iterable[Any]) -> None:
        self._tracers = list(tracers)

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        for t in self._tracers:
            t.event(run_id, kind, payload)


class NullTracer:
    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        return None
