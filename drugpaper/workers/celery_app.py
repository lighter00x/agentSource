from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from celery import Celery  # type: ignore[import-untyped]

from drugpaper.config.settings import Settings


def redis_db(url: str, index: int) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + str(index), parts.query, parts.fragment))


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or Settings()
    broker = (
        settings.celery_broker_url or redis_db(settings.redis_url, 11)
        if settings.redis_url
        else settings.celery_broker_url
    )
    backend = (
        settings.celery_result_backend or redis_db(settings.redis_url, 12)
        if settings.redis_url
        else settings.celery_result_backend
    )
    if not broker:
        broker = "memory://"
    app = Celery(
        "drugpaper", broker=broker, backend=backend or "cache+memory://", include=["drugpaper.workers.tasks"]
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": 86400},
        result_backend_transport_options={"visibility_timeout": 86400},
        visibility_timeout=86400,
        task_default_queue="batch",
        task_routes={"drugpaper.process_document": {"queue": "batch"}},
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
    )
    return app


app = create_celery()
