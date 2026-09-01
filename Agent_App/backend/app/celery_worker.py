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
    task_reject_on_worker_lost=True,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    task_publish_retry=True,
    task_publish_retry_policy={"max_retries": 5, "interval_start": 0, "interval_step": 0.5, "interval_max": 5},
    worker_send_task_events=True,
    task_send_sent_event=True,
    result_expires=3600,
    timezone="UTC",
)
