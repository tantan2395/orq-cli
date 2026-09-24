"""Context manager generating immutable per-turn workflow context projections."""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from orchestrator.models import AgentTurnContext, OrchestrationTask, RoleConfig, WorkflowRun


class ContextManager:
    """Projects bounded, immutable context snapshots for agent turns."""

    def __init__(self, base_prompts_dir: Optional[Path] = None):
        self.base_prompts_dir = base_prompts_dir or Path("prompts")

    def load_role_contract(self, prompt_file: Optional[str]) -> str:
        """Reads behavioral contract markdown from prompt file."""
        if not prompt_file:
            return ""
        path = Path(prompt_file)
        if not path.is_absolute():
            path = self.base_prompts_dir / path.name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def create_turn_context(
        self,
        workflow_run: WorkflowRun,
        role_config: RoleConfig,
        task: OrchestrationTask,
        previous_decisions: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentTurnContext:
        """Creates an immutable context snapshot for an agent execution turn."""
        turn_id = f"turn_{uuid.uuid4().hex[:10]}"
        contract_text = self.load_role_contract(role_config.prompt_file)

        payload = task.payload
        bounded_task = payload.get("task") or payload.get("summary") or task.type
        constraints = payload.get("constraints", [])
        acceptance_criteria = payload.get("acceptance_criteria", [])
        artifacts = payload.get("artifacts", [])

        return AgentTurnContext(
            turn_id=turn_id,
            workflow_run_id=workflow_run.run_id,
            stage_id=workflow_run.current_stage,
            role=role_config.name,
            objective=f"Execute workflow '{workflow_run.definition_id}' at stage '{workflow_run.current_stage}'",
            role_contract=contract_text,
            bounded_task=str(bounded_task),
            constraints=list(constraints),
            acceptance_criteria=list(acceptance_criteria),
            relevant_artifacts=list(artifacts),
            previous_decisions=previous_decisions or [],
        )

    def assemble_effective_prompt(self, context: AgentTurnContext) -> str:
        """Formats the immutable turn context into an explicit prompt for the agent CLI."""
        sections = [
            f"[AGENT IDENTITY CONTRACT]\nROLE: {context.role}\nWORKFLOW STAGE: {context.stage_id}\nOBJECTIVE: {context.objective}",
        ]

        if context.role_contract:
            sections.append(f"[ROLE BEHAVIORAL CONTRACT]\n{context.role_contract}")

        sections.append(f"[BOUNDED TASK]\n{context.bounded_task}")

        if context.constraints:
            constraint_lines = "\n".join(f"- {c}" for c in context.constraints)
            sections.append(f"[CONSTRAINTS]\n{constraint_lines}")

        if context.acceptance_criteria:
            crit_lines = "\n".join(f"- {c}" for c in context.acceptance_criteria)
            sections.append(f"[ACCEPTANCE CRITERIA]\n{crit_lines}")

        if context.relevant_artifacts:
            art_lines = "\n".join(f"- {a}" for a in context.relevant_artifacts)
            sections.append(f"[REFERENCED ARTIFACTS]\n{art_lines}")

        return "\n\n".join(sections)
