"""P6 S6.1/S6.3 controlled chemistry acceptance; public HTTP optional, no business database writes."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx2

from drugpaper.chem.normalize import canonical
from drugpaper.chem.opsin import OpsinClient
from drugpaper.config.settings import Settings
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.ids import new_id
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext

ROOT = Path(__file__).resolve().parents[1]
NAME = "2-(Fluoromethoxy)-4′-(S-methanesulfonimidoyl)-1,1′-biphenyl"
# Recorded P5 result; comparing against it does not certify chemistry accuracy.
REFERENCE = "CS(=N)(=O)c1ccc(-c2ccccc2OCF)cc1"


async def verify(out: Path, opsin_url: str = "", control: str = "sample26") -> bool:
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{out / 'chemistry.db'}",
        blob_root=out / "blobs",
        skills_root=out / "skills",
        runs_dir=out / "runs",
        redis_url="",
        mineru_url="",
        opsin_base_url=opsin_url,
        skills_watch_enabled=False,
    )
    services = Services.from_settings(settings)
    checks = []
    requests = []
    name, label, formula, reference = (
        (NAME, "26", "C14H14FNO2S", REFERENCE) if control == "sample26" else ("Ethanol", "1", "C2H6O", "CCO")
    )
    try:
        await services.db.create_all()
        doc = new_id("doc")
        ref = f"{doc}/r1/p000.b000"
        # This controlled paragraph comes from the design contract, not the imported article.
        raw = f"{name} ({label}). HRMS calcd for {formula} [M+H]+."
        async with services.db.session() as session:
            session.add(Document(id=doc, user_id="default", sha256=doc))
            await session.flush()
            await save_ir(
                session,
                IRDocument(
                    document_id=doc,
                    pages=[PageInfo(page=0, width_pt=100, height_pt=100)],
                    nodes=[
                        DocumentNode(
                            document_id=doc,
                            revision=1,
                            local_id="p000.b000",
                            node_type="paragraph",
                            text_raw=raw,
                            section_role="experimental",
                            geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
                        )
                    ],
                ),
            )
        if not opsin_url:

            def handler(request):
                requests.append(str(request.url))
                return httpx2.Response(200, json={"status": "SUCCESS", "smiles": reference})

            services._opsin = OpsinClient(
                "http://replay/opsin", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            )
        bundle = await services.skills()
        ctx = ToolContext(document_id=doc, run_id="p6_chemistry_verify", trace_id="p6_chemistry_verify")
        named = await bundle.runtime.run(
            "name_to_structure", ref, {}, await bundle.context_for(ctx, ref), force=True
        )
        (out / "name-result.json").write_text(
            json.dumps(
                {
                    "status": named.status,
                    "error": named.error,
                    "artifacts": [a.model_dump(mode="json") for a in named.artifacts],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        payload = named.artifacts[0].payload if named.artifacts else {}
        candidates = payload.get("structures", [])
        checks.append(
            {"name": "name candidate committed", "ok": named.status == "completed" and bool(candidates)}
        )
        if candidates:
            candidate = candidates[0]
            checks.append(
                {
                    "name": "formula matches controlled HRMS",
                    "ok": candidate["validation"]["formula_match"] is True,
                    "formula": candidate["formula"],
                    "basis": candidate["validation"]["formula_basis"],
                }
            )
            checks.append(
                {
                    "name": "matches reference InChIKey",
                    "ok": candidate["inchikey"] == canonical(reference).inchikey,
                    "inchikey": candidate["inchikey"],
                    "reference": canonical(reference).inchikey,
                }
            )
            checks.append(
                {
                    "name": "raw evidence spans",
                    "ok": all(raw[slice(*e["char_span"])] for e in candidate["evidence"]),
                }
            )
        resolved = await bundle.runtime.run(
            "compound_resolve", doc, {}, await bundle.context_for(ctx, doc, include_document_image=False)
        )
        (out / "ledger-result.json").write_text(
            json.dumps(
                {
                    "status": resolved.status,
                    "error": resolved.error,
                    "artifacts": [a.model_dump(mode="json") for a in resolved.artifacts],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        compounds = resolved.artifacts[0].payload.get("compounds", []) if resolved.artifacts else []
        checks.append(
            {
                "name": "OPSIN candidate adopted for controlled label",
                "ok": bool(candidates)
                and any(
                    c["primary_label"] == label
                    and c["accepted_structure_id"] == candidates[0]["structure_id"]
                    for c in compounds
                ),
            }
        )
        if candidates:
            cached = await bundle.runtime.run(
                "name_to_structure", ref, {}, await bundle.context_for(ctx, ref)
            )
            checks.append({"name": "successful candidate cache", "ok": cached.cached})
        report = {
            "stage": "p6-s6.1-s6.3",
            "mode": "live-opsin" if opsin_url else "replay",
            "control": control,
            "checks": checks,
            "passed": all(c["ok"] for c in checks),
            "p6_complete": False,
            "scope": "Controlled paragraph from design contract; HRMS is not present in current sample fixture. "
            "Recorded P5 structure agreement is not independent chemistry or stereo certification.",
            "model_calls": 0,
            "replay_requests": len(requests),
            "remaining": [
                "sample/SI experimental names and real HRMS evidence",
                "OPSIN production deployment",
                "DocLookup",
                "ReaderAgent/checkpoints",
                "path validation",
                "profiles/exports",
                "10-paper silver evaluation",
            ],
        }
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return bool(report["passed"])
    finally:
        await services.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "samples/outputs/phase-6/20261005-chemistry/replay"
    )
    parser.add_argument(
        "--opsin-url", default="", help="Explicit OPSIN JSON base URL; no public service used by default"
    )
    parser.add_argument("--control", choices=["sample26", "ethanol"], default="sample26")
    args = parser.parse_args()
    return 0 if asyncio.run(verify(args.out, args.opsin_url, args.control)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
