"""Work Unit：重跑的最小单位（设计文档 6.11 节）。

    work_unit_key = {skill_name}:{target_ref}[:{suffix}]
    例：visual_classify:doc_x/r1/p003.g000、paper_qualify:doc_x、profile_project:doc_x:drug_optimization_v1

每个 Work Unit 在任意时刻只有一个 active Artifact 版本。
"""

from __future__ import annotations


def work_unit_key(skill: str, target_ref: str, suffix: str | None = None) -> str:
    if not skill or ":" in skill:
        raise ValueError(f"非法 Skill 名：{skill!r}")
    if not target_ref:
        raise ValueError("target_ref 不能为空")
    return f"{skill}:{target_ref}" + (f":{suffix}" if suffix else "")


def split_work_unit_key(key: str) -> tuple[str, str]:
    """→ (skill, target_ref[:suffix])。"""
    skill, _, rest = key.partition(":")
    return skill, rest


def node_dependency(node_ref: str, geometry_hash: str | None = None) -> str:
    """节点作为上游依赖的标识：`node:<ref>@<geometry_hash>`。"""
    return f"node:{node_ref}" + (f"@{geometry_hash}" if geometry_hash else "")
