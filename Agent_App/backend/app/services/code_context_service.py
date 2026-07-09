from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import CodeContext, UploadedFile
from app.services.java_analysis import extract_balanced_braces, strip_java_comments


METHOD_CONTEXT_COLUMNS = [
    "FQN",
    "Signature",
    "Jimple Code Representation",
    "Method Source",
    "Field Context",
    "Constructor/Helper Signatures",
    "Throws/Modifiers",
]

FIELD_ALIASES = {
    "all": "all",
    "fqn": "fqn",
    "fnq": "fqn",
    "signature": "signature",
    "method_signature": "signature",
    "jimple": "jimple",
    "jimple_code": "jimple",
    "method_source": "method_source",
    "source": "method_source",
    "field_context": "field_context",
    "fields": "field_context",
    "helper_signatures": "helper_signatures",
    "constructor_helper_signatures": "helper_signatures",
    "throws_modifiers": "throws_modifiers",
    "modifiers": "throws_modifiers",
    "throws": "throws_modifiers",
}

FIELD_LABELS = {
    "fqn": "FQN",
    "signature": "Method Signature",
    "jimple": "Jimple Code",
    "method_source": "Method Source",
    "field_context": "Field Context",
    "helper_signatures": "Constructor/Helper Signatures",
    "throws_modifiers": "Modifiers & Throws",
}


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... <truncated {len(text) - max_chars} chars>"


def canonical_field(field: str | None) -> str:
    if not field:
        return "all"
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", field.strip().lower()).strip("_")
    return FIELD_ALIASES.get(normalized, normalized if normalized in FIELD_LABELS else "all")


def infer_context_field(message: str) -> str:
    lower = message.lower()
    if any(token in lower for token in ["jimple", "中间表示", "ir", "字节码"]):
        return "jimple"
    if any(token in lower for token in ["method source", "方法源码", "源码", "源代码"]):
        return "method_source"
    if any(token in lower for token in ["field context", "字段上下文", "字段", "field"]):
        return "field_context"
    if any(token in lower for token in ["constructor", "helper", "构造", "辅助方法", "helper signatures"]):
        return "helper_signatures"
    if any(token in lower for token in ["throws", "modifier", "modifiers", "异常", "修饰符"]):
        return "throws_modifiers"
    if any(token in lower for token in ["signature", "签名"]):
        return "signature"
    if any(token in lower for token in ["fqn", "fnq", "fully qualified", "全限定名", "完整类名"]):
        return "fqn"
    return "all"


def class_fqn_from_analysis(analysis: dict[str, Any]) -> str:
    package_name = analysis.get("package") or ""
    class_name = analysis.get("class_name") or Path(str(analysis.get("file_name") or "Unknown.java")).stem
    return f"{package_name}.{class_name}" if package_name else class_name


def class_name_from_analysis(analysis: dict[str, Any]) -> str:
    return str(analysis.get("class_name") or Path(str(analysis.get("file_name") or "Unknown.java")).stem)


def class_fqn_from_method_fqn(fqn: str) -> str:
    head = fqn.split("(", 1)[0]
    if "." not in head:
        return ""
    return head.rsplit(".", 1)[0]


def method_name_from_fqn(fqn: str) -> str:
    head = fqn.split("(", 1)[0]
    return head.rsplit(".", 1)[-1] if head else ""


