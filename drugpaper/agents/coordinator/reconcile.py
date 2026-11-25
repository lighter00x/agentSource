from __future__ import annotations

from drugpaper.agents.figure.report import FigureReport


def questions(reports: list[FigureReport], max_questions: int = 8) -> dict[str, list[str]]:
    sightings: dict[str, list[tuple[str, str]]] = {}
    missing = []
    for report in reports:
        for molecule in report.extraction.get("molecules", []):
            label = molecule.get("reference")
            structure = molecule.get("inchikey") or molecule.get("structure_id")
            if label and structure:
                sightings.setdefault(label, []).append((report.node_ref, structure))
        if report.extraction.get("orientation") == "compounds_as_rows":
            for row in report.extraction.get("rows", []):
                if row and row[0].get("text"):
                    missing.append((report.node_ref, row[0]["text"]))
    out: dict[str, list[str]] = {}
    count = 0
    for label, entries in sorted(sightings.items()):
        if len({s for _, s in entries}) > 1:
            for ref, _ in sorted(set(entries)):
                if count >= max_questions:
                    return out
                out.setdefault(ref, []).append(
                    f"编号 {label} 在不同图中对应不同结构。请仅核对本图中的编号、位置和结构证据，说明是否应维持本图绑定。"
                )
                count += 1
    for ref, label in missing:
        if label not in sightings and count < max_questions:
            out.setdefault(ref, []).append(
                f"表格编号 {label} 尚无图内结构。请核对编号与母核/变量证据，不推测缺失结构。"
            )
            count += 1
    return out
