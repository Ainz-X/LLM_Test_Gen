from __future__ import annotations

import csv
import io
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile


APP = FastAPI(title="A3 Sandbox Runner", docs_url=None, redoc_url=None, openapi_url=None)
JACOCO_VERSION = "0.8.12"
JACOCO_AGENT_PATH = Path(os.getenv("JACOCO_AGENT_PATH", "/opt/java-libs/org.jacoco.agent-0.8.12-runtime.jar"))
EXTRACTOR_JAR = Path(os.getenv("METHOD_CONTEXT_EXTRACTOR_JAR", "/app/LLM_Test_Gen/Java_Scripts/method-context-extractor/target/method-context-extractor-0.2.0-SNAPSHOT-jar-with-dependencies.jar"))
SETTINGS_XML = Path("/opt/sandbox/maven-settings.xml")
RUNNER_TOKEN = os.getenv("SANDBOX_RUNNER_TOKEN", "")
MAX_ARCHIVE_BYTES = int(os.getenv("MAX_PROJECT_ARCHIVE_BYTES", str(150 * 1024 * 1024)))
MAX_ARCHIVE_ENTRIES = int(os.getenv("MAX_PROJECT_ARCHIVE_ENTRIES", "10000"))
MAX_UNPACKED_BYTES = int(os.getenv("MAX_PROJECT_UNPACKED_BYTES", str(750 * 1024 * 1024)))
MAX_COMPRESSION_RATIO = int(os.getenv("MAX_PROJECT_COMPRESSION_RATIO", "100"))
PROCESS_FILE_BYTES = int(os.getenv("SANDBOX_PROCESS_FILE_BYTES", str(512 * 1024 * 1024)))


def _authorized(authorization: str | None) -> None:
    expected = f"Bearer {RUNNER_TOKEN}"
    if not RUNNER_TOKEN or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized sandbox request.")


def _safe_relative(name: str) -> Path:
    path = Path(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe archive path.")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _extract_archive(content: bytes, destination: Path) -> None:
    if len(content) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Project archive exceeds sandbox size limit.")
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise HTTPException(status_code=413, detail="Project archive has too many entries.")
        for info in members:
            if info.is_dir():
                continue
            try:
                relative = _safe_relative(info.filename)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if _is_symlink(info):
                raise HTTPException(status_code=400, detail="Symbolic links are not allowed in project archives.")
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise HTTPException(status_code=413, detail="Project archive exceeds unpacked size limit.")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise HTTPException(status_code=413, detail="Project archive compression ratio exceeds sandbox limit.")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))


def _force_java8(project: Path) -> None:
    replacements = {
        r"(<maven\.compiler\.source>\s*)(?:1\.)?[5-7](\s*</maven\.compiler\.source>)": r"\g<1>1.8\2",
        r"(<maven\.compiler\.target>\s*)(?:1\.)?[5-7](\s*</maven\.compiler\.target>)": r"\g<1>1.8\2",
        r"(<source>\s*)(?:1\.)?[5-7](\s*</source>)": r"\g<1>1.8\2",
        r"(<target>\s*)(?:1\.)?[5-7](\s*</target>)": r"\g<1>1.8\2",
    }
    for pom in project.rglob("pom.xml"):
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


def _isolate_existing_tests(project: Path) -> list[str]:
    ignored: list[str] = []
    for candidate in [project / "src" / "test" / "java", project / "test_suite", project / "tests", project / "evosuite-tests"]:
        if not candidate.exists():
            continue
        destination = candidate.parent / f".a3_ignored_{candidate.name}"
        shutil.move(str(candidate), str(destination))
        ignored.append(str(candidate.relative_to(project)).replace("\\", "/"))
    return ignored


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (PROCESS_FILE_BYTES, PROCESS_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (96, 96))


