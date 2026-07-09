from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


TEST_SOURCE_MARKERS = (
    "/src/test/java/",
    "/src/integration-test/java/",
    "/test_suite/",
    "/tests/",
    "/a3_generated/",
    "/generated-test/",
    "/generated-tests/",
)

TEST_SOURCE_PREFIXES = (
    "test/",
    "tests/",
    "test_suite/",
)

TEST_CLASS_SUFFIXES = (
    "Test",
    "Tests",
    "TestCase",
    "IT",
    "ITCase",
)


def normalized_java_path(name: str) -> str:
    return name.replace("\\", "/").strip("/")


def classify_java_source(name: str, class_name: str | None = None) -> tuple[bool, str]:
    normalized = normalized_java_path(name)
    lowered = f"/{normalized.lower()}/"
    lowered_no_wrap = normalized.lower()
    for prefix in TEST_SOURCE_PREFIXES:
        if lowered_no_wrap.startswith(prefix):
            return True, f"path_prefix:{prefix.rstrip('/')}"
    for marker in TEST_SOURCE_MARKERS:
        if marker in lowered:
            return True, f"path_marker:{marker.strip('/')}"

    basename = PurePosixPath(normalized).name
    stem = basename[:-5] if basename.endswith(".java") else PurePosixPath(normalized).stem
    if stem.endswith(TEST_CLASS_SUFFIXES) or stem.endswith("_Test") or stem.endswith("_Tests"):
        return True, "class_suffix:test"
    if class_name and (
        class_name.endswith(TEST_CLASS_SUFFIXES)
        or class_name.endswith("_Test")
        or class_name.endswith("_Tests")
    ):
        return True, "class_name_suffix:test"
    return False, ""


def source_role_analysis(analysis: dict[str, Any] | None, name: str) -> dict[str, Any]:
    next_analysis = dict(analysis or {})
    is_test_source, reason = classify_java_source(name, next_analysis.get("class_name"))
    next_analysis["_is_test_source"] = is_test_source
    next_analysis["_source_role"] = "test" if is_test_source else "production"
    if reason:
        next_analysis["_test_source_reason"] = reason
    else:
        next_analysis.pop("_test_source_reason", None)
    return next_analysis


def file_source_name(file: Any) -> str:
    analysis = getattr(file, "analysis", None) or {}
    return str(analysis.get("_project_relative_path") or getattr(file, "original_name", ""))


def is_uploaded_test_source(file: Any) -> bool:
    analysis = getattr(file, "analysis", None) or {}
    if analysis.get("_is_test_source") is True:
        return True
    name = file_source_name(file)
    return classify_java_source(name, analysis.get("class_name"))[0]


def test_source_reason(file: Any) -> str:
    analysis = getattr(file, "analysis", None) or {}
    if analysis.get("_test_source_reason"):
        return str(analysis["_test_source_reason"])
    name = file_source_name(file)
    return classify_java_source(name, analysis.get("class_name"))[1]
