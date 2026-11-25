"""drugpaper 命令行。

drugpaper db upgrade                                   # 数据库迁移（DATABASE_URL，默认本地 SQLite）
drugpaper ingest --offline <MinerU auto 目录>          # 离线导入已有解析产物
drugpaper ingest <paper.pdf>                           # 在线：提交 MinerU 服务（MINERU_URL）
drugpaper ask <layout.json> "<问题>"                   # P1：直接基于 layout.json 问答
drugpaper ask --doc <document_id> "<问题>"             # P2：基于 IR 问答，引用带页码与区域
drugpaper resume --doc <document_id> <run_id>          # 从检查点恢复一次中断的问答
drugpaper serve [--host 127.0.0.1] [--port 8000]       # 启动 API
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import orjson
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from drugpaper.agent.checkpoint import is_postgres_url, open_checkpointer, use_selector_loop_on_windows
from drugpaper.agent.interfaces import Tracer
from drugpaper.agent.qa import QaAgent, build_ir_qa_agent, build_qa_agent
from drugpaper.agent.state import AgentRunResult, Budget
from drugpaper.config.models_file import ModelsConfigError, ResolvedModel, load_default_model
from drugpaper.config.settings import Settings, get_settings
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.gateway.openai_compat import OpenAICompatClient
from drugpaper.gateway.task_router import RoutedModelClient, TaskRouter
from drugpaper.services import Services
from drugpaper.tracing.console import ConsolePrinter
from drugpaper.tracing.jsonl import CompositeTracer, JsonlTracer

app = typer.Typer(help="药物论文理解 Agent。", no_args_is_help=True, add_completion=False)
db_app = typer.Typer(help="数据库管理。", no_args_is_help=True)
app.add_typer(db_app, name="db")


@app.callback()
def main() -> None:
    """drugpaper 命令行工具。"""


def _run_async[T](settings: Settings, coro_fn: Callable[[], Awaitable[T]]) -> T:
    if is_postgres_url(settings.database_url):
        use_selector_loop_on_windows()  # psycopg 异步连接不支持 Windows 的 ProactorEventLoop
    return asyncio.run(coro_fn())  # type: ignore[arg-type]


# ================================================================ 输出


def _render(console: Console, agent: QaAgent, result: AgentRunResult, trace_path: Path | None) -> None:
    ans = result.answer
    console.print(f"\n[bold]答案[/]\n{escape(str(ans.get('answer', '')))}\n")
    citations: list[dict[str, Any]] = ans.get("citations") or []
    evidence = {e["node_id"]: e for e in result.evidence}
    if citations:
        table = Table(title="引用", show_lines=False)
        table.add_column("节点")
        table.add_column("页码", justify="right")
        table.add_column("区域（归一化 x0,y0,x1,y1）")
        table.add_column("原文片段")
        for c in citations:
            ref = str(c.get("ref"))
            try:
                block = agent.source.block(ref)
                page, snippet = str(block.page), c.get("quote") or block.text[:120]
            except KeyError:
                page, snippet = "?", c.get("quote") or ""
            ev = evidence.get(ref) or next((e for k, e in evidence.items() if k.endswith("/" + ref)), None)
            region = ""
            if ev and ev.get("regions"):
                r0 = ev["regions"][0]
                b = r0["bbox"]
                page = str(r0["page"])
                region = f"{b['x0']:.3f},{b['y0']:.3f},{b['x1']:.3f},{b['y1']:.3f}"
                if len(ev["regions"]) > 1:
                    region += f" (+{len(ev['regions']) - 1})"
            table.add_row(escape(ref.rsplit("/", 1)[-1]), page, region, escape(str(snippet)))
        console.print(table)
    for w in ans.get("warnings") or result.warnings:
        console.print(f"[yellow]⚠ {escape(str(w))}[/]")
    console.print(
        f"\nstop_reason=[bold]{result.stop_reason}[/]  steps={result.steps}  model_calls={result.model_calls}  "
        f"tool_calls={result.tool_calls}  tokens={result.usage.input_tokens}+{result.usage.output_tokens}  "
        f"{result.elapsed_ms} ms  run_id={result.run_id}"
    )
    if result.error:
        console.print(f"[red]error: {escape(result.error)}[/]")
    if trace_path is not None:
        console.print(f"trace: {trace_path}")


def _load_model(console: Console, settings: Settings) -> ResolvedModel:
    try:
        return load_default_model(settings)
    except ModelsConfigError as e:
        console.print(
            f"[red]模型配置错误：{escape(str(e))}[/]\n请检查 .env（参考 .env.example）和 {settings.models_config_path}"
        )
        raise typer.Exit(code=2) from e


def _text_client(settings: Settings, primary: ResolvedModel) -> RoutedModelClient | OpenAICompatClient:
    # Preserve standalone/unrouted configs used by integrations and legacy clients.
    if not primary.stage:
        return OpenAICompatClient(primary)
    return RoutedModelClient(TaskRouter.from_file(settings.models_config_path), OpenAICompatClient)


async def _with_ir_agent(
    settings: Settings,
    model_cfg: ResolvedModel,
    doc_id: str,
    budget: Budget,
    verbose: bool,
    action: Callable[[QaAgent], Awaitable[AgentRunResult]],
) -> tuple[QaAgent, AgentRunResult]:
    from drugpaper.persistence.repos.nodes import active_revision
    from drugpaper.services import Services
    from drugpaper.text.index import InMemorySearch, PostgresSearch, SearchBackend
    from drugpaper.tracing.db import DbTracer

    sv = Services.from_settings(settings)
    client = _text_client(settings, model_cfg)
    jsonl = JsonlTracer(settings.runs_dir) if settings.trace_jsonl else None
    db_tracer = DbTracer(sv.db, jsonl=jsonl)
    tracer: Tracer = CompositeTracer([db_tracer, ConsolePrinter()]) if verbose else db_tracer
    try:
        source = await IrDocumentSource.load(sv.db, doc_id)
        # 首次 setup 含 CREATE INDEX CONCURRENTLY，不能持有会阻塞它的业务事务。
        async with open_checkpointer(settings.database_url) as saver, sv.db.session() as s:
            backend: SearchBackend
            if sv.db.is_postgres:
                rev = await active_revision(s, doc_id)
                backend = PostgresSearch(s, doc_id, rev or source.revision)
            else:
                backend = InMemorySearch(source.ir.nodes)
            agent = build_ir_qa_agent(
                source,
                client,
                tracer,
                budget=budget,
                search_backend=backend,
                checkpointer=saver,
                skill_services=await sv.skills(),
            )
            result = await action(agent)
        await db_tracer.aclose()
        return agent, result
    finally:
        await client.aclose()
        await sv.aclose()


def _output(
    console: Console, agent: QaAgent, result: AgentRunResult, json_out: bool, trace: Path | None
) -> None:
    if json_out:
        typer.echo(orjson.dumps(result.model_dump(mode="json"), option=orjson.OPT_INDENT_2).decode())
    else:
        _render(console, agent, result, trace)
    if result.stop_reason == "error":
        raise typer.Exit(code=1)


# ================================================================ 命令


@app.command()
def ask(
    targets: Annotated[
        list[str],
        typer.Argument(help='`<layout.json> "<问题>"`；使用 --doc 时只需 `"<问题>"`', show_default=False),
    ],
    doc: Annotated[str | None, typer.Option("--doc", help="已入库文档的 document_id（基于 IR 问答）")] = None,
    max_steps: Annotated[
        int, typer.Option("--max-steps", min=1, help="最大步数（一次模型调用+工具执行）")
    ] = 8,
    max_model_calls: Annotated[int, typer.Option("--max-model-calls", min=1, help="最大模型调用次数")] = 10,
    max_tool_calls: Annotated[int, typer.Option("--max-tool-calls", min=1, help="最大工具调用次数")] = 20,
    max_wall_time: Annotated[float, typer.Option("--max-wall-time", min=1, help="最大耗时（秒）")] = 180,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="实时打印执行过程")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="输出 AgentRunResult 的 JSON")] = False,
) -> None:
    """回答关于论文的问题，答案附带引用（--doc 模式下引用带页码与区域）。"""
    console = Console()
    settings = get_settings()
    budget = Budget(
        max_steps=max_steps,
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        max_wall_time_s=max_wall_time,
    )
    if doc is not None:
        if len(targets) != 1:
            raise typer.BadParameter('使用 --doc 时只需要一个参数："<问题>"')
        model_cfg = _load_model(console, settings)
        question = targets[0]
        agent, result = _run_async(
            settings,
            lambda: _with_ir_agent(settings, model_cfg, doc, budget, verbose, lambda a: a.ask(question)),
        )
        _output(console, agent, result, json_out, None)
        return

    if len(targets) != 2:
        raise typer.BadParameter(
            '用法：drugpaper ask <layout.json> "<问题>" 或 drugpaper ask --doc <id> "<问题>"'
        )
    layout, question = Path(targets[0]), targets[1]
    if not layout.is_file():
        raise typer.BadParameter(f"文件不存在：{layout}")
    model_cfg = _load_model(console, settings)
    jsonl = JsonlTracer(settings.runs_dir)
    tracer: Tracer = CompositeTracer([jsonl, ConsolePrinter()]) if verbose else jsonl

    async def _run() -> tuple[QaAgent, AgentRunResult]:
        client = _text_client(settings, model_cfg)
        try:
            agent = build_qa_agent(layout, client, tracer, budget=budget)
            return agent, await agent.ask(question)
        finally:
            await client.aclose()

    agent, result = asyncio.run(_run())
    _output(console, agent, result, json_out, jsonl.path_for(result.run_id))


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="中断的 run_id（ask 输出中可见）")],
    doc: Annotated[str, typer.Option("--doc", help="document_id")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """从 checkpointer（PostgreSQL）恢复一次中断的问答，从最后完成的步骤继续。"""
    console = Console()
    settings = get_settings()
    if not is_postgres_url(settings.database_url):
        console.print("[yellow]当前 DATABASE_URL 不是 PostgreSQL，检查点只在内存中，无法跨进程恢复。[/]")
    model_cfg = _load_model(console, settings)
    agent, result = _run_async(
        settings,
        lambda: _with_ir_agent(settings, model_cfg, doc, Budget(), verbose, lambda a: a.resume(run_id)),
    )
    _output(console, agent, result, json_out, None)


@app.command()
def ingest(
    source: Annotated[
        Path, typer.Argument(help="PDF 文件；--offline 时为 MinerU 输出的 auto/ 目录", exists=True)
    ],
    offline: Annotated[bool, typer.Option("--offline", help="离线导入已有的 MinerU 解析产物")] = False,
    force: Annotated[bool, typer.Option("--force", help="文档已存在时仍重新解析（新 revision）")] = False,
    no_render: Annotated[bool, typer.Option("--no-render", help="跳过页面图渲染")] = False,
) -> None:
    """上传 → 解析 → 构建 IR 入库 → 渲染页面图。"""
    from drugpaper.orchestration.pipeline_graph import DocumentPipeline
    from drugpaper.services import Services

    console = Console()
    settings = get_settings()

    async def _run() -> Any:
        sv = Services.from_settings(settings)
        try:
            if not sv.db.is_postgres:
                await sv.db.create_all()  # 本地 SQLite：无需迁移即可使用
            pipe = DocumentPipeline(sv, render_pages=not no_render)
            if offline:
                return await pipe.run(offline_dir=source, force=force)
            return await pipe.run(pdf=source.read_bytes(), filename=source.name, force=force)
        finally:
            await sv.aclose()

    res = _run_async(settings, _run)
    typer.echo(orjson.dumps(res.model_dump(mode="json"), option=orjson.OPT_INDENT_2).decode())
    if res.status == "failed":
        console.print(f"[red]失败：{escape(str(res.error))}[/]")
        raise typer.Exit(code=1)


@db_app.command("upgrade")
def db_upgrade(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """运行 Alembic 迁移（DATABASE_URL）。"""
    from drugpaper.persistence.migrate import upgrade

    settings = get_settings()
    upgrade(settings.database_url, revision)
    typer.echo(f"已迁移到 {revision}：{settings.database_url.split('@')[-1]}")


@app.command()
def molecules(
    doc: Annotated[str, typer.Option("--doc", help="已导入的 document_id")],
    force: Annotated[bool, typer.Option("--force", help="绕过 Skill 缓存重新识别")] = False,
) -> None:
    """执行 P4 分子检测、逐分子识别、标注、标签候选与化合物归一。"""
    from drugpaper.orchestration.molecule_pipeline import MoleculePipeline
    from drugpaper.services import Services

    settings = get_settings()

    async def run() -> Any:
        services = Services.from_settings(settings)
        try:
            if not services.db.is_postgres:
                await services.db.create_all()
            return await MoleculePipeline(services).run(doc, force=force)
        finally:
            await services.aclose()

    result = _run_async(settings, run)
    typer.echo(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
) -> None:
    """启动 API（本阶段没有鉴权，只应绑定本机或内网地址）。"""
    import uvicorn

    settings = get_settings()
    if is_postgres_url(settings.database_url):
        use_selector_loop_on_windows()
    uvicorn.run("drugpaper.api.app:create_app", factory=True, host=host, port=port)


@app.command("figures")
def figures(doc: str = typer.Option(..., "--doc"), force: bool = typer.Option(False, "--force")) -> None:
    """Run the P5 coordinator and figure agents for an existing document."""

    async def run() -> None:
        from drugpaper.orchestration.figure_pipeline import FigurePipeline

        sv = Services.from_settings()
        try:
            result = await FigurePipeline(sv).run(doc, force=force)
            typer.echo(json.dumps(result, ensure_ascii=False, default=str))
        finally:
            await sv.aclose()

    asyncio.run(run())


@app.command("text-facts")
def text_facts(
    doc: Annotated[str, typer.Option("--doc", help="已导入的 document_id")],
    force: Annotated[bool, typer.Option("--force", help="绕过正文 Skill 缓存")] = False,
) -> None:
    """P6：正文抽取、实验名称解析、化合物归一与页包；保留缺失依赖和待复核状态。"""
    from drugpaper.orchestration.text_pipeline import TextPipeline

    settings = get_settings()

    async def run() -> Any:
        services = Services.from_settings(settings)
        try:
            if not services.db.is_postgres:
                await services.db.create_all()
            return await TextPipeline(services).run(doc, force=force)
        finally:
            await services.aclose()

    result = _run_async(settings, run)
    typer.echo(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    if result["status"] == "failed":
        raise typer.Exit(code=1)


@app.command("understand")
def understand(
    doc: Annotated[str, typer.Option("--doc", help="已导入的 document_id")],
    prepare_text: Annotated[bool, typer.Option("--prepare-text/--no-prepare-text")] = True,
    from_page: Annotated[int | None, typer.Option("--from-page", min=0)] = None,
    export_dir: Annotated[Path | None, typer.Option("--export-dir")] = None,
) -> None:
    """P6：逐页阅读、综合与路径校验、固定 JSON 投影；可保存完整导出包。"""
    from drugpaper.exporters.package import create_export
    from drugpaper.orchestration.full_text_pipeline import FullTextPipeline

    settings = get_settings()

    async def run() -> Any:
        sv = Services.from_settings(settings)
        try:
            if not sv.db.is_postgres:
                await sv.db.create_all()
            result = await FullTextPipeline(sv).run(doc, prepare_text=prepare_text, from_page=from_page)
            if export_dir is not None and result.get("result_id"):
                exported = await create_export(sv, doc, result_id=result["result_id"])
                import asyncio

                await asyncio.to_thread(export_dir.mkdir, parents=True, exist_ok=True)
                data = await sv.store.get(exported["blob_ref"])
                await asyncio.to_thread((export_dir / (exported["export_id"] + ".zip")).write_bytes, data)
                result["export"] = exported
            return result
        finally:
            await sv.aclose()

    result = _run_async(settings, run)
    typer.echo(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    if result["status"] in {"failed", "pending"}:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
