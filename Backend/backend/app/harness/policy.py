from __future__ import annotations

from collections.abc import Sequence

from .contracts import (
    ApprovalGate,
    ApprovalRecord,
    ArtifactRef,
    CriticGateResult,
    MemoryCandidate,
    MemoryEvaluation,
    PolicyDecision,
    PolicyGate,
)


_APPROVAL_POLICY_GATES: dict[ApprovalGate, PolicyGate] = {
    "strategy": "strategy_approval",
    "execution": "execution_approval",
    "calendar": "calendar_approval",
}


def _has_approval(
    approvals: Sequence[ApprovalRecord],
    *,
    session_id: str,
    gate: ApprovalGate,
    artifact: ArtifactRef,
) -> bool:
    return any(
        record.approves(session_id=session_id, gate=gate, artifact=artifact)
        for record in approvals
    )


class PolicyEngine:
    """Pure, fail-closed policy decisions consumed by the Harness scheduler/controllers."""

    def decide_planning_progress(
        self,
        *,
        session_id: str,
        runtime_blocked: bool,
        blocking_unknowns: Sequence[str] = (),
        next_agent: str | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> PolicyDecision:
        if runtime_blocked:
            return PolicyDecision(
                subject="planning_progress",
                action="block_runtime",
                allowed=False,
                reason="Runtime recovery is required before another Agent may be invoked.",
                sessionId=session_id,
                requiredGates=("runtime",),
                failedGates=("runtime",),
            )
        if blocking_unknowns:
            return PolicyDecision(
                subject="user_question",
                action="wait_user",
                allowed=False,
                reason="Decision-blocking user information is still missing.",
                sessionId=session_id,
                requiredGates=("goal_completion",),
                failedGates=("goal_completion",),
            )
        if approval_gate:
            policy_gate = _APPROVAL_POLICY_GATES[approval_gate]
            return PolicyDecision(
                subject="planning_progress",
                action="wait_approval",
                allowed=False,
                reason=f"The {approval_gate} approval gate must be completed before planning continues.",
                sessionId=session_id,
                requiredApproval=approval_gate,
                requiredGates=(policy_gate,),
                failedGates=(policy_gate,),
            )
        if next_agent:
            return PolicyDecision(
                subject="planning_progress",
                action="invoke_agent",
                allowed=True,
                reason=f"All current policy gates passed; invoke {next_agent}.",
                sessionId=session_id,
                nextAgent=next_agent,
            )
        return PolicyDecision(
            subject="planning_progress",
            action="finish",
            allowed=True,
            reason="No Agent invocation or user decision is pending.",
            sessionId=session_id,
        )

    def authorize_calendar_write(
        self,
        *,
        session_id: str,
        planning_mode: str,
        strategy_artifact: ArtifactRef | None,
        execution_artifact: ArtifactRef | None,
        critic: CriticGateResult | None,
        approvals: Sequence[ApprovalRecord],
    ) -> PolicyDecision:
        required: tuple[PolicyGate, ...] = (
            "strategy_approval",
            "execution_approval",
            "critic",
            "calendar_approval",
        )
        failed: list[PolicyGate] = []
        if planning_mode != "model_backed":
            failed.append("runtime")

        strategy_approved = bool(
            strategy_artifact
            and strategy_artifact.session_id == session_id
            and strategy_artifact.kind == "strategy_portfolio"
            and _has_approval(
                approvals,
                session_id=session_id,
                gate="strategy",
                artifact=strategy_artifact,
            )
        )
        if not strategy_approved:
            failed.append("strategy_approval")

        execution_approved = bool(
            execution_artifact
            and execution_artifact.session_id == session_id
            and execution_artifact.kind == "execution_blueprint"
            and _has_approval(
                approvals,
                session_id=session_id,
                gate="execution",
                artifact=execution_artifact,
            )
        )
        if not execution_approved:
            failed.append("execution_approval")

        critic_passed = bool(
            critic
            and critic.passed
            and execution_artifact
            and critic.execution_artifact.same_version(execution_artifact)
            and critic.evaluated_execution_artifact.same_version(execution_artifact)
            and critic.critique_artifact.kind == "critique_report"
            and critic.critique_artifact.session_id == session_id
        )
        if not critic_passed:
            failed.append("critic")

        calendar_approved = bool(
            execution_artifact
            and execution_artifact.session_id == session_id
            and execution_artifact.kind == "execution_blueprint"
            and _has_approval(
                approvals,
                session_id=session_id,
                gate="calendar",
                artifact=execution_artifact,
            )
        )
        if not calendar_approved:
            failed.append("calendar_approval")

        if not failed:
            return PolicyDecision(
                subject="calendar_write",
                action="allow",
                allowed=True,
                reason="Strategy, execution, independent Critic, and Calendar approval gates all passed for the current artifact versions.",
                sessionId=session_id,
                requiredGates=required,
            )

        approval_order: tuple[tuple[PolicyGate, ApprovalGate], ...] = (
            ("strategy_approval", "strategy"),
            ("execution_approval", "execution"),
            ("calendar_approval", "calendar"),
        )
        required_approval = next((gate for policy_gate, gate in approval_order if policy_gate in failed), None)
        hard_failure = "runtime" in failed or "critic" in failed
        return PolicyDecision(
            subject="calendar_write",
            action="deny" if hard_failure else "wait_approval",
            allowed=False,
            reason=(
                "Calendar write is blocked because the runtime or independent Critic gate failed."
                if hard_failure
                else f"Calendar write is waiting for {required_approval} approval bound to the current artifact version."
            ),
            sessionId=session_id,
            requiredApproval=required_approval,
            requiredGates=required,
            failedGates=tuple(failed),
        )

    def authorize_memory_persistence(
        self,
        *,
        candidate: MemoryCandidate,
        evaluation: MemoryEvaluation | None,
    ) -> PolicyDecision:
        failures: list[str] = []
        if evaluation is None:
            failures.append("missing independent Memory Evaluation")
        else:
            if not evaluation.id.strip():
                failures.append("evaluation has no auditable id")
            if evaluation.evaluator_agent_id != "memory_evaluator":
                failures.append("evaluation was not produced by the Memory Evaluation Agent")
            if not evaluation.allowed:
                failures.append("Memory Evaluation rejected the candidate")
            if evaluation.session_id != candidate.session_id or evaluation.candidate_id != candidate.id:
                failures.append("evaluation is not bound to this session and candidate")
            if not evaluation.source_artifact or not evaluation.source_artifact.same_version(candidate.source_artifact):
                failures.append("evaluation is not bound to the source artifact version")
            if not (evaluation.durable_rule or "").strip():
                failures.append("evaluation contains no durable rule")
            if not (evaluation.evidence or "").strip():
                failures.append("evaluation contains no evidence")
        if candidate.source_artifact.kind != "planning_learning_update":
            failures.append("candidate source is not a Planning Learning artifact")
        if candidate.source_artifact.session_id != candidate.session_id:
            failures.append("candidate source artifact belongs to another session")

        if failures:
            return PolicyDecision(
                subject="memory_persistence",
                action="deny",
                allowed=False,
                reason="; ".join(failures),
                sessionId=candidate.session_id,
                requiredGates=("memory_evaluation",),
                failedGates=("memory_evaluation",),
            )
        return PolicyDecision(
            subject="memory_persistence",
            action="allow",
            allowed=True,
            reason="An independent, evidence-backed Memory Evaluation approved this candidate and source artifact version.",
            sessionId=candidate.session_id,
            requiredGates=("memory_evaluation",),
        )


__all__ = ["PolicyEngine"]
