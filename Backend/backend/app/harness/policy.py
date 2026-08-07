from __future__ import annotations

from collections.abc import Sequence

from .contracts import (
    ApprovalGate,
    ApprovalRecord,
    ArtifactRef,
    MemoryCandidate,
    MemoryEvaluation,
    PolicyDecision,
    PolicyGate,
)


_APPROVAL_POLICY_GATES: dict[ApprovalGate, PolicyGate] = {
    "calendar": "calendar_permission",
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
                requiredGates=("understanding_confirmation",),
                failedGates=("understanding_confirmation",),
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
        final_approval: ArtifactRef | None,
        calendar_proposal: ArtifactRef | None,
        plan_quality_passed: bool,
        schedule_quality_passed: bool,
        approvals: Sequence[ApprovalRecord],
    ) -> PolicyDecision:
        required: tuple[PolicyGate, ...] = (
            "plan_quality",
            "schedule_quality",
            "final_approval",
            "calendar_permission",
        )
        failed: list[PolicyGate] = []
        if planning_mode != "model_backed":
            failed.append("runtime")

        if not plan_quality_passed:
            failed.append("plan_quality")
        if not schedule_quality_passed:
            failed.append("schedule_quality")
        if not final_approval or final_approval.session_id != session_id or final_approval.kind != "final_approval_bundle":
            failed.append("final_approval")
        if not calendar_proposal or calendar_proposal.session_id != session_id or calendar_proposal.kind != "calendar_proposal":
            failed.append("final_approval")

        calendar_approved = bool(
            final_approval
            and _has_approval(
                approvals,
                session_id=session_id,
                gate="calendar",
                artifact=final_approval,
            )
        )
        if not calendar_approved:
            failed.append("calendar_permission")

        if not failed:
            return PolicyDecision(
                subject="calendar_write",
                action="allow",
                allowed=True,
                reason="Plan quality, schedule quality, final approval, and Calendar permission passed for the current versions.",
                sessionId=session_id,
                requiredGates=required,
            )

        approval_order: tuple[tuple[PolicyGate, ApprovalGate], ...] = (
            ("calendar_permission", "calendar"),
        )
        required_approval = next((gate for policy_gate, gate in approval_order if policy_gate in failed), None)
        hard_failure = any(gate in failed for gate in ("runtime", "plan_quality", "schedule_quality", "final_approval"))
        return PolicyDecision(
            subject="calendar_write",
            action="deny" if hard_failure else "wait_approval",
            allowed=False,
            reason=(
                "Calendar write is blocked because a runtime, quality, or final-approval gate failed."
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
        if candidate.source_artifact.kind != "learning_observation":
            failures.append("candidate source is not a Learning Observation artifact")
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
