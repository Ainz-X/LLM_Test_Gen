#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import html
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import traceback
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

try:
    from openai import OpenAI, OpenAIError
except Exception:  # pragma: no cover - keeps local no-dependency mode usable.
    OpenAI = None  # type: ignore[assignment]
    OpenAIError = Exception  # type: ignore[assignment]


ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_SCRIPTS = ROOT_DIR / "LLM_Test_Gen" / "Python_Scripts"
if str(PYTHON_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_SCRIPTS))

import a3_agent  # noqa: E402
import a3_pipeline as pipeline  # noqa: E402


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
DATA_DIR = ROOT_DIR / "LLM_Test_Gen" / "Data"
WEB_DATA_DIR = DATA_DIR / "WebAgent"
UPLOAD_DIR = WEB_DATA_DIR / "uploads"
GENERATED_DIR = WEB_DATA_DIR / "generated"
CHAT_LOG_DIR = WEB_DATA_DIR / "chat_logs"
DEFAULT_MODEL = "gpt-4o-mini"


JsonDict = Dict[str, Any]


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except OSError:
        pass


def safe_slug(name: str, default: str = "file") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return slug or default


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(value), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def capture_stdout(func: Callable[[argparse.Namespace], None], namespace: argparse.Namespace) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        func(namespace)
    return buffer.getvalue().strip()


def extract_balanced_braces(text: str, start_index: int) -> str:
    depth = 0
    in_string: Optional[str] = None
    escaped = False
    begin = -1
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in {'"', "'"}:
            in_string = char
            continue
        if char == "{":
            if depth == 0:
                begin = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and begin >= 0:
                return text[begin : index + 1]
    return ""


def analyze_java_source(source: str, file_name: str) -> JsonDict:
    package_match = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", source, re.MULTILINE)
    package_name = package_match.group(1) if package_match else ""
    imports = re.findall(r"^\s*import\s+([^;]+);", source, re.MULTILINE)
    class_match = re.search(
        r"\b(?:public\s+)?(?:final\s+|abstract\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)",
        source,
    )
    class_name = class_match.group(1) if class_match else Path(file_name).stem

    method_pattern = re.compile(
        r"(?P<prefix>(?:public|protected|private|static|final|synchronized|abstract|native|strictfp|\s)+)"
        r"(?P<return>[A-Za-z_$][\w$<>\[\].?,\s]*?)\s+"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*"
        r"\((?P<params>[^()]*)\)\s*"
        r"(?P<throws>throws\s+[^{;]+)?(?P<body>[{;])",
        re.MULTILINE,
    )
    methods: List[JsonDict] = []
    for match in method_pattern.finditer(source):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            continue
        if name == class_name:
            return_type = "<constructor>"
        else:
            return_type = " ".join(match.group("return").split())
        prefix = " ".join(match.group("prefix").split())
        params = " ".join(match.group("params").split())
        body_marker = match.group("body")
        body = extract_balanced_braces(source, match.end("body") - 1) if body_marker == "{" else ""
        methods.append(
            {
                "name": name,
                "return_type": return_type,
                "modifiers": prefix,
                "parameters": params,
                "throws": (match.group("throws") or "").strip(),
                "line": source[: match.start()].count("\n") + 1,
                "branch_hint_count": len(re.findall(r"\b(if|else|switch|case|for|while|catch|\?)\b", body)),
                "has_body": bool(body),
            }
        )

    public_methods = [m for m in methods if "public" in m["modifiers"].split()]
    test_targets = public_methods or methods[:8]
    dependencies = sorted(
        {
            token
            for token in re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", source)
            if token not in {class_name, "String", "Integer", "Long", "Boolean", "Double", "Float", "Object"}
        }
    )[:30]

    return {
        "file_name": file_name,
        "package": package_name,
        "class_name": class_name,
        "imports": imports,
        "method_count": len(methods),
        "methods": methods[:40],
        "suggested_test_targets": test_targets[:12],
        "dependency_hints": dependencies,
        "line_count": source.count("\n") + 1,
    }


