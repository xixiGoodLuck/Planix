from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.harness.contracts import (
    ArtifactRef,
    MemoryCandidate,
    MemoryEvaluation,
)
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
            "strategy_portfolio": "strategy",
            "execution_blueprint": "execution",
            "critique_report": "critic",
            "planning_learning_update": "feedback",
        }.get(kind, "agent"),
        status="approved",
    )


def _approved(
    controller: HumanApprovalController,
    gate: str,
    artifact: ArtifactRef,
) -> None:
    request = controller.request(session_id=SESSION, gate=gate, artifact=artifact)
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
        next_agent="strategy",
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
        next_agent="strategy",
    )
    assert running.allowed is True
    assert running.action == "invoke_agent"
    assert running.next_agent == "strategy"


def test_human_approvals_are_bound_to_session_artifact_and_version() -> None:
    controller = HumanApprovalController()
    strategy_v1 = _artifact("strategy_portfolio")
    request = controller.request(session_id=SESSION, gate="strategy", artifact=strategy_v1)
    controller.decide(request.id, approved=True)
    assert controller.is_approved(session_id=SESSION, gate="strategy", artifact=strategy_v1)
    assert not controller.is_approved(
        session_id=SESSION,
        gate="strategy",
        artifact=_artifact("strategy_portfolio", version=2),
    )

    foreign = strategy_v1.model_copy(update={"session_id": "other-session"})
    with pytest.raises(ValueError, match="another session"):
        controller.request(session_id=SESSION, gate="strategy", artifact=foreign)
    with pytest.raises(ValueError, match="must bind"):
        controller.request(session_id=SESSION, gate="calendar", artifact=strategy_v1)


def test_repairs_invalidate_the_changed_and_downstream_approvals() -> None:
    controller = HumanApprovalController()
    strategy = _artifact("strategy_portfolio")
    execution = _artifact("execution_blueprint")
    _approved(controller, "strategy", strategy)
    _approved(controller, "execution", execution)
    _approved(controller, "calendar", execution)

    invalidated = controller.invalidate_after_repair(
        session_id=SESSION,
        repaired_artifact="execution_blueprint",
    )
    assert {record.gate for record in invalidated} == {"execution", "calendar"}
    assert controller.is_approved(session_id=SESSION, gate="strategy", artifact=strategy)
    assert not controller.is_approved(session_id=SESSION, gate="execution", artifact=execution)
    assert not controller.is_approved(session_id=SESSION, gate="calendar", artifact=execution)

    controller.invalidate_after_repair(
        session_id=SESSION,
        repaired_artifact="strategy_portfolio",
    )
    assert not controller.is_approved(session_id=SESSION, gate="strategy", artifact=strategy)


@pytest.mark.parametrize(
    "upstream_kind",
    ["user_goal_model", "goal_completion", "reality_assessment", "evidence_pack"],
)
def test_upstream_repairs_invalidate_all_downstream_approvals(upstream_kind: str) -> None:
    controller = HumanApprovalController()
    strategy = _artifact("strategy_portfolio")
    execution = _artifact("execution_blueprint")
    _approved(controller, "strategy", strategy)
    _approved(controller, "execution", execution)
    _approved(controller, "calendar", execution)
    invalidated = controller.invalidate_after_repair(
        session_id=SESSION,
        repaired_artifact=upstream_kind,
    )
    assert {record.gate for record in invalidated} == {"strategy", "execution", "calendar"}


def test_critic_controller_returns_execution_repair_and_rejects_stale_review() -> None:
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
    decision = controller.policy_decision(repair)
    assert repair.passed is False
    assert decision.action == "repair_artifact"
    assert decision.repair_target == "execution_blueprint"

    stale = controller.assess(
        report={"status": "passed", "score": 95, "calendarWritable": True},
        critique_artifact=_artifact("critique_report", version=2),
        execution_artifact=_artifact("execution_blueprint", version=2),
        evaluated_execution_artifact=execution_v1,
    )
    assert stale.passed is False
    assert controller.policy_decision(stale).action == "deny"


@pytest.mark.parametrize(
    "report",
    [
        {
            "status": "passed",
            "score": 95,
            "calendarWritable": True,
            "issues": [{"severity": "major"}],
            "repairRequests": [],
        },
        {
            "status": "passed",
            "score": 95,
            "calendarWritable": True,
            "issues": [],
            "repairRequests": [{"targetAgent": "execution_designer"}],
        },
    ],
    ids=["major-issue", "repair-request"],
)
def test_critic_controller_rejects_inconsistent_passed_reports(report) -> None:
    controller = CriticController()
    execution = _artifact("execution_blueprint")

    gate = controller.assess(
        report=report,
        critique_artifact=_artifact("critique_report"),
        execution_artifact=execution,
        evaluated_execution_artifact=execution,
    )

    assert gate.passed is False
    assert controller.policy_decision(gate).allowed is False


