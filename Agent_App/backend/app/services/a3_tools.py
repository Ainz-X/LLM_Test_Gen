from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path
from typing import Any

from app.core.config import DATA_DIR, PROJECT_ROOT, settings


PYTHON_SCRIPTS = PROJECT_ROOT / "LLM_Test_Gen" / "Python_Scripts"
if str(PYTHON_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_SCRIPTS))

import a3_agent  # noqa: E402


def agent_context(dry_run_tools: bool = False) -> a3_agent.AgentContext:
    return a3_agent.AgentContext(
        paths=a3_agent.AgentPaths(
            config=DATA_DIR / "targets.yaml",
            method_csv=DATA_DIR / "Method_Context.csv",
            test_csv=DATA_DIR / "Test_Data.csv",
            summary_csv=DATA_DIR / "Coverage_Summary_agent_app.csv",
            suite_dir=DATA_DIR / "Generated_Suites",
            agent_prompt=DATA_DIR / "Prompts" / "agent_system_prompt.md",
            run_dir=DATA_DIR / "Agent" / "runs",
        ),
        planner_model=settings.openai_model,
        inner_model=settings.openai_model,
        api_key_env="OPENAI_API_KEY",
        dry_run_tools=dry_run_tools,
        max_observation_chars=5000,
    )


def inspect_workspace(project_key: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {"feedback_limit": 5}
    if project_key:
        args["project_key"] = project_key
    return a3_agent.inspect_workspace(agent_context(), args)


def validate_workspace() -> dict[str, Any]:
    return a3_agent.validate_workspace(agent_context(), {})


def prepare_feedback_round(max_round: int = 2, dry_run: bool = True) -> dict[str, Any]:
    return a3_agent.prepare_feedback_round(agent_context(dry_run_tools=dry_run), {"max_round": max_round, "dry_run": dry_run})
