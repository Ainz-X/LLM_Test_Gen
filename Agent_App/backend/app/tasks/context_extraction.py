from __future__ import annotations

import csv
import datetime as dt
import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.celery_worker import celery_app
from app.core.config import settings
from app.db import SessionLocal, init_db
from app.models import AgentJob, CodeContext, UploadedFile
from app.services.code_context_service import build_code_context, class_fqn_from_analysis, class_fqn_from_method_fqn


EXTRACTOR_VERSION = "method-context-extractor-0.2.0"


def update_job(db: Session, job: AgentJob, *, status: str | None = None, progress: int | None = None, stage: str | None = None, message: str | None = None, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
    if stage is not None:
        job.stage = stage
    if message is not None:
        job.message = message
    if result is not None:
        job.result_json = result
    if error is not None:
        job.error = error
    job.updated_at = dt.datetime.utcnow()
    if status == "running" and job.started_at is None:
        job.started_at = dt.datetime.utcnow()
    if status in {"succeeded", "failed", "cancelled"}:
        job.finished_at = dt.datetime.utcnow()
    db.commit()


def cancelled(db: Session, job: AgentJob) -> bool:
    db.refresh(job)
    return bool(job.cancel_requested or job.status == "cancelled")


def run_process(db: Session, job: AgentJob, command: list[str], cwd: Path, timeout: int, stage: str, message: str, progress: int) -> dict[str, Any]:
    update_job(db, job, status="running", progress=progress, stage=stage, message=f"{progress}%：{message}")
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=5)
            return {
                "return_code": process.returncode,
                "output": (stdout or "") + (stderr or ""),
                "elapsed_seconds": int(time.monotonic() - started),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            elapsed = int(time.monotonic() - started)
            if cancelled(db, job):
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "return_code": None,
                    "output": (stdout or "") + (stderr or ""),
                    "elapsed_seconds": elapsed,
                    "cancelled": True,
                }
            if elapsed >= timeout:
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "return_code": None,
                    "output": (stdout or "") + (stderr or ""),
                    "elapsed_seconds": elapsed,
                    "timed_out": True,
                }
            update_job(db, job, progress=progress, stage=stage, message=f"{progress}%：{message}已运行 {elapsed} 秒，仍在正常执行...")


def force_maven_java8(work_project: Path) -> None:
    replacements = {
        r"(<maven\.compiler\.source>\s*)(?:1\.)?[5-7](\s*</maven\.compiler\.source>)": r"\g<1>1.8\2",
        r"(<maven\.compiler\.target>\s*)(?:1\.)?[5-7](\s*</maven\.compiler\.target>)": r"\g<1>1.8\2",
        r"(<source>\s*)(?:1\.)?[5-7](\s*</source>)": r"\g<1>1.8\2",
        r"(<target>\s*)(?:1\.)?[5-7](\s*</target>)": r"\g<1>1.8\2",
    }
    for pom in work_project.rglob("pom.xml"):
        text = pom.read_text(encoding="utf-8", errors="replace")
        updated = text
        for pattern, replacement in replacements.items():
            updated = re.sub(pattern, replacement, updated)
        if "<maven.compiler.source>" not in updated and "<properties>" in updated:
            updated = updated.replace(
                "<properties>",
                "<properties>\n    <maven.compiler.source>1.8</maven.compiler.source>\n    <maven.compiler.target>1.8</maven.compiler.target>",
                1,
            )
        if updated != text:
            pom.write_text(updated, encoding="utf-8")


def class_name_from_fqn(fqn: str) -> str:
    return class_fqn_from_method_fqn(fqn).rsplit(".", 1)[-1]


def method_hash(method_fqn: str) -> str:
    return hashlib.sha256(method_fqn.encode("utf-8")).hexdigest()


def upsert_context(
    db: Session,
    *,
    user_id: str,
    file: UploadedFile,
    project_id: str,
    method_fqn: str,
    signature: str,
    jimple: str,
    method_source: str,
    field_context: str,
    helper_signatures: str,
    throws_modifiers: str,
    source_path: str,
    context_source: str,
    metadata: dict[str, Any],
) -> None:
    digest = method_hash(method_fqn)
    row = (
        db.query(CodeContext)
        .filter(
            CodeContext.user_id == user_id,
            CodeContext.file_sha256 == file.sha256,
            CodeContext.extractor_version == EXTRACTOR_VERSION,
            CodeContext.method_fqn_hash == digest,
        )
        .one_or_none()
    )
    if row is None:
        row = CodeContext(
            user_id=user_id,
            file_id=file.id,
            project_id=project_id,
            file_sha256=file.sha256,
            extractor_version=EXTRACTOR_VERSION,
            method_fqn_hash=digest,
            method_fqn=method_fqn,
        )
        db.add(row)
    row.file_id = file.id
    row.project_id = project_id
    row.signature = signature
    row.jimple = jimple
    row.method_source = method_source
    row.field_context = field_context
    row.helper_signatures = helper_signatures
    row.throws_modifiers = throws_modifiers
    row.source_path = source_path
    row.context_source = context_source
    row.metadata_json = metadata
    row.updated_at = dt.datetime.utcnow()


