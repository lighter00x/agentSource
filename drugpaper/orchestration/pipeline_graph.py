"""文档管线骨架（执行方案 S2.12）：ingest → mineru_parse → build_ir → render_and_index。

- 状态只存引用（document_id、blob_ref、revision），不存文件内容；
- 任一节点失败：`documents.status=failed`，`error={code, message, stage}`，管线结束；
- 本阶段由 CLI / API 后台任务同步调用；P5 改为 Celery 任务，并换成 Postgres checkpointer 以支持崩溃恢复。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from drugpaper.parsers.mineru_legacy.importer import read_output_dir, store_result
from drugpaper.persistence.repos.documents import create_document, get_document, set_status
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.render.pages import inspect_pdf
from drugpaper.services import Services
from drugpaper.storage.assets import register_asset
from drugpaper.storage.base import sha256_hex


class PipelineState(TypedDict, total=False):
    document_id: str
    user_id: str
    filename: str | None
    pdf_ref: str | None
    offline_dir: str | None
    revision: int
    raw: dict[str, str]
    paths: dict[str, str]
    source_name: str | None
    node_count: int
    page_images: int
    status: str
    error: dict[str, Any] | None
    timings_ms: dict[str, int]
    skipped: bool
    figure_understanding: dict[str, Any]
    full_text_understanding: dict[str, Any]


class PipelineResult(BaseModel):
    document_id: str
    status: str
    revision: int | None = None
    node_count: int = 0
    page_images: int = 0
    skipped: bool = False
    error: dict[str, Any] | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)
    figure_understanding: dict[str, Any] | None = None
    full_text_understanding: dict[str, Any] | None = None


Stage = Literal["ingest", "mineru_parse", "build_ir", "render_and_index"]


class DocumentPipeline:
    def __init__(
        self,
        services: Services,
        *,
        render_pages: bool = True,
        understand_figures: bool = False,
        understand_full_text: bool = False,
    ) -> None:
        self.sv = services
        self.render_pages = render_pages
        self.understand_figures = understand_figures or understand_full_text
        g: StateGraph[PipelineState] = StateGraph(PipelineState)
        g.add_node("ingest", self._wrap("ingest", self._ingest))
        g.add_node("mineru_parse", self._wrap("mineru_parse", self._mineru_parse))
        g.add_node("build_ir", self._wrap("build_ir", self._build_ir))
        g.add_node("render_and_index", self._wrap("render_and_index", self._render_and_index))
        g.add_edge(START, "ingest")
        for a, b in [
            ("ingest", "mineru_parse"),
            ("mineru_parse", "build_ir"),
            ("build_ir", "render_and_index"),
        ]:
            g.add_conditional_edges(a, self._next, {"next": b, "end": END})
        if understand_figures or understand_full_text:
            g.add_node("figure_understanding", self._understand)
            g.add_conditional_edges(
                "render_and_index", self._next, {"next": "figure_understanding", "end": END}
            )
            if understand_full_text:
                g.add_node("full_text_understanding", self._understand_text)
                g.add_edge("figure_understanding", "full_text_understanding")
                g.add_edge("full_text_understanding", END)
            else:
                g.add_edge("figure_understanding", END)
        else:
            g.add_edge("render_and_index", END)
        self.graph = g.compile()

    # ------------------------------------------------------------ 入口

    async def run(
        self,
        *,
        pdf: bytes | None = None,
        filename: str | None = None,
        offline_dir: Path | str | None = None,
        force: bool = False,
        user_id: str = "default",
    ) -> PipelineResult:
        """在线模式传 `pdf`；离线导入传 `offline_dir`（MinerU 的 auto/ 目录，PDF 取其中的 *_origin.pdf）。"""
        if pdf is None and offline_dir is None:
            raise ValueError("需要 pdf 或 offline_dir")
        if pdf is None:
            files = read_output_dir(offline_dir)  # type: ignore[arg-type]
            origin = next((v for k, v in files.items() if k.endswith("_origin.pdf")), None)
            if origin is None:
                raise ValueError(f"{offline_dir} 中没有 *_origin.pdf")
            pdf = origin
            filename = filename or next(k for k in files if k.endswith("_origin.pdf"))
        pdf_ref = await self.sv.store.put(pdf, "application/pdf")
        state: PipelineState = {
            "user_id": user_id,
            "filename": filename,
            "pdf_ref": pdf_ref,
            "offline_dir": str(offline_dir) if offline_dir is not None else None,
            "timings_ms": {},
            "skipped": False,
            "error": None,
        }
        if not force:
            async with self.sv.db.session(user_id) as s:
                doc, created = await create_document(s, sha256=pdf_ref, filename=filename, user_id=user_id)
                if not created and doc.status == "ready":
                    cached_id, cached_status, cached_revision = doc.id, doc.status, doc.active_revision
                else:
                    cached_id = None
            if cached_id:
                figures = None
                if self.understand_figures:
                    from drugpaper.orchestration.figure_pipeline import FigurePipeline

                    figures = await FigurePipeline(self.sv).run(cached_id, user_id=user_id)
                full_text = None
                if "full_text_understanding" in self.graph.nodes:
                    from drugpaper.orchestration.full_text_pipeline import FullTextPipeline

                    full_text = await FullTextPipeline(self.sv).run(cached_id, user_id=user_id)
                return PipelineResult(
                    document_id=cached_id,
                    status=cached_status,
                    revision=cached_revision,
                    skipped=True,
                    figure_understanding=figures,
                    full_text_understanding=full_text,
                )
        out: PipelineState = await self.graph.ainvoke(state)  # type: ignore[assignment]
        return PipelineResult(
            document_id=out["document_id"],
            status=out.get("status", "failed"),
            revision=out.get("revision"),
            node_count=out.get("node_count", 0),
            page_images=out.get("page_images", 0),
            error=out.get("error"),
            timings_ms=out.get("timings_ms", {}),
            figure_understanding=out.get("figure_understanding"),
            full_text_understanding=out.get("full_text_understanding"),
        )

    async def _understand_text(self, state: PipelineState) -> dict[str, Any]:
        from drugpaper.orchestration.full_text_pipeline import FullTextPipeline

        result = await FullTextPipeline(self.sv).run(state["document_id"], user_id=state["user_id"])
        return {"full_text_understanding": result}

    async def _understand(self, state: PipelineState) -> dict[str, Any]:
        from drugpaper.orchestration.figure_pipeline import FigurePipeline

        started = time.perf_counter()
        figures = await FigurePipeline(self.sv).run(state["document_id"], user_id=state["user_id"])
        return {
            "figure_understanding": figures,
            "timings_ms": {
                **state["timings_ms"],
                "figure_understanding": int((time.perf_counter() - started) * 1000),
            },
        }

    # ------------------------------------------------------------ 包装：计时与失败处理

    @staticmethod
    def _next(state: PipelineState) -> str:
        return "end" if state.get("error") else "next"

    def _wrap(self, stage: Stage, fn: Any) -> Any:
        async def node(state: PipelineState) -> dict[str, Any]:
            t0 = time.perf_counter()
            try:
                update: dict[str, Any] = await fn(state)
            except Exception as e:  # noqa: BLE001 - 管线失败要落库而不是抛给调用方
                err = {
                    "code": f"{stage}_failed",
                    "stage": stage,
                    "message": f"{type(e).__name__}: {e}"[:1000],
                }
                doc_id = state.get("document_id")
                if doc_id:
                    async with self.sv.db.session(state.get("user_id", "default")) as s:
                        await set_status(s, doc_id, "failed", error=err)
                update = {"error": err, "status": "failed"}
            timings = dict(state.get("timings_ms", {}))
            timings[stage] = int((time.perf_counter() - t0) * 1000)
            update["timings_ms"] = timings
            return update

        return node

    # ------------------------------------------------------------ 节点

    async def _ingest(self, st: PipelineState) -> dict[str, Any]:
        pdf_ref = st["pdf_ref"]
        assert pdf_ref is not None
        uid = st.get("user_id", "default")
        async with self.sv.db.session(uid) as s:
            doc, _ = await create_document(s, sha256=pdf_ref, filename=st.get("filename"), user_id=uid)
            await register_asset(s, doc.id, "pdf", pdf_ref, content_type="application/pdf", user_id=uid)
            await set_status(s, doc.id, "parsing")
            return {"document_id": doc.id, "status": "parsing"}

    async def _mineru_parse(self, st: PipelineState) -> dict[str, Any]:
        uid = st.get("user_id", "default")
        if st.get("offline_dir"):
            files = read_output_dir(st["offline_dir"])  # type: ignore[arg-type]
        else:
            pdf = await self.sv.store.get(st["pdf_ref"])  # type: ignore[arg-type]
            files = await self.sv.mineru().parse(pdf, st.get("filename") or "document.pdf")
        pdf = await self.sv.store.get(st["pdf_ref"])  # type: ignore[arg-type]
        async with self.sv.db.session(uid) as s:
            stored = await store_result(
                s, self.sv.store, st["document_id"], files, user_id=uid, extra_pdf=pdf
            )
            await set_status(s, st["document_id"], "parsed")
        return {
            "revision": stored.revision,
            "raw": stored.raw,
            "paths": stored.paths,
            "source_name": stored.source_name,
            "status": "parsed",
        }

    async def _build_ir(self, st: PipelineState) -> dict[str, Any]:
        uid = st.get("user_id", "default")
        raw = st["raw"]
        layout = await self.sv.store.get(raw["raw_layout_json"])
        middle = await self.sv.store.get(raw["raw_middle_json"]) if "raw_middle_json" in raw else None
        pdf_ref = raw.get("origin_pdf") or st["pdf_ref"]
        assert pdf_ref is not None
        info = inspect_pdf(await self.sv.store.get(pdf_ref))
        paths = st.get("paths", {})
        ir = build_ir(
            layout,
            middle,
            document_id=st["document_id"],
            revision=st["revision"],
            source_name=st.get("source_name"),
            pdf_page_count=info.page_count,
            scanned_pages=info.scanned_pages,
            available_assets=set(paths),
            pdf_page_sizes=info.page_sizes,
        )
        for n in ir.nodes:  # 图表节点：图片资产的 blob 引用（F1：表格内容只有图片）
            for key in ("image_path",):
                p = n.attributes.get(key)
                if p and p in paths:
                    n.attributes["image_blob"] = paths[p]
        async with self.sv.db.session(uid) as s:
            await save_ir(s, ir, user_id=uid)
            await set_status(s, st["document_id"], "ir_built")
        return {"node_count": len(ir.nodes), "status": "ir_built"}

    async def _render_and_index(self, st: PipelineState) -> dict[str, Any]:
        uid = st.get("user_id", "default")
        pdf_ref = st.get("raw", {}).get("origin_pdf") or st["pdf_ref"]
        assert pdf_ref is not None
        rendered = 0
        if self.render_pages:
            info = inspect_pdf(await self.sv.store.get(pdf_ref))
            dpi = self.sv.settings.render_dpi
            async with self.sv.db.session(uid) as s:
                for page in range(info.page_count):
                    ref = await self.sv.renderer.render_page(pdf_ref, page, dpi)
                    await register_asset(
                        s,
                        st["document_id"],
                        "page_image",
                        ref,
                        content_type="image/png",
                        path=f"pages/{page:03d}@{dpi}.png",
                        meta={"page": page, "dpi": dpi},
                        user_id=uid,
                    )
                    rendered += 1
        # 规范化文本与 tsv 已在 save_ir 中写入（PostgreSQL）；这里确认状态
        async with self.sv.db.session(uid) as s:
            doc = await get_document(s, st["document_id"])
            assert doc is not None
            await set_status(s, st["document_id"], "ready")
        return {"page_images": rendered, "status": "ready"}


def document_sha(pdf: bytes) -> str:
    return sha256_hex(pdf)
