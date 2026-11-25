"""已部署环境真实验收；结果不包含令牌、API Key 或数据库密码。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
from redis.asyncio import Redis
from sqlalchemy import text

from drugpaper.config.settings import get_settings
from drugpaper.detection.remote import RemoteDetectionService
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.gateway.base import ChatRequest, Message, ToolCall
from drugpaper.gateway.task_router import RoutedModelClient, TaskRouter
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    settings = get_settings()
    out = (
        ROOT
        / "samples/outputs/deployment"
        / datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    )
    out.mkdir(parents=True)
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    sv = Services.from_settings(settings)
    remote = RemoteDetectionService(settings)
    redis = Redis.from_url(settings.redis_url)
    models = RoutedModelClient(TaskRouter.from_file(settings.models_config_path))
    try:
        async with sv.db.session() as session:
            version = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            extensions = list(
                (
                    await session.execute(
                        text("SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')")
                    )
                ).scalars()
            )
        add(
            "database",
            version == "0006_chemistry" and len(extensions) == 2,
            {"migration": version, "extensions": extensions},
        )
        add("redis", bool(await redis.ping()), "PING")
        async with httpx2.AsyncClient(trust_env=False, timeout=10) as client:
            response = await client.get("http://127.0.0.1:18000/healthz")
            add("api", response.status_code == 200, response.json())
            response = await client.get("http://127.0.0.1:18000/api/v1/skills/catalog")
            names = [row["name"] for row in response.json()]
            add(
                "capability catalog",
                all(name in names for name in ["molecule.detect", "molecule.crop", "som.render"]),
                names,
            )
            response = await client.get("http://127.0.0.1:19000/minio/health/ready")
            add("minio", response.status_code == 200, "readiness")
            for path in ("/health", "/openapi.json"):
                response = await client.get(settings.mineru_url.rstrip("/") + path)
                if response.status_code == 200:
                    break
            add("mineru", response.status_code == 200, {"status": response.status_code, "path": path})
        # 根据现有样例，从两个用户并发访问常驻 Worker。
        paths = [
            ROOT / "samples/outputs/molecule-detection/20261001-202449" / "image_0_mol_1.png",
            ROOT / "samples/outputs/molecule-detection/20261001-202449" / "image_1_mol_1.png",
        ]
        reports = await asyncio.gather(
            *(
                remote.detect(user, path.read_bytes())
                for user in ("deployment_a", "deployment_b")
                for path in paths
            )
        )
        hit = await remote.detect("deployment_a", paths[0].read_bytes())
        add(
            "shared detector",
            all(r.provenance.get("device") == "0" for r in reports) and hit.cached,
            {
                "counts": [len(r.detections) for r in reports],
                "device": reports[0].provenance["device"],
                "cache_hit": hit.cached,
                "weights_sha256": reports[0].provenance["weights_sha256"],
            },
        )
        try:
            health = await sv.molparser().health()
            add("molparser", True, health)
        except Exception as exc:
            add("molparser", False, type(exc).__name__)
        response = await models.chat(
            ChatRequest(messages=[Message(role="user", content="请仅回复 READY")], max_tokens=1024),
            task="chat",
        )
        add(
            "text model",
            bool(response.content),
            {"model": response.model, "reply": response.content, "latency_ms": response.latency_ms},
        )
        # 保留一篇去重的离线样例，验证实际文档节点 → Tool → GPU Worker 链路。
        document = await DocumentPipeline(sv, render_pages=False).run(
            offline_dir=ROOT / "tests/fixtures/jmedchem_2c00949"
        )
        source = await IrDocumentSource.load(sv.db, document.document_id)
        visual = next((n for n in source.ir.nodes if n.node_type == "scheme" and n.geometry.regions), None)
        if visual is None:
            visual = next(n for n in source.ir.nodes if n.node_type == "figure" and n.geometry.regions)
        bundle = await sv.skills()
        result = await bundle.tools.invoke(
            ToolContext(
                run_id="deployment_verify", trace_id="deployment_verify", document_id=document.document_id
            ),
            ToolCall(
                id="deployment_detect",
                name="molecule.detect",
                arguments={"target_ref": source.ref(visual.local_id)},
            ),
        )
        add(
            "document tool gpu",
            result.ok,
            {
                "document_id": document.document_id,
                "target_ref": source.ref(visual.local_id),
                "output": result.data,
                "error": result.error,
            },
        )
    finally:
        await models.aclose()
        await redis.aclose()
        await remote.aclose()
        await sv.aclose()
    (out / "report.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    (out / "report.md").write_text(
        "# 本机部署验收\n\n"
        + "\n".join(f"- {'PASS' if row['ok'] else 'FAIL'} {row['name']}：{row['detail']}" for row in checks)
        + "\n"
    )
    print("Report: " + str(out / "report.md"))
    if not all(row["ok"] for row in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
