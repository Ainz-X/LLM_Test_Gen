#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from string import Template
from typing import Dict, Iterable, List, Optional, Tuple

import yaml
from openai import OpenAI, OpenAIError

ROOT_DIR = Path(__file__).resolve().parents[2]
D4J_HOME_ENV = os.getenv("D4J_HOME")
DEFAULT_DEFECTS4J = Path(
    os.getenv(
        "A3_DEFECTS4J",
        str(Path(D4J_HOME_ENV) / "framework" / "bin" / "defects4j") if D4J_HOME_ENV else "defects4j",
    )
)
DEFAULT_JAVA_HOME = os.getenv("A3_BUILD_JAVA_HOME") or os.getenv("JAVA_HOME") or ""

STANDARD_PARTITIONS: Tuple[Tuple[str, str], ...] = (
    (
        "null-empty-boundary",
        "Target null handling, empty inputs, zero-length collections, and tight boundary values when type-compatible.",
    ),
    (
        "nominal-stateful",
        "Target normal behavior with valid setup, representative inputs, and state transitions that should succeed.",
    ),
    (
        "exception-invalid",
        "Target invalid inputs, exceptional flows, and guard behavior without duplicating nominal coverage.",
    ),
)

RESULT_COLUMNS = [
    "Project Key",
    "Project Directory",
    "Target Class",
    "Generation Unit ID",
    "InputPartition",
    "TargetIntent",
    "Round",
    "FeedbackSummary",
    "Model",
    "Prompt Path",
    "Generated Code",
    "Code After Formatting",
    "Saved Path",
    "Compile Status",
    "Compile Diagnostics",
    "Compile Attempts",
    "Runnable Test Code",
    "Execution Status",
    "Execution Diagnostics",
    "Coverage Feedback",
    "Bug Evidence",
    "Suite Role",
    "Include In Final Suite",
    "Failure Type",
    "Failure Root Cause",
]

METHOD_CONTEXT_COLUMNS = [
    "FQN",
    "Signature",
    "Jimple Code Representation",
    "Method Source",
    "Field Context",
    "Constructor/Helper Signatures",
    "Throws/Modifiers",
]

SUMMARY_COLUMNS = [
    "project_key",
    "project_dir",
    "target_class",
    "suite_archive",
    "suite_role_filter",
    "suite_source_count",
    "included_row_count",
    "compile_success_rows",
    "runnable_test_classes",
    "executed_test_classes",
    "passed_test_classes",
    "executed_test_methods",
    "failed_test_methods",
    "passed_test_methods",
    "pass_rate_classes_pct",
    "pass_rate_methods_pct",
    "lines_total",
    "lines_covered",
    "line_coverage_pct",
    "branches_total",
    "branches_covered",
    "branch_coverage_pct",
    "bug_identified",
    "bug_evidence",
    "test_command_exit_code",
    "coverage_command_exit_code",
    "test_output_excerpt",
    "coverage_output_excerpt",
]

