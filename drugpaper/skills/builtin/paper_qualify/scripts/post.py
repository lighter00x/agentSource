from typing import Any

from drugpaper.skills.protocol import SkillContext


def postprocess(payload: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    if payload["confidence"] < 0.6:
        payload["qualified"] = True
        payload["needs_review"] = True
    return payload
