from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AgentJob, UploadedFile, User


ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass(frozen=True)
class JobSubmission:
    job: AgentJob
    enqueue: bool
    reused: bool


def _file_snapshot(file: UploadedFile) -> dict[str, str]:
    analysis = file.analysis or {}
    return {
        "file_id": file.id,
        "sha256": file.sha256,
        "project_id": str(analysis.get("_project_id") or ""),
        "relative_path": str(analysis.get("_project_relative_path") or file.original_name),
    }


def workload_key(
    *,
    user_id: str,
    kind: str,
    files: Iterable[UploadedFile],
    parameters: dict[str, Any],
    force: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    snapshots = sorted((_file_snapshot(file) for file in files), key=lambda item: item["file_id"])
    payload: dict[str, Any] = {
        "user_id": user_id,
        "kind": kind,
        "files": snapshots,
        "parameters": parameters,
    }
    if force:
        # Force is an explicit new attempt, not an accidental duplicate click.
        payload["force_attempt"] = uuid.uuid4().hex
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), snapshots


def submit_job(
    db: Session,
    *,
    user: User,
    kind: str,
    files: list[UploadedFile],
    snapshot_files: list[UploadedFile] | None = None,
    request: dict[str, Any],
    parameters: dict[str, Any],
    force: bool = False,
    client_key: str | None = None,
) -> JobSubmission:
    key, snapshots = workload_key(
        user_id=user.id,
        kind=kind,
        files=snapshot_files or files,
        parameters=parameters,
        force=force,
    )
    request_json = {
        **request,
        "target_file_ids": [file.id for file in files],
        "file_snapshots": snapshots,
        "client_idempotency_key": client_key or "",
        "workload_parameters": parameters,
    }
    existing = (
        db.query(AgentJob)
        .filter(AgentJob.user_id == user.id, AgentJob.idempotency_key == key)
        .one_or_none()
    )
    if existing:
        if existing.status in ACTIVE_JOB_STATUSES | {"succeeded"}:
            return JobSubmission(existing, enqueue=False, reused=True)
        # A cancelled or failed job is deliberately retried in-place. The
        # operation stays idempotent while users can recover from transient
        # worker, model, or Maven failures without creating another record.
        existing.status = "queued"
        existing.progress = 0
        existing.stage = "queued"
        existing.message = "Retry queued for the same workload."
        existing.error = ""
        existing.result_json = {}
        existing.cancel_requested = False
        existing.external_id = ""
        existing.started_at = None
        existing.finished_at = None
        existing.updated_at = dt.datetime.utcnow()
        existing.request_json = request_json
        db.commit()
        db.refresh(existing)
        return JobSubmission(existing, enqueue=True, reused=True)

    job = AgentJob(
        user_id=user.id,
        idempotency_key=key,
        kind=kind,
        status="queued",
        progress=0,
        stage="queued",
        message="Queued for background execution.",
        request_json=request_json,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent duplicate submit lost the race. Return the winner.
        db.rollback()
        existing = (
            db.query(AgentJob)
            .filter(AgentJob.user_id == user.id, AgentJob.idempotency_key == key)
            .one()
        )
        return JobSubmission(existing, enqueue=False, reused=True)
    db.refresh(job)
    return JobSubmission(job, enqueue=True, reused=False)


def mark_queue_failure(db: Session, job: AgentJob, exc: Exception) -> None:
    job.status = "failed"
    job.progress = 100
    job.stage = "queue_failed"
    job.message = "Background task submission failed. Check Redis and the Celery worker."
    job.error = f"{type(exc).__name__}: {exc}"
    job.finished_at = dt.datetime.utcnow()
    job.updated_at = dt.datetime.utcnow()
    db.commit()
