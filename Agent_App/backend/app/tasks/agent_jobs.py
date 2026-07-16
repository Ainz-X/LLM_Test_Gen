from __future__ import annotations

from typing import Any

from app.celery_worker import celery_app
from app.db import SessionLocal, init_db
from app.models import AgentJob, GeneratedArtifact, UploadedFile, User
from app.services.agent_service import AgentService, GenerationCancelled, compact_tool_result
from app.tasks.context_extraction import cancelled, update_job


def _progress(index: int, total: int) -> int:
    return 5 + round((index / max(total, 1)) * 90)


def _files_for_job(service: AgentService, file_ids: list[str]) -> list[UploadedFile]:
    rows = (
        service.db.query(UploadedFile)
        .filter(UploadedFile.user_id == service.user.id, UploadedFile.id.in_(file_ids))
        .all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[file_id] for file_id in file_ids if file_id in by_id]


def _finalize_cancelled(db: Any, job: AgentJob, result: dict[str, Any]) -> dict[str, Any]:
    update_job(
        db,
        job,
        status="cancelled",
        progress=100,
        stage="cancelled",
        message="Cancelled at a safe task boundary.",
        result=result,
    )
    return result


@celery_app.task(name="batch_generate_tests")
def batch_generate_tests_task(job_id: str) -> dict[str, Any]:
    init_db()
    db = SessionLocal()
    try:
        job = db.get(AgentJob, job_id)
        if not job or job.status == "cancelled":
            return {"ok": False, "job_id": job_id, "cancelled": True}
        user = db.get(User, job.user_id)
        if not user:
            update_job(db, job, status="failed", progress=100, stage="user_missing", message="Job owner no longer exists.")
            return {"ok": False, "job_id": job_id, "error": "user_missing"}
        service = AgentService(db, user)
        request = job.request_json or {}
        rows = _files_for_job(service, list(request.get("target_file_ids") or []))
        skipped = list(request.get("initial_skipped") or [])
        generated: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        total = len(rows)
        update_job(db, job, status="running", progress=3, stage="preparing", message=f"Preparing {total} Java files.")
        for index, row in enumerate(rows, start=1):
            if cancelled(db, job):
                return _finalize_cancelled(
                    db,
                    job,
                    {"ok": False, "generated": generated, "failed": failed, "skipped": skipped, "total": total},
                )
            label = (row.analysis or {}).get("_project_relative_path") or row.original_name
            update_job(
                db,
                job,
                progress=_progress(index - 1, total),
                stage="generating",
                message=f"Generating {index}/{total}: {label}",
                result={"generated": generated, "failed": failed, "skipped": skipped, "total": total},
            )
            try:
                result = service.tool_generate_tests(
                    {
                        "file_id": row.id,
                        "goal": request.get("goal"),
                        "test_name": request.get("test_name"),
                        "test_name_mode": request.get("test_name_mode"),
                    },
                    cancel_check=lambda: cancelled(db, job),
                )
                if result.get("ok"):
                    generated.append(
                        {
                            "file_id": row.id,
                            "file_name": row.original_name,
                            "artifact_id": result.get("artifact_id"),
                            "artifact_file": result.get("file_name"),
                        }
                    )
                else:
                    failed.append({"file_id": row.id, "file_name": row.original_name, "error": result.get("reason") or result.get("error") or "Generation failed."})
            except GenerationCancelled:
                db.rollback()
                return _finalize_cancelled(
                    db,
                    job,
                    {"ok": False, "generated": generated, "failed": failed, "skipped": skipped, "total": total},
                )
            except Exception as exc:
                db.rollback()
                failed.append({"file_id": row.id, "file_name": row.original_name, "error": f"{type(exc).__name__}: {exc}"})
            update_job(
                db,
                job,
                progress=_progress(index, total),
                stage="generating",
                message=f"Completed {index}/{total}: {label}",
                result={"generated": generated, "failed": failed, "skipped": skipped, "total": total},
            )
        result = {
            "ok": not failed,
            "tool": "batch_generate_tests",
            "selected_count": total,
            "generated_count": len(generated),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "generated": generated,
            "failed": failed,
            "skipped": skipped[:100],
        }
        update_job(db, job, status="succeeded", progress=100, stage="completed", message=f"Generated {len(generated)} tests; {len(failed)} failed.", result=result)
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(AgentJob, job_id)
        if job:
            update_job(db, job, status="failed", progress=100, stage="failed", message="Batch test generation failed.", error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "job_id": job_id, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()


@celery_app.task(name="batch_run_coverage")
def batch_run_coverage_task(job_id: str) -> dict[str, Any]:
    init_db()
    db = SessionLocal()
    try:
        job = db.get(AgentJob, job_id)
        if not job or job.status == "cancelled":
            return {"ok": False, "job_id": job_id, "cancelled": True}
        user = db.get(User, job.user_id)
        if not user:
            raise RuntimeError("job owner no longer exists")
        service = AgentService(db, user)
        targets = list((job.request_json or {}).get("targets") or [])
        reports: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        total = len(targets)
        update_job(db, job, status="running", progress=3, stage="preparing", message=f"Preparing coverage for {total} tests.")
        for index, target in enumerate(targets, start=1):
            if cancelled(db, job):
                return _finalize_cancelled(db, job, {"ok": False, "reports": reports, "failed": failed, "total": total})
            label = target.get("file_name") or target.get("artifact_id")
            update_job(db, job, progress=_progress(index - 1, total), stage="coverage", message=f"Running JaCoCo {index}/{total}: {label}", result={"reports": reports, "failed": failed, "total": total})
            try:
                result = service.tool_run_coverage({"artifact_id": target["artifact_id"]})
                item = {"file_id": target.get("file_id"), "artifact_id": target["artifact_id"], "result": compact_tool_result(result)}
                (reports if result.get("ok") else failed).append(item)
            except Exception as exc:
                db.rollback()
                failed.append({"file_id": target.get("file_id"), "artifact_id": target.get("artifact_id"), "error": f"{type(exc).__name__}: {exc}"})
            update_job(db, job, progress=_progress(index, total), stage="coverage", message=f"Completed JaCoCo {index}/{total}: {label}", result={"reports": reports, "failed": failed, "total": total})
        result = {"ok": not failed, "tool": "batch_run_coverage", "coverage_count": len(reports), "failed_count": len(failed), "reports": reports, "failed": failed}
        update_job(db, job, status="succeeded", progress=100, stage="completed", message=f"Completed coverage for {len(reports)} tests.", result=result)
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(AgentJob, job_id)
        if job:
            update_job(db, job, status="failed", progress=100, stage="failed", message="Batch coverage failed.", error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "job_id": job_id, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()


@celery_app.task(name="batch_repair_low_coverage")
def batch_repair_low_coverage_task(job_id: str) -> dict[str, Any]:
    init_db()
    db = SessionLocal()
    try:
        job = db.get(AgentJob, job_id)
        if not job or job.status == "cancelled":
            return {"ok": False, "job_id": job_id, "cancelled": True}
        user = db.get(User, job.user_id)
        if not user:
            raise RuntimeError("job owner no longer exists")
        service = AgentService(db, user)
        targets = list((job.request_json or {}).get("targets") or [])
        repaired: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        total = len(targets)
        update_job(db, job, status="running", progress=3, stage="preparing", message=f"Preparing low-coverage repair for {total} tests.")
        for index, target in enumerate(targets, start=1):
            if cancelled(db, job):
                return _finalize_cancelled(db, job, {"ok": False, "repaired": repaired, "failed": failed, "total": total})
            label = target.get("file_name") or target.get("file_id")
            update_job(db, job, progress=_progress(index - 1, total), stage="repair", message=f"Repairing coverage {index}/{total}: {label}", result={"repaired": repaired, "failed": failed, "total": total})
            try:
                reply, steps = service.repair_low_coverage(None, target["file_id"], target.get("artifact_id"))
                repaired.append({"file_id": target["file_id"], "artifact_id": target.get("artifact_id"), "reply": reply, "steps": [compact_tool_result(step) for step in steps]})
            except Exception as exc:
                db.rollback()
                failed.append({"file_id": target.get("file_id"), "artifact_id": target.get("artifact_id"), "error": f"{type(exc).__name__}: {exc}"})
            update_job(db, job, progress=_progress(index, total), stage="repair", message=f"Completed repair {index}/{total}: {label}", result={"repaired": repaired, "failed": failed, "total": total})
        result = {"ok": not failed, "tool": "batch_repair_low_coverage", "repaired_count": len(repaired), "failed_count": len(failed), "repaired": repaired, "failed": failed}
        update_job(db, job, status="succeeded", progress=100, stage="completed", message=f"Completed {len(repaired)} repair workflows.", result=result)
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(AgentJob, job_id)
        if job:
            update_job(db, job, status="failed", progress=100, stage="failed", message="Batch low-coverage repair failed.", error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "job_id": job_id, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()
