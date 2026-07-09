from __future__ import annotations

import hashlib
import json
from pathlib import Path
from string import Template
from typing import Any

import yaml

from app.core.config import DATA_DIR
from app.services.code_context_service import context_for_prompt, truncate_text


PROMPT_TEMPLATE_DIR = DATA_DIR / "Prompts" / "prompt_template"


def load_prompt_template(name: str) -> tuple[dict[str, Any], Path | None]:
    path = PROMPT_TEMPLATE_DIR / f"{name}.yaml"
    if not path.exists():
        return {}, None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data, path


def prompt_hash(system: str, user: str) -> str:
    digest = hashlib.sha256()
    digest.update(system.encode("utf-8"))
    digest.update(b"\n---USER---\n")
    digest.update(user.encode("utf-8"))
    return digest.hexdigest()


def render_legacy_method_payload(template: dict[str, Any], context: dict[str, Any], goal: str) -> str:
    user_input_template = template.get("user_input_template") or ""
    if not user_input_template:
        return context_for_prompt(context)
    rendered: list[str] = []
    for method in context.get("methods", [])[:6]:
        payload = {
            "FQN": method.get("fqn", ""),
            "SIGNATURE": method.get("signature", ""),
            "JIMPLE": method.get("jimple", "") or "<unavailable>",
            "METHOD_SOURCE": method.get("method_source", "") or "<unavailable>",
            "FIELD_CONTEXT": method.get("field_context", "") or "<unavailable>",
            "HELPER_SIGNATURES": method.get("helper_signatures", "") or "<unavailable>",
            "THROWS_MODIFIERS": method.get("throws_modifiers", "") or "<unavailable>",
            "INPUT_PARTITION": "file_level_uploaded_source",
            "TARGET_INTENT": goal,
            "FEEDBACK_SUMMARY": "No previous feedback for this upload turn.",
        }
        rendered.append(Template(user_input_template).safe_substitute(payload))
    return "\n\n---\n\n".join(rendered) or context_for_prompt(context)


def render_generation_prompt(
    goal: str,
    analysis: dict[str, Any],
    source: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    template, path = load_prompt_template("generation")
    system = (
        template.get("system")
        or "You generate Java JUnit 4 tests for uploaded source files. Return only compilable Java source code."
    )
    task = template.get("task") or (
        "Generate one self-contained JUnit 4 test class. Return Java source only, with no markdown fences."
    )
    legacy_context = render_legacy_method_payload(template, context, goal)
    user = (
        f"{task}\n\n"
        "Current deployable Agent context:\n"
        "- The user uploaded a Java file or project zip through the web app.\n"
        "- Use the extracted A3 context below when it is available; if Jimple is unavailable, rely on Method Source and Java source.\n"
        "- Generate a single JUnit 4 test class for the active file. Keep package compatibility with the source class.\n"
        "- Prefer deterministic assertions and avoid placeholder assertions.\n\n"
        f"Goal:\n{goal}\n\n"
        f"Uploaded file analysis JSON:\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
        f"Extracted A3 method context:\n{legacy_context}\n\n"
        f"Full source excerpt:\n{truncate_text(source, 18000)}"
    )
    return {
        "system": system,
        "user": user,
        "template": str(path) if path else "inline:fallback-generation",
        "hash": prompt_hash(system, user),
        "context_source": context.get("context_source"),
        "context_available_fields": context.get("available_fields", []),
    }


def render_repair_prompt(
    instruction: str,
    analysis: dict[str, Any],
    diagnosis: list[dict[str, Any]],
    compile_log: str,
    source: str,
    current_code: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    template, path = load_prompt_template("repair")
    system = (
        template.get("system")
        or "You repair Java JUnit 4 tests for uploaded source files. Return only compilable Java source code."
    )
    task = template.get("task") or "Repair the generated JUnit 4 test so it compiles and preserves useful assertions."
    user = (
        f"{task}\n\n"
        f"Instruction:\n{instruction}\n\n"
        f"Source analysis:\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
        f"Extracted A3 method context:\n{context_for_prompt(context, max_methods=6, max_chars=12000)}\n\n"
        f"Static diagnosis:\n{json.dumps(diagnosis, ensure_ascii=False)}\n\n"
        f"Compiler diagnostics:\n{truncate_text(compile_log or '<none>', 8000)}\n\n"
        f"Uploaded source:\n{truncate_text(source, 14000)}\n\n"
        f"Previous test code:\n{truncate_text(current_code, 14000)}"
    )
    return {
        "system": system,
        "user": user,
        "template": str(path) if path else "inline:fallback-repair",
        "hash": prompt_hash(system, user),
        "context_source": context.get("context_source"),
        "context_available_fields": context.get("available_fields", []),
    }
