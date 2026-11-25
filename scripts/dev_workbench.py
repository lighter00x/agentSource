"""独立前端联调后端。默认离线，可显式接入服务；不迁移业务库，启动不执行推理。"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from contextlib import suppress
from pathlib import Path

import uvicorn

from drugpaper.api.app import create_app
from drugpaper.config.settings import Settings
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.services import Services


async def serve(port: int, data_dir: Path | None, fixture: Path | None, connected: bool = False) -> None:
    root = (data_dir or Path(tempfile.mkdtemp(prefix="drugpaper-web-dev-"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Isolated storage always wins; connected mode reads only service settings from .env.
    connections = {}
    if connected:
        upstream = Settings()
        connections = {
            key: value
            for key, value in upstream.model_dump().items()
            if key.startswith(("mineru_", "molparser_", "opsin_", "yolo_", "figure_exploration_"))
        }
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{root / 'workbench.db'}",
        blob_root=root / "blobs",
        runs_dir=root / "runs",
        skills_watch_enabled=False,
        redis_url="",
        celery_broker_url="",
        celery_result_backend="",
        render_dpi=100,
        **({"mineru_url": "", "molparser_base_url": "", "opsin_base_url": ""} | connections),
    )
    sv = Services.from_settings(settings)
    try:
        await sv.db.create_all()
        if fixture is not None:
            result = await DocumentPipeline(sv).run(offline_dir=fixture)
            if result.status != "ready":
                raise RuntimeError(f"离线论文导入失败：{result.status}")
            # No fake production facts or successful model predictions are seeded.
            print(f"离线论文已导入：{result.document_id}", flush=True)
        print(f"隔离联调数据目录：{root}\nAPI：http://127.0.0.1:{port}/docs", flush=True)
        print(
            "已接入配置中的解析/检测/识别服务，仍使用隔离数据库；启动不执行模型。"
            if connected
            else "此实例未配置 MinerU/MolParser/Redis；不会在启动时调用模型。",
            flush=True,
        )
    except BaseException:
        await sv.aclose()
        raise
    # Let the application close Services during its lifespan shutdown, before
    # uvicorn re-raises SIGINT/SIGTERM; cleanup after serve() can be cancelled.
    app = create_app()
    app.state.services = sv
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--data-dir", type=Path, help="专用联调目录；默认新建临时目录，永不使用业务库")
    parser.add_argument("--fixture", type=Path, help="可选的已有 MinerU 离线产物目录")
    parser.add_argument(
        "--connected", action="store_true", help="接入本地配置的MinerU/分子服务，仍保持开发库隔离"
    )
    args = parser.parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(serve(args.port, args.data_dir, args.fixture, args.connected))


if __name__ == "__main__":
    main()
