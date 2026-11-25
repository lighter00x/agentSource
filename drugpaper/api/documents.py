"""文档接口：上传、状态、列表、树、页面图、页内节点（设计文档 15.1、15.2 节）。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, File, Query, UploadFile
from fastapi.responses import Response

from drugpaper.api.deps import ServicesDep, SessionDep, pdf_ref_of, require_document, require_ir
from drugpaper.api.errors import ApiError, ErrorResponse
from drugpaper.api.schemas import (
    DocumentCreated,
    DocumentInfo,
    DocumentList,
    DocumentTree,
    NodeBrief,
    PageNodes,
    TreeNode,
)
from drugpaper.domain.nodes import DocumentNode
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.documents import create_document, list_documents
from drugpaper.persistence.repos.nodes import get_tree, nodes_on_page
from drugpaper.services import Services
from drugpaper.storage.base import sha256_hex

router = APIRouter(
    tags=["documents"], responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}
)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def doc_info(d: Document) -> DocumentInfo:
    q = d.quality_report or {}
    counts: dict[str, int] = {}
    for i in q.get("issues", []):
        counts[i.get("code", "?")] = counts.get(i.get("code", "?"), 0) + 1
    meta = {k: v for k, v in (d.meta or {}).items() if k != "page_info"}
    return DocumentInfo(
        document_id=d.id,
        status=d.status,
        filename=d.filename,
        title=d.title,
        doi=d.doi,
        active_revision=d.active_revision,
        meta=meta,
        quality={
            "issue_counts": counts,
            "page_count_layout": q.get("page_count_layout"),
            "page_count_pdf": q.get("page_count_pdf"),
            "scanned_pages": q.get("scanned_pages", []),
        },
        error=d.error,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def node_brief(n: DocumentNode, preview_chars: int = 80) -> NodeBrief:
    text = n.text_norm or ""
    return NodeBrief(
        node_id=n.ref,
        local_id=n.local_id,
        type=n.node_type,
        subtype=n.subtype,
        label=n.label,
        page=n.page,
        section=n.section_role,
        regions=n.geometry.regions,
        preview=(text[:preview_chars] + ("…" if len(text) > preview_chars else "")) if text else None,
    )


async def _run_pipeline(sv: Services, pdf: bytes, filename: str) -> None:
    await DocumentPipeline(sv).run(pdf=pdf, filename=filename, force=True)


async def _run_workbench_pipeline(sv: Services, pdf: bytes, filename: str, mode: str) -> None:
    result = await DocumentPipeline(sv).run(pdf=pdf, filename=filename, force=True)
    async with sv.db.session() as session:
        doc = await session.get(Document, result.document_id)
        if doc:
            doc.meta = {**(doc.meta or {}), "workbench_mode": mode}
        await session.commit()
        if result.status == "ready" and mode == "auto":
            from drugpaper.api.workbench import auto

            await auto(sv, session, result.document_id)


async def _start_workbench_auto(sv: Services, document_id: str) -> None:
    from drugpaper.api.workbench import auto

    async with sv.db.session() as session:
        await auto(sv, session, document_id)


@router.post("/documents", status_code=202, response_model=DocumentCreated)
async def upload_document(
    sv: ServicesDep,
    s: SessionDep,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    understand: Annotated[bool, Query(description="Queue full ingestion and figure understanding")] = False,
    mode: Annotated[Literal["manual", "auto"] | None, Query(description="Workbench parsing mode")] = None,
) -> DocumentCreated:
    """上传单篇 PDF（multipart `file=`），后台运行管线：解析 → IR → 渲染。"""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "文件超过大小限制", limit_bytes=MAX_UPLOAD_BYTES)
    if not data.startswith(b"%PDF-"):
        raise ApiError(400, "NOT_PDF", "文件不是有效的 PDF（魔数校验失败）")
    filename = (file.filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    doc, created = await create_document(s, sha256=sha256_hex(data), filename=filename)
    await s.commit()
    if mode:
        doc.meta = {**(doc.meta or {}), "workbench_mode": mode}
        await s.commit()
    if not created and doc.status in ("ready", "parsing", "parsed", "ir_built"):
        if mode == "auto" and doc.status == "ready":
            background.add_task(_start_workbench_auto, sv, doc.id)
        return DocumentCreated(document_id=doc.id, status=doc.status, duplicate=True)
    if mode:
        background.add_task(_run_workbench_pipeline, sv, data, filename, mode)
    elif understand:
        from drugpaper.workers.tasks import process_document

        await sv.store.put(data, "application/pdf")
        try:
            await __import__("asyncio").to_thread(
                process_document.apply_async, args=[doc.id, "default"], queue="batch"
            )
        except Exception as exc:
            raise ApiError(503, "QUEUE_UNAVAILABLE", "无法派发文档任务", document_id=doc.id) from exc
    else:
        background.add_task(_run_pipeline, sv, data, filename)
    return DocumentCreated(document_id=doc.id, status="uploaded", duplicate=not created)


@router.get("/documents", response_model=DocumentList)
async def get_documents(
    s: SessionDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DocumentList:
    return DocumentList(documents=[doc_info(d) for d in await list_documents(s, status=status, limit=limit)])


@router.get("/documents/{document_id}", response_model=DocumentInfo)
async def get_document_info(s: SessionDep, document_id: str) -> DocumentInfo:
    """状态、元数据、质量报告摘要。"""
    return doc_info(await require_document(s, document_id))


@router.get("/documents/{document_id}/tree", response_model=DocumentTree)
async def get_document_tree(s: SessionDep, document_id: str) -> DocumentTree:
    """节点树（轻量，不含全文）。"""
    doc = await require_ir(s, document_id)
    tree: dict[str, Any] = await get_tree(s, document_id)
    return DocumentTree(
        document_id=document_id, revision=doc.active_revision or 1, root=TreeNode.model_validate(tree)
    )


@router.get(
    "/documents/{document_id}/pages/{page}/image",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def get_page_image(
    sv: ServicesDep,
    s: SessionDep,
    document_id: str,
    page: int,
    dpi: Annotated[int, Query(ge=36, le=300)] = 144,
) -> Response:
    await require_document(s, document_id)
    pdf = await pdf_ref_of(s, document_id)
    try:
        ref = await sv.renderer.render_page(pdf, page, dpi)
    except IndexError as e:
        raise ApiError(404, "NOT_FOUND", str(e), page=page) from e
    return Response(await sv.store.get(ref), media_type="image/png", headers={"X-Blob-Ref": ref})


@router.get("/documents/{document_id}/pages/{page}/nodes", response_model=PageNodes)
async def get_page_nodes(
    s: SessionDep, document_id: str, page: int, include_discarded: Annotated[bool, Query()] = False
) -> PageNodes:
    """页内节点及 regions（供前端 Overlay）。"""
    await require_ir(s, document_id)
    nodes = await nodes_on_page(s, document_id, page, include_discarded=include_discarded)
    structural = {"document", "front_matter", "section"}
    return PageNodes(
        document_id=document_id,
        page=page,
        nodes=[node_brief(n) for n in nodes if n.node_type not in structural],
    )
