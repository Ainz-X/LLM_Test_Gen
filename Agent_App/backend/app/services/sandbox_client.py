from __future__ import annotations

import io
import json
import os
import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


SKIPPED_DIRECTORIES = {".git", ".gradle", ".idea", ".mvn", "node_modules", "target", "build"}


class SandboxUnavailable(RuntimeError):
    pass


def _archive_project(project_root: Path) -> io.BytesIO:
    if not project_root.is_dir():
        raise SandboxUnavailable("Project snapshot directory is missing.")

    buffer = io.BytesIO()
    total = 0
    entries = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in project_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(project_root)
            except ValueError:
                continue
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            try:
                mode = path.stat().st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            size = path.stat().st_size
            total += size
            entries += 1
            if entries > settings.max_project_archive_entries or total > settings.max_project_unpacked_bytes:
                raise SandboxUnavailable("Project snapshot exceeds the sandbox archive limits.")
            archive.write(path, relative.as_posix())
    buffer.seek(0)
    if buffer.getbuffer().nbytes > settings.max_project_archive_bytes:
        raise SandboxUnavailable("Compressed project snapshot exceeds the sandbox upload limit.")
    return buffer


def run_sandbox_operation(operation: str, project_root: Path, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    if not settings.sandbox_runner_enabled:
        raise SandboxUnavailable("Sandbox runner is disabled.")
    if not settings.sandbox_runner_token:
        raise SandboxUnavailable("Sandbox runner token is not configured.")

    archive = _archive_project(project_root)
    data = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value) for key, value in payload.items()}
    data["operation"] = operation
    headers = {"Authorization": f"Bearer {settings.sandbox_runner_token}"}
    timeout = max(timeout_seconds, settings.sandbox_runner_timeout_seconds) + 20
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            response = client.post(
                f"{settings.sandbox_runner_url.rstrip('/')}/v1/run",
                headers=headers,
                data=data,
                files={"archive": ("project.zip", archive, "application/zip")},
            )
    except httpx.HTTPError as exc:
        raise SandboxUnavailable(f"Sandbox runner request failed: {exc}") from exc
    finally:
        archive.close()

    try:
        body = response.json()
    except ValueError as exc:
        raise SandboxUnavailable(f"Sandbox runner returned a non-JSON response ({response.status_code}).") from exc
    if response.status_code >= 400:
        raise SandboxUnavailable(str(body.get("detail") or body.get("error") or "Sandbox runner rejected the request."))
    if not isinstance(body, dict):
        raise SandboxUnavailable("Sandbox runner returned an invalid result.")
    return body