def test_calendar_write_requires_strategy_execution_critic_and_calendar_gates() -> None:
    policy = PolicyEngine()
    approvals = HumanApprovalController()
    strategy = _artifact("strategy_portfolio")
    execution = _artifact("execution_blueprint")
    critic = _passed_critic(execution)
    _approved(approvals, "strategy", strategy)
    _approved(approvals, "execution", execution)

    waiting = policy.authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        strategy_artifact=strategy,
        execution_artifact=execution,
        critic=critic,
        approvals=approvals.records,
    )
    assert waiting.allowed is False
    assert waiting.action == "wait_approval"
    assert waiting.required_approval == "calendar"
    assert waiting.required_gates == (
        "strategy_approval",
        "execution_approval",
        "critic",
        "calendar_approval",
    )

    _approved(approvals, "calendar", execution)
    allowed = policy.authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        strategy_artifact=strategy,
        execution_artifact=execution,
        critic=critic,
        approvals=approvals.records,
    )
    assert allowed.allowed is True
    assert allowed.action == "allow"


@pytest.mark.parametrize("permission", ["low", "medium", "high"])
def test_calendar_approval_cannot_be_bypassed_by_command_permission(permission: str) -> None:
    # Command permission is deliberately not an input to Harness policy.
    strategy = _artifact("strategy_portfolio")
    execution = _artifact("execution_blueprint")
    approvals = HumanApprovalController()
    _approved(approvals, "strategy", strategy)
    _approved(approvals, "execution", execution)
    decision = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        strategy_artifact=strategy,
        execution_artifact=execution,
        critic=_passed_critic(execution),
        approvals=approvals.records,
    )
    assert permission in {"low", "medium", "high"}
    assert decision.allowed is False
    assert decision.required_approval == "calendar"


def test_calendar_policy_fails_closed_for_runtime_critic_and_stale_versions() -> None:
    strategy = _artifact("strategy_portfolio")
    execution_v1 = _artifact("execution_blueprint")
    execution_v2 = _artifact("execution_blueprint", version=2)
    approvals = HumanApprovalController()
    _approved(approvals, "strategy", strategy)
    _approved(approvals, "execution", execution_v1)
    _approved(approvals, "calendar", execution_v1)

    stale = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="model_backed",
        strategy_artifact=strategy,
        execution_artifact=execution_v2,
        critic=_passed_critic(execution_v2),
        approvals=approvals.records,
    )
    assert {"execution_approval", "calendar_approval"}.issubset(stale.failed_gates)

    blocked = PolicyEngine().authorize_calendar_write(
        session_id=SESSION,
        planning_mode="blocked_model_unavailable",
        strategy_artifact=strategy,
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
        self.calls = 0

    def evaluate(self, _candidate):
        self.calls += 1
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
    assert result.policy_decision.allowed is True
    assert len(repository.calls) == 1
    draft, positive = repository.calls[0]
    assert draft.statement == "Prefer tasks with a smaller first action."
    assert draft.evidence == candidate.evidence
    assert positive is True


def test_memory_controller_fails_closed_on_rejection_mismatch_or_evaluator_error() -> None:
    candidate = _candidate()
    cases = [
        _evaluation(candidate, allowed=False),
        _evaluation(candidate).model_copy(update={"candidate_id": "other-candidate"}),
        _evaluation(candidate).model_copy(update={"source_artifact": _artifact("planning_learning_update", version=2)}),
    ]
    for evaluation in cases:
        repository = _Repository()
        result = MemoryController(
            evaluator=_Evaluator(evaluation),
            repository=repository,
        ).evaluate_and_persist(candidate)
        assert result.persisted is False
        assert result.policy_decision.allowed is False
        assert repository.calls == []

    repository = _Repository()
    failed = MemoryController(
        evaluator=_Evaluator(error=RuntimeError("model unavailable")),
        repository=repository,
    ).evaluate_and_persist(candidate)
    assert failed.persisted is False
    assert failed.evaluation is None
    assert failed.error == "model unavailable"
    assert repository.calls == []


def test_memory_evaluator_contract_and_checkpoint_are_artifact_first() -> None:
    assert MEMORY_EVALUATOR_CONTRACT.input_artifacts == ("planning_learning_update",)
    assert MEMORY_EVALUATOR_CONTRACT.output_artifact == "memory_evaluation"
    assert "evaluate_memory" in MEMORY_EVALUATOR_CONTRACT.permissions

    learning = _artifact("planning_learning_update")
    checkpoint = HarnessCheckpoint(
        artifactRefs={"planning_learning_update": learning},
        artifactVersions={"planning_learning_update": 1},
    )
    state = PersistentCognitiveState(sessionId=SESSION, checkpoint=checkpoint)
    payload = state.model_dump(by_alias=True)
    assert payload["checkpoint"]["artifactRefs"]["planning_learning_update"]["id"] == learning.id
    assert "artifactBodies" not in payload["checkpoint"]
    assert "content" not in payload["checkpoint"]
