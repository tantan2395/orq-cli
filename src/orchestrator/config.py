"""Configuration manager for agent-orchestrator."""

from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel, Field
import yaml

from orchestrator.models import (
    ExecutionPolicy,
    ModelProfile,
    ReasoningEffort,
    RoleConfig,
    StageConfig,
    StageType,
    WorkflowDefinition,
)

DEFAULT_CONFIG_PATH = Path("orchestrator.yaml")


class OrchestratorConfig(BaseModel):
    """Root configuration structure for the orchestrator runtime."""
    workspace: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    model_profiles: Dict[str, ModelProfile] = Field(default_factory=dict)
    roles: Dict[str, RoleConfig] = Field(default_factory=dict)
    workflows: Dict[str, WorkflowDefinition] = Field(default_factory=dict)
    active_workflow: str = Field(default="default_review_dev_loop")
    ipc_socket_path: str = Field(default="/tmp/agent_orchestrator.sock")
    sqlite_db_path: str = Field(default=".orchestrator/state.db")


def get_default_config() -> OrchestratorConfig:
    """Returns the default configuration matching the role-profile architecture."""
    model_profiles = {
        "strategic": ModelProfile(
            id="strategic",
            model="gpt-6-astra",
            reasoning=ReasoningEffort.HIGH,
            description="Deep architectural strategy, decomposition, and gate evaluation",
        ),
        "implementation": ModelProfile(
            id="implementation",
            model="gemini-3.8-flash-high",
            reasoning=ReasoningEffort.MEDIUM,
            description="Fast, reliable, high-context code implementation and refactoring",
        ),
        "adversarial": ModelProfile(
            id="adversarial",
            model="gpt-6-astra",
            reasoning=ReasoningEffort.HIGH,
            description="Rigorous, adversarial code review and independent verification",
        ),
        "lightweight": ModelProfile(
            id="lightweight",
            model="gemini-3.8-flash-low",
            reasoning=ReasoningEffort.LOW,
            description="Rapid test execution, smoke checks, and build runs",
        ),
    }

    roles = {
        "decision_maker": RoleConfig(
            name="decision_maker",
            agent="codex",
            profile="strategic",
            prompt_file="prompts/decision_maker.md",
            description="Lead Architect and Decision Maker",
        ),
        "developer": RoleConfig(
            name="developer",
            agent="agy",
            profile="implementation",
            prompt_file="prompts/developer.md",
            description="Core Feature Developer",
        ),
        "code_reviewer": RoleConfig(
            name="code_reviewer",
            agent="codex",
            profile="adversarial",
            prompt_file="prompts/code_reviewer.md",
            description="Adversarial Code Reviewer",
        ),
        "tester": RoleConfig(
            name="tester",
            agent="agy",
            profile="lightweight",
            prompt_file="prompts/tester.md",
            description="Verification & Test Engineer",
        ),
    }

    stages = {
        "strategy": StageConfig(
            name="strategy",
            role="decision_maker",
            type=StageType.SEQUENTIAL,
            description="High-level architecture, requirements analysis, and bounded slice planning",
            next_on_handoff="implementation",
        ),
        "implementation": StageConfig(
            name="implementation",
            role="developer",
            type=StageType.SEQUENTIAL,
            description="Code implementation, test fixture creation, and local validation",
            next_on_review_request="review",
        ),
        "review": StageConfig(
            name="review",
            role="code_reviewer",
            type=StageType.CONDITIONAL,
            description="Independent code review, probe verification, and gate check",
            next_on_changes_requested="implementation",
            next_on_approved="final_review",
        ),
        "final_review": StageConfig(
            name="final_review",
            role="decision_maker",
            type=StageType.SEQUENTIAL,
            description="Lead acceptance sign-off, checkpoint commit verification, and next milestone planning",
            next_on_approved="done",
        ),
    }

    workflows = {
        "default_review_dev_loop": WorkflowDefinition(
            id="default_review_dev_loop",
            version=1,
            description="Standard decision-maker -> developer -> reviewer -> final-review loop",
            initial_stage="strategy",
            stages=stages,
        )
    }

    return OrchestratorConfig(
        workspace=ExecutionPolicy(workspace_root=".", use_git_worktree=True),
        model_profiles=model_profiles,
        roles=roles,
        workflows=workflows,
        active_workflow="default_review_dev_loop",
        ipc_socket_path="/tmp/agent_orchestrator.sock",
        sqlite_db_path=".orchestrator/state.db",
    )


def load_config(path: Optional[Path] = None) -> OrchestratorConfig:
    """Loads configuration from YAML file or returns defaults."""
    target_path = path or DEFAULT_CONFIG_PATH
    if not target_path.exists():
        cfg = get_default_config()
        save_config(cfg, target_path)
        return cfg

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return OrchestratorConfig.model_validate(data)


def save_config(config: OrchestratorConfig, path: Optional[Path] = None) -> None:
    """Saves configuration to YAML file."""
    target_path = path or DEFAULT_CONFIG_PATH
    data = config.model_dump(mode="json")
    with open(target_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)
