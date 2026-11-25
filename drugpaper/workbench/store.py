from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STAGES = [
    "classify",
    "plan",
    "detect",
    "recognize",
    "som",
    "structure",
    "post",
    "check_l1",
    "check_l2",
    "report",
    "deliver",
]
LABELS = [
    "类型判断",
    "Skill 路由",
    "分子检测",
    "分子识别",
    "编号图",
    "结构化提取与审查",
    "回填与映射",
    "确定性检查",
    "模型复核",
    "图表报告",
    "精简过程结果",
]
ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def write(path: Path, value: Any, *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, ensure_ascii=False, default=str, indent=indent)
        if indent is not None:
            f.write("\n")
    os.replace(tmp, path)


def read(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def root_for(sv: Any, document_id: str, revision: int) -> Path:
    return (
        Path(sv.settings.blob_root).resolve().parent
        / "workbench"
        / digest(["default", document_id, revision])
    )


def figure_dir(root: Path, node_ref: str) -> Path:
    return root / "figures" / digest(node_ref)


def safe_path(root: Path, name: str) -> Path:
    p = (root / name).resolve()
    if not name or Path(name).is_absolute() or not p.is_relative_to(root.resolve()) or p.is_symlink():
        raise ValueError("非法文件路径")
    return p


def editable(name: str) -> bool:
    p = Path(name)
    return (
        not p.is_absolute()
        and ".." not in p.parts
        and p.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}
        and (
            name.startswith(
                ("drugpaper/skills/", "drugpaper/tools/", "drugpaper/chem/", "drugpaper/vision/", "prompts/")
            )
            or name == "config/routing.yaml"
        )
    )


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split()[2] != "Z"
    except (ProcessLookupError, PermissionError):
        return False


def run_dir(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"wd_[A-Za-z0-9]+", run_id):
        raise ValueError("非法运行ID")
    return root / "runs" / run_id


async def materials_stamp(sv: Any, document_id: str, revision: int, ref: str) -> str:
    from sqlalchemy import or_, select

    from drugpaper.persistence.models import NodeRow

    local = ref.rsplit("/", 1)[-1]
    async with sv.db.session() as session:
        rows = list(
            (
                await session.execute(
                    select(NodeRow)
                    .where(
                        NodeRow.document_id == document_id,
                        NodeRow.revision == revision,
                        or_(NodeRow.local_id == local, NodeRow.parent_local_id == local),
                    )
                    .order_by(NodeRow.local_id)
                )
            ).scalars()
        )
        return digest(
            [
                {
                    "id": r.local_id,
                    "geometry": r.geometry,
                    "status": r.status,
                    "structure_id": (r.attributes or {}).get("structure_id"),
                }
                for r in rows
            ]
        )
