"""解析产物转存与离线导入（执行方案 S2.2）。

- `read_output_dir(dir)`：读取一个已有的 `auto/` 目录（样例论文、MinerU 服务不可用时的开发）；
- `store_result(...)`：每个文件存入 BlobStore，登记为资产，创建 `parse_revisions` 记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.parsers.mineru_legacy.paths import SKIPPED_KINDS, asset_kind, safe_rel_path
from drugpaper.persistence.repos.documents import create_revision
from drugpaper.storage.assets import register_asset
from drugpaper.storage.base import BlobStore, sniff_content_type

PARSER_NAME = "mineru_legacy"
PARSER_VERSION = "1"


@dataclass
class StoredResult:
    revision: int
    raw: dict[str, str] = field(default_factory=dict)  # kind → blob_ref（layout / middle / origin_pdf …）
    paths: dict[str, str] = field(default_factory=dict)  # 相对路径 → blob_ref
    source_name: str | None = None  # 产物文件名前缀（如 10_1021_acs_jmedchem_2c00949）

    @property
    def available_paths(self) -> set[str]:
        return set(self.paths)


def resolve_output_dir(path: Path | str) -> Path:
    """接受 `{文档名}/auto`、`{文档名}`（含 auto 子目录）或直接含 `*_layout.json` 的目录。"""
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"目录不存在：{p}")
    if list(p.glob("*_layout.json")):
        return p
    auto = p / "auto"
    if auto.is_dir() and list(auto.glob("*_layout.json")):
        return auto
    raise FileNotFoundError(f"{p} 下找不到 *_layout.json")


def read_output_dir(path: Path | str) -> dict[str, bytes]:
    root = resolve_output_dir(path)
    files: dict[str, bytes] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = safe_rel_path(f.relative_to(root).as_posix())
        if rel is None or asset_kind(rel) in SKIPPED_KINDS:
            continue
        files[rel] = f.read_bytes()
    return files


def source_name_of(files: dict[str, bytes]) -> str | None:
    for rel in files:
        if rel.endswith("_layout.json") and "/" not in rel:
            return rel[: -len("_layout.json")]
    return None


async def store_result(
    session: AsyncSession,
    store: BlobStore,
    document_id: str,
    files: dict[str, bytes],
    *,
    user_id: str = "default",
    extra_pdf: bytes | None = None,
) -> StoredResult:
    """转存解析产物。`extra_pdf`：上传的原始 PDF（产物中没有 *_origin.pdf 时作为 origin_pdf 登记）。"""
    stored = StoredResult(revision=0, source_name=source_name_of(files))
    for rel, data in sorted(files.items()):
        kind = asset_kind(rel)
        if kind in SKIPPED_KINDS:
            continue
        ctype = sniff_content_type(data)
        if rel.endswith(".md"):
            ctype = "text/markdown"
        ref = await store.put(data, ctype)
        await register_asset(session, document_id, kind, ref, content_type=ctype, path=rel, user_id=user_id)
        stored.paths[rel] = ref
        if kind not in ("image", "merged_image", "other"):
            stored.raw.setdefault(kind, ref)
    if "origin_pdf" not in stored.raw and extra_pdf is not None:
        ref = await store.put(extra_pdf, "application/pdf")
        await register_asset(
            session, document_id, "origin_pdf", ref, content_type="application/pdf", user_id=user_id
        )
        stored.raw["origin_pdf"] = ref
    if "raw_layout_json" not in stored.raw:
        raise ValueError("解析产物中缺少 *_layout.json")
    rev = await create_revision(
        session,
        document_id,
        parser=PARSER_NAME,
        parser_version=PARSER_VERSION,
        raw_blob_refs={**stored.raw, "source_name": stored.source_name, "files": len(stored.paths)},
        user_id=user_id,
    )
    stored.revision = rev.revision
    return stored
