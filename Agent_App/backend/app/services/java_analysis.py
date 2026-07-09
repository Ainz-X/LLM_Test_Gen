from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def strip_java_comments(source: str) -> str:
    result: list[str] = []
    index = 0
    in_string = ""
    escaped = False
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = char
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            result.extend("  ")
            index += 2
            while index < len(source) and source[index] != "\n":
                result.append(" ")
                index += 1
            continue

        if char == "/" and next_char == "*":
            result.extend("  ")
            index += 2
            while index < len(source):
                if source[index] == "*" and index + 1 < len(source) and source[index + 1] == "/":
                    result.extend("  ")
                    index += 2
                    break
                result.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def extract_balanced_braces(text: str, start_index: int) -> str:
    depth = 0
    in_string = ""
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
                in_string = ""
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


def analyze_java_source(source: str, file_name: str) -> dict[str, Any]:
    code = strip_java_comments(source)
    package_match = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", code, re.MULTILINE)
    package_name = package_match.group(1) if package_match else ""
    imports = re.findall(r"^\s*import\s+([^;]+);", code, re.MULTILINE)
    class_match = re.search(
        r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*"
        r"(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)",
        code,
        re.MULTILINE,
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
    methods = []
    for match in method_pattern.finditer(code):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            continue
        body = extract_balanced_braces(source, match.end("body") - 1) if match.group("body") == "{" else ""
        methods.append(
            {
                "name": name,
                "return_type": " ".join(match.group("return").split()) if name != class_name else "<constructor>",
                "modifiers": " ".join(match.group("prefix").split()),
                "parameters": " ".join(match.group("params").split()),
                "throws": (match.group("throws") or "").strip(),
                "line": source[: match.start()].count("\n") + 1,
                "branch_hint_count": len(re.findall(r"\b(if|else|switch|case|for|while|catch|\?)\b", body)),
                "has_body": bool(body),
            }
        )
    public_methods = [method for method in methods if "public" in method["modifiers"].split()]
    dependencies = sorted(
        {
            token
            for token in re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", source)
            if token not in {class_name, "String", "Integer", "Long", "Boolean", "Double", "Float", "Object"}
        }
    )[:40]
    return {
        "file_name": file_name,
        "package": package_name,
        "class_name": class_name,
        "imports": imports,
        "method_count": len(methods),
        "methods": methods[:60],
        "suggested_test_targets": (public_methods or methods)[:16],
        "dependency_hints": dependencies,
        "line_count": source.count("\n") + 1,
    }


def junit4_scaffold(analysis: dict[str, Any]) -> str:
    package_line = f"package {analysis['package']};\n\n" if analysis.get("package") else ""
    class_name = analysis.get("class_name") or "UploadedClass"
    methods = analysis.get("suggested_test_targets") or []
    body = []
    for method in methods[:8]:
        safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", method.get("name", "behavior"))
        body.append(
            f"""    @Test
    public void {safe_name}_documentsExpectedBehavior() throws Exception {{
        // Replace this scaffold assertion with a behavior-specific oracle.
        // Target: {method.get('return_type', '')} {method.get('name', '')}({method.get('parameters', '')})
        org.junit.Assert.assertTrue(true);
    }}"""
        )
    if not body:
        body.append(
            f"""    @Test
    public void uploadedClassLoads() {{
        org.junit.Assert.assertNotNull({class_name}.class);
    }}"""
        )
    return package_line + "import org.junit.Test;\n\n" + f"public class {class_name}Test {{\n\n" + "\n\n".join(body) + "\n}\n"
