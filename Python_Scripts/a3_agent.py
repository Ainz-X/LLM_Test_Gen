#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import io
import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI, OpenAIError

import a3_pipeline as pipeline


ROOT_DIR = pipeline.ROOT_DIR
DATA_DIR = ROOT_DIR / "LLM_Test_Gen" / "Data"
PROMPT_TEMPLATE_DIR = DATA_DIR / "Prompts" / "prompt_template"
AGENT_DIR = DATA_DIR / "Agent"
DEFAULT_AGENT_PROMPT = DATA_DIR / "Prompts" / "agent_system_prompt.md"


JsonDict = Dict[str, Any]


@dataclass
class AgentPaths:
    config: Path
    method_csv: Path
    test_csv: Path
    summary_csv: Path
    suite_dir: Path
    agent_prompt: Path
    run_dir: Path


@dataclass
class AgentContext:
    paths: AgentPaths
    planner_model: str
    inner_model: str
    api_key_env: str
    dry_run_tools: bool
    max_observation_chars: int


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: JsonDict
    handler: Callable[[AgentContext, JsonDict], JsonDict]


def json_dumps(value: Any, max_chars: Optional[int] = None) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n...<truncated>"
    return text


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def count_by(rows: List[Dict[str, str]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = row.get(field, "") or "<blank>"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def project_filter(rows: List[Dict[str, str]], project_key: Optional[str]) -> List[Dict[str, str]]:
    if not project_key:
        return rows
    return [row for row in rows if row.get("Project Key") == project_key]


def capture_pipeline_output(func: Callable[[argparse.Namespace], None], namespace: argparse.Namespace) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        func(namespace)
    return buffer.getvalue().strip()


def ok_result(tool: str, data: JsonDict) -> JsonDict:
    return {"ok": True, "tool": tool, **data}


def error_result(tool: str, message: str, **extra: Any) -> JsonDict:
    return {"ok": False, "tool": tool, "error": message, **extra}


def template_path(kind: str) -> Path:
    mapping = {
        "generation": PROMPT_TEMPLATE_DIR / "generation.yaml",
        "feedback": PROMPT_TEMPLATE_DIR / "feedback.yaml",
        "repair": PROMPT_TEMPLATE_DIR / "repair.yaml",
    }
    if kind not in mapping:
        raise ValueError(f"Unknown template kind: {kind}")
    return mapping[kind]


def inspect_workspace(ctx: AgentContext, args: JsonDict) -> JsonDict:
    project_key = args.get("project_key") or None
    method_rows = read_csv_rows(ctx.paths.method_csv)
    test_rows = project_filter(read_csv_rows(ctx.paths.test_csv), project_key)
    summary_paths = [ctx.paths.summary_csv]
    if not ctx.paths.summary_csv.exists():
        summary_paths.extend(
            [
                DATA_DIR / "Coverage_Summary_final_combined.csv",
                DATA_DIR / "Coverage_Summary_optimized_broad.csv",
                DATA_DIR / "Coverage_Summary_balanced_quality.csv",
                DATA_DIR / "Coverage_Summary_baseline.csv",
            ]
        )
    config = pipeline.load_yaml(ctx.paths.config) if ctx.paths.config.exists() else {}

    latest_summary: Dict[str, Dict[str, str]] = {}
    available_summary_files: List[str] = []
    for summary_path in summary_paths:
        if not summary_path.exists():
            continue
        available_summary_files.append(str(summary_path))
        for row in project_filter(read_csv_rows(summary_path), project_key):
            key = row.get("project_key") or row.get("Project Key") or "unknown"
            latest_summary[f"{summary_path.name}:{key}"] = {
                "line_coverage_pct": row.get("line_coverage_pct", ""),
                "branch_coverage_pct": row.get("branch_coverage_pct", ""),
                "pass_rate_classes_pct": row.get("pass_rate_classes_pct", ""),
                "pass_rate_methods_pct": row.get("pass_rate_methods_pct", ""),
                "bug_identified": row.get("bug_identified", ""),
                "included_row_count": row.get("included_row_count", ""),
                "suite_role_filter": row.get("suite_role_filter", ""),
            }

    rows_with_feedback = [
        {
            "project_key": row.get("Project Key", ""),
            "unit": row.get("Generation Unit ID", ""),
            "round": row.get("Round", ""),
            "compile_status": row.get("Compile Status", ""),
            "execution_status": row.get("Execution Status", ""),
            "coverage_feedback": (row.get("Coverage Feedback", "") or "")[:500],
            "failure_type": row.get("Failure Type", ""),
        }
        for row in test_rows
        if row.get("Coverage Feedback") or row.get("Execution Diagnostics") or row.get("Compile Diagnostics")
    ][: args.get("feedback_limit", 5)]

    recommendations: List[str] = []
    if not method_rows:
        recommendations.append("Method_Context.csv is missing or empty; refresh method context before generation.")
    if not test_rows:
        recommendations.append("No Test_Data rows are visible for this scope; expand generation units or inspect project filters.")
    if any(row.get("Generated Code") and not row.get("Compile Status") for row in test_rows):
        recommendations.append("Some generated rows have no compile status; call compile_and_repair.")
    if any(row.get("Compile Status") == "SUCCESS" and not row.get("Execution Status") for row in test_rows):
        recommendations.append("Some compilable rows have no execution result; call evaluate_suite.")
    if any(row.get("Coverage Feedback") for row in test_rows):
        recommendations.append("Coverage feedback exists; call prepare_feedback_round if another refinement round is useful.")

    return ok_result(
        "inspect_workspace",
        {
            "project_filter": project_key or "all",
            "configured_projects": sorted((config.get("projects") or {}).keys()),
            "method_row_count": len(method_rows),
            "test_row_count": len(test_rows),
            "compile_status_counts": count_by(test_rows, "Compile Status"),
            "execution_status_counts": count_by(test_rows, "Execution Status"),
            "suite_role_counts": count_by(test_rows, "Suite Role"),
            "round_counts": count_by(test_rows, "Round"),
            "latest_summary": latest_summary,
            "available_summary_files": available_summary_files,
            "recent_feedback": rows_with_feedback,
            "recommendations": recommendations,
        },
    )


def validate_workspace(ctx: AgentContext, args: JsonDict) -> JsonDict:
    namespace = argparse.Namespace(
        config=ctx.paths.config,
        method_csv=ctx.paths.method_csv,
        test_csv=ctx.paths.test_csv,
        prompt_dir=PROMPT_TEMPLATE_DIR,
        summary_csv=ctx.paths.summary_csv if ctx.paths.summary_csv.exists() else None,
    )
    try:
        output = capture_pipeline_output(pipeline.validate, namespace)
    except SystemExit as exc:
        return error_result("validate_workspace", "validation failed", exit_code=exc.code)
    except Exception as exc:
        return error_result("validate_workspace", str(exc), traceback=traceback.format_exc(limit=5))
    return ok_result("validate_workspace", {"output": output or "validation completed"})


def expand_generation_units(ctx: AgentContext, args: JsonDict) -> JsonDict:
    allow_overwrite = bool(args.get("allow_overwrite", False))
    if ctx.dry_run_tools:
        return ok_result("expand_generation_units", {"dry_run": True, "would_write": str(ctx.paths.test_csv)})
    if ctx.paths.test_csv.exists() and not allow_overwrite:
        return error_result(
            "expand_generation_units",
            "Refusing to overwrite existing Test_Data.csv without allow_overwrite=true.",
            test_csv=str(ctx.paths.test_csv),
        )
    namespace = argparse.Namespace(config=ctx.paths.config, method_csv=ctx.paths.method_csv, output=ctx.paths.test_csv)
    try:
        output = capture_pipeline_output(pipeline.expand_units, namespace)
    except Exception as exc:
        return error_result("expand_generation_units", str(exc), traceback=traceback.format_exc(limit=5))
    return ok_result("expand_generation_units", {"output": output, "test_csv": str(ctx.paths.test_csv)})


def generate_tests(ctx: AgentContext, args: JsonDict) -> JsonDict:
    project_key = args.get("project_key") or None
    fqn_filter = args.get("fqn_filter") or None
    template_kind = args.get("template_kind") or "generation"
    if template_kind not in {"generation", "feedback"}:
        return error_result("generate_tests", "template_kind must be generation or feedback")

    max_rows = args.get("max_rows")
    dry_run = ctx.dry_run_tools or bool(args.get("dry_run", False))
    prompt_dir = DATA_DIR / "Prompts" / "agent_generated"
    namespace = argparse.Namespace(
        config=ctx.paths.config,
        test_csv=ctx.paths.test_csv,
        template=template_path(template_kind),
        prompt_dir=prompt_dir,
        model=args.get("model") or ctx.inner_model,
        api_key_env=ctx.api_key_env,
        project_key=project_key,
        fqn_filter=fqn_filter,
        temperature=float(args.get("temperature", 0.2)),
        max_rows=int(max_rows) if max_rows is not None else None,
        overwrite=bool(args.get("overwrite", False)),
        dry_run=dry_run,
    )
    try:
        output = capture_pipeline_output(pipeline.generate, namespace)
    except SystemExit as exc:
        return error_result("generate_tests", "generation exited", exit_code=exc.code)
    except OpenAIError as exc:
        return error_result("generate_tests", f"OpenAIError: {exc}")
    except Exception as exc:
        return error_result("generate_tests", str(exc), traceback=traceback.format_exc(limit=5))
    return ok_result(
        "generate_tests",
        {
            "output": output,
            "project_key": project_key or "all",
            "fqn_filter": fqn_filter or "",
            "template_kind": template_kind,
            "dry_run": dry_run,
        },
    )


def compile_and_repair(ctx: AgentContext, args: JsonDict) -> JsonDict:
    if ctx.dry_run_tools or bool(args.get("dry_run", False)):
        return ok_result("compile_and_repair", {"dry_run": True, "skipped": "compile/repair can modify test files"})
    namespace = argparse.Namespace(
        config=ctx.paths.config,
        test_csv=ctx.paths.test_csv,
        template=template_path("repair"),
        prompt_dir=DATA_DIR / "Prompts" / "agent_repair",
        model=args.get("model") or ctx.inner_model,
        api_key_env=ctx.api_key_env,
        project_key=args.get("project_key") or None,
        temperature=float(args.get("temperature", 0.0)),
        max_rows=int(args["max_rows"]) if args.get("max_rows") is not None else None,
        max_attempts=int(args.get("max_attempts", 3)),
        overwrite=bool(args.get("overwrite", False)),
        dry_run=False,
    )
    try:
        output = capture_pipeline_output(pipeline.compile_repair, namespace)
    except Exception as exc:
        return error_result("compile_and_repair", str(exc), traceback=traceback.format_exc(limit=5))
    return ok_result("compile_and_repair", {"output": output, "project_key": args.get("project_key") or "all"})


def evaluate_suite(ctx: AgentContext, args: JsonDict) -> JsonDict:
    if ctx.dry_run_tools or bool(args.get("dry_run", False)):
        return ok_result("evaluate_suite", {"dry_run": True, "skipped": "evaluation runs Defects4J"})
    include_roles = args.get("include_roles", "")
    suite_source = args.get("suite_source") or "a3agent"
    summary_csv = Path(args["summary_csv"]) if args.get("summary_csv") else ctx.paths.summary_csv
    if not summary_csv.is_absolute():
        summary_csv = ROOT_DIR / summary_csv
    namespace = argparse.Namespace(
        config=ctx.paths.config,
        test_csv=ctx.paths.test_csv,
        summary_csv=summary_csv,
        suite_dir=ctx.paths.suite_dir,
        suite_source=suite_source,
        include_roles=include_roles,
    )
    try:
        output = capture_pipeline_output(pipeline.evaluate, namespace)
    except Exception as exc:
        return error_result("evaluate_suite", str(exc), traceback=traceback.format_exc(limit=5))
    rows = read_csv_rows(summary_csv)
    return ok_result(
        "evaluate_suite",
        {
            "output": output,
            "summary_csv": str(summary_csv),
            "suite_source": suite_source,
            "include_roles": include_roles,
            "summary_rows": rows,
        },
    )


def prepare_feedback_round(ctx: AgentContext, args: JsonDict) -> JsonDict:
    if ctx.dry_run_tools or bool(args.get("dry_run", False)):
        return ok_result("prepare_feedback_round", {"dry_run": True, "skipped": "feedback preparation appends rows"})
    namespace = argparse.Namespace(
        config=ctx.paths.config,
        test_csv=ctx.paths.test_csv,
        max_round=int(args.get("max_round", 2)),
    )
    try:
        output = capture_pipeline_output(pipeline.prepare_feedback, namespace)
    except Exception as exc:
        return error_result("prepare_feedback_round", str(exc), traceback=traceback.format_exc(limit=5))
    return ok_result("prepare_feedback_round", {"output": output, "max_round": namespace.max_round})


def refresh_method_context(ctx: AgentContext, args: JsonDict) -> JsonDict:
    if ctx.dry_run_tools or bool(args.get("dry_run", False)):
        return ok_result("refresh_method_context", {"dry_run": True, "script": "Java_Scripts/extract_method_context.sh"})
    allow_overwrite = bool(args.get("allow_overwrite", False))
    if ctx.paths.method_csv.exists() and not allow_overwrite:
        return error_result(
            "refresh_method_context",
            "Refusing to overwrite existing Method_Context.csv without allow_overwrite=true.",
            method_csv=str(ctx.paths.method_csv),
        )
    script = ROOT_DIR / "LLM_Test_Gen" / "Java_Scripts" / "extract_method_context.sh"
    command = ["bash", str(script), str(ctx.paths.method_csv)]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=int(args.get("timeout_seconds", 900)),
            check=False,
        )
    except FileNotFoundError:
        return error_result("refresh_method_context", "bash was not found; source env.sh and run the extractor manually.")
    except subprocess.TimeoutExpired:
        return error_result("refresh_method_context", "extractor timed out")
    if completed.returncode != 0:
        return error_result("refresh_method_context", "extractor failed", output=completed.stdout[-4000:])
    return ok_result("refresh_method_context", {"output": completed.stdout[-4000:], "method_csv": str(ctx.paths.method_csv)})


def tool_specs() -> Dict[str, ToolSpec]:
    project_key_schema = {"type": "string", "enum": ["codec", "collections", "compress"]}
    return {
        "inspect_workspace": ToolSpec(
            name="inspect_workspace",
            description="Read CSV/config state and summarize methods, generated rows, compile status, execution status, coverage summaries, and recommended next actions.",
            parameters={
                "type": "object",
                "properties": {
                    "project_key": project_key_schema,
                    "feedback_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "additionalProperties": False,
            },
            handler=inspect_workspace,
        ),
        "validate_workspace": ToolSpec(
            name="validate_workspace",
            description="Run the existing pipeline validator against method context, test data, prompt templates, and the selected summary CSV.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=validate_workspace,
        ),
        "refresh_method_context": ToolSpec(
            name="refresh_method_context",
            description="Run the existing Java extractor script to rebuild Method_Context.csv. This is guarded because it can overwrite extracted context.",
            parameters={
                "type": "object",
                "properties": {
                    "allow_overwrite": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": False},
                    "timeout_seconds": {"type": "integer", "minimum": 60, "maximum": 3600, "default": 900},
                },
                "additionalProperties": False,
            },
            handler=refresh_method_context,
        ),
        "expand_generation_units": ToolSpec(
            name="expand_generation_units",
            description="Expand Method_Context.csv into Test_Data.csv using the existing fuzzing-inspired partitions.",
            parameters={
                "type": "object",
                "properties": {
                    "allow_overwrite": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            handler=expand_generation_units,
        ),
        "generate_tests": ToolSpec(
            name="generate_tests",
            description="Call the existing generator for selected rows. Use template_kind=feedback for feedback rows and generation for ordinary rows.",
            parameters={
                "type": "object",
                "properties": {
                    "project_key": project_key_schema,
                    "fqn_filter": {"type": "string"},
                    "template_kind": {"type": "string", "enum": ["generation", "feedback"], "default": "generation"},
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 200},
                    "temperature": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.2},
                    "overwrite": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": False},
                    "model": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=generate_tests,
        ),
        "compile_and_repair": ToolSpec(
            name="compile_and_repair",
            description="Compile generated tests with Defects4J and run repair prompts for compile failures, capped by max_attempts.",
            parameters={
                "type": "object",
                "properties": {
                    "project_key": project_key_schema,
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 200},
                    "max_attempts": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                    "temperature": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
                    "overwrite": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": False},
                    "model": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=compile_and_repair,
        ),
        "evaluate_suite": ToolSpec(
            name="evaluate_suite",
            description="Evaluate compilable generated tests through Defects4J external suites and write a coverage/pass-rate summary CSV.",
            parameters={
                "type": "object",
                "properties": {
                    "include_roles": {"type": "string", "description": "Comma-separated Suite Role values, e.g. coverage,diagnostic-coverage,bug-evidence."},
                    "suite_source": {"type": "string", "default": "a3agent"},
                    "summary_csv": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            handler=evaluate_suite,
        ),
        "prepare_feedback_round": ToolSpec(
            name="prepare_feedback_round",
            description="Append follow-up generation rows using coverage and execution feedback from previous evaluation.",
            parameters={
                "type": "object",
                "properties": {
                    "max_round": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            handler=prepare_feedback_round,
        ),
    }


def openai_tool_schema(spec: ToolSpec) -> JsonDict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def append_run_log(run_log: Path, event: JsonDict) -> None:
    run_log.parent.mkdir(parents=True, exist_ok=True)
    with run_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def execute_tool(ctx: AgentContext, specs: Dict[str, ToolSpec], name: str, raw_arguments: str) -> JsonDict:
    if name not in specs:
        return error_result(name, "Unknown tool")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return error_result(name, f"Invalid JSON arguments: {exc}")
    try:
        return specs[name].handler(ctx, arguments)
    except Exception as exc:
        return error_result(name, str(exc), traceback=traceback.format_exc(limit=8))


def assistant_message_to_dict(message: Any) -> JsonDict:
    data: JsonDict = {"role": "assistant", "content": message.content or ""}
    if getattr(message, "tool_calls", None):
        data["tool_calls"] = [
            {
                "id": call.id,
                "type": call.type,
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]
    return data


def run_llm_agent(ctx: AgentContext, goal: str, max_steps: int, temperature: float) -> JsonDict:
    api_key = os.getenv(ctx.api_key_env)
    if not api_key:
        raise SystemExit(
            f"Environment variable {ctx.api_key_env} is required for the LLM planner. "
            "Use --planner scripted --dry-run-tools for a local structure check."
        )

    specs = tool_specs()
    system_prompt = ctx.paths.agent_prompt.read_text(encoding="utf-8")
    client = OpenAI(api_key=api_key, timeout=120, max_retries=0)
    run_log = ctx.paths.run_dir / f"agent_run_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.jsonl"
    messages: List[JsonDict] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Goal:\n"
                f"{goal}\n\n"
                "Before taking mutating actions, inspect the workspace. "
                "Use tools until you can explain the result or a blocker."
            ),
        },
    ]

    final_content = ""
    for step in range(1, max_steps + 1):
        append_run_log(run_log, {"event": "request", "step": step, "messages": messages[-4:]})
        response = client.chat.completions.create(
            model=ctx.planner_model,
            messages=messages,
            tools=[openai_tool_schema(spec) for spec in specs.values()],
            tool_choice="auto",
            temperature=temperature,
            timeout=120,
        )
        message = response.choices[0].message
        assistant_msg = assistant_message_to_dict(message)
        messages.append(assistant_msg)
        append_run_log(run_log, {"event": "assistant", "step": step, "message": assistant_msg})

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            final_content = message.content or ""
            break

        for call in tool_calls:
            result = execute_tool(ctx, specs, call.function.name, call.function.arguments)
            content = json_dumps(result, max_chars=ctx.max_observation_chars)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": content,
                }
            )
            append_run_log(
                run_log,
                {
                    "event": "tool_result",
                    "step": step,
                    "tool": call.function.name,
                    "arguments": call.function.arguments,
                    "result": result,
                },
            )
    else:
        final_content = f"Stopped after reaching max_steps={max_steps}."

    return {"final": final_content, "run_log": str(run_log)}


def run_scripted_agent(ctx: AgentContext, goal: str) -> JsonDict:
    specs = tool_specs()
    run_log = ctx.paths.run_dir / f"scripted_agent_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.jsonl"
    plan = [
        ("inspect_workspace", {}),
        ("validate_workspace", {}),
    ]
    observations = []
    for index, (name, arguments) in enumerate(plan, start=1):
        result = execute_tool(ctx, specs, name, json.dumps(arguments))
        observations.append(result)
        append_run_log(
            run_log,
            {
                "event": "scripted_tool_result",
                "step": index,
                "goal": goal,
                "tool": name,
                "arguments": arguments,
                "result": result,
            },
        )
    return {
        "final": "Scripted planner completed inspect_workspace and validate_workspace. Use --planner llm for model-chosen tools.",
        "observations": observations,
        "run_log": str(run_log),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled tool-calling agent wrapper for Assignment 3.")
    parser.add_argument("--goal", required=True, help="Natural-language objective for the agent planner.")
    parser.add_argument("--planner", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--planner-model", default="gpt-4o-mini")
    parser.add_argument("--inner-model", default="gpt-4o-mini")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--config", type=Path, default=DATA_DIR / "targets.yaml")
    parser.add_argument("--method-csv", type=Path, default=DATA_DIR / "Method_Context.csv")
    parser.add_argument("--test-csv", type=Path, default=DATA_DIR / "Test_Data.csv")
    parser.add_argument("--summary-csv", type=Path, default=DATA_DIR / "Coverage_Summary_agent.csv")
    parser.add_argument("--suite-dir", type=Path, default=DATA_DIR / "Generated_Suites")
    parser.add_argument("--agent-prompt", type=Path, default=DEFAULT_AGENT_PROMPT)
    parser.add_argument("--run-dir", type=Path, default=AGENT_DIR / "runs")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-observation-chars", type=int, default=6000)
    parser.add_argument(
        "--dry-run-tools",
        action="store_true",
        help="Let tool calls report intended actions without running heavy or mutating pipeline steps.",
    )
    return parser


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def main() -> None:
    args = build_parser().parse_args()
    paths = AgentPaths(
        config=resolve_path(args.config),
        method_csv=resolve_path(args.method_csv),
        test_csv=resolve_path(args.test_csv),
        summary_csv=resolve_path(args.summary_csv),
        suite_dir=resolve_path(args.suite_dir),
        agent_prompt=resolve_path(args.agent_prompt),
        run_dir=resolve_path(args.run_dir),
    )
    if not paths.agent_prompt.exists():
        raise SystemExit(f"Agent system prompt not found: {paths.agent_prompt}")

    ctx = AgentContext(
        paths=paths,
        planner_model=args.planner_model,
        inner_model=args.inner_model,
        api_key_env=args.api_key_env,
        dry_run_tools=args.dry_run_tools,
        max_observation_chars=args.max_observation_chars,
    )

    if args.planner == "scripted":
        result = run_scripted_agent(ctx, args.goal)
    else:
        result = run_llm_agent(ctx, args.goal, args.max_steps, args.temperature)

    print(json_dumps(result))


if __name__ == "__main__":
    main()
