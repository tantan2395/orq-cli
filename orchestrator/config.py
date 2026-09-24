"""Configuration manager for agent-orchestrator."""

from pathlib import Path
from typing import Optional
import yaml

from orchestrator.models import (
    OrchestratorConfig,
    ReasoningEffort,
    RoleConfig,
    StageConfig,
    WorkflowConfig,
)


DEFAULT_CONFIG_PATH = Path("orchestrator.yaml")


def get_default_config() -> OrchestratorConfig:
    """Returns the default configuration matching the user's role and workflow architecture."""
    roles = {
        "decision_maker": RoleConfig(
            name="decision_maker",
            agent="codex",
            model="gpt-6-astra",
            reasoning=ReasoningEffort.HIGH,
            system_prompt=(
                "You are the Decision Maker and Lead Architect. Your role is to set strategy, "
                "bound implementation slices, evaluate trade-offs, and produce rigorous acceptance criteria. "
                "When you are ready to delegate tasks, call the Orchestrator MCP tool agents_handoff."
            ),
        ),
        "developer": RoleConfig(
            name="developer",
            agent="agy",
            model="gemini-3.8-flash-high",
            reasoning=ReasoningEffort.MEDIUM,
            system_prompt=(
                "You are the Core Developer. Your role is to implement bounded tasks delegated to you. "
                "Follow all constraints and acceptance criteria strictly. "
                "When your implementation and test evidence are ready, call the Orchestrator MCP tool review_request."
            ),
        ),
        "code_reviewer": RoleConfig(
            name="code_reviewer",
            agent="codex",
            model="gpt-6-astra",
            reasoning=ReasoningEffort.HIGH,
            system_prompt=(
                "You are the Code Reviewer. Your role is to independently verify code changes, test suites, "
                "and verification evidence against acceptance criteria. Do not approve until evidence passes. "
                "Submit decisions and reports via the Orchestrator MCP."
            ),
        ),
        "tester": RoleConfig(
            name="tester",
            agent="agy",
            model="gemini-3.8-flash-low",
            reasoning=ReasoningEffort.LOW,
            system_prompt="You are the Verification & Test Engineer. Run automated probes, build checks, and report results.",
        ),
    }

    stages = {
        "strategy": StageConfig(
            name="strategy",
            role="decision_maker",
            description="High-level architecture, requirements analysis, and bounded slice planning",
            next_on_handoff="implementation",
        ),
        "implementation": StageConfig(
            name="implementation",
            role="developer",
            description="Code implementation, test fixture creation, and local validation",
            next_on_review_request="review",
        ),
        "review": StageConfig(
            name="review",
            role="code_reviewer",
            description="Independent code review, probe verification, and gate check",
            next_on_changes_requested="implementation",
            next_on_approved="final_review",
        ),
        "final_review": StageConfig(
            name="final_review",
            role="decision_maker",
            description="Lead acceptance sign-off, checkpoint commit verification, and next milestone planning",
            next_on_approved="done",
        ),
    }

    workflows = {
        "default": WorkflowConfig(
            name="default",
            initial_stage="strategy",
            stages=stages,
        )
    }

    return OrchestratorConfig(
        workspace_root=".",
        roles=roles,
        workflows=workflows,
        active_workflow="default",
        review_reports_dir="review-reports",
    )


def load_config(path: Optional[Path] = None) -> OrchestratorConfig:
    """Loads configuration from a YAML file, or returns defaults if not found."""
    target_path = path or DEFAULT_CONFIG_PATH
    if not target_path.exists():
        cfg = get_default_config()
        save_config(cfg, target_path)
        return cfg

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return OrchestratorConfig.model_validate(data)


def save_config(config: OrchestratorConfig, path: Optional[Path] = None) -> None:
    """Saves configuration to a YAML file."""
    target_path = path or DEFAULT_CONFIG_PATH
    data = config.model_dump(mode="json")
    with open(target_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)