def method_context_candidates(file: UploadedFile) -> list[Path]:
    analysis = file.analysis or {}
    raw_roots = [
        analysis.get("_project_context_root"),
        analysis.get("_project_root"),
        analysis.get("_project_storage_root"),
    ]
    candidates: list[Path] = []
    for raw in raw_roots:
        if not raw:
            continue
        root = Path(str(raw))
        candidates.extend(
            [
                root / "Method_Context.csv",
                root / "Data" / "Method_Context.csv",
                root / "LLM_Test_Gen" / "Data" / "Method_Context.csv",
            ]
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen and candidate.exists():
            unique.append(candidate)
            seen.add(key)
    return unique


def row_matches_file(row: dict[str, str], class_fqn: str, class_name: str) -> bool:
    row_class = class_fqn_from_method_fqn(row.get("FQN", ""))
    if not row_class:
        return False
    return row_class == class_fqn or (not "." in class_fqn and row_class.endswith(f".{class_name}"))


def load_method_context_rows(file: UploadedFile) -> tuple[list[dict[str, str]], list[str]]:
    analysis = file.analysis or {}
    class_fqn = class_fqn_from_analysis(analysis)
    class_name = class_name_from_analysis(analysis)
    checked: list[str] = []
    for candidate in method_context_candidates(file):
        checked.append(str(candidate))
        rows: list[dict[str, str]] = []
        try:
            with candidate.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if row_matches_file(row, class_fqn, class_name):
                        row["_context_source"] = str(candidate)
                        rows.append(row)
        except OSError:
            continue
        if rows:
            return rows, checked
    return [], checked


def load_db_context_rows(db: Session | None, file: UploadedFile) -> list[CodeContext]:
    if db is None:
        return []
    return (
        db.query(CodeContext)
        .filter(CodeContext.user_id == file.user_id, CodeContext.file_sha256 == file.sha256)
        .order_by(CodeContext.method_fqn.asc())
        .all()
    )


METHOD_PATTERN = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*"
    r"(?P<prefix>(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp)\s+)*)"
    r"(?P<return>[A-Za-z_$][\w$<>\[\].?,]*(?:\s+[A-Za-z_$][\w$<>\[\].?,]*)*)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*)\)\s*"
    r"(?P<throws>throws\s+[^{;]+)?(?P<body>[{;])",
    re.MULTILINE,
)


def source_snippets_by_method(source: str) -> dict[tuple[str, int], str]:
    code = strip_java_comments(source)
    snippets: dict[tuple[str, int], str] = {}
    for match in METHOD_PATTERN.finditer(code):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            continue
        line = source[: match.start()].count("\n") + 1
        start = source.rfind("\n", 0, match.start()) + 1
        if match.group("body") == "{":
            body_start = match.end("body") - 1
            body = extract_balanced_braces(source, body_start)
            end = body_start + len(body) if body else match.end()
        else:
            semicolon = source.find(";", match.end("body") - 1)
            end = semicolon + 1 if semicolon >= 0 else match.end()
        snippets[(name, line)] = source[start:end].strip()
    return snippets


def fallback_method_rows(file: UploadedFile, source: str) -> list[dict[str, Any]]:
    analysis = file.analysis or {}
    class_fqn = class_fqn_from_analysis(analysis)
    snippets = source_snippets_by_method(source)
    rows: list[dict[str, Any]] = []
    for method in analysis.get("methods", []):
        name = str(method.get("name") or "")
        params = str(method.get("parameters") or "")
        return_type = str(method.get("return_type") or "")
        throws = str(method.get("throws") or "")
        modifiers = str(method.get("modifiers") or "")
        line = int(method.get("line") or 0)
        signature = f"{return_type} {name}({params})".strip()
        if throws:
            signature = f"{signature} {throws}"
        rows.append(
            {
                "fqn": f"{class_fqn}.{name}({params})",
                "method_name": name,
                "signature": signature,
                "jimple": "",
                "method_source": snippets.get((name, line), ""),
                "field_context": "",
                "helper_signatures": "",
                "throws_modifiers": " ".join(part for part in [modifiers, throws] if part),
                "line": line,
                "context_source": "uploaded_source_static_analysis",
                "unavailable": {
                    "jimple": "Jimple requires the old SootUp/classpath extraction output; it is not produced by the lightweight upload parser.",
                    "field_context": "Field context was not present in Method_Context.csv for this uploaded file.",
                    "helper_signatures": "Constructor/helper signatures were not present in Method_Context.csv for this uploaded file.",
                },
            }
        )
    return rows


