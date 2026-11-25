"""Conservatively recognize edits confined to downstream visual extraction."""

from __future__ import annotations

import ast
from pathlib import Path


def visual_extraction_only(path: str, before: Path, after: Path) -> bool:
    if path not in {
        "drugpaper/skills/figure.py",
        "drugpaper/skills/prompt_materials.py",
        "drugpaper/skills/execution.py",
        "drugpaper/skills/molecule.py",
        "drugpaper/tools/builtin/chem.py",
    }:
        return False
    try:
        previous, current = ast.parse(before.read_text()), ast.parse(after.read_text())
    except (OSError, SyntaxError):
        return False

    class Projection(ast.NodeTransformer):
        def visit_ClassDef(self, node):
            if path == "drugpaper/tools/builtin/chem.py" and node.name == "DeliveryIn":
                node.body = [
                    n
                    for n in node.body
                    if not (
                        isinstance(n, ast.AnnAssign)
                        and isinstance(n.target, ast.Name)
                        and n.target.id == "blocked_instance_paths"
                    )
                ]
            return self.generic_visit(node)

        def visit_Call(self, node):
            if (
                path == "drugpaper/tools/builtin/chem.py"
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
                and node.func.attr == "to_thread"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "deliver"
            ):
                node.keywords = [k for k in node.keywords if k.arg != "blocked_instance_paths"]
            return self.generic_visit(node)

        def visit_Module(self, node):
            drop = {
                "drugpaper/skills/figure.py": {"table_extract"},
                "drugpaper/skills/molecule.py": {"mol_backfill"},
                "drugpaper/skills/prompt_materials.py": {
                    "document_evidence_images",
                    "check_evidence_request",
                },
            }.get(path)
            if drop:
                node.body = [
                    n
                    for n in node.body
                    if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) or n.name not in drop
                ]
            return self.generic_visit(node)

        def visit_If(self, node):
            if (
                path == "drugpaper/skills/execution.py"
                and ast.dump(node.test)
                == ast.dump(ast.parse("cls.semantic_model is not None", mode="eval").body)
                and not node.orelse
            ):
                # Only scheme_extract assigns a semantic_model; package schema and
                # the classifier/detection execution path remain in the comparison.
                return None
            if (
                path == "drugpaper/skills/execution.py"
                and ast.dump(node.test)
                == ast.dump(
                    ast.parse(
                        'manifest.vision_structuring and skill.name != "structure_audit"', mode="eval"
                    ).body
                )
                and not node.orelse
            ):
                return None
            if (
                path == "drugpaper/skills/execution.py"
                and ast.dump(node.test)
                == ast.dump(
                    ast.Attribute(
                        value=ast.Name(id="manifest", ctx=ast.Load()),
                        attr="vision_structuring",
                        ctx=ast.Load(),
                    )
                )
                and not node.orelse
            ):
                return None
            return self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if path == "drugpaper/skills/execution.py":
                ignored = {
                    "drugpaper.gateway.base": {"ImagePart"},
                    "drugpaper.skills.prompt_materials": {
                        "document_evidence_images",
                        "check_evidence_request",
                    },
                }.get(node.module)
                if ignored:
                    node.names = [n for n in node.names if n.name not in ignored]
                    if not node.names:
                        return None
            return node

    return ast.dump(Projection().visit(previous)) == ast.dump(Projection().visit(current))