TEMPLATE_REQUIRED_MARKERS = {
    "generation": [
        "@persona",
        "@terminology",
        "@instruction",
        "$FQN",
        "$SIGNATURE",
        "$JIMPLE",
        "$METHOD_SOURCE",
        "$FIELD_CONTEXT",
        "$HELPER_SIGNATURES",
        "$THROWS_MODIFIERS",
        "$INPUT_PARTITION",
        "$TARGET_INTENT",
        "$FEEDBACK_SUMMARY",
    ],
    "repair": [
        "@persona",
        "@instruction",
        "$FQN",
        "$SIGNATURE",
        "$METHOD_SOURCE",
        "$COMPILER_DIAGNOSTICS",
        "$PREVIOUS_TEST_CODE",
    ],
    "feedback": [
        "@persona",
        "@instruction",
        "$FQN",
        "$SIGNATURE",
        "$JIMPLE",
        "$METHOD_SOURCE",
        "$FIELD_CONTEXT",
        "$HELPER_SIGNATURES",
        "$THROWS_MODIFIERS",
        "$PREVIOUS_TEST_CODE",
        "$FEEDBACK_SUMMARY",
    ],
}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_rows(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def write_rows(csv_path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def sanitized_token(text: str) -> str:
    text = text.replace("[]", "Array")
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def package_name_for_fqn(fqn: str) -> str:
    class_part = fqn.split("(", 1)[0]
    class_name = class_part.rsplit(".", 1)[0]
    return class_name.rsplit(".", 1)[0]


def class_name_for_fqn(fqn: str) -> str:
    class_part = fqn.split("(", 1)[0]
    return class_part.rsplit(".", 1)[0].rsplit(".", 1)[1]


def method_name_for_fqn(fqn: str) -> str:
    class_part = fqn.split("(", 1)[0]
    return class_part.rsplit(".", 1)[1]


def params_for_fqn(fqn: str) -> List[str]:
    params = fqn.split("(", 1)[1].rstrip(")")
    if not params:
        return []
    return [param.strip() for param in params.split(",") if param.strip()]


def ensure_result_columns(fieldnames: Iterable[str]) -> List[str]:
    ordered = list(fieldnames)
    for column in RESULT_COLUMNS:
        if column not in ordered:
            ordered.append(column)
    return ordered


def percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def infer_project_key(fqn: str, config: dict) -> str:
    for project_key, project_cfg in config["projects"].items():
        class_prefix = project_cfg["class_fqn"] + "."
        if fqn.startswith(class_prefix):
            return project_key
    raise ValueError(f"Unable to map FQN to project: {fqn}")


def generation_unit_id(fqn: str, partition: str, round_id: int) -> str:
    class_name = class_name_for_fqn(fqn)
    method_name = method_name_for_fqn(fqn)
    params = params_for_fqn(fqn)
    param_token = "_".join(sanitized_token(param) for param in params) or "noargs"
    return "_".join(
        [
            sanitized_token(class_name),
            sanitized_token(method_name),
            param_token,
            f"r{round_id}",
            sanitized_token(partition),
        ]
    )


def desired_test_class_name(row: Dict[str, str]) -> str:
    return f"{row['Generation Unit ID']}_Test"


def project_root(row: Dict[str, str]) -> Path:
    return ROOT_DIR / row["Project Directory"]


def managed_test_root(project_cfg: dict, config: dict) -> Path:
    return ROOT_DIR / project_cfg["workspace_dir"] / config["managed_test_root"]


def managed_test_dir_for_row(row: Dict[str, str], config: dict) -> Path:
    project_cfg = config["projects"][row["Project Key"]]
    package_name = package_name_for_fqn(row["FQN"])
    return ROOT_DIR / project_cfg["workspace_dir"] / config["managed_test_root"] / Path(*package_name.split("."))


def desired_test_file_path(row: Dict[str, str], config: dict) -> Path:
    return managed_test_dir_for_row(row, config) / f"{desired_test_class_name(row)}.java"


def clear_managed_dir(project_key: str, config: dict) -> None:
    project_cfg = config["projects"][project_key]
    managed_root = ROOT_DIR / project_cfg["workspace_dir"] / config["managed_test_root"]
    if managed_root.exists():
        shutil.rmtree(managed_root)


def version_id_for_project(project_cfg: dict) -> str:
    return f"{project_cfg['bug_id']}b"


def suite_archive_path(project_cfg: dict, suite_dir: Path, suite_source: str) -> Path:
    archive_name = f"{project_cfg['project_id']}-{version_id_for_project(project_cfg)}-{suite_source}.1.tar.bz2"
    return suite_dir / archive_name


def external_suite_source_root(suite_dir: Path, project_key: str) -> Path:
    return suite_dir / "sources" / project_key


def stage_external_suite(project_rows: List[Dict[str, str]], suite_root: Path) -> int:
    if suite_root.exists():
        shutil.rmtree(suite_root)
    suite_root.mkdir(parents=True, exist_ok=True)

    staged = 0
    for row in project_rows:
        package_name = package_name_for_fqn(row["FQN"])
        target_path = suite_root / Path(*package_name.split(".")) / f"{desired_test_class_name(row)}.java"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(row["Runnable Test Code"], encoding="utf-8")
        staged += 1
    return staged


def create_suite_archive(source_root: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:bz2") as handle:
        for path in sorted(source_root.rglob("*.java")):
            handle.add(path, arcname=str(path.relative_to(source_root)))


def extract_java_snippet(raw_response: str) -> str:
    pattern = re.compile(r"```(?:java)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(raw_response)
    if match:
        return match.group(1).strip()
    return raw_response.strip()


def normalize_package(source: str, package_name: str) -> str:
    source = source.replace("\r\n", "\n").strip()
    package_stmt = f"package {package_name};"
    package_pattern = re.compile(r"^\s*package\s+[\w.]+\s*;\s*", re.MULTILINE)
    if package_pattern.search(source):
        source = package_pattern.sub(package_stmt + "\n\n", source, count=1)
        source = package_pattern.sub("", source)
    else:
        source = package_stmt + "\n\n" + source
    return source.strip() + "\n"


def normalize_public_class(source: str, class_name: str) -> str:
    public_class_pattern = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)")
    private_class_pattern = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
    if public_class_pattern.search(source):
        source = public_class_pattern.sub(f"public class {class_name}", source, count=1)
    elif private_class_pattern.search(source):
        source = private_class_pattern.sub(f"public class {class_name}", source, count=1)
    return source


def format_generated_code(raw_response: str, row: Dict[str, str]) -> str:
    snippet = extract_java_snippet(raw_response)
    package_name = package_name_for_fqn(row["FQN"])
    snippet = normalize_package(snippet, package_name)
    snippet = normalize_public_class(snippet, desired_test_class_name(row))
    return snippet.strip() + "\n"


def build_messages(template: dict, user_input: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": template["system"]},
        {"role": "user", "content": template["task"]},
        {"role": "user", "content": user_input},
    ]


def save_prompt(prompt_dir: Path, row: Dict[str, str], messages: List[Dict[str, str]], phase: str) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    prompt_path = prompt_dir / f"{phase}_{row['Generation Unit ID']}_{timestamp}.md"
    with prompt_path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(f"## {message['role'].upper()}\n\n")
            handle.write(message["content"].rstrip())
            handle.write("\n\n")
    return prompt_path


def call_model(client: OpenAI, model: str, messages: List[Dict[str, str]], temperature: float) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=120,
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        return "".join(
            fragment["text"]
            for fragment in content
            if isinstance(fragment, dict) and fragment.get("type") == "text"
        )
    return content or ""


def defects4j_env() -> Dict[str, str]:
    env = os.environ.copy()
    if DEFAULT_JAVA_HOME:
        env["JAVA_HOME"] = DEFAULT_JAVA_HOME
        env["A3_BUILD_JAVA_HOME"] = DEFAULT_JAVA_HOME
    shim_dir = ROOT_DIR / "LLM_Test_Gen" / "Java_Scripts" / "java_shims"
    path_parts = [str(shim_dir)]
    if DEFAULT_JAVA_HOME:
        path_parts.append(str(Path(DEFAULT_JAVA_HOME) / "bin"))
    path_parts.append(env.get("PATH", ""))
    env["PATH"] = ":".join(path_parts)
    return env


def run_command(command: List[str], cwd: Path) -> Tuple[int, str]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=defects4j_env(),
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode, combined.strip()


def expand_units(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    method_rows = load_rows(args.method_csv)
    expanded: List[Dict[str, str]] = []

    for method_row in method_rows:
        project_key = infer_project_key(method_row["FQN"], config)
        project_cfg = config["projects"][project_key]
        for partition, base_intent in STANDARD_PARTITIONS:
            expanded.append(
                build_generation_row(
                    method_row=method_row,
                    project_key=project_key,
                    project_cfg=project_cfg,
                    partition=partition,
                    target_intent=base_intent,
                    round_id=0,
                    feedback_summary="",
                )
            )
        if method_row["FQN"] == project_cfg["focal_bug_method_fqn"]:
            expanded.append(
                build_generation_row(
                    method_row=method_row,
                    project_key=project_key,
                    project_cfg=project_cfg,
                    partition="bug-targeted",
                    target_intent=project_cfg["bug_target_intent"],
                    round_id=0,
                    feedback_summary="",
                )
            )

    fieldnames = ensure_result_columns(method_rows[0].keys() if method_rows else [])
    write_rows(args.output, expanded, fieldnames)
    print(f"Wrote {len(expanded)} generation units to {args.output}")


def build_generation_row(
    method_row: Dict[str, str],
    project_key: str,
    project_cfg: dict,
    partition: str,
    target_intent: str,
    round_id: int,
    feedback_summary: str,
) -> Dict[str, str]:
    row = dict(method_row)
    row["Project Key"] = project_key
    row["Project Directory"] = project_cfg["workspace_dir"]
    row["Target Class"] = project_cfg["class_fqn"]
    row["Generation Unit ID"] = generation_unit_id(method_row["FQN"], partition, round_id)
    row["InputPartition"] = partition
    row["TargetIntent"] = target_intent
    row["Round"] = str(round_id)
    row["FeedbackSummary"] = feedback_summary
    for column in RESULT_COLUMNS:
        row.setdefault(column, "")
    return row


def render_template_payload(template: dict, row: Dict[str, str], previous_code: str = "", compiler_diagnostics: str = "") -> str:
    payload = {
        "FQN": row.get("FQN", ""),
        "SIGNATURE": row.get("Signature", ""),
        "JIMPLE": row.get("Jimple Code Representation", ""),
        "METHOD_SOURCE": row.get("Method Source", ""),
        "FIELD_CONTEXT": row.get("Field Context", ""),
        "HELPER_SIGNATURES": row.get("Constructor/Helper Signatures", ""),
        "THROWS_MODIFIERS": row.get("Throws/Modifiers", ""),
        "INPUT_PARTITION": row.get("InputPartition", ""),
        "TARGET_INTENT": row.get("TargetIntent", ""),
        "FEEDBACK_SUMMARY": row.get("FeedbackSummary", ""),
        "PREVIOUS_TEST_CODE": previous_code,
        "COMPILER_DIAGNOSTICS": compiler_diagnostics,
    }
    return Template(template["user_input_template"]).safe_substitute(payload)


def generate(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    template = load_yaml(args.template)
    rows = load_rows(args.test_csv)
    fieldnames = ensure_result_columns(rows[0].keys() if rows else [])

    client = None
    if not args.dry_run:
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            raise SystemExit(f"Environment variable {args.api_key_env} is required for API calls.")
        client = OpenAI(api_key=api_key, timeout=120, max_retries=0)

    processed = 0
    for row in rows:
        if args.project_key and row["Project Key"] != args.project_key:
            continue
        if args.fqn_filter and args.fqn_filter not in row["FQN"]:
            continue
        if not args.overwrite and row.get("Generated Code"):
            continue
        if args.max_rows and processed >= args.max_rows:
            break

        payload = render_template_payload(template, row, previous_code=row.get("Runnable Test Code", ""))
        messages = build_messages(template, payload)
        prompt_path = save_prompt(args.prompt_dir, row, messages, "generation")
        row["Prompt Path"] = str(prompt_path)
        row["Model"] = args.model

        if args.dry_run:
            row["Generated Code"] = "DRY RUN"
            row["Code After Formatting"] = ""
            row["Saved Path"] = ""
            processed += 1
            continue

        raw_response = call_model(client, args.model, messages, args.temperature)
        formatted = format_generated_code(raw_response, row)
        saved_path = desired_test_file_path(row, config)
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_text(formatted, encoding="utf-8")

        row["Generated Code"] = raw_response
        row["Code After Formatting"] = formatted
        row["Saved Path"] = str(saved_path.relative_to(project_root(row)))
        processed += 1
        write_rows(args.test_csv, rows, fieldnames)

    write_rows(args.test_csv, rows, fieldnames)
    print(f"Updated {args.test_csv} with {processed} generated rows")


def compile_repair(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    template = load_yaml(args.template)
    rows = load_rows(args.test_csv)
    fieldnames = ensure_result_columns(rows[0].keys() if rows else [])

    client = None
    if not args.dry_run:
        api_key = os.getenv(args.api_key_env)
        if api_key:
            client = OpenAI(api_key=api_key, timeout=120, max_retries=0)

    per_project_cleared: Dict[str, bool] = {}
    processed = 0
    for row in rows:
        if args.project_key and row["Project Key"] != args.project_key:
            continue
        if not row.get("Code After Formatting"):
            continue
        if row.get("Compile Status") in {"SUCCESS", "FAILED"} and not args.overwrite:
            continue
        if args.max_rows and processed >= args.max_rows:
            break

        project_key = row["Project Key"]
        project_dir = project_root(row)
        if not per_project_cleared.get(project_key):
            clear_managed_dir(project_key, config)
            per_project_cleared[project_key] = True

        candidate = row["Code After Formatting"]
        compile_attempts = 0
        last_diagnostics = ""
        success = False

        while compile_attempts < args.max_attempts:
            compile_attempts += 1
            test_file = desired_test_file_path(row, config)
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(candidate, encoding="utf-8")

            code, diagnostics = run_command([str(DEFAULT_DEFECTS4J), "compile", "-w", str(project_dir)], ROOT_DIR)
            last_diagnostics = diagnostics
            if code == 0:
                success = True
                row["Runnable Test Code"] = candidate
                row["Compile Status"] = "SUCCESS"
                row["Saved Path"] = str(test_file.relative_to(project_dir))
                break

            if client is None:
                break

            payload = render_template_payload(template, row, previous_code=candidate, compiler_diagnostics=diagnostics)
            messages = build_messages(template, payload)
            prompt_path = save_prompt(args.prompt_dir, row, messages, "repair")
            row["Prompt Path"] = str(prompt_path)
            row["Model"] = row.get("Model") or args.model
            try:
                raw_response = call_model(client, args.model, messages, args.temperature)
            except OpenAIError as exc:
                last_diagnostics = f"{diagnostics}\n\nOpenAIError: {exc}"
                break
            candidate = format_generated_code(raw_response, row)
            row["Generated Code"] = raw_response
            row["Code After Formatting"] = candidate

        row["Compile Attempts"] = str(compile_attempts)
        row["Compile Diagnostics"] = last_diagnostics
        if not success:
            row["Compile Status"] = "FAILED"
            failed_path = desired_test_file_path(row, config)
            if failed_path.exists():
                failed_path.unlink()

        processed += 1
        write_rows(args.test_csv, rows, fieldnames)

    write_rows(args.test_csv, rows, fieldnames)
    print(f"Processed {processed} compile/repair rows")


def parse_all_tests(path: Path) -> Dict[str, List[str]]:
    results: Dict[str, List[str]] = {}
    if not path.exists():
        return results
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or "(" not in line or not line.endswith(")"):
            continue
        method_name, class_name = line[:-1].split("(", 1)
        results.setdefault(class_name, []).append(method_name)
    return results


def parse_failing_tests(path: Path) -> Dict[str, str]:
    blocks: Dict[str, str] = {}
    if not path.exists():
        return blocks
    current_key: Optional[str] = None
    lines: List[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw_line.startswith("--- "):
            if current_key is not None:
                blocks[current_key] = "\n".join(lines).strip()
            current_key = raw_line[4:].strip()
            lines = []
        else:
            lines.append(raw_line)
    if current_key is not None:
        blocks[current_key] = "\n".join(lines).strip()
    return blocks


def parse_coverage_summary(coverage_xml: Path, target_class: str) -> Dict[str, object]:
    tree = ET.parse(coverage_xml)
    root = tree.getroot()
    target_filename = target_class.replace(".", "/") + ".java"
    class_elements = [
        candidate
        for candidate in root.findall(".//class")
        if candidate.attrib.get("filename") == target_filename
    ]
    if not class_elements:
        class_elements = [
            candidate
            for candidate in root.findall(".//class")
            if candidate.attrib.get("name") == target_class
        ]
    if not class_elements:
        return {
            "lines_total": 0,
            "lines_covered": 0,
            "branches_total": 0,
            "branches_covered": 0,
            "uncovered_lines": [],
            "partial_branch_lines": [],
        }

    lines_total = 0
    lines_covered = 0
    branches_total = 0
    branches_covered = 0
    uncovered_lines: List[int] = []
    partial_branch_lines: List[int] = []

    for class_element in class_elements:
        class_lines = class_element.findall("./lines/line")
        if not class_lines:
            class_lines = class_element.findall(".//line")

        for line in class_lines:
            number = int(line.attrib["number"])
            hits = int(line.attrib.get("hits", "0"))
            lines_total += 1
            if hits > 0:
                lines_covered += 1
            else:
                uncovered_lines.append(number)
            if line.attrib.get("branch") == "true":
                condition = line.attrib.get("condition-coverage", "")
                match = re.search(r"\((\d+)/(\d+)\)", condition)
                if match:
                    covered, total = int(match.group(1)), int(match.group(2))
                    branches_covered += covered
                    branches_total += total
                    if covered < total:
                        partial_branch_lines.append(number)

    return {
        "lines_total": lines_total,
        "lines_covered": lines_covered,
        "branches_total": branches_total,
        "branches_covered": branches_covered,
        "uncovered_lines": sorted(set(uncovered_lines)),
        "partial_branch_lines": sorted(set(partial_branch_lines)),
    }


def summarize_coverage_feedback(summary: Dict[str, object]) -> str:
    uncovered = ", ".join(str(value) for value in summary["uncovered_lines"][:20]) or "none"
    partial = ", ".join(str(value) for value in summary["partial_branch_lines"][:20]) or "none"
    return f"Uncovered lines: {uncovered}. Partial branch lines: {partial}."


def source_file_for_target_class(project_cfg: dict) -> Path:
    relative_class_path = Path(*project_cfg["class_fqn"].split(".")).with_suffix(".java")
    return ROOT_DIR / project_cfg["workspace_dir"] / "src" / "main" / "java" / relative_class_path


def feedback_source_excerpt(project_cfg: dict, feedback_text: str, context_radius: int = 3, max_lines: int = 40) -> str:
    numbers = sorted({int(value) for value in re.findall(r"\b\d+\b", feedback_text or "")})
    if not numbers:
        return ""
    source_path = source_file_for_target_class(project_cfg)
    if not source_path.exists():
        return ""
    source_lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    selected = set()
    for number in numbers[:20]:
        start = max(1, number - context_radius)
        end = min(len(source_lines), number + context_radius)
        selected.update(range(start, end + 1))
    rendered = [f"{line_no}: {source_lines[line_no - 1]}" for line_no in sorted(selected)[:max_lines]]
    return "Target source excerpt around uncovered/partial lines:\n" + "\n".join(rendered)


def parse_role_filter(raw_roles: str) -> set[str]:
    return {role.strip() for role in raw_roles.split(",") if role.strip()}


def inferred_suite_role(row: Dict[str, str]) -> str:
    if row.get("Suite Role"):
        return row["Suite Role"].strip()
    if row.get("InputPartition") == "bug-targeted":
        return "bug-evidence"
    return "generated"


def row_selected_for_suite(row: Dict[str, str], allowed_roles: set[str]) -> bool:
    include_flag = row.get("Include In Final Suite", "").strip().lower()
    if include_flag in {"no", "false", "0", "discard", "discarded"}:
        return False
    role = inferred_suite_role(row)
    if allowed_roles and role not in allowed_roles:
        return False
    return True


def classify_failure(row: Dict[str, str], diagnostics: str) -> Tuple[str, str]:
    text = (diagnostics or "").strip()
    lowered = text.lower()
    if row.get("Compile Status") == "FAILED":
        return "compile", textwrap.shorten(text, width=300, placeholder="...")
    if row.get("InputPartition") == "bug-targeted" and row.get("Execution Status") == "FAIL":
        return "bug-triggered", textwrap.shorten(text, width=300, placeholder="...")
    if "classnotfoundexception" in lowered:
        return "suite-configuration", textwrap.shorten(text, width=300, placeholder="...")
    if "assertionfailederror" in lowered or "assertionerror" in lowered:
        return "oracle", textwrap.shorten(text, width=300, placeholder="...")
    if "exception" in lowered or "error" in lowered:
        return "runtime", textwrap.shorten(text, width=300, placeholder="...")
    if row.get("Execution Status") == "NOT_RUN":
        return "not-run", textwrap.shorten(text, width=300, placeholder="...")
    return "", ""


def write_summary_rows(summary_path: Path, rows: List[Dict[str, object]]) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SUMMARY_COLUMNS})


def evaluate(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    rows = load_rows(args.test_csv)
    fieldnames = ensure_result_columns(rows[0].keys() if rows else [])
    allowed_roles = parse_role_filter(args.include_roles or "")

    summary_rows: List[Dict[str, object]] = []
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["Project Key"], []).append(row)

    for project_key, project_cfg in config["projects"].items():
        project_rows = grouped.get(project_key, [])
        project_cfg = config["projects"][project_key]
        for row in project_rows:
            if row.get("Compile Status") == "FAILED":
                row["Failure Type"], row["Failure Root Cause"] = classify_failure(row, row.get("Compile Diagnostics", ""))

        runnable_rows = [
            row
            for row in project_rows
            if row.get("Compile Status") == "SUCCESS"
            and row.get("Runnable Test Code")
            and row_selected_for_suite(row, allowed_roles)
        ]
        project_dir = ROOT_DIR / project_cfg["workspace_dir"]
        all_tests: Dict[str, List[str]] = {}
        failing_blocks: Dict[str, str] = {}
        coverage_summary = {
            "lines_total": 0,
            "lines_covered": 0,
            "branches_total": 0,
            "branches_covered": 0,
            "uncovered_lines": [],
            "partial_branch_lines": [],
        }
        coverage_feedback = summarize_coverage_feedback(coverage_summary)
        suite_archive = ""
        staged_count = 0
        test_code = 0
        coverage_code = 0
        test_output = ""
        coverage_output = ""

        if runnable_rows:
            suite_root = external_suite_source_root(args.suite_dir, project_key)
            staged_count = stage_external_suite(runnable_rows, suite_root)
            archive_path = suite_archive_path(project_cfg, args.suite_dir, args.suite_source)
            create_suite_archive(suite_root, archive_path)
            suite_archive = str(archive_path)

            for stale_name in ("all_tests", "failing_tests", "coverage.xml"):
                stale_path = project_dir / stale_name
                if stale_path.exists():
                    stale_path.unlink()

            test_code, test_output = run_command(
                [
                    str(DEFAULT_DEFECTS4J),
                    "test",
                    "-w",
                    str(project_dir),
                    "-s",
                    str(archive_path),
                ],
                ROOT_DIR,
            )
            coverage_code, coverage_output = run_command(
                [
                    str(DEFAULT_DEFECTS4J),
                    "coverage",
                    "-w",
                    str(project_dir),
                    "-s",
                    str(archive_path),
                    "-i",
                    project_cfg["instrument_file"],
                ],
                ROOT_DIR,
            )

            if test_code == 0:
                all_tests = parse_all_tests(project_dir / "all_tests")
                failing_blocks = parse_failing_tests(project_dir / "failing_tests")
            if coverage_code == 0 and (project_dir / "coverage.xml").exists():
                coverage_summary = parse_coverage_summary(project_dir / "coverage.xml", project_cfg["class_fqn"])
            coverage_feedback = summarize_coverage_feedback(coverage_summary)

        for row in runnable_rows:
            test_class = f"{package_name_for_fqn(row['FQN'])}.{desired_test_class_name(row)}"
            executed_methods = all_tests.get(test_class, [])
            failing_entries = {
                key: value
                for key, value in failing_blocks.items()
                if key.startswith(test_class + "::")
            }
            row["Coverage Feedback"] = coverage_feedback
            if test_code != 0 and not executed_methods and not failing_entries:
                row["Execution Status"] = "SUITE_RUN_FAILED"
                row["Execution Diagnostics"] = test_output.strip()
            elif failing_entries:
                row["Execution Status"] = "FAIL"
                diagnostics = []
                for key, value in failing_entries.items():
                    diagnostics.append(f"{key}\n{value}".strip())
                row["Execution Diagnostics"] = "\n\n".join(diagnostics).strip()
            elif executed_methods:
                row["Execution Status"] = "PASS"
                row["Execution Diagnostics"] = test_output.strip()
            else:
                row["Execution Status"] = "NOT_RUN"
                row["Execution Diagnostics"] = test_output.strip()

            if row["InputPartition"] == "bug-targeted" and failing_entries:
                first_key = next(iter(failing_entries))
                row["Bug Evidence"] = textwrap.shorten(
                    f"{first_key} | {project_cfg['expected_bug_signal']}",
                    width=400,
                    placeholder="...",
                )
            if row["Execution Status"] == "PASS":
                row["Failure Type"] = ""
                row["Failure Root Cause"] = ""
            else:
                row["Failure Type"], row["Failure Root Cause"] = classify_failure(row, row.get("Execution Diagnostics", ""))

        compile_success_rows = sum(1 for row in project_rows if row.get("Compile Status") == "SUCCESS")
        executed_test_classes = sum(1 for row in runnable_rows if row.get("Execution Status") in {"PASS", "FAIL"})
        passed_test_classes = sum(1 for row in runnable_rows if row.get("Execution Status") == "PASS")
        executed_test_methods = sum(len(methods) for methods in all_tests.values())
        failed_test_methods = sum(1 for key in failing_blocks if "::" in key)
        passed_test_methods = max(executed_test_methods - failed_test_methods, 0)
        bug_evidence = " || ".join(
            row["Bug Evidence"]
            for row in runnable_rows
            if row.get("Bug Evidence")
        )
        bug_identified = any(
            row.get("InputPartition") == "bug-targeted" and row.get("Execution Status") == "FAIL"
            for row in runnable_rows
        )

        summary_rows.append(
            {
                "project_key": project_key,
                "project_dir": project_cfg["workspace_dir"],
                "target_class": project_cfg["class_fqn"],
                "suite_archive": suite_archive,
                "suite_role_filter": ",".join(sorted(allowed_roles)) if allowed_roles else "default",
                "suite_source_count": staged_count,
                "included_row_count": len(runnable_rows),
                "compile_success_rows": compile_success_rows,
                "runnable_test_classes": len(runnable_rows),
                "executed_test_classes": executed_test_classes,
                "passed_test_classes": passed_test_classes,
                "executed_test_methods": executed_test_methods,
                "failed_test_methods": failed_test_methods,
                "passed_test_methods": passed_test_methods,
                "pass_rate_classes_pct": percent(passed_test_classes, executed_test_classes),
                "pass_rate_methods_pct": percent(passed_test_methods, executed_test_methods),
                "line_coverage_pct": percent(coverage_summary["lines_covered"], coverage_summary["lines_total"]),
                "branch_coverage_pct": percent(coverage_summary["branches_covered"], coverage_summary["branches_total"]),
                "bug_identified": "yes" if bug_identified else "no",
                "bug_evidence": bug_evidence,
                "test_command_exit_code": test_code,
                "coverage_command_exit_code": coverage_code,
                "test_output_excerpt": test_output[:4000],
                "coverage_output_excerpt": coverage_output[:4000],
                **coverage_summary,
            }
        )

    write_rows(args.test_csv, rows, fieldnames)
    if summary_rows:
        write_summary_rows(args.summary_csv, summary_rows)
        print(f"Wrote coverage summary to {args.summary_csv}")


def validate_test_source(source: str, expected_class_name: str) -> List[str]:
    issues: List[str] = []
    if "```" in source:
        issues.append("formatted test source still contains markdown fences")
    public_classes = re.findall(r"\bpublic\s+class\s+([A-Za-z_]\w*)", source)
    if len(public_classes) != 1:
        issues.append(f"expected exactly one public class, found {len(public_classes)}")
    elif public_classes[0] != expected_class_name:
        issues.append(f"public class name {public_classes[0]} does not match expected {expected_class_name}")
    return issues


def validate(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    errors: List[str] = []

    method_rows = load_rows(args.method_csv)
    method_header = list(method_rows[0].keys()) if method_rows else []
    missing_method_columns = [column for column in METHOD_CONTEXT_COLUMNS if column not in method_header]
    if missing_method_columns:
        errors.append(f"Method_Context.csv is missing columns: {', '.join(missing_method_columns)}")

    allowed_classes = {project_cfg["class_fqn"] for project_cfg in config["projects"].values()}
    seen_fqns = set()
    for row in method_rows:
        fqn = row.get("FQN", "")
        if fqn in seen_fqns:
            errors.append(f"duplicate focal method in Method_Context.csv: {fqn}")
        seen_fqns.add(fqn)

        class_name = fqn.split("(", 1)[0].rsplit(".", 1)[0] if fqn else ""
        if class_name not in allowed_classes:
            errors.append(f"unexpected target class in Method_Context.csv: {fqn}")
        for column in METHOD_CONTEXT_COLUMNS:
            if not row.get(column, "").strip():
                errors.append(f"blank value for {column} in Method_Context.csv row {fqn}")

    for template_name, markers in TEMPLATE_REQUIRED_MARKERS.items():
        template_path = args.prompt_dir / f"{template_name}.yaml"
        if not template_path.exists():
            errors.append(f"missing prompt template: {template_path}")
            continue
        content = template_path.read_text(encoding="utf-8")
        lowered = content.lower()
        if "example" in lowered or "sample" in lowered:
            errors.append(f"prompt template should be zero-shot but contains example/sample language: {template_path}")
        for marker in markers:
            if marker not in content:
                errors.append(f"prompt template {template_path} is missing marker {marker}")

    if args.test_csv.exists():
        test_rows = load_rows(args.test_csv)
        seen_units = set()
        round_zero_units: Dict[str, List[Dict[str, str]]] = {}
        for row in test_rows:
            fqn = row.get("FQN", "")
            project_key = row.get("Project Key", "")
            if project_key not in config["projects"]:
                errors.append(f"unknown project key in Test_Data.csv: {project_key}")
                continue

            expected_project = infer_project_key(fqn, config)
            if expected_project != project_key:
                errors.append(f"project mismatch for {fqn}: expected {expected_project}, found {project_key}")
            expected_class = config["projects"][project_key]["class_fqn"]
            if row.get("Target Class") != expected_class:
                errors.append(f"target class mismatch for {fqn}: expected {expected_class}, found {row.get('Target Class')}")

            try:
                round_id = int(row.get("Round") or 0)
            except ValueError:
                round_id = -1
                errors.append(f"non-integer round in Test_Data.csv for {fqn}: {row.get('Round')}")

            expected_unit = generation_unit_id(fqn, row.get("InputPartition", ""), round_id if round_id >= 0 else 0)
            if row.get("Generation Unit ID") != expected_unit:
                errors.append(f"generation unit id mismatch for {fqn}: expected {expected_unit}, found {row.get('Generation Unit ID')}")
            if row.get("Generation Unit ID") in seen_units:
                errors.append(f"duplicate Generation Unit ID: {row.get('Generation Unit ID')}")
            seen_units.add(row.get("Generation Unit ID"))

            if round_id == 0:
                round_zero_units.setdefault(fqn, []).append(row)

            for column in ("Code After Formatting", "Runnable Test Code"):
                if row.get(column):
                    for issue in validate_test_source(row[column], desired_test_class_name(row)):
                        errors.append(f"{column} validation failed for {row['Generation Unit ID']}: {issue}")

            if row.get("Compile Status") == "SUCCESS" and not row.get("Runnable Test Code"):
                errors.append(f"Compile Status is SUCCESS but Runnable Test Code is blank for {row['Generation Unit ID']}")
            if row.get("Execution Status") == "FAIL" and row.get("InputPartition") == "bug-targeted" and not row.get("Bug Evidence"):
                errors.append(f"bug-targeted failing row is missing Bug Evidence for {row['Generation Unit ID']}")

        for method_row in method_rows:
            fqn = method_row["FQN"]
            project_key = infer_project_key(fqn, config)
            project_cfg = config["projects"][project_key]
            initial_rows = round_zero_units.get(fqn, [])
            expected_partitions = {partition for partition, _ in STANDARD_PARTITIONS}
            if fqn == project_cfg["focal_bug_method_fqn"]:
                expected_partitions.add("bug-targeted")
            actual_partitions = {row.get("InputPartition", "") for row in initial_rows}
            if actual_partitions != expected_partitions:
                errors.append(
                    f"round-0 partitions mismatch for {fqn}: expected {sorted(expected_partitions)}, found {sorted(actual_partitions)}"
                )

    if args.summary_csv and args.summary_csv.exists():
        summary_rows = load_rows(args.summary_csv)
        seen_projects = set()
        for row in summary_rows:
            project_key = row.get("project_key", "")
            seen_projects.add(project_key)
            if project_key not in config["projects"]:
                errors.append(f"unexpected project key in summary csv: {project_key}")
                continue
            try:
                lines_total = int(float(row.get("lines_total", "0")))
                lines_covered = int(float(row.get("lines_covered", "0")))
                branches_total = int(float(row.get("branches_total", "0")))
                branches_covered = int(float(row.get("branches_covered", "0")))
            except ValueError:
                errors.append(f"non-numeric coverage totals in summary for {project_key}")
                continue
            if lines_covered > lines_total:
                errors.append(f"lines_covered exceeds lines_total in summary for {project_key}")
            if branches_covered > branches_total:
                errors.append(f"branches_covered exceeds branches_total in summary for {project_key}")
        expected_projects = set(config["projects"].keys())
        if seen_projects != expected_projects:
            errors.append(f"summary csv project set mismatch: expected {sorted(expected_projects)}, found {sorted(seen_projects)}")

    if errors:
        print("Validation failed:")
        for issue in errors:
            print(f"- {issue}")
        raise SystemExit(1)

    print(f"Validation passed for {len(method_rows)} method rows.")


def prepare_feedback(args: argparse.Namespace) -> None:
    config = load_yaml(args.config)
    rows = load_rows(args.test_csv)
    fieldnames = ensure_result_columns(rows[0].keys() if rows else [])

    grouped: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["FQN"], []).append(row)

    appended = 0
    for fqn, method_rows in grouped.items():
        existing_rounds = [int(row.get("Round") or 0) for row in method_rows]
        max_round = max(existing_rounds) if existing_rounds else 0
        if max_round >= args.max_round:
            continue

        best_row = next(
            (
                row
                for row in method_rows
                if row.get("Compile Status") == "SUCCESS"
                and (row.get("Coverage Feedback") or row.get("Execution Diagnostics"))
            ),
            None,
        )
        if best_row is None:
            continue

        new_round = max_round + 1
        project_cfg = config["projects"][best_row["Project Key"]]
        combined_feedback = " ".join(
            value for value in [best_row.get("Coverage Feedback", ""), best_row.get("Execution Diagnostics", "")] if value
        ).strip()
        source_excerpt = feedback_source_excerpt(config["projects"][best_row["Project Key"]], combined_feedback)
        if source_excerpt:
            combined_feedback = f"{combined_feedback}\n\n{source_excerpt}"
        new_row = build_generation_row(
            method_row=best_row,
            project_key=best_row["Project Key"],
            project_cfg=project_cfg,
            partition=best_row["InputPartition"],
            target_intent=best_row["TargetIntent"],
            round_id=new_round,
            feedback_summary=combined_feedback,
        )
        rows.append(new_row)
        appended += 1

    write_rows(args.test_csv, rows, fieldnames)
    print(f"Appended {appended} feedback rows")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assignment 3 orchestration pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    expand_parser = subparsers.add_parser("expand-units", help="Expand Method_Context.csv into Test_Data.csv")
    expand_parser.add_argument("--config", required=True, type=Path)
    expand_parser.add_argument("--method-csv", required=True, type=Path)
    expand_parser.add_argument("--output", required=True, type=Path)
    expand_parser.set_defaults(func=expand_units)

    generate_parser = subparsers.add_parser("generate", help="Render prompts and optionally call GPT-4o-mini")
    generate_parser.add_argument("--config", required=True, type=Path)
    generate_parser.add_argument("--test-csv", required=True, type=Path)
    generate_parser.add_argument("--template", required=True, type=Path)
    generate_parser.add_argument("--prompt-dir", required=True, type=Path)
    generate_parser.add_argument("--model", default="gpt-4o-mini")
    generate_parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    generate_parser.add_argument("--project-key")
    generate_parser.add_argument("--fqn-filter")
    generate_parser.add_argument("--temperature", type=float, default=0.2)
    generate_parser.add_argument("--max-rows", type=int)
    generate_parser.add_argument("--overwrite", action="store_true")
    generate_parser.add_argument("--dry-run", action="store_true")
    generate_parser.set_defaults(func=generate)

    repair_parser = subparsers.add_parser("compile-repair", help="Compile generated tests and run repair prompts")
    repair_parser.add_argument("--config", required=True, type=Path)
    repair_parser.add_argument("--test-csv", required=True, type=Path)
    repair_parser.add_argument("--template", required=True, type=Path)
    repair_parser.add_argument("--prompt-dir", required=True, type=Path)
    repair_parser.add_argument("--model", default="gpt-4o-mini")
    repair_parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    repair_parser.add_argument("--project-key")
    repair_parser.add_argument("--temperature", type=float, default=0.0)
    repair_parser.add_argument("--max-rows", type=int)
    repair_parser.add_argument("--max-attempts", type=int, default=3)
    repair_parser.add_argument("--overwrite", action="store_true")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.set_defaults(func=compile_repair)

    evaluate_parser = subparsers.add_parser("evaluate", help="Run tests, parse coverage, and update CSV diagnostics")
    evaluate_parser.add_argument("--config", required=True, type=Path)
    evaluate_parser.add_argument("--test-csv", required=True, type=Path)
    evaluate_parser.add_argument("--summary-csv", required=True, type=Path)
    evaluate_parser.add_argument("--suite-dir", type=Path, default=ROOT_DIR / "LLM_Test_Gen" / "Data" / "Generated_Suites")
    evaluate_parser.add_argument("--suite-source", default="a3generated")
    evaluate_parser.add_argument(
        "--include-roles",
        default="",
        help="Comma-separated Suite Role values to include. Empty preserves legacy behavior except rows explicitly excluded.",
    )
    evaluate_parser.set_defaults(func=evaluate)

    feedback_parser = subparsers.add_parser("prepare-feedback", help="Append follow-up generation rows from feedback")
    feedback_parser.add_argument("--config", required=True, type=Path)
    feedback_parser.add_argument("--test-csv", required=True, type=Path)
    feedback_parser.add_argument("--max-round", type=int, default=2)
    feedback_parser.set_defaults(func=prepare_feedback)

    validate_parser = subparsers.add_parser("validate", help="Validate extracted context, prompts, CSV rows, and summaries")
    validate_parser.add_argument("--config", required=True, type=Path)
    validate_parser.add_argument("--method-csv", required=True, type=Path)
    validate_parser.add_argument("--test-csv", required=True, type=Path)
    validate_parser.add_argument("--prompt-dir", required=True, type=Path)
    validate_parser.add_argument("--summary-csv", type=Path)
    validate_parser.set_defaults(func=validate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
