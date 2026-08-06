from __future__ import annotations

from datetime import date

from ..contracts import CriticReport, ExecutionPlanArtifact


class CognitiveCriticRuleError(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


def validate_execution_blueprint(blueprint: ExecutionPlanArtifact) -> None:
    """Enforce structural invariants after the independent model critic runs."""

    issues: list[str] = []
    task_ids = [task.id for task in blueprint.tasks]
    known_ids = set(task_ids)
    if len(task_ids) != len(known_ids):
        issues.append("task ids must be unique")

    for task in blueprint.tasks:
        prefix = task.id or "task"
        if not task.title.strip():
            issues.append(f"{prefix} has no title")
        if not task.purpose.strip():
            issues.append(f"{prefix} has no purpose")
        if not task.why_now.strip():
            issues.append(f"{prefix} has no sequencing rationale")
        if not task.action_steps or any(not step.strip() for step in task.action_steps):
            issues.append(f"{prefix} has no action steps")
        if not task.completion_evidence or any(not item.strip() for item in task.completion_evidence):
            issues.append(f"{prefix} has no completion evidence")
        if not task.deliverable.strip():
            issues.append(f"{prefix} has no deliverable")
        if not task.resources:
            issues.append(f"{prefix} has no resources")
        if not task.fallback_action.strip():
            issues.append(f"{prefix} has no fallback action")

        unknown_dependencies = [item for item in task.dependencies if item not in known_ids]
        if unknown_dependencies:
            issues.append(f"{prefix} has unknown dependencies: {', '.join(unknown_dependencies)}")
        if task.scheduled_date:
            try:
                date.fromisoformat(task.scheduled_date)
            except ValueError:
                issues.append(f"{prefix} has invalid scheduledDate")

        for index, resource in enumerate(task.resources, start=1):
            if not resource.title.strip():
                issues.append(f"{prefix} resource {index} has no title")
            if not resource.exact_usage.strip():
                issues.append(f"{prefix} resource {index} has no exact usage")
            if not resource.expected_contribution.strip():
                issues.append(f"{prefix} resource {index} has no expected contribution")

    if issues:
        raise CognitiveCriticRuleError(issues)


def calendar_write_allowed(
    *,
    planning_mode: str,
    critique: CriticReport | None,
    strategy_approved: bool,
    execution_approved: bool,
) -> bool:
    """Keep Calendar writes behind model, critic, and human approval gates."""

    return bool(
        planning_mode == "model_backed"
        and critique
        and critique.status == "passed"
        and critique.calendar_writable
        and not any(issue.severity == "blocker" for issue in critique.issues)
        and not critique.repair_requests
        and strategy_approved
        and execution_approved
    )
