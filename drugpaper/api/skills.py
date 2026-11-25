"""P3 能力目录只读 API 与显式管理员热加载。"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from drugpaper.skills.bootstrap import SkillServices

router = APIRouter(tags=["skills"])


@router.get("/skills")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    bundle: SkillServices = await request.app.state.services.skills()
    return [
        {"name": s.name, "version": s.version, "hash": s.content_hash, "scope": s.scope}
        for s in await bundle.registry.list_visible()
    ]


@router.get("/skills/catalog")
async def catalog(
    request: Request, audience: str = "chat", toolkit: str | None = None, q: str = ""
) -> list[dict[str, Any]]:
    bundle: SkillServices = await request.app.state.services.skills()
    return [
        c.card()
        for c in bundle.index.visible(audience=audience, toolkit=toolkit)
        if q.lower() in (c.name + c.l0 + c.l1).lower()
    ]


@router.get("/skills/{name}/versions")
async def versions(name: str, request: Request) -> list[dict[str, Any]]:
    bundle: SkillServices = await request.app.state.services.skills()
    return [
        {
            "id": v.id,
            "name": v.name,
            "version": v.version,
            "hash": v.content_hash,
            "status": v.status,
            "errors": v.errors,
            "test_report": v.test_report,
        }
        for v in await bundle.registry.versions(name)
    ]


@router.get("/skills/{name}/card")
async def card(name: str, request: Request, audience: str = "chat") -> dict[str, Any]:
    bundle: SkillServices = await request.app.state.services.skills()
    for cap in bundle.index.visible(audience=audience):
        if cap.name == name:
            return cap.card()
    raise HTTPException(404, "能力不可见或不存在")


@router.get("/skills/{name}/resources")
async def resources(name: str, request: Request) -> dict[str, Any]:
    bundle: SkillServices = await request.app.state.services.skills()
    try:
        skill = await bundle.registry.resolve(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"name": name, "resources": skill.resources_index}


@router.post("/skills:reload")
async def reload_skills(request: Request, x_admin_token: str = Header(default="")) -> dict[str, Any]:
    configured = request.app.state.services.settings.skills_admin_token
    if not configured or not secrets.compare_digest(configured, x_admin_token):
        raise HTTPException(403, "需要配置并提供管理员 token")
    bundle: SkillServices = await request.app.state.services.skills()
    await bundle.watcher.reload()
    return {"status": "ok", "registry_version": bundle.registry.version}