def store_lightweight_context(db: Session, user_id: str, file: UploadedFile, project_id: str) -> int:
    context = build_code_context(file, max_methods=200, max_field_chars=20000)
    count = 0
    for method in context.get("methods", []):
        method_fqn = str(method.get("fqn") or "")
        if not method_fqn:
            continue
        upsert_context(
            db,
            user_id=user_id,
            file=file,
            project_id=project_id,
            method_fqn=method_fqn,
            signature=str(method.get("signature") or ""),
            jimple="",
            method_source=str(method.get("method_source") or ""),
            field_context="",
            helper_signatures="",
            throws_modifiers=str(method.get("throws_modifiers") or ""),
            source_path=file.storage_path,
            context_source="lightweight_static_analysis",
            metadata={"reason": "No compiled classpath was available, so SootUp/Jimple extraction was skipped."},
        )
        count += 1
    return count


def source_roots(work_project: Path) -> list[Path]:
    roots = [path for path in work_project.rglob("src/main/java") if path.is_dir()]
    return roots or [work_project]


def class_dirs(work_project: Path) -> list[Path]:
    return [path for path in work_project.rglob("target/classes") if path.is_dir()]


def parse_extractor_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def store_extractor_rows(db: Session, user_id: str, files: list[UploadedFile], project_id: str, csv_path: Path) -> int:
    by_class = {class_fqn_from_analysis(file.analysis or {}): file for file in files}
    count = 0
    for row in parse_extractor_csv(csv_path):
        method_fqn = row.get("FQN", "")
        class_fqn = class_fqn_from_method_fqn(method_fqn)
        file = by_class.get(class_fqn)
        if file is None:
            # Fall back to simple class-name matching for default-package or parser edge cases.
            simple = class_name_from_fqn(method_fqn)
            file = next((candidate for candidate in files if (candidate.analysis or {}).get("class_name") == simple), None)
        if file is None or not method_fqn:
            continue
        upsert_context(
            db,
            user_id=user_id,
            file=file,
            project_id=project_id,
            method_fqn=method_fqn,
            signature=row.get("Signature", ""),
            jimple=row.get("Jimple Code Representation", ""),
            method_source=row.get("Method Source", ""),
            field_context=row.get("Field Context", ""),
            helper_signatures=row.get("Constructor/Helper Signatures", ""),
            throws_modifiers=row.get("Throws/Modifiers", ""),
            source_path=(file.analysis or {}).get("_project_relative_path") or file.storage_path,
            context_source="sootup_javaparser_extractor",
            metadata={"extractor_csv": str(csv_path)},
        )
        count += 1
    return count


def mark_files_context(db: Session, files: list[UploadedFile], job: AgentJob, rows_by_file: dict[str, int]) -> None:
    now = dt.datetime.utcnow().isoformat()
    for file in files:
        analysis = dict(file.analysis or {})
        analysis["_context_job_id"] = job.id
        analysis["_context_extracted_at"] = now
        analysis["_context_rows"] = rows_by_file.get(file.id, 0)
        file.analysis = analysis


