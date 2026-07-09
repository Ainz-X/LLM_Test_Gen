from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.models import Conversation, GeneratedArtifact, UploadedFile, User
from app.schemas import (
    ArtifactOut,
    ArtifactReadOut,
    BatchFileDeleteIn,
    BatchFileDeleteOut,
    BatchGenerateIn,
    BatchUploadOut,
    UploadedFileOut,
)
from app.security import get_current_user
from app.services.agent_service import AgentService
from app.services.java_analysis import analyze_java_source
from app.services.storage_service import put_object, remove_object


router = APIRouter(prefix="/files", tags=["files"])

ZIP_IGNORED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".mvn/wrapper",
    "__macosx",
    "build",
    "node_modules",
    "out",
    "target",
}


def safe_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in name).strip("._") or "Uploaded.java"


def safe_relative_path(name: str) -> tuple[Path, str]:
    raw = name.replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("unsafe relative path")
        parts.append(safe_name(part))
    if not parts:
        raise ValueError("empty relative path")
    return Path(*parts), "/".join(parts)


def detect_project_root(relative_names: list[str]) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    for name in relative_names:
        lower = name.lower()
        parent = str(Path(name).parent).replace("\\", "/")
        if parent == ".":
            parent = ""
        if lower.endswith("pom.xml"):
            candidates.append(("maven", parent))
        elif lower.endswith("build.gradle") or lower.endswith("build.gradle.kts"):
            candidates.append(("gradle", parent))
    if not candidates:
        return "java-set", ""
    candidates.sort(key=lambda item: (len(item[1].split("/")) if item[1] else 0, item[1]))
    return candidates[0]


def should_skip_zip_member(name: str) -> bool:
    parts = [part.lower() for part in name.replace("\\", "/").split("/") if part]
    if not parts:
        return True
    if parts[0] == "__macosx":
        return True
    normalized = "/".join(parts)
    return any(part in ZIP_IGNORED_DIRS for part in parts) or any(
        normalized.startswith(f"{ignored}/") for ignored in ZIP_IGNORED_DIRS if "/" in ignored
    )


def persist_java(name: str, content: bytes, db: Session, user: User) -> UploadedFile:
    safe = safe_name(name)
    if not safe.endswith(".java"):
        raise ValueError("Only .java files are supported")
    digest = hashlib.sha256(content).hexdigest()
    folder = settings.storage_dir / "uploads" / user.id / digest[:16]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe
    path.write_bytes(content)
    source = content.decode("utf-8", errors="replace")
    analysis = analyze_java_source(source, safe)
    object_key = f"uploads/{user.id}/{digest[:16]}/{safe}"
    stored_object = put_object(path, object_key)
    if stored_object:
        analysis["_object_key"] = stored_object
    record = UploadedFile(
        user_id=user.id,
        original_name=safe,
        storage_path=str(path),
        sha256=digest,
        size_bytes=len(content),
        analysis=analysis,
    )
    db.add(record)
    return record