def normalize_csv_row(row: dict[str, str], max_field_chars: int) -> dict[str, Any]:
    fqn = row.get("FQN", "")
    return {
        "fqn": fqn,
        "method_name": method_name_from_fqn(fqn),
        "signature": truncate_text(row.get("Signature", ""), max_field_chars),
        "jimple": truncate_text(row.get("Jimple Code Representation", ""), max_field_chars),
        "method_source": truncate_text(row.get("Method Source", ""), max_field_chars),
        "field_context": truncate_text(row.get("Field Context", ""), max_field_chars),
        "helper_signatures": truncate_text(row.get("Constructor/Helper Signatures", ""), max_field_chars),
        "throws_modifiers": truncate_text(row.get("Throws/Modifiers", ""), max_field_chars),
        "context_source": row.get("_context_source", "Method_Context.csv"),
        "unavailable": {},
    }


def normalize_db_row(row: CodeContext, max_field_chars: int) -> dict[str, Any]:
    return {
        "fqn": row.method_fqn,
        "method_name": method_name_from_fqn(row.method_fqn),
        "signature": truncate_text(row.signature or "", max_field_chars),
        "jimple": truncate_text(row.jimple or "", max_field_chars),
        "method_source": truncate_text(row.method_source or "", max_field_chars),
        "field_context": truncate_text(row.field_context or "", max_field_chars),
        "helper_signatures": truncate_text(row.helper_signatures or "", max_field_chars),
        "throws_modifiers": truncate_text(row.throws_modifiers or "", max_field_chars),
        "context_source": row.context_source or "code_contexts",
        "source_path": row.source_path or "",
        "unavailable": {},
    }


def build_code_context(
    file: UploadedFile,
    db: Session | None = None,
    field: str | None = None,
    method_filter: str | None = None,
    max_methods: int = 12,
    max_field_chars: int = 6000,
) -> dict[str, Any]:
    analysis = file.analysis or {}
    requested_field = canonical_field(field)
    db_rows = load_db_context_rows(db, file)
    checked_sources: list[str] = []
    if db_rows:
        methods = [normalize_db_row(row, max_field_chars) for row in db_rows]
        context_source = "code_context_db"
        checked_sources = ["code_contexts"]
        notes = [f"已从数据库读取 {len(methods)} 行方法级上下文；相同文件内容会复用同一份提取结果。"]
    else:
        source = Path(file.storage_path).read_text(encoding="utf-8", errors="replace")
        csv_rows, checked_sources = load_method_context_rows(file)
        if csv_rows:
            methods = [normalize_csv_row(row, max_field_chars) for row in csv_rows]
            context_source = "method_context_csv"
            notes = [f"匹配到项目工作区 Method_Context.csv 中的 {len(methods)} 个方法上下文。"]
        else:
            methods = fallback_method_rows(file, source)
            context_source = "uploaded_source_static_analysis"
            notes = [
                "数据库和项目 Method_Context.csv 中都没有可用上下文，已降级为上传源码的轻量静态分析；"
                "该模式不会生成 Jimple。"
            ]

    if method_filter:
        needle = method_filter.lower()
        methods = [
            method
            for method in methods
            if needle in str(method.get("fqn", "")).lower()
            or needle in str(method.get("method_name", "")).lower()
            or needle in str(method.get("signature", "")).lower()
        ]
    total_methods = len(methods)
    methods = methods[:max_methods]

    available_fields = [
        key
        for key in FIELD_LABELS
        if key == "fqn" or any(str(method.get(key) or "").strip() for method in methods)
    ]
    unavailable_fields = [key for key in FIELD_LABELS if key not in available_fields]
    class_fqn = class_fqn_from_analysis(analysis)
    return {
        "ok": True,
        "tool": "read_code_context",
        "file": {"id": file.id, "name": file.original_name},
        "class_fqn": class_fqn,
        "class_name": class_name_from_analysis(analysis),
        "package": analysis.get("package") or "",
        "relative_path": analysis.get("_project_relative_path") or analysis.get("file_name") or file.original_name,
        "context_source": context_source,
        "checked_sources": checked_sources,
        "requested_field": requested_field,
        "available_fields": available_fields,
        "unavailable_fields": unavailable_fields,
        "method_count": total_methods,
        "returned_method_count": len(methods),
        "methods": methods,
        "notes": notes,
    }