def _kill_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    extractor = len(command) >= 2 and command[0] == "java" and command[1] == "-jar"
    java_options = (
        "-Xmx1280m -XX:MaxMetaspaceSize=384m -XX:CompressedClassSpaceSize=192m"
        if extractor
        else "-Xmx512m -XX:MaxMetaspaceSize=256m -XX:CompressedClassSpaceSize=128m"
    )
    environment = {
        "HOME": "/home/app",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "MAVEN_OPTS": f"{java_options} -Dmaven.repo.local=/home/app/.m2/repository",
        "JAVA_TOOL_OPTIONS": java_options,
    }
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
        preexec_fn=_limits,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(1, timeout_seconds))
        return {"return_code": process.returncode, "output": (stdout or "") + (stderr or ""), "elapsed_seconds": int(time.monotonic() - started)}
    except subprocess.TimeoutExpired:
        _kill_group(process)
        stdout, stderr = process.communicate()
        return {"return_code": None, "output": (stdout or "") + (stderr or ""), "elapsed_seconds": int(time.monotonic() - started), "timed_out": True}


def _mvn_args(*args: str) -> list[str]:
    settings = ["-s", str(SETTINGS_XML)] if SETTINGS_XML.exists() else []
    return ["mvn", *settings, "-B", "-U", *args]


def _parse_csv(path: Path, target_class: str) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "reason": "JaCoCo CSV report was not created."}
    rows = list(csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines()))
    counters = (("instruction", "INSTRUCTION"), ("branch", "BRANCH"), ("complexity", "COMPLEXITY"), ("line", "LINE"), ("method", "METHOD"), ("class_counter", "CLASS"))

    def metric(row: dict[str, str], prefix: str) -> dict[str, Any]:
        missed = int(row.get(f"{prefix}_MISSED", 0) or 0)
        covered = int(row.get(f"{prefix}_COVERED", 0) or 0)
        total = missed + covered
        return {"missed": missed, "covered": covered, "total": total, "percent": round(covered * 100 / total, 2) if total else None}

    classes = [{"package": row.get("PACKAGE", ""), "class_name": row.get("CLASS", ""), **{key: metric(row, prefix) for key, prefix in counters}} for row in rows]
    expected = target_class.replace("/", ".").strip()

    def matches_target(item: dict[str, Any]) -> bool:
        class_name = str(item["class_name"]).replace("/", ".")
        package = str(item["package"]).replace("/", ".").strip(".")
        full_name = f"{package}.{class_name}" if package else class_name
        simple_name = class_name.split("$", 1)[0].rsplit(".", 1)[-1]
        return full_name == expected or simple_name == expected.rsplit(".", 1)[-1]

    matches = [item for item in classes if matches_target(item)]
    target = matches[0] if matches else None
    return {"ok": bool(classes), "target_class": target, "classes": classes, "matched": bool(target)}