def persist_project_files(
    project_name: str,
    file_items: list[tuple[str, bytes]],
    db: Session,
    user: User,
) -> tuple[list[UploadedFile], list[dict[str, str]]]:
    digest_source = hashlib.sha256()
    for raw_name, content in sorted(file_items, key=lambda item: item[0]):
        digest_source.update(raw_name.replace("\\", "/").encode("utf-8", errors="replace"))
        digest_source.update(hashlib.sha256(content).hexdigest().encode("ascii"))
    project_id = digest_source.hexdigest()[:16]
    project_folder = settings.storage_dir / "projects" / user.id / project_id
    project_folder.mkdir(parents=True, exist_ok=True)

    rejected: list[dict[str, str]] = []
    java_items: list[tuple[str, Path, bytes]] = []
    relative_names: list[str] = []
    for raw_name, content in file_items:
        try:
            relative_path, relative_name = safe_relative_path(raw_name)
        except ValueError as exc:
            rejected.append({"name": raw_name, "reason": str(exc)})
            continue
        target = project_folder / relative_path
        try:
            target.resolve().relative_to(project_folder.resolve())
        except ValueError:
            rejected.append({"name": raw_name, "reason": "unsafe project path"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        relative_names.append(relative_name)
        if relative_name.endswith(".java"):
            java_items.append((relative_name, target, content))

    build_tool, build_root_rel = detect_project_root(relative_names)
    build_root = project_folder / Path(build_root_rel) if build_root_rel else project_folder
    uploaded: list[UploadedFile] = []
    for relative_name, path, content in java_items:
        source = content.decode("utf-8", errors="replace")
        analysis = analyze_java_source(source, Path(relative_name).name)
        object_key = f"projects/{user.id}/{project_id}/{relative_name}"
        stored_object = put_object(path, object_key)
        if stored_object:
            analysis["_object_key"] = stored_object
        analysis.update(
            {
                "_project_id": project_id,
                "_project_name": project_name,
                "_project_root": str(build_root),
                "_project_storage_root": str(project_folder),
                "_project_build_tool": build_tool,
                "_project_relative_path": relative_name,
            }
        )
        display_name = relative_name if len(relative_name) <= 255 else "..." + relative_name[-252:]
        record = UploadedFile(
            user_id=user.id,
            original_name=display_name,
            storage_path=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            analysis=analysis,
        )
        db.add(record)
        uploaded.append(record)
    return uploaded, rejected


def persist_project_zip(name: str, content: bytes, db: Session, user: User) -> tuple[list[UploadedFile], list[dict[str, str]]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            file_items: list[tuple[str, bytes]] = []
            rejected: list[dict[str, str]] = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if should_skip_zip_member(info.filename):
                    continue
                if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                    rejected.append({"name": info.filename, "reason": "unsafe zip path"})
                    continue
                file_items.append((info.filename, archive.read(info)))
    except zipfile.BadZipFile:
        return [], [{"name": name, "reason": "invalid zip file"}]
    uploaded, nested_rejected = persist_project_files(name, file_items, db, user)
    return uploaded, [*rejected, *nested_rejected]


def delete_file_record(file: UploadedFile, db: Session, user: User) -> int:
    artifacts = (
        db.query(GeneratedArtifact)
        .filter(GeneratedArtifact.user_id == user.id, GeneratedArtifact.file_id == file.id)
        .all()
    )
    for artifact in artifacts:
        remove_object((artifact.metadata_json or {}).get("object_key"))
        path = Path(artifact.storage_path)
        if path.exists():
            path.unlink()
        db.delete(artifact)

    remove_object((file.analysis or {}).get("_object_key"))
    upload_path = Path(file.storage_path)
    if upload_path.exists():
        upload_path.unlink()

    (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id, Conversation.active_file_id == file.id)
        .update({Conversation.active_file_id: None}, synchronize_session=False)
    )
    db.delete(file)
    return len(artifacts)


@router.post("/upload", response_model=UploadedFileOut)
def upload_java(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = safe_name(file.filename or "Uploaded.java")
    if not name.endswith(".java"):
        raise HTTPException(status_code=400, detail="Only .java files are supported")
    content = file.file.read()
    record = persist_java(name, content, db, user)
    db.commit()
    db.refresh(record)
    return record


@router.post("/upload/batch", response_model=BatchUploadOut)
def upload_batch(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    uploaded: list[UploadedFile] = []
    rejected: list[dict[str, str]] = []

    file_items = [(file.filename or "Uploaded.java", file.file.read()) for file in files]
    zip_items = [(name, content) for name, content in file_items if name.lower().endswith(".zip")]
    loose_items = [(name, content) for name, content in file_items if not name.lower().endswith(".zip")]

    for raw_name, content in zip_items:
        zip_uploaded, zip_rejected = persist_project_zip(raw_name, content, db, user)
        uploaded.extend(zip_uploaded)
        rejected.extend(zip_rejected)

    for raw_name, content in loose_items:
        normalized_name = raw_name.replace("\\", "/")
        if "/" in normalized_name:
            rejected.append({"name": raw_name, "reason": "project folders must be uploaded as a .zip file"})
            continue
        if not raw_name.endswith(".java"):
            rejected.append({"name": raw_name, "reason": "only loose .java files or project .zip files are supported"})
            continue
        try:
            uploaded.append(persist_java(raw_name, content, db, user))
        except ValueError as exc:
            rejected.append({"name": raw_name, "reason": str(exc)})
    db.commit()
    for record in uploaded:
        db.refresh(record)
    return BatchUploadOut(files=uploaded, rejected=rejected)


@router.get("", response_model=list[UploadedFileOut])
def list_files(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(UploadedFile).filter(UploadedFile.user_id == user.id).order_by(UploadedFile.created_at.desc()).all()


@router.post("/delete/batch", response_model=BatchFileDeleteOut)
def delete_files_batch(
    payload: BatchFileDeleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted: list[str] = []
    not_found: list[str] = []
    artifact_count = 0
    for file_id in payload.file_ids:
        file = db.get(UploadedFile, file_id)
        if not file or file.user_id != user.id:
            not_found.append(file_id)
            continue
        artifact_count += delete_file_record(file, db, user)
        deleted.append(file_id)
    db.commit()
    return BatchFileDeleteOut(ok=not not_found, deleted_file_ids=deleted, deleted_artifacts=artifact_count, not_found=not_found)


@router.post("/generate/batch")
def generate_tests_batch(
    payload: BatchGenerateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    service = AgentService(db, user)
    return service.tool_batch_generate_tests(
        {
            "file_ids": payload.file_ids or [],
            "only_missing": payload.only_missing,
            "max_files": payload.max_files,
            "goal": payload.goal,
        }
    )


@router.get("/{file_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(file_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    file = db.get(UploadedFile, file_id)
    if not file or file.user_id != user.id:
        raise HTTPException(status_code=404, detail="File not found")
    return (
        db.query(GeneratedArtifact)
        .filter(GeneratedArtifact.user_id == user.id, GeneratedArtifact.file_id == file_id)
        .order_by(GeneratedArtifact.created_at.desc())
        .all()
    )


@router.delete("/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    file = db.get(UploadedFile, file_id)
    if not file or file.user_id != user.id:
        raise HTTPException(status_code=404, detail="File not found")
    deleted_artifacts = delete_file_record(file, db, user)
    db.commit()
    return {"ok": True, "deleted_file_id": file_id, "deleted_artifacts": deleted_artifacts}


@router.get("/artifacts/{artifact_id}", response_model=ArtifactReadOut)
def read_artifact(artifact_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    artifact = db.get(GeneratedArtifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    code = Path(artifact.storage_path).read_text(encoding="utf-8", errors="replace")
    return ArtifactReadOut(artifact=artifact, code=code)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    artifact = db.get(GeneratedArtifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(artifact.storage_path, filename=Path(artifact.storage_path).name)


@router.get("/artifacts.zip")
def download_artifacts_zip(
    file_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(GeneratedArtifact).filter(GeneratedArtifact.user_id == user.id)
    if file_id:
        file = db.get(UploadedFile, file_id)
        if not file or file.user_id != user.id:
            raise HTTPException(status_code=404, detail="File not found")
        query = query.filter(GeneratedArtifact.file_id == file_id)
    artifacts = query.order_by(GeneratedArtifact.created_at.desc()).all()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            source_file = db.get(UploadedFile, artifact.file_id)
            folder = safe_name((source_file.analysis.get("class_name") if source_file else None) or (source_file.original_name if source_file else "tests"))
            archive.write(artifact.storage_path, arcname=f"{folder}/{Path(artifact.storage_path).name}")
    buffer.seek(0)
    filename = "a3-agent-artifacts.zip" if file_id is None else f"{file_id}-artifacts.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
