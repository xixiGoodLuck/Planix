from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.harness.contracts import ArtifactRef, MemoryCandidate, MemoryEvaluation
from app.harness.controllers import CriticController, HumanApprovalController, MemoryController
from app.harness.policy import PolicyEngine
from app.harness.registry import MEMORY_EVALUATOR_CONTRACT
from app.harness.state import HarnessCheckpoint, PersistentCognitiveState


SESSION = "harness-session"


def _artifact(kind: str, *, version: int = 1, suffix: str = "") -> ArtifactRef:
    return ArtifactRef(
        id=f"{kind}-v{version}{suffix}",
        sessionId=SESSION,
        kind=kind,
        version=version,
        owner={
            "execution_blueprint": "execution",
            "critique_report": "critic",
            "planning_learning_update": "feedback",
        }.get(kind, "agent"),
        status="approved",
    )


def _approve_calendar(controller: HumanApprovalController, artifact: ArtifactRef) -> None:
    request = controller.request(session_id=SESSION, gate="calendar", artifact=artifact)
    controller.decide(request.id, approved=True)


def _passed_critic(execution: ArtifactRef):
    return CriticController().assess(
        report={"status": "passed", "score": 95, "calendarWritable": True, "issues": [], "repairRequests": []},
        critique_artifact=_artifact("critique_report", version=execution.version),
        execution_artifact=execution,
        evaluated_execution_artifact=execution,
    )


def test_policy_progress_never_turns_runtime_failure_into_a_user_question() -> None:
    policy = PolicyEngine()
    blocked = policy.decide_planning_progress(
        session_id=SESSION,
        runtime_blocked=True,
        blocking_unknowns=("irrelevant stale question",),
        next_agent="planner",
    )
    assert blocked.action == "block_runtime"
    assert blocked.failed_gates == ("runtime",)

    waiting = policy.decide_planning_progress(
        session_id=SESSION,
        runtime_blocked=False,
        blocking_unknowns=("Which outcome matters?",),
    )
    assert waiting.action == "wait_user"

    running = policy.decide_planning_progress(
        session_id=SESSION,
        runtime_blocked=False,
        next_agent="planner",
    )
    assert running.allowed is True
    assert running.next_agent == "planner"


def test_calendar_approval_is_bound_to_session_artifact_and_version() -> None:
    controller = HumanApprovalController()
    execution_v1 = _artifact("execution_blueprint")
    _approve_calendar(controller, execution_v1)
    assert controller.is_approved(session_id=SESSION, gate="calendar", artifact=execution_v1)
    assert not controller.is_approved(
        session_id=SESSION,
        gate="calendar",
        artifact=_artifact("execution_blueprint", version=2),
    )

    foreign = execution_v1.model_copy(update={"session_id": "other-session"})
    with pytest.raises(ValueError, match="another session"):
        controller.request(session_id=SESSION, gate="calendar", artifact=foreign)
    with pytest.raises(ValueError, match="must bind"):
        controller.request(session_id=SESSION, gate="calendar", artifact=_artifact("plan_blueprint"))


@pytest.mark.parametrize(
    "repaired_kind",
    [
        "understanding_snapshot",
        "constraint_set",
        "context_pack",
        "plan_blueprint",
        "plan_quality_report",
        "schedule_blueprint",
        "schedule_quality_report",
        "calendar_proposal",
        "execution_blueprint",
        "critique_report",
    ],
)
def test_any_formal_plan_repair_invalidates_calendar_approval(repaired_kind: str) -> None:
    controller = HumanApprovalController()
    execution = _artifact("execution_blueprint")
    _approve_calendar(controller, execution)
    invalidated = controller.invalidate_after_repair(
        session_id=SESSION,
        repaired_artifact=repaired_kind,
    )
    assert [record.gate for record in invalidated] == ["calendar"]
    assert not controller.is_approved(session_id=SESSION, gate="calendar", artifact=execution)


def test_critic_controller_returns_plan_repair_and_rejects_stale_review() -> None:
    controller = CriticController()
    execution_v1 = _artifact("execution_blueprint")
    repair = controller.assess(
        report={
            "status": "needs_repair",
            "calendarWritable": False,
            "issues": [{"severity": "major"}],
            "repairRequests": [{"targetAgent": "execution_designer"}],
        },
        critique_artifact=_artifact("critique_report"),
        execution_artifact=execution_v1,
        evaluated_execution_artifact=execution_v1,
    )
    assert controller.policy_decision(repair).repair_target == "execution_blueprint"

    stale = controller.assess(
        report={"status": "passed", "score": 95, "calendarWritable": True},
        critique_artifact=_artifact("critique_report", version=2),
        execution_artifact=_artifact("execution_blueprint", version=2),
        evaluated_execution_artifact=execution_v1,
    )
    assert stale.passed is False