def junit4_scaffold(analysis: JsonDict) -> str:
    package_line = f"package {analysis['package']};\n\n" if analysis.get("package") else ""
    class_name = analysis.get("class_name") or "UploadedClass"
    test_class = f"{class_name}Test"
    targets = analysis.get("suggested_test_targets") or []
    test_methods: List[str] = []
    for index, method in enumerate(targets[:6], start=1):
        name = re.sub(r"[^A-Za-z0-9_]+", "_", method.get("name", f"behavior{index}"))
        test_methods.append(
            f"""    @Test
    public void {name}_documentsExpectedBehavior() throws Exception {{
        // TODO: Instantiate {class_name}, provide representative inputs, and assert the expected result.
        // Target signature: {method.get('modifiers', '')} {method.get('return_type', '')} {method.get('name', '')}({method.get('parameters', '')})
        org.junit.Assert.assertTrue("Replace this scaffold assertion with a behavior-specific oracle.", true);
    }}"""
        )
    if not test_methods:
        test_methods.append(
            f"""    @Test
    public void uploadedClassLoads() {{
        org.junit.Assert.assertNotNull({class_name}.class);
    }}"""
        )
    return (
        package_line
        + "import org.junit.Test;\n\n"
        + f"public class {test_class} {{\n\n"
        + "\n\n".join(test_methods)
        + "\n}\n"
    )