def selected_field_values(context: dict[str, Any], field: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for method in context.get("methods", []):
        label = str(method.get("fqn") or method.get("method_name") or "<method>")
        value = str(method.get(field) or "").strip()
        if value:
            values.append((label, value))
    return values


def format_code_context_answer(message: str, context: dict[str, Any]) -> str:
    if not context.get("ok"):
        return "读取当前代码上下文失败：" + str(context.get("error") or context)
    field = canonical_field(context.get("requested_field") or infer_context_field(message))
    class_fqn = context.get("class_fqn") or context.get("class_name") or "当前类"
    source = context.get("context_source")
    notes = "\n".join(f"- {note}" for note in context.get("notes", []))

    if field == "all":
        available = ", ".join(FIELD_LABELS[key] for key in context.get("available_fields", []) if key in FIELD_LABELS)
        unavailable = ", ".join(FIELD_LABELS[key] for key in context.get("unavailable_fields", []) if key in FIELD_LABELS)
        methods = "\n".join(
            f"- `{method.get('fqn')}`"
            for method in context.get("methods", [])[:12]
        ) or "- 暂未识别到方法。"
        return (
            f"当前类 `{class_fqn}` 的代码上下文来源：`{source}`。\n\n"
            f"可回答字段：{available or '无'}。\n"
            f"暂不可用字段：{unavailable or '无'}。\n\n"
            f"方法上下文：\n{methods}\n\n"
            f"{notes}"
        ).strip()

    if field == "fqn":
        values = selected_field_values(context, "fqn")
        if not values:
            return f"当前类 FQN：`{class_fqn}`。"
        lines = "\n".join(f"- `{value}`" for _, value in values[:20])
        return f"当前类 `{class_fqn}` 的方法 FQN 如下：\n{lines}"

    values = selected_field_values(context, field)
    label = FIELD_LABELS.get(field, field)
    if not values:
        checked = "\n".join(f"- `{path}`" for path in context.get("checked_sources", [])[:5]) or "- 未发现可检查的 Method_Context.csv。"
        return (
            f"当前文件没有可用的 `{label}`。\n\n"
            f"原因：`{context.get('context_source')}` 只能提供这些字段："
            f"{', '.join(FIELD_LABELS[key] for key in context.get('available_fields', []) if key in FIELD_LABELS) or '无'}。\n\n"
            f"已检查的上下文来源：\n{checked}"
        )

    sections: list[str] = []
    for fqn, value in values[:5]:
        if field in {"jimple", "method_source"}:
            sections.append(f"`{fqn}`\n```java\n{value}\n```")
        else:
            sections.append(f"`{fqn}`\n{value}")
    prefix = f"当前类 `{class_fqn}` 的 `{label}` 来自 `{source}`。"
    if len(values) > 5:
        prefix += f" 匹配到 {len(values)} 个方法，先展示前 5 个。"
    return prefix + "\n\n" + "\n\n".join(sections)


def context_for_prompt(context: dict[str, Any], max_methods: int = 6, max_chars: int = 14000) -> str:
    blocks: list[str] = []
    for method in context.get("methods", [])[:max_methods]:
        block = (
            f"FQN: {method.get('fqn', '')}\n"
            f"Signature: {method.get('signature', '')}\n"
            f"Jimple Code Representation:\n{method.get('jimple', '') or '<unavailable>'}\n"
            f"Method Source:\n{method.get('method_source', '') or '<unavailable>'}\n"
            f"Field Context:\n{method.get('field_context', '') or '<unavailable>'}\n"
            f"Constructor/Helper Signatures:\n{method.get('helper_signatures', '') or '<unavailable>'}\n"
            f"Throws/Modifiers: {method.get('throws_modifiers', '') or '<unavailable>'}"
        )
        blocks.append(block)
    text = "\n\n---\n\n".join(blocks) or "<no method context available>"
    return truncate_text(text, max_chars)
