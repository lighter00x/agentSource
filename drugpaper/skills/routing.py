"""图像类型确定性 Skill 链；合并 schema 和 D38 的校验在加载时完成。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from drugpaper.skills.loader import LoadedSkill


class SkillRouting:
    def __init__(self, config: dict[str, Any], skills: dict[str, LoadedSkill]) -> None:
        if config.get("schema_version") != 1:
            raise ValueError("routing schema_version 必须为 1")
        self.rules = config.get("routes", [])
        planned = set(config.get("planned", []))
        for rule in self.rules:
            subtypes = rule.get("match", {}).get("subtype", "")
            for subtype in subtypes if isinstance(subtypes, list) else [subtypes]:
                count = 0
                for step in rule["run"]:
                    vision = False
                    for name in step.split("+"):
                        expanded = name.replace("{subtype}", subtype)
                        if expanded not in skills and expanded not in planned:
                            raise ValueError(f"未知 Skill：{expanded}")
                        manifest = skills[expanded].manifest if expanded in skills else None
                        vision |= bool(manifest and manifest.vision_structuring)
                    count += int(vision)
                if count > 1:
                    raise ValueError("D38：每条链最多一次视觉结构化调用")

    @classmethod
    def from_file(cls, path: Path, skills: dict[str, LoadedSkill]) -> SkillRouting:
        return cls(yaml.safe_load(path.read_text()), skills)

    def resolve_chain(self, image_type: str, *, describe_figures: bool = False) -> list[str]:
        for rule in self.rules:
            match = rule["match"].get("subtype")
            if (
                match == image_type
                or isinstance(match, list)
                and image_type in match
                or rule["match"].get("any")
            ):
                if rule.get("when") and not describe_figures:
                    continue
                return [step.replace("{subtype}", image_type) for step in rule["run"]]
        return []
