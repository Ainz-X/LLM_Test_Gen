from __future__ import annotations

from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "a3_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.context_extraction", "app.tasks.agent_jobs"],
)

celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    result_expires=3600,
    timezone="UTC",
)
