from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentSkill:
    id: str
    label: str
    summary: str
    tools: tuple[str, ...]
    intents: tuple[str, ...]
    read_only: bool
    side_effecting: bool
    max_steps: int = 1

    def brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "read_only": self.read_only,
            "side_effecting": self.side_effecting,
            "max_steps": self.max_steps,
        }

    def catalog_entry(self) -> dict[str, Any]:
        entry = self.brief()
        entry["tools"] = list(self.tools)
        entry["intents"] = list(self.intents)
        return entry


class SkillRegistry:
    def __init__(self, skills: tuple[AgentSkill, ...]):
        self._skills = skills
        self._by_id = {skill.id: skill for skill in skills}
        self._by_intent = {
            intent: skill
            for skill in skills
            for intent in skill.intents
        }
        self._by_tool: dict[str, AgentSkill] = {}
        for skill in skills:
            for tool in skill.tools:
                self._by_tool.setdefault(tool, skill)

    def all(self) -> tuple[AgentSkill, ...]:
        return self._skills

    def get(self, skill_id: str | None) -> AgentSkill:
        if skill_id and skill_id in self._by_id:
            return self._by_id[skill_id]
        return self._by_id["general_chat"]

    def for_intent(self, intent: str, mode: str = "ask") -> AgentSkill:
        if intent in self._by_intent:
            return self._by_intent[intent]
        if mode == "read":
            return self._by_id["code_understanding"]
        if mode == "act":
            return self._by_id["workspace_ops"]
        return self._by_id["general_chat"]

    def for_tool(self, tool_name: str) -> AgentSkill:
        return self._by_tool.get(tool_name, self._by_id["workspace_ops"])

    def allows_tool(self, skill_id: str | None, tool_name: str) -> bool:
        return tool_name in self.get(skill_id).tools

    def allowed_tools(self, skill_id: str | None) -> tuple[str, ...]:
        return self.get(skill_id).tools

    def catalog(self) -> list[dict[str, Any]]:
        return [skill.catalog_entry() for skill in self._skills]

    def prompt_catalog(self, selected_skill_id: str | None = None) -> str:
        selected = self.get(selected_skill_id).id if selected_skill_id else ""
        lines = []
        for skill in self._skills:
            marker = "selected" if skill.id == selected else "available"
            tools = ", ".join(skill.tools)
            lines.append(f"- {skill.id} ({marker}): {skill.summary} Tools: {tools}.")
        return "\n".join(lines)

    def filter_tool_schemas(self, schemas: list[dict[str, Any]], skill_id: str | None) -> list[dict[str, Any]]:
        if not skill_id:
            return schemas
        allowed = set(self.allowed_tools(skill_id))
        return [
            schema
            for schema in schemas
            if schema.get("function", {}).get("name") in allowed
        ]


DEFAULT_SKILL_REGISTRY = SkillRegistry(
    (
        AgentSkill(
            id="general_chat",
            label="General chat",
            summary="Answer conceptual or planning questions without changing project state.",
            tools=("list_skills", "read_memories"),
            intents=("chat",),
            read_only=True,
            side_effecting=False,
            max_steps=1,
        ),
        AgentSkill(
            id="workspace_ops",
            label="Workspace operations",
            summary="Inspect workspace health, validate A3 assets, and expose the skill catalog.",
            tools=("list_skills", "inspect_workspace", "validate_workspace", "prepare_feedback_round"),
            intents=("list_skills",),
            read_only=False,
            side_effecting=True,
            max_steps=1,
        ),
        AgentSkill(
            id="code_understanding",
            label="Code understanding",
            summary="Read uploaded Java structure and extracted A3 context such as FQN, signatures, Jimple, fields, helpers, and throws.",
            tools=("list_files", "analyze_file", "read_code_context"),
            intents=("read_code_context", "describe_current_file", "list_source_files", "analyze_file"),
            read_only=True,
            side_effecting=False,
            max_steps=1,
        ),
        AgentSkill(
            id="artifact_management",
            label="Artifact management",
            summary="List, read, and explain generated JUnit artifacts without creating or repairing code.",
            tools=("list_artifacts", "read_artifact", "explain_artifact"),
            intents=("list_artifacts", "explain_latest_test"),
            read_only=True,
            side_effecting=False,
            max_steps=1,
        ),
        AgentSkill(
            id="test_generation",
            label="Test generation",
            summary="Create one or many JUnit 4 test artifacts from uploaded Java sources and extracted context.",
            tools=("generate_tests", "batch_generate_tests"),
            intents=("generate_tests", "batch_generate_tests"),
            read_only=False,
            side_effecting=True,
            max_steps=1,
        ),
        AgentSkill(
            id="coverage_analysis",
            label="Coverage analysis",
            summary="Compile, run JUnit, and collect JaCoCo coverage for generated artifacts.",
            tools=("list_artifacts", "compile_artifact", "run_coverage"),
            intents=("run_coverage", "_legacy_run_coverage"),
            read_only=False,
            side_effecting=True,
            max_steps=1,
        ),
        AgentSkill(
            id="test_repair",
            label="Test repair",
            summary="Diagnose compile or runtime failures and create repaired versions of generated tests.",
            tools=("list_artifacts", "compile_artifact", "diagnose_artifact", "repair_artifact"),
            intents=("repair_latest", "diagnose_latest"),
            read_only=False,
            side_effecting=True,
            max_steps=2,
        ),
        AgentSkill(
            id="memory",
            label="Memory",
            summary="Read or store stable user and project preferences.",
            tools=("read_memories", "remember"),
            intents=("remember",),
            read_only=False,
            side_effecting=True,
            max_steps=1,
        ),
    )
)