def extract_java_from_model_response(text: str) -> str:
    fenced = re.search(r"```(?:java)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip() + "\n"
    return text.strip() + "\n"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: JsonDict
    handler: Callable[[JsonDict], JsonDict]


class WebAgent:
    def __init__(self, model: str, api_key_env: str, dry_run_tools: bool = False) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.dry_run_tools = dry_run_tools
        self.agent_context = a3_agent.AgentContext(
            paths=a3_agent.AgentPaths(
                config=DATA_DIR / "targets.yaml",
                method_csv=DATA_DIR / "Method_Context.csv",
                test_csv=DATA_DIR / "Test_Data.csv",
                summary_csv=DATA_DIR / "Coverage_Summary_agent.csv",
                suite_dir=DATA_DIR / "Generated_Suites",
                agent_prompt=DATA_DIR / "Prompts" / "agent_system_prompt.md",
                run_dir=DATA_DIR / "Agent" / "runs",
            ),
            planner_model=model,
            inner_model=model,
            api_key_env=api_key_env,
            dry_run_tools=dry_run_tools,
            max_observation_chars=5000,
        )
        self.tools = self._build_tools()

    def _build_tools(self) -> Dict[str, ToolSpec]:
        return {
            "inspect_a3_workspace": ToolSpec(
                name="inspect_a3_workspace",
                description="Inspect existing A3 CSV state, compile status, execution status, coverage summaries, and recommendations.",
                parameters={
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "enum": ["codec", "collections", "compress"]},
                        "feedback_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    },
                    "additionalProperties": False,
                },
                handler=self.inspect_a3_workspace,
            ),
            "validate_a3_workspace": ToolSpec(
                name="validate_a3_workspace",
                description="Run the existing A3 validator over method context, test data, and prompt templates.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self.validate_a3_workspace,
            ),
            "analyze_uploaded_java": ToolSpec(
                name="analyze_uploaded_java",
                description="Analyze a previously uploaded Java file and return class, methods, dependencies, and testing targets.",
                parameters={
                    "type": "object",
                    "properties": {"file_id": {"type": "string"}},
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
                handler=self.analyze_uploaded_java_tool,
            ),
            "generate_tests_for_upload": ToolSpec(
                name="generate_tests_for_upload",
                description="Generate a JUnit 4 test file for an uploaded Java source file. Uses the model when an API key is available; otherwise writes a scaffold.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "testing_goal": {"type": "string"},
                        "max_methods": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
                    },
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
                handler=self.generate_tests_for_upload,
            ),
            "explain_testing_strategy": ToolSpec(
                name="explain_testing_strategy",
                description="Explain likely test partitions, edge cases, and feedback strategy for an uploaded Java file or A3 target.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "project_key": {"type": "string", "enum": ["codec", "collections", "compress"]},
                    },
                    "additionalProperties": False,
                },
                handler=self.explain_testing_strategy,
            ),
            "prepare_a3_feedback_round": ToolSpec(
                name="prepare_a3_feedback_round",
                description="Append feedback-driven generation rows using existing A3 coverage/execution feedback. Mutates Test_Data.csv unless dry-run mode is enabled.",
                parameters={
                    "type": "object",
                    "properties": {
                        "max_round": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                handler=self.prepare_a3_feedback_round,
            ),
            "list_generated_tests": ToolSpec(
                name="list_generated_tests",
                description="List generated test files for uploaded Java sources.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self.list_generated_tests,
            ),
        }

    def openai_tools(self) -> List[JsonDict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self.tools.values()
        ]

    def call_tool(self, name: str, args: JsonDict) -> JsonDict:
        if name not in self.tools:
            return {"ok": False, "tool": name, "error": "Unknown tool"}
        try:
            return self.tools[name].handler(args)
        except Exception as exc:
            return {"ok": False, "tool": name, "error": str(exc), "traceback": traceback.format_exc(limit=5)}

    def inspect_a3_workspace(self, args: JsonDict) -> JsonDict:
        return a3_agent.inspect_workspace(self.agent_context, args)

    def validate_a3_workspace(self, args: JsonDict) -> JsonDict:
        return a3_agent.validate_workspace(self.agent_context, args)

    def upload_file(self, file_name: str, content: bytes) -> JsonDict:
        file_id = uuid.uuid4().hex[:12]
        clean_name = safe_slug(file_name, "Uploaded.java")
        if not clean_name.endswith(".java"):
            clean_name += ".java"
        file_dir = UPLOAD_DIR / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        source_path = file_dir / clean_name
        source_path.write_bytes(content)
        source = content.decode("utf-8", errors="replace")
        analysis = analyze_java_source(source, clean_name)
        metadata = {
            "file_id": file_id,
            "file_name": clean_name,
            "source_path": str(source_path),
            "uploaded_at": now_iso(),
            "analysis": analysis,
        }
        write_json(file_dir / "metadata.json", metadata)
        return {"ok": True, **metadata}

    def load_upload(self, file_id: str) -> Tuple[JsonDict, str]:
        metadata_path = UPLOAD_DIR / safe_slug(file_id) / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Unknown uploaded file_id: {file_id}")
        metadata = read_json(metadata_path, {})
        source = read_text(Path(metadata["source_path"]))
        return metadata, source

    def analyze_uploaded_java_tool(self, args: JsonDict) -> JsonDict:
        metadata, source = self.load_upload(args["file_id"])
        analysis = analyze_java_source(source, metadata["file_name"])
        metadata["analysis"] = analysis
        write_json(UPLOAD_DIR / metadata["file_id"] / "metadata.json", metadata)
        return {"ok": True, "tool": "analyze_uploaded_java", "file_id": metadata["file_id"], "analysis": analysis}

    def generate_tests_for_upload(self, args: JsonDict) -> JsonDict:
        metadata, source = self.load_upload(args["file_id"])
        analysis = metadata.get("analysis") or analyze_java_source(source, metadata["file_name"])
        max_methods = int(args.get("max_methods", 6))
        testing_goal = args.get("testing_goal") or "Generate meaningful JUnit 4 tests for public behavior, edge cases, and exceptions."
        api_key = os.getenv(self.api_key_env)
        raw_response = ""
        used_model = False

        if api_key and OpenAI is not None and not self.dry_run_tools:
            client = OpenAI(api_key=api_key, timeout=120, max_retries=0)
            prompt = (
                "You are a Java unit test generation agent.\n"
                "Return only one compilable JUnit 4 Java test class. Do not use markdown fences.\n"
                "Prefer deterministic assertions, edge cases, and exception tests where appropriate.\n"
                "If dependencies are missing or construction is ambiguous, create focused tests for static/pure methods and document assumptions in short comments.\n\n"
                f"Testing goal: {testing_goal}\n\n"
                f"Source analysis:\n{json_dumps({**analysis, 'suggested_test_targets': analysis.get('suggested_test_targets', [])[:max_methods]})}\n\n"
                f"Java source:\n{source[:18000]}"
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You generate Java JUnit 4 tests for uploaded source files."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                timeout=120,
            )
            raw_response = response.choices[0].message.content or ""
            test_code = extract_java_from_model_response(raw_response)
            used_model = True
        else:
            test_code = junit4_scaffold({**analysis, "suggested_test_targets": analysis.get("suggested_test_targets", [])[:max_methods]})

        class_name = analysis.get("class_name") or Path(metadata["file_name"]).stem
        generated_dir = GENERATED_DIR / metadata["file_id"]
        generated_dir.mkdir(parents=True, exist_ok=True)
        test_path = generated_dir / f"{class_name}Test.java"
        test_path.write_text(test_code, encoding="utf-8")
        record = {
            "file_id": metadata["file_id"],
            "source_file": metadata["file_name"],
            "generated_at": now_iso(),
            "test_path": str(test_path),
            "used_model": used_model,
            "model": self.model if used_model else "",
            "testing_goal": testing_goal,
            "raw_response": raw_response,
        }
        write_json(generated_dir / "generation.json", record)
        return {
            "ok": True,
            "tool": "generate_tests_for_upload",
            "file_id": metadata["file_id"],
            "test_path": str(test_path),
            "used_model": used_model,
            "test_code": test_code,
            "note": "Generated model-backed test." if used_model else "OPENAI_API_KEY not available; wrote a local scaffold.",
        }

    def explain_testing_strategy(self, args: JsonDict) -> JsonDict:
        if args.get("file_id"):
            metadata, _ = self.load_upload(args["file_id"])
            analysis = metadata.get("analysis", {})
            methods = analysis.get("suggested_test_targets", [])
            partitions = [
                "nominal behavior with representative valid inputs",
                "null/empty/boundary inputs",
                "invalid inputs and exception paths",
                "state transitions for mutating methods",
                "round-trip or serialization behavior when applicable",
            ]
            return {
                "ok": True,
                "tool": "explain_testing_strategy",
                "scope": "uploaded_java",
                "class_name": analysis.get("class_name"),
                "recommended_partitions": partitions,
                "high_value_methods": methods[:8],
                "dependency_hints": analysis.get("dependency_hints", []),
            }
        project_key = args.get("project_key")
        rows = load_csv(DATA_DIR / "Coverage_Summary_final_combined.csv")
        selected = [row for row in rows if not project_key or row.get("project_key") == project_key]
        return {
            "ok": True,
            "tool": "explain_testing_strategy",
            "scope": "a3_workspace",
            "project_key": project_key or "all",
            "summary": selected,
            "strategy": [
                "Repair compile failures before trying to improve coverage.",
                "Keep bug-evidence failures separate from pass-oriented coverage tests.",
                "Use coverage feedback to create targeted follow-up rows.",
                "Use balanced suite roles when pass rate matters more than maximum coverage.",
            ],
        }

    def prepare_a3_feedback_round(self, args: JsonDict) -> JsonDict:
        return a3_agent.prepare_feedback_round(self.agent_context, args)

    def list_generated_tests(self, args: JsonDict) -> JsonDict:
        files = []
        if GENERATED_DIR.exists():
            for path in sorted(GENERATED_DIR.glob("*/*.java")):
                files.append(
                    {
                        "file_id": path.parent.name,
                        "name": path.name,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    }
                )
        return {"ok": True, "tool": "list_generated_tests", "files": files}

    def scripted_reply(self, message: str, file_id: Optional[str]) -> JsonDict:
        lower = message.lower()
        tool_results: List[JsonDict] = []
        if file_id and any(word in lower for word in ["generate", "生成", "test", "测试"]):
            tool_results.append(self.call_tool("generate_tests_for_upload", {"file_id": file_id, "testing_goal": message}))
        elif file_id and any(word in lower for word in ["analyze", "分析", "method", "方法"]):
            tool_results.append(self.call_tool("analyze_uploaded_java", {"file_id": file_id}))
        elif any(word in lower for word in ["coverage", "覆盖率", "workspace", "项目状态", "状态"]):
            tool_results.append(self.call_tool("inspect_a3_workspace", {}))
        elif any(word in lower for word in ["validate", "验证", "校验"]):
            tool_results.append(self.call_tool("validate_a3_workspace", {}))
        elif any(word in lower for word in ["strategy", "策略", "怎么测", "还能做什么"]):
            args: JsonDict = {"file_id": file_id} if file_id else {}
            tool_results.append(self.call_tool("explain_testing_strategy", args))
        else:
            args = {"file_id": file_id} if file_id else {}
            tool_results.append(self.call_tool("explain_testing_strategy", args))

        reply = self.render_scripted_reply(message, tool_results)
        return {"reply": reply, "tool_results": tool_results, "planner": "scripted"}

    def render_scripted_reply(self, message: str, results: List[JsonDict]) -> str:
        lines = ["我先用本地规则选择了合适的工具。"]
        for result in results:
            tool = result.get("tool", "unknown")
            if tool == "generate_tests_for_upload" and result.get("ok"):
                lines.append(f"已生成测试文件：`{result.get('test_path')}`。")
                if not result.get("used_model"):
                    lines.append("当前没有可用的 `OPENAI_API_KEY`，所以生成的是可编辑的 JUnit 4 骨架。")
            elif tool == "analyze_uploaded_java" and result.get("ok"):
                analysis = result.get("analysis", {})
                lines.append(
                    f"这个文件看起来是 `{analysis.get('class_name')}`，共识别到 {analysis.get('method_count')} 个方法。"
                )
            elif tool == "inspect_a3_workspace" and result.get("ok"):
                lines.append(
                    f"当前 A3 workspace 有 {result.get('method_row_count')} 个方法上下文、{result.get('test_row_count')} 个生成单元。"
                )
            elif tool == "validate_a3_workspace" and result.get("ok"):
                lines.append(result.get("output", "验证完成。"))
            elif tool == "explain_testing_strategy" and result.get("ok"):
                if result.get("scope") == "uploaded_java":
                    lines.append("建议按 nominal、边界、异常、状态变化、round-trip/序列化这几类来生成测试。")
                else:
                    lines.append("A3 项目里可以继续做覆盖率解释、失败分类、反馈轮次生成、最终 suite 选择。")
            elif not result.get("ok"):
                lines.append(f"`{tool}` 遇到问题：{result.get('error')}")
        return "\n\n".join(lines)

    def llm_reply(self, message: str, file_id: Optional[str], history: List[JsonDict]) -> JsonDict:
        api_key = os.getenv(self.api_key_env)
        if not api_key or OpenAI is None:
            return self.scripted_reply(message, file_id)

        client = OpenAI(api_key=api_key, timeout=120, max_retries=0)
        system = (
            "You are a web-based testing agent for an LLM Java test-generation project. "
            "You can chat with the user, inspect the existing A3 workspace, analyze uploaded Java files, "
            "generate JUnit 4 tests, explain coverage and failures, and suggest next steps. "
            "Use tools when concrete workspace or file information is needed. Keep replies concise and concrete."
        )
        context = f"Active uploaded file_id: {file_id or '<none>'}"
        messages: List[JsonDict] = [{"role": "system", "content": system}, {"role": "system", "content": context}]
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": message})

        tool_results: List[JsonDict] = []
        final_text = ""
        for _ in range(4):
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.openai_tools(),
                tool_choice="auto",
                temperature=0.2,
                timeout=120,
            )
            choice = response.choices[0].message
            assistant_msg: JsonDict = {"role": "assistant", "content": choice.content or ""}
            if choice.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": call.type,
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in choice.tool_calls
                ]
            messages.append(assistant_msg)
            if not choice.tool_calls:
                final_text = choice.content or ""
                break
            for call in choice.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if call.function.name in {"analyze_uploaded_java", "generate_tests_for_upload"} and "file_id" not in args and file_id:
                    args["file_id"] = file_id
                result = self.call_tool(call.function.name, args)
                tool_results.append(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
                        "content": json.dumps(result, ensure_ascii=False)[:7000],
                    }
                )
        if not final_text:
            final_text = "我已经调用工具完成了检查，但没有收到模型的最终总结。请查看工具结果。"
        return {"reply": final_text, "tool_results": tool_results, "planner": "llm"}

    def chat(self, message: str, file_id: Optional[str], history: List[JsonDict]) -> JsonDict:
        try:
            result = self.llm_reply(message, file_id, history)
        except OpenAIError as exc:
            result = self.scripted_reply(message, file_id)
            result["reply"] += f"\n\n模型调用失败，已回退到本地规则 planner：`{exc}`"
        self.log_chat(message, file_id, result)
        return {"ok": True, **result}

    def log_chat(self, message: str, file_id: Optional[str], result: JsonDict) -> None:
        CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {"time": now_iso(), "message": message, "file_id": file_id, "result": result}
        with (CHAT_LOG_DIR / f"{dt.date.today().isoformat()}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class WebAgentHandler(BaseHTTPRequestHandler):
    server_version = "A3WebAgent/0.1"

    @property
    def agent(self) -> WebAgent:
        return self.server.agent  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        try:
            sys.stderr.write("[%s] %s\n" % (now_iso(), format % args))
        except OSError:
            pass

    def send_json(self, status: int, payload: JsonDict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            path = "/index.html"
        if path.startswith("/generated/"):
            target = GENERATED_DIR / path.removeprefix("/generated/")
            self.serve_file(target, GENERATED_DIR)
            return
        target = STATIC_DIR / path.lstrip("/")
        self.serve_file(target, STATIC_DIR)

    def serve_file(self, target: Path, base: Path) -> None:
        try:
            resolved = target.resolve()
            base_resolved = base.resolve()
            if not str(resolved).startswith(str(base_resolved)) or not resolved.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            body = resolved.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/upload":
                self.handle_upload()
            elif self.path == "/api/chat":
                self.handle_chat()
            elif self.path == "/api/tool":
                self.handle_tool()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": str(exc), "traceback": traceback.format_exc(limit=5)},
            )

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def handle_chat(self) -> None:
        payload = json.loads(self.read_body().decode("utf-8"))
        message = str(payload.get("message", "")).strip()
        file_id = payload.get("file_id") or None
        history = payload.get("history") or []
        if not message:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "message is required"})
            return
        self.send_json(HTTPStatus.OK, self.agent.chat(message, file_id, history))

    def handle_tool(self) -> None:
        payload = json.loads(self.read_body().decode("utf-8"))
        name = payload.get("tool")
        args = payload.get("args") or {}
        if not name:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "tool is required"})
            return
        self.send_json(HTTPStatus.OK, self.agent.call_tool(name, args))

    def handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "multipart/form-data is required"})
            return
        boundary_match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
        if not boundary_match:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "multipart boundary is missing"})
            return
        boundary = boundary_match.group("boundary").strip('"').encode("utf-8")
        body = self.read_body()
        file_name = "Uploaded.java"
        file_content: Optional[bytes] = None
        for raw_part in body.split(b"--" + boundary):
            if not raw_part or raw_part in {b"--\r\n", b"--"}:
                continue
            part = raw_part.strip(b"\r\n")
            if b"\r\n\r\n" not in part:
                continue
            header_blob, content = part.split(b"\r\n\r\n", 1)
            headers = header_blob.decode("utf-8", errors="replace")
            disposition = re.search(r'Content-Disposition:.*name="(?P<name>[^"]+)"(?:;\s*filename="(?P<filename>[^"]+)")?', headers, re.I)
            if not disposition:
                continue
            if disposition.group("name") == "file":
                file_name = disposition.group("filename") or file_name
                file_content = content.rstrip(b"\r\n")
                break
        if file_content is None:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "file part is required"})
            return
        self.send_json(HTTPStatus.OK, self.agent.upload_file(file_name, file_content))


class WebAgentServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], handler_class: type[BaseHTTPRequestHandler], agent: WebAgent) -> None:
        super().__init__(server_address, handler_class)
        self.agent = agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Web chat interface for the A3 tool-calling test-generation agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--dry-run-tools", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    for directory in (UPLOAD_DIR, GENERATED_DIR, CHAT_LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    agent = WebAgent(model=args.model, api_key_env=args.api_key_env, dry_run_tools=args.dry_run_tools)
    server = WebAgentServer((args.host, args.port), WebAgentHandler, agent)
    safe_print(f"A3 Web Agent running at http://{args.host}:{args.port}")
    safe_print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        try:
            WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
            (WEB_DATA_DIR / "server_startup_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except BaseException:
            pass
        raise
