"""从 active Skill、Tool 和工具包建立可见性索引，保留分层 token 计数。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.persistence.models import CapabilityIndexRow
from drugpaper.skills.loader import LoadedSkill
from drugpaper.skills.registry import SkillRegistry
from drugpaper.tools.registry import ToolRegistry


@dataclass
class Capability:
    name: str
    kind: str
    l0: str
    l1: str
    scope: str = "builtin"
    audiences: list[str] = field(default_factory=lambda: ["chat", "lookup", "figure_agent", "reader_agent"])
    toolkit: str | None = None
    triggers: dict[str, list[str]] = field(default_factory=dict)
    invocation: str = "tool_call"
    tools: list[str] = field(default_factory=list)
    skill: LoadedSkill | None = None

    def card(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "scope": self.scope,
            "toolkit": self.toolkit,
            "audiences": self.audiences,
            "l0": self.l0,
            "l1": self.l1,
            "triggers": self.triggers,
            "invocation": self.invocation,
            "tokens": {"L0": estimate_tokens(self.l0), "L1": estimate_tokens(self.l1)},
        }


class CapabilityIndex:
    def __init__(self, registry: SkillRegistry | None, tools: ToolRegistry) -> None:
        self.registry, self.tools = registry, tools
        self.entries: dict[str, Capability] = {}
        self.version = -1
        self.refresh_tools()

    def refresh_tools(self) -> None:
        for name in self.tools.names():
            if name in META_NAMES:
                continue
            tool = self.tools.get(name)
            if getattr(tool, "is_skill", False):
                continue
            self.entries[name] = Capability(
                name,
                "tool",
                tool.spec.description,
                str(tool.spec.to_schema().parameters),
                tools=[name],
                triggers={"intents": [name, tool.spec.description]},
            )

    async def refresh(self) -> None:
        self.entries = {}
        self.refresh_tools()
        if self.registry:
            for skill in await self.registry.list_visible():
                manifest = skill.manifest
                assert manifest is not None
                d = manifest.disclosure
                self.entries[skill.name] = Capability(
                    skill.name,
                    "skill",
                    skill.l0,
                    skill.l1,
                    scope=skill.scope,
                    audiences=list(d.audiences),
                    toolkit=d.toolkit,
                    triggers=d.triggers.model_dump(),
                    invocation=d.invocation,
                    tools=[skill.name],
                    skill=skill,
                )
            toolkits = sorted({c.toolkit for c in self.entries.values() if c.toolkit})
            for toolkit in toolkits:
                members = [c for c in self.entries.values() if c.toolkit == toolkit]
                self.entries[toolkit] = Capability(
                    toolkit,
                    "toolkit",
                    f"{toolkit} 工具包",
                    "\n".join(c.l0 for c in members),
                    toolkit=toolkit,
                    audiences=sorted({a for c in members for a in c.audiences}),
                    tools=[t for c in members if c.invocation == "tool_call" for t in c.tools],
                )
            self.version = self.registry.version
            async with self.registry.db.session() as s:
                await s.execute(delete(CapabilityIndexRow))
                for cap in self.entries.values():
                    s.add(
                        CapabilityIndexRow(
                            registry_version=self.version,
                            name=cap.name,
                            kind=cap.kind,
                            scope=cap.scope,
                            toolkit=cap.toolkit,
                            audiences=cap.audiences,
                            l0_text=cap.l0,
                            l1_text=cap.l1,
                            triggers=cap.triggers,
                            invocation=cap.invocation,
                            token_counts=cap.card()["tokens"],
                        )
                    )

    def visible(
        self, user_id: str = "default", audience: str = "chat", toolkit: str | None = None
    ) -> list[Capability]:
        return [
            c
            for c in self.entries.values()
            if audience in c.audiences
            and c.scope in {"builtin", "system"}
            and (toolkit is None or c.toolkit == toolkit)
        ]


META_NAMES = {
    "find_capabilities",
    "describe_capability",
    "load_capability",
    "unload_capability",
    "read_skill_resource",
}