def _coverage(project: Path, test_relative_path: str, test_code: str, test_class: str, target_class: str, test_timeout: int, report_timeout: int) -> dict[str, Any]:
    if not JACOCO_AGENT_PATH.exists():
        return {"ok": False, "stage": "jacoco_agent", "reason": "JaCoCo runtime agent is unavailable in sandbox."}
    ignored = _isolate_existing_tests(project)
    target = project / _safe_relative(test_relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(test_code, encoding="utf-8")
    arg_line = f"-javaagent:{JACOCO_AGENT_PATH}=destfile={(project / 'target' / 'jacoco.exec').as_posix()}"
    test = _run(_mvn_args("-Dmaven.compiler.source=1.8", "-Dmaven.compiler.target=1.8", "-Djacoco.skip=true", f"-DargLine={arg_line}", f"-Dtest={test_class.rsplit('.', 1)[-1]}", "test"), project, test_timeout)
    if test.get("timed_out") or test.get("return_code") != 0:
        return {"ok": False, "stage": "maven_test", "ignored_existing_test_sources": ignored, **test}
    report = _run(_mvn_args("-Dmaven.compiler.source=1.8", "-Dmaven.compiler.target=1.8", f"org.jacoco:jacoco-maven-plugin:{JACOCO_VERSION}:report"), project, report_timeout)
    if report.get("timed_out") or report.get("return_code") != 0:
        return {"ok": False, "stage": "jacoco_report", "ignored_existing_test_sources": ignored, "output": f"{test.get('output', '')}{report.get('output', '')}", "elapsed_seconds": {"test": test.get("elapsed_seconds"), "report": report.get("elapsed_seconds")}, **{key: value for key, value in report.items() if key != "output"}}
    coverage = _parse_csv(project / "target" / "site" / "jacoco" / "jacoco.csv", target_class)
    return {"ok": bool(coverage.get("ok")), "coverage": coverage, "junit_output": f"{test.get('output', '')}{report.get('output', '')}"[-12000:], "ignored_existing_test_sources": ignored, "elapsed_seconds": {"test": test.get("elapsed_seconds"), "report": report.get("elapsed_seconds")}}


def _compile(project: Path, test_relative_path: str, test_code: str, timeout: int) -> dict[str, Any]:
    ignored = _isolate_existing_tests(project)
    target = project / _safe_relative(test_relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(test_code, encoding="utf-8")
    result = _run(_mvn_args("-Dmaven.compiler.source=1.8", "-Dmaven.compiler.target=1.8", "-DskipTests", "test-compile"), project, timeout)
    return {"ok": not result.get("timed_out") and result.get("return_code") == 0, "stage": "maven_compile", "ignored_existing_test_sources": ignored, **result}


def _context(project: Path, include_classes: list[str], compile_timeout: int, extract_timeout: int) -> dict[str, Any]:
    compile_result = _run(_mvn_args("-q", "-Dmaven.compiler.source=1.8", "-Dmaven.compiler.target=1.8", "-DskipTests", "compile"), project, compile_timeout)
    if compile_result.get("timed_out") or compile_result.get("return_code") != 0:
        return {"ok": False, "stage": "maven_compile", **compile_result}
    if not EXTRACTOR_JAR.exists():
        return {"ok": False, "stage": "sootup_javaparser", "reason": "Method context extractor is unavailable in sandbox."}
    roots = [path for path in project.rglob("src/main/java") if path.is_dir()] or [project]
    classes = [path for path in project.rglob("target/classes") if path.is_dir()]
    if not classes:
        return {"ok": False, "stage": "sootup_javaparser", "reason": "No compiled target/classes directory was produced."}
    output = project / "Method_Context.csv"
    command = ["java", "-jar", str(EXTRACTOR_JAR)]
    for value in classes:
        command.extend(["--input", str(value)])
    for value in roots:
        command.extend(["--source-root", str(value)])
    command.extend(["--output", str(output), "--verbose"])
    for value in include_classes:
        command.extend(["--include-class", value])
    extract = _run(command, project, extract_timeout)
    if extract.get("timed_out") or extract.get("return_code") != 0:
        return {"ok": False, "stage": "sootup_javaparser", **extract}
    return {"ok": True, "csv": output.read_text(encoding="utf-8", errors="replace") if output.exists() else "", "compile_elapsed_seconds": compile_result.get("elapsed_seconds"), "extract_elapsed_seconds": extract.get("elapsed_seconds")}


@APP.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@APP.post("/v1/run")
async def run_operation(
    archive: UploadFile = File(...),
    operation: str = Form(...),
    test_relative_path: str = Form(""),
    test_code: str = Form(""),
    test_class: str = Form(""),
    target_class: str = Form(""),
    include_classes: str = Form("[]"),
    compile_timeout: int = Form(300),
    test_timeout: int = Form(300),
    report_timeout: int = Form(180),
    extract_timeout: int = Form(300),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorized(authorization)
    if operation not in {"compile", "coverage", "context"}:
        raise HTTPException(status_code=400, detail="Unsupported sandbox operation.")
    if operation in {"compile", "coverage"} and (not test_relative_path or not test_class):
        raise HTTPException(status_code=400, detail="Test path and class are required.")
    try:
        requested_classes = json.loads(include_classes)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="include_classes must be JSON.") from exc
    if not isinstance(requested_classes, list) or not all(isinstance(item, str) for item in requested_classes):
        raise HTTPException(status_code=400, detail="include_classes must be a string array.")
    content = await archive.read(MAX_ARCHIVE_BYTES + 1)
    with tempfile.TemporaryDirectory(prefix="a3-sandbox-", dir="/tmp") as temp_dir:
        project = Path(temp_dir) / "project"
        project.mkdir()
        _extract_archive(content, project)
        _force_java8(project)
        if not (project / "pom.xml").exists():
            raise HTTPException(status_code=400, detail="Only Maven projects are supported by sandbox execution.")
        if operation == "compile":
            return _compile(project, test_relative_path, test_code, compile_timeout)
        if operation == "coverage":
            return _coverage(project, test_relative_path, test_code, test_class, target_class, test_timeout, report_timeout)
        return _context(project, requested_classes, compile_timeout, extract_timeout)
