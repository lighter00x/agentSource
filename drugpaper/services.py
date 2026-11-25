"""进程级依赖装配：数据库、Blob 存储、渲染器、MinerU 客户端。CLI、API、管线、验证脚本共用。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drugpaper.detection.service import DetectionService
    from drugpaper.skills.bootstrap import SkillServices

from drugpaper.chem.opsin import OpsinClient
from drugpaper.config.settings import Settings, get_settings
from drugpaper.gateway.molparser import MolParserClient
from drugpaper.parsers.mineru_legacy.client import MinerUClient
from drugpaper.persistence.db import Database
from drugpaper.render.pages import PageRenderer
from drugpaper.storage.localfs import LocalFsBlobStore


@dataclass
class Services:
    settings: Settings
    db: Database
    store: LocalFsBlobStore
    renderer: PageRenderer
    _mineru: MinerUClient | None = field(default=None, repr=False)
    _molparser: MolParserClient | None = field(default=None, repr=False)
    _opsin: OpsinClient | None = field(default=None, repr=False)
    _skills: SkillServices | None = field(default=None, repr=False)
    _detector: DetectionService | None = field(default=None, repr=False)
    _skills_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def store_for(self, user_id: str) -> LocalFsBlobStore:
        if self.store.user_id == user_id:
            return self.store
        return LocalFsBlobStore(self.settings.blob_root, user_id=user_id)

    def renderer_for(self, user_id: str) -> PageRenderer:
        return PageRenderer(
            self.store_for(user_id), Path(self.settings.blob_root) / user_id / "_render_cache"
        )

    def detector(self) -> DetectionService:
        if self._detector is None:
            from drugpaper.detection.backend import YoloBackend
            from drugpaper.detection.remote import RemoteDetectionService
            from drugpaper.detection.service import MoleculeDetectionService

            self._detector = (
                RemoteDetectionService(self.settings, wait_for_capacity=True)
                if self.settings.yolo_backend == "remote"
                else MoleculeDetectionService(
                    YoloBackend(self.settings.yolo_model_path, self.settings.yolo_device), self.settings
                )
            )
        return self._detector

    async def skills(self) -> SkillServices:
        async with self._skills_lock:
            if self._skills is None:
                from drugpaper.skills.bootstrap import bootstrap

                self._skills = await bootstrap(self)
        return self._skills

    @classmethod
    def from_settings(cls, settings: Settings | None = None, *, user_id: str = "default") -> Services:
        s = settings or get_settings()
        store = LocalFsBlobStore(s.blob_root, user_id=user_id)
        return cls(
            settings=s,
            db=Database(s.database_url),
            store=store,
            renderer=PageRenderer(store, Path(s.blob_root) / "_render_cache"),
        )

    def mineru(self) -> MinerUClient:
        if self._mineru is not None:
            return self._mineru
        if not self.settings.mineru_url:
            raise RuntimeError("未配置 MINERU_URL；离线导入请使用 `drugpaper ingest --offline <auto 目录>`")
        if self._mineru is None:
            self._mineru = MinerUClient(
                self.settings.mineru_url,
                max_inflight=self.settings.mineru_max_inflight,
                timeout_s=self.settings.mineru_timeout_s,
                poll_interval_s=self.settings.mineru_poll_interval_s,
                submit_attempts=self.settings.mineru_submit_attempts,
                backoff_s=self.settings.mineru_backoff_s,
                submit_timeout_s=self.settings.mineru_submit_timeout_s,
                status_timeout_s=self.settings.mineru_status_timeout_s,
                result_timeout_s=self.settings.mineru_result_timeout_s,
            )
        return self._mineru

    def molparser(self) -> MolParserClient:
        if self._molparser is None:
            self._molparser = MolParserClient(
                self.settings.molparser_base_url,
                timeout_s=self.settings.molparser_timeout_s,
                rec_only=self.settings.molparser_rec_only,
            )
        return self._molparser

    def opsin(self) -> OpsinClient:
        if self._opsin is None:
            self._opsin = OpsinClient(self.settings.opsin_base_url, timeout_s=self.settings.opsin_timeout_s)
        return self._opsin

    async def aclose(self) -> None:
        if self._detector is not None:
            await self._detector.aclose()
        if self._skills is not None:
            await self._skills.models.aclose()
        if self._mineru is not None:
            await self._mineru.aclose()
        if self._molparser is not None:
            await self._molparser.aclose()
        if self._opsin is not None:
            await self._opsin.aclose()
        await self.db.dispose()
