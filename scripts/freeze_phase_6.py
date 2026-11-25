"""Freeze the P6 schemas, profile, OpenAPI and content hashes reproducibly."""

import hashlib
import json
from pathlib import Path

from drugpaper.agents.lookup.models import LookupBatchResult
from drugpaper.agents.reader.graph import ReaderProgress
from drugpaper.agents.reader.notes import NoteDelta, ReaderNotes
from drugpaper.agents.reader.page_pack import PagePack
from drugpaper.api.app import export_openapi
from drugpaper.exporters.models import ExportManifest
from drugpaper.facts.compounds import Compound
from drugpaper.facts.name_extraction import NameExtraction
from drugpaper.facts.paths import PathProposal, Step
from drugpaper.facts.text_models import TextExtraction
from drugpaper.profiles.projector import PROFILE_ROOT


def freeze(destination: Path = Path("docs/interfaces/phase-6")) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    models = [
        PagePack,
        NoteDelta,
        ReaderNotes,
        ReaderProgress,
        LookupBatchResult,
        PathProposal,
        Step,
        ExportManifest,
        Compound,
        NameExtraction,
        TextExtraction,
    ]
    for model in models:
        (destination / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        )
    for source in (PROFILE_ROOT / "output.schema.json", PROFILE_ROOT / "profile.yaml"):
        (destination / ("Profile.schema.json" if source.suffix == ".json" else "profile.yaml")).write_bytes(
            source.read_bytes()
        )
    export_openapi(destination.parent / "openapi-p6.json")
    files = [*destination.glob("*.json"), *destination.glob("*.yaml"), destination.parent / "openapi-p6.json"]
    hashes = {
        str(p.relative_to(destination.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)
        if p.name != "manifest.json"
    }
    (destination / "manifest.json").write_text(
        json.dumps(
            {"phase": "P6", "interface_version": "0.6.1", "files": hashes}, ensure_ascii=False, indent=2
        )
        + "\n"
    )
    return hashes


if __name__ == "__main__":
    print(json.dumps(freeze(), indent=2))