def extract_maven_project(db: Session, job: AgentJob, files: list[UploadedFile], project_id: str) -> tuple[int, dict[str, Any]]:
    root_value = (files[0].analysis or {}).get("_project_root")
    if not root_value:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": "Project root is missing."}
    project_root = Path(root_value)
    if not (project_root / "pom.xml").exists():
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": "Only Maven projects are supported for SootUp extraction in this worker."}
    extractor_jar = settings.method_context_extractor_jar
    if not extractor_jar.exists():
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": f"Extractor JAR not found: {extractor_jar}"}

    job_root = settings.storage_dir / "context_jobs" / job.id / project_id
    if job_root.exists():
        shutil.rmtree(job_root)
    job_root.mkdir(parents=True, exist_ok=True)
    work_project = job_root / "project"
    update_job(db, job, status="running", progress=15, stage="copy_project", message="15%：复制项目到后台任务工作区...")
    shutil.copytree(project_root, work_project, ignore=shutil.ignore_patterns("target", "build", ".git", ".gradle", "node_modules"))
    force_maven_java8(work_project)

    mvn = shutil.which("mvn")
    if not mvn:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": "mvn was not found in worker PATH."}

    compile_result = run_process(
        db,
        job,
        [mvn, "-q", "-Dmaven.compiler.source=1.8", "-Dmaven.compiler.target=1.8", "-DskipTests", "compile"],
        work_project,
        max(settings.context_extract_timeout_seconds, 180),
        "maven_compile",
        "Maven 正在编译项目，为 SootUp 生成 .class 字节码",
        35,
    )
    if compile_result.get("cancelled"):
        raise RuntimeError("cancelled")
    if compile_result.get("timed_out") or compile_result.get("return_code") != 0:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {
            "mode": "lightweight",
            "reason": "Maven compile failed; stored JavaParser-like lightweight context without Jimple.",
            "compile_output": str(compile_result.get("output") or "")[-4000:],
        }

    roots = source_roots(work_project)
    classes = class_dirs(work_project)
    if not classes:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": "No target/classes directories were produced."}

    output_csv = job_root / "Method_Context.csv"
    include_classes = sorted({class_fqn_from_analysis(file.analysis or {}) for file in files if (file.analysis or {}).get("class_name")})
    command = ["java", "-jar", str(extractor_jar)]
    for class_dir in classes:
        command.extend(["--input", str(class_dir)])
    for source_root in roots:
        command.extend(["--source-root", str(source_root)])
    command.extend(["--output", str(output_csv), "--verbose"])
    for class_fqn in include_classes:
        command.extend(["--include-class", class_fqn])
    extract_result = run_process(
        db,
        job,
        command,
        work_project,
        max(settings.context_extract_timeout_seconds, 300),
        "sootup_javaparser",
        "SootUp/JavaParser 正在提取 Jimple、方法源码和字段上下文",
        70,
    )
    if extract_result.get("cancelled"):
        raise RuntimeError("cancelled")
    if extract_result.get("timed_out") or extract_result.get("return_code") != 0:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {
            "mode": "lightweight",
            "reason": "SootUp extractor failed; stored lightweight context without Jimple.",
            "extract_output": str(extract_result.get("output") or "")[-4000:],
        }

    update_job(db, job, progress=88, stage="store_context", message="88%：正在把提取结果去重写入数据库...")
    rows = store_extractor_rows(db, job.user_id, files, project_id, output_csv)
    if rows == 0:
        rows = sum(store_lightweight_context(db, job.user_id, file, project_id) for file in files)
        return rows, {"mode": "lightweight", "reason": "Extractor produced no matching rows."}
    return rows, {
        "mode": "sootup_javaparser",
        "source_roots": [str(path) for path in roots],
        "class_dirs": [str(path) for path in classes],
        "output_csv": str(output_csv),
    }


@celery_app.task(name="extract_code_context")
def extract_code_context_task(job_id: str) -> dict[str, Any]:
    init_db()
    db = SessionLocal()
    try:
        job = db.get(AgentJob, job_id)
        if job is None:
            return {"ok": False, "error": "job not found"}
        update_job(db, job, status="running", progress=3, stage="load", message="3%：读取待提取文件列表...")
        request = job.request_json or {}
        file_ids = request.get("file_ids") or []
        query = db.query(UploadedFile).filter(UploadedFile.user_id == job.user_id)
        if file_ids:
            query = query.filter(UploadedFile.id.in_(file_ids))
        files = query.order_by(UploadedFile.created_at.asc()).all()
        if not files:
            update_job(db, job, status="failed", progress=100, stage="load", message="没有找到可提取的 Java 文件。", error="No files")
            return {"ok": False, "error": "No files"}

        grouped: dict[str, list[UploadedFile]] = {}
        for file in files:
            analysis = file.analysis or {}
            project_id = analysis.get("_project_id") or f"loose:{file.id}"
            grouped.setdefault(project_id, []).append(file)

        total_rows = 0
        group_results: list[dict[str, Any]] = []
        rows_by_file = {file.id: 0 for file in files}
        for index, (project_id, group_files) in enumerate(grouped.items(), start=1):
            if cancelled(db, job):
                update_job(db, job, status="cancelled", progress=100, stage="cancelled", message="任务已中断。")
                return {"ok": False, "cancelled": True}
            update_job(db, job, progress=8, stage="group", message=f"8%：准备提取项目 {index}/{len(grouped)}：{project_id}")
            rows, info = extract_maven_project(db, job, group_files, project_id)
            total_rows += rows
            for file in group_files:
                rows_by_file[file.id] = db.query(CodeContext).filter(CodeContext.user_id == job.user_id, CodeContext.file_id == file.id).count()
            group_results.append({"project_id": project_id, "file_count": len(group_files), "rows": rows, **info})
            db.commit()

        mark_files_context(db, files, job, rows_by_file)
        result = {"ok": True, "file_count": len(files), "context_rows": total_rows, "groups": group_results}
        update_job(db, job, status="succeeded", progress=100, stage="done", message=f"100%：上下文提取完成，写入 {total_rows} 行。", result=result)
        return result
    except RuntimeError as exc:
        if str(exc) == "cancelled":
            job = db.get(AgentJob, job_id)
            if job:
                update_job(db, job, status="cancelled", progress=100, stage="cancelled", message="任务已中断。")
            return {"ok": False, "cancelled": True}
        raise
    except Exception as exc:
        job = db.get(AgentJob, job_id)
        if job:
            update_job(db, job, status="failed", progress=100, stage="failed", message="上下文提取失败。", error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()