def test_calendar_write_requires_current_critic_and_calendar_approval() -> None:
    execution = _artifact("execution_blueprint")
    approvals = HumanApprovalController()
    policy = PolicyEngine()
    waiting = policy.authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        execution_artifact=execution,
        critic=_passed_critic(execution),
        approvals=approvals.records,
    )
    assert waiting.required_approval == "calendar"
    assert waiting.required_gates == ("critic", "calendar_approval")

    _approve_calendar(approvals, execution)
    allowed = policy.authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        execution_artifact=execution,
        critic=_passed_critic(execution),
        approvals=approvals.records,
    )
    assert allowed.allowed is True


@pytest.mark.parametrize("permission", ["low", "medium", "high"])
def test_command_permission_cannot_bypass_calendar_approval(permission: str) -> None:
    execution = _artifact("execution_blueprint")
    decision = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        execution_artifact=execution,
        critic=_passed_critic(execution),
        approvals=[],
    )
    assert permission in {"low", "medium", "high"}
    assert decision.allowed is False
    assert decision.required_approval == "calendar"


def test_calendar_policy_fails_closed_for_runtime_critic_and_stale_versions() -> None:
    execution_v1 = _artifact("execution_blueprint")
    execution_v2 = _artifact("execution_blueprint", version=2)
    approvals = HumanApprovalController()
    _approve_calendar(approvals, execution_v1)

    stale = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        execution_artifact=execution_v2,
        critic=_passed_critic(execution_v2),
        approvals=approvals.records,
    )
    assert stale.failed_gates == ("calendar_approval",)

    blocked = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="blocked_model_unavailable",
        execution_artifact=execution_v1,
        critic=None,
        approvals=approvals.records,
    )
    assert blocked.action == "deny"
    assert {"runtime", "critic"}.issubset(blocked.failed_gates)


class _Repository:
    def __init__(self):
        self.calls = []

    def upsert(self, draft, *, positive=None):
        self.calls.append((draft, positive))
        return SimpleNamespace(id="memory-1")


class _Evaluator:
    def __init__(self, evaluation=None, error: Exception | None = None):
        self.evaluation = evaluation
        self.error = error

    def evaluate(self, _candidate):
        if self.error:
            raise self.error
        return self.evaluation


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        id="candidate-1",
        sessionId=SESSION,
        sourceArtifact=_artifact("planning_learning_update"),
        category="preference",
        statement="The user may prefer shorter tasks.",
        evidence="The user said this task is too hard.",
        domainScope=["python_career"],
        confidence=0.65,
    )


def _evaluation(candidate: MemoryCandidate, *, allowed: bool = True) -> MemoryEvaluation:
    return MemoryEvaluation(
        id="evaluation-1",
        sessionId=candidate.session_id,
        candidateId=candidate.id,
        sourceArtifact=candidate.source_artifact,
        evaluatorAgentId="memory_evaluator",
        allowed=allowed,
        reason="Useful beyond the current plan." if allowed else "This is plan-specific feedback.",
        durableRule="Prefer tasks with a smaller first action." if allowed else None,
        evidence=candidate.evidence if allowed else None,
        confidence=0.72 if allowed else 0.2,
    )


def test_memory_controller_requires_independent_bound_evaluation_before_write() -> None:
    candidate = _candidate()
    repository = _Repository()
    result = MemoryController(
        evaluator=_Evaluator(_evaluation(candidate)),
        repository=repository,
    ).evaluate_and_persist(candidate)
    assert result.persisted is True
    assert result.memory_id == "memory-1"
    assert len(repository.calls) == 1


def test_memory_controller_fails_closed_on_rejection_mismatch_or_evaluator_error() -> None:
    candidate = _candidate()
    cases = [
        _evaluation(candidate, allowed=False),
        _evaluation(candidate).model_copy(update={"candidate_id": "other-candidate"}),
        _evaluation(candidate).model_copy(update={"source_artifact": _artifact("planning_learning_update", version=2)}),
    ]
    for evaluation in cases:
        repository = _Repository()
        result = MemoryController(evaluator=_Evaluator(evaluation), repository=repository).evaluate_and_persist(candidate)
        assert result.persisted is False
        assert repository.calls == []

    repository = _Repository()
    failed = MemoryController(
        evaluator=_Evaluator(error=RuntimeError("model unavailable")),
        repository=repository,
    ).evaluate_and_persist(candidate)
    assert failed.persisted is False
    assert failed.evaluation is None
    assert repository.calls == []


def test_memory_evaluator_contract_and_checkpoint_are_artifact_first() -> None:
    assert MEMORY_EVALUATOR_CONTRACT.input_artifacts == ("planning_learning_update",)
    assert MEMORY_EVALUATOR_CONTRACT.output_artifact == "memory_evaluation"
    learning = _artifact("planning_learning_update")
    checkpoint = HarnessCheckpoint(
        artifactRefs={"planning_learning_update": learning},
        artifactVersions={"planning_learning_update": 1},
    )
    state = PersistentCognitiveState(sessionId=SESSION, checkpoint=checkpoint)
    payload = state.model_dump(by_alias=True)
    assert payload["checkpoint"]["artifactRefs"]["planning_learning_update"]["id"] == learning.id
    assert "artifactBodies" not in payload["checkpoint"]
