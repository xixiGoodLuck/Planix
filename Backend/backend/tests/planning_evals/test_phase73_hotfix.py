from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from fastapi import HTTPException

from app.cognitive_planning import CognitiveOSRuntime
from app.cognitive_planning.agents import CognitiveModelClient, PlanningModelUnavailable
from app.db import get_conn
from app.harness.persistence import HarnessStateRepository
from app.schemas import CreatePlanningSessionRequest, PlanningSessionTextRequest
from app.services import model_provider
from app.services.ai_settings import EffectiveAiSettings, ModelRoutingRuleConfig
from app.services.cognitive_planning.contracts import (
    Constraint,
    DecisionRelevantUnknown,
    EvidencePack,
    FeasibilityJudgment,
    GoalQuestion,
    GoalSuccessModel,
    KnownFact,
    SafePlanningError,
    StrategyPortfolio,
    UserGoalModel,
)
from app.services.llm import LlmClient
from app.services.model_provider import ModelCallError, ModelCallResult

from .test_cognitive_kernel import StubCognitiveModel


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase73.db'}")
    return tmp_path


class SemanticGoProgressionModel(StubCognitiveModel):
    def __init__(self, *, fail_goal: bool = False, fail_strategy: bool = False):
        super().__init__()
        self.fail_goal = fail_goal
        self.fail_strategy = fail_strategy
        self.attempts_by_task: Counter[str] = Counter()

    def complete_contract(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        contract_type,
        stage: str = "",
        **kwargs: Any,
    ):
        self.attempts_by_task[task_type] += 1
        if contract_type is UserGoalModel and self.fail_goal:
            raise PlanningModelUnavailable(
                stage,
                SafePlanningError(
                    stage=stage,
                    errorType="auth_error",
                    message="The goal model credentials are unavailable.",
                    retryable=False,
                    attempts=[
                        {
                            "provider": "deepseek",
                            "model": "goal-test",
                            "status": "error",
                            "errorType": "auth_error",
                            "latencyMs": 2,
                        }
                    ],
                ),
            )
        if contract_type is StrategyPortfolio and self.fail_strategy:
            raise PlanningModelUnavailable(
                stage,
                SafePlanningError(
                    stage=stage,
                    errorType="auth_error",
                    message="The strategy model credentials are unavailable.",
                    retryable=False,
                    attempts=[
                        {
                            "provider": "deepseek",
                            "model": "strategy-test",
                            "status": "error",
                            "errorType": "auth_error",
                            "latencyMs": 3,
                        }
                    ],
                ),
            )
        return super().complete_contract(
            task_type=task_type,
            payload=payload,
            contract_type=contract_type,
            stage=stage,
            **kwargs,
        )

    @staticmethod
    def _user_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            str(item.get("content") or "")
            for item in payload.get("conversationHistory", [])
            if item.get("role") == "user"
        )

    def _goal(self, payload: dict[str, Any]) -> UserGoalModel:
        text = self._user_text(payload)
        lowered = text.lower()
        has_web_goal = "web开发" in lowered or "web 开发" in lowered
        has_python_background = "学过python" in lowered or "学过 python" in lowered
        has_web_background = "做过web开发" in lowered or "做过 web 开发" in lowered
        has_outcomes = "找工作" in text and "个人项目" in text
        has_weekly_time = "每周20小时" in text or "每周 20 小时" in text

        blocker: DecisionRelevantUnknown | None = None
        question: GoalQuestion
        if not has_web_goal:
            blocker = DecisionRelevantUnknown(
                key="target_use",
                description="Go 最终用于哪类开发结果",
                whyItChangesThePlan="目标用途会改变技术重点和成功标准。",
                impact="strategy",
                priority="blocking",
            )
            question = GoalQuestion(
                question="你希望 Go 最终用于哪类开发结果？",
                whyThisQuestionMatters="用途会改变技术重点和成功标准。",
                expectedDecisionImpact="strategy",
            )
        elif not has_outcomes:
            blocker = DecisionRelevantUnknown(
                key="desired_outcomes",
                description="Web 开发能力最终服务于求职、工作还是个人产出",
                whyItChangesThePlan="预期结果会改变项目深度和验收证据。",
                impact="success_criteria",
                priority="blocking",
            )
            question = GoalQuestion(
                question="你希望这项能力最终服务于什么结果？",
                whyThisQuestionMatters="预期结果会改变项目深度和验收证据。",
                expectedDecisionImpact="success criteria",
            )
        elif not has_weekly_time:
            blocker = DecisionRelevantUnknown(
                key="weekly_capacity",
                description="每周可稳定投入的时间",
                whyItChangesThePlan="可用时间决定首个项目的现实范围。",
                impact="feasibility",
                priority="blocking",
            )
            question = GoalQuestion(
                question="你每周能稳定投入多少时间？",
                whyThisQuestionMatters="可用时间决定首个项目的现实范围。",
                expectedDecisionImpact="scope and feasibility",
            )
        else:
            question = GoalQuestion(
                question="你希望何时完成第一个可展示版本？",
                whyThisQuestionMatters="期限可以优化节奏，但不会阻止当前策略设计。",
                expectedDecisionImpact="schedule refinement",
            )

        known_facts = [
            KnownFact(
                key="goal",
                statement="学习 Go 并用于 Web 开发" if has_web_goal else "学习 Go 语言",
                sourceText=text,
                confidence=1,
            )
        ]
        current_knowledge: list[str] = []
        if has_python_background:
            known_facts.append(
                KnownFact(key="python_background", statement="有 Python 经验", sourceText=text, confidence=1)
            )
            current_knowledge.append("有 Python 经验")
        if has_web_background:
            known_facts.append(
                KnownFact(key="web_background", statement="有 Web 开发经验", sourceText=text, confidence=1)
            )
            current_knowledge.append("有 Web 开发经验")
        if has_outcomes:
            known_facts.append(
                KnownFact(
                    key="purpose",
                    statement="用于找工作和个人项目",
                    sourceText=text,
                    confidence=1,
                )
            )
        if has_weekly_time:
            known_facts.append(
                KnownFact(key="weekly_time", statement="每周可投入 20 小时", sourceText=text, confidence=1)
            )

        unknowns = [blocker] if blocker else [
            DecisionRelevantUnknown(
                key="deadline",
                description="第一个可展示版本的期限",
                whyItChangesThePlan="期限只调整节奏，不改变已明确的策略方向。",
                impact="schedule",
                priority="optional",
            )
        ]
        return UserGoalModel(
            goalStatement="Go Web development" if has_web_goal else "Learn the Go language",
            desiredChange="Build employable Go Web development capability through personal projects",
            domain="go_web_development",
            possibleIntents=["找工作", "个人项目"] if has_outcomes else ["Web 开发"],
            currentKnowledge=current_knowledge,
            uncertainties=[item.description for item in unknowns],
            userLanguage=[item for item in text.splitlines() if item],
            hardConstraints=(
                [Constraint(statement="每周 20 小时", sourceText="每周20小时", category="time")]
                if has_weekly_time
                else []
            ),
            knownFacts=known_facts,
            decisionRelevantUnknowns=unknowns,
            successModel=GoalSuccessModel(
                definition="Can build and explain a reviewable Go Web service for job search and personal projects.",
                measurableSignals=["A working service passes integration checks."],
                intermediateMilestones=["The first API endpoint is reviewable."],
            ),
            feasibilityJudgment=FeasibilityJudgment(
                summary="Feasible with the stated background and weekly time budget."
            ),
            questions=[question],
            confidence=0.94 if blocker is None else 0.7,
            # Intentionally stale: the independent completion judge must own progression.
            canProceedToEvidence=False,
        )


class CriticalGoalProgressionModel(SemanticGoProgressionModel):
    def __init__(self, critical_mode: str):
        super().__init__()
        self.critical_mode = critical_mode

    def _goal(self, payload: dict[str, Any]) -> UserGoalModel:
        goal = super()._goal(payload)
        if self.critical_mode == "consistency":
            return goal.model_copy(
                update={"consistency_warnings": ["The requested outcome conflicts with the saved goal."]}
            )
        unknowns = [
            item.model_copy(update={"impact": self.critical_mode})
            if item.priority == "blocking"
            else item
            for item in goal.decision_relevant_unknowns
        ]
        return goal.model_copy(update={"decision_relevant_unknowns": unknowns})


class EvidenceTruncationRecoveryModel(SemanticGoProgressionModel):
    """Blocks once at Evidence, then delegates recovery to the real routed client."""

    def __init__(self):
        super().__init__()
        self.block_evidence = True
        self.routed_evidence_client: CognitiveModelClient | None = None
        self.routed_evidence_json = ""

    def complete_contract(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        contract_type,
        stage: str = "",
        **kwargs: Any,
    ):
        if contract_type is not EvidencePack:
            return super().complete_contract(
                task_type=task_type,
                payload=payload,
                contract_type=contract_type,
                stage=stage,
                **kwargs,
            )

        self.attempts_by_task[task_type] += 1
        if self.block_evidence:
            raise PlanningModelUnavailable(
                stage,
                SafePlanningError(
                    stage=stage,
                    errorType="model_output_truncated",
                    message="The Evidence output was truncated twice.",
                    retryable=True,
                    attempts=[
                        {
                            "provider": "deepseek",
                            "model": "deepseek-test",
                            "status": "error",
                            "errorType": "model_output_truncated",
                            "latencyMs": 2,
                        },
                        {
                            "provider": "deepseek",
                            "model": "deepseek-test",
                            "status": "error",
                            "errorType": "model_output_truncated",
                            "latencyMs": 3,
                            "automaticRetry": True,
                        },
                    ],
                ),
            )

        assert self.routed_evidence_client is not None
        self.routed_evidence_json = self._evidence(payload).model_dump_json(by_alias=True)
        return self.routed_evidence_client.complete_contract(
            task_type=task_type,
            payload=payload,
            contract_type=contract_type,
            stage=stage,
            **kwargs,
        )


def _full_go_request() -> str:
    return "\n".join(
        [
            "我要学go语言",
            "为了web开发",
            "学过python也做过web开发",
            "找工作和个人项目",
            "每周20小时",
        ]
    )


def _artifact_ids(session, artifact_type: str) -> list[str]:
    return [item.id for item in session.artifacts if item.artifact_type == artifact_type]


def test_phase73_multiturn_go_goal_completes_semantically_and_advances_to_strategy(isolated_db) -> None:
    model = SemanticGoProgressionModel()
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-go-progression",
            userInput="我要学go语言",
        )
    )
    assert session.goal_completion and session.goal_completion["complete"] is False

    followups = [
        "为了web开发",
        "学过python也做过web开发",
        "找工作和个人项目",
        "每周20小时",
    ]
    for index, text in enumerate(followups):
        session = runtime.clarify(session.session_id, PlanningSessionTextRequest(text=text))
        if index < len(followups) - 1:
            assert session.goal_completion and session.goal_completion["complete"] is False
            assert session.status == "needs_goal_clarification"

    assert session.status == "waiting_design_approval"
    assert session.business_status == "strategy_pending"
    assert session.runtime_status == "idle"
    assert session.goal_completion == {
        "complete": True,
        "blockingUnknowns": [],
        "optionalUnknowns": [
            "第一个可展示版本的期限",
            "你希望何时完成第一个可展示版本？",
        ],
        "nextStage": "strategy",
    }
    assert session.pending_question is None
    assert session.strategy_portfolio is not None
    assert model.attempts_by_task["planning_goal_model"] == 5

    goal_text = json.dumps(session.goal_model, ensure_ascii=False)
    assert "Go Web development" in goal_text
    assert "Python 经验" in goal_text
    assert "Web 开发经验" in goal_text
    assert "找工作和个人项目" in goal_text
    assert "每周可投入 20 小时" in goal_text
    assert session.user_input.splitlines() == ["我要学go语言", *followups]


def test_phase73_strategy_auth_failure_preserves_state_and_resumes_only_strategy(isolated_db) -> None:
    model = SemanticGoProgressionModel(fail_strategy=True)
    runtime = CognitiveOSRuntime(model_client=model)

    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-strategy-recovery",
            userInput=_full_go_request(),
        )
    )

    assert blocked.status == "MODEL_UNAVAILABLE"
    assert blocked.business_status == "strategy_pending"
    assert blocked.runtime_status == "blocked_model"
    assert blocked.goal_completion and blocked.goal_completion["complete"] is True
    assert blocked.goal_completion["nextStage"] == "strategy"
    assert blocked.pending_question is None
    assert blocked.cognitive_metadata
    assert blocked.cognitive_metadata.planning_mode == "blocked_model_unavailable"
    assert blocked.model_failure
    assert blocked.model_failure.resume_node == "strategy"
    assert blocked.pending_input is None
    assert blocked.strategy_portfolio is None
    assert blocked.execution_blueprint is None
    assert blocked.critique_report is None
    assert blocked.design_proposal is None
    assert not any(item.agent == "Strategy Agent" for item in blocked.decisions)

    stable_types = ("user_goal_model", "goal_completion", "reality_assessment", "evidence_pack")
    stable_artifacts = {kind: _artifact_ids(blocked, kind) for kind in stable_types}
    assert all(stable_artifacts.values())
    assert _artifact_ids(blocked, "strategy_portfolio") == []
    counts_before = {
        task: model.attempts_by_task[task]
        for task in ("planning_goal_model", "planning_reality", "planning_evidence")
    }
    assert model.attempts_by_task["planning_strategy"] == 1
    blocked_harness = HarnessStateRepository().recover(blocked.session_id)
    assert blocked_harness.pending_agent == "strategy"
    assert blocked_harness.waiting_state == "model_recovery"
    assert blocked_harness.artifact_versions["evidence_pack"] == 1

    model.fail_strategy = False
    # A new runtime instance must restore the exact pending Agent/checkpoint.
    recovered_runtime = CognitiveOSRuntime(model_client=model)
    recovered = recovered_runtime.continue_current_stage(blocked.session_id)

    assert recovered.session_id == blocked.session_id
    assert recovered.status == "waiting_design_approval"
    assert recovered.business_status == "strategy_pending"
    assert recovered.runtime_status == "idle"
    assert recovered.cognitive_metadata
    assert recovered.cognitive_metadata.planning_mode == "model_backed"
    assert recovered.goal_completion == blocked.goal_completion
    assert recovered.strategy_portfolio is not None
    assert recovered.execution_blueprint is None
    assert model.attempts_by_task["planning_strategy"] == 2
    assert {
        task: model.attempts_by_task[task]
        for task in ("planning_goal_model", "planning_reality", "planning_evidence")
    } == counts_before
    for kind, ids in stable_artifacts.items():
        assert _artifact_ids(recovered, kind) == ids
    assert len(_artifact_ids(recovered, "strategy_portfolio")) == 1
    assert not any(
        item.agent == "Strategy Agent" and item.decision == "block"
        for item in recovered.decisions
    )
    resumed_harness = HarnessStateRepository().recover(blocked.session_id)
    assert resumed_harness.pending_agent is None
    assert resumed_harness.waiting_state == "strategy_approval"
    assert resumed_harness.artifact_versions["strategy_portfolio"] == 1
    recovery_events = [
        item
        for item in HarnessStateRepository().events(blocked.session_id)
        if item.event_type == "recovery_action"
    ]
    assert any(item.decision == "checkpoint_resume" for item in recovery_events)


def test_phase73_evidence_double_truncation_preserves_artifacts_then_retry_control_auto_recovers(
    isolated_db,
    monkeypatch,
) -> None:
    model = EvidenceTruncationRecoveryModel()
    runtime = CognitiveOSRuntime(model_client=model)

    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-evidence-truncation-recovery",
            userInput=_full_go_request(),
        )
    )

    assert blocked.status == "MODEL_UNAVAILABLE"
    assert blocked.business_status == "evidence_pending"
    assert blocked.runtime_status == "blocked_model"
    assert blocked.model_failure
    assert blocked.model_failure.stage == "evidence_synthesis"
    assert blocked.model_failure.resume_node == "evidence"
    assert blocked.model_failure.retryable is True
    assert blocked.model_failure.automatic_retry_attempted is True
    assert [
        (item.provider, item.status, item.error_type)
        for item in blocked.model_failure.attempts
    ] == [
        ("deepseek", "error", "model_output_truncated"),
        ("deepseek", "error", "model_output_truncated"),
    ]
    assert blocked.pending_input is None
    assert blocked.goal_model is not None
    assert blocked.goal_completion and blocked.goal_completion["complete"] is True
    assert blocked.reality_assessment is not None
    assert blocked.evidence_pack is None
    assert blocked.strategy_portfolio is None
    assert blocked.execution_blueprint is None

    stable_types = ("user_goal_model", "goal_completion", "reality_assessment")
    stable_artifacts = {
        kind: [(item.id, item.version) for item in blocked.artifacts if item.artifact_type == kind]
        for kind in stable_types
    }
    assert all(stable_artifacts.values())
    assert _artifact_ids(blocked, "evidence_pack") == []
    assert _artifact_ids(blocked, "strategy_portfolio") == []
    calls_before = Counter(model.attempts_by_task)
    assert calls_before["planning_goal_model"] == 1
    assert calls_before["planning_reality"] == 1
    assert calls_before["planning_evidence"] == 1
    assert calls_before["planning_strategy"] == 0

    blocked_harness = HarnessStateRepository().recover(blocked.session_id)
    assert blocked_harness.pending_agent == "evidence"
    assert blocked_harness.waiting_state == "model_recovery"
    assert blocked_harness.artifact_versions["user_goal_model"] == 1
    assert blocked_harness.artifact_versions["goal_completion"] == 1
    assert blocked_harness.artifact_versions["reality_assessment"] == 1
    assert "evidence_pack" not in blocked_harness.artifact_versions
    assert "strategy_portfolio" not in blocked_harness.artifact_versions

    with get_conn() as conn:
        row_before = conn.execute(
            "SELECT user_input, conversation_history_json FROM planning_sessions WHERE id = ?",
            (blocked.session_id,),
        ).fetchone()
    assert row_before is not None

    routed_settings = EffectiveAiSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-test",
        api_key="sk-test-local",
        temperature=0.2,
        timeout_seconds=10,
        updated_at="",
    )
    provider_calls: list[tuple[str, int, int]] = []

    def fake_rule(task_type: str, _active_provider: str) -> ModelRoutingRuleConfig:
        return ModelRoutingRuleConfig(task_type, "deepseek", (), False)

    def fake_settings(
        provider: str,
        _active_settings: EffectiveAiSettings | None = None,
    ) -> EffectiveAiSettings:
        assert provider == "deepseek"
        return routed_settings

    def fake_complete(self, request):
        provider_calls.append((self.settings.provider, request.max_tokens, request.max_token_cap))
        if len(provider_calls) == 1:
            return (
                None,
                ModelCallError(
                    "truncated",
                    "model_output_truncated",
                    detail="raw provider detail must remain private",
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (
            ModelCallResult(
                text=model.routed_evidence_json,
                provider=self.settings.provider,
                model=self.settings.model,
                latency_ms=4,
            ),
            None,
        )

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)
    llm = LlmClient()
    llm.settings = routed_settings
    model.routed_evidence_client = CognitiveModelClient(llm=llm)
    model.block_evidence = False

    recovered = runtime.clarify(
        blocked.session_id,
        PlanningSessionTextRequest(text="请重试当前深度规划"),
    )

    assert recovered.session_id == blocked.session_id
    assert recovered.status == "waiting_design_approval"
    assert recovered.business_status == "strategy_pending"
    assert recovered.runtime_status == "idle"
    assert recovered.model_failure is None
    assert recovered.pending_input is None
    assert recovered.evidence_pack is not None
    assert recovered.strategy_portfolio is not None
    assert recovered.execution_blueprint is None
    assert recovered.critique_report is None
    assert provider_calls == [
        ("deepseek", 6600, 13200),
        ("deepseek", 13200, 13200),
    ]

    for kind, refs in stable_artifacts.items():
        assert [
            (item.id, item.version)
            for item in recovered.artifacts
            if item.artifact_type == kind
        ] == refs
    assert len(_artifact_ids(recovered, "evidence_pack")) == 1
    assert len(_artifact_ids(recovered, "strategy_portfolio")) == 1
    assert model.attempts_by_task["planning_goal_model"] == calls_before["planning_goal_model"]
    assert model.attempts_by_task["planning_reality"] == calls_before["planning_reality"]
    assert model.attempts_by_task["planning_evidence"] == calls_before["planning_evidence"] + 1
    assert model.attempts_by_task["planning_strategy"] == 1

    evidence_decision = next(
        item
        for item in reversed(recovered.decisions)
        if item.agent == "Evidence Agent" and item.model_usage is not None
    )
    assert evidence_decision.model_usage.task_type == "planning_evidence"
    assert [
        (item.provider, item.status, item.error_type, bool(item.automatic_retry))
        for item in evidence_decision.model_usage.attempts
    ] == [
        ("deepseek", "error", "model_output_truncated", False),
        ("deepseek", "success", None, True),
    ]
    evidence_usage = evidence_decision.model_usage.model_dump(by_alias=True, exclude_none=True)
    assert "detail" not in json.dumps(evidence_usage)
    assert "raw provider detail" not in json.dumps(evidence_usage)

    with get_conn() as conn:
        row_after = conn.execute(
            "SELECT user_input, conversation_history_json FROM planning_sessions WHERE id = ?",
            (blocked.session_id,),
        ).fetchone()
    assert row_after is not None
    assert row_after["user_input"] == row_before["user_input"]
    assert row_after["conversation_history_json"] == row_before["conversation_history_json"]
    assert "请重试当前深度规划" not in row_after["user_input"]
    assert "请重试当前深度规划" not in row_after["conversation_history_json"]

    resumed_harness = HarnessStateRepository().recover(blocked.session_id)
    assert resumed_harness.pending_agent is None
    assert resumed_harness.waiting_state == "strategy_approval"
    assert resumed_harness.artifact_versions["user_goal_model"] == 1
    assert resumed_harness.artifact_versions["goal_completion"] == 1
    assert resumed_harness.artifact_versions["reality_assessment"] == 1
    assert resumed_harness.artifact_versions["evidence_pack"] == 1
    assert resumed_harness.artifact_versions["strategy_portfolio"] == 1


def test_phase73_goal_auth_failure_after_new_information_retries_goal_stage(isolated_db) -> None:
    model = SemanticGoProgressionModel()
    runtime = CognitiveOSRuntime(model_client=model)
    initial = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-goal-recovery",
            userInput="Learn Go",
        )
    )
    assert initial.goal_completion and initial.goal_completion["complete"] is False
    initial_goal = initial.goal_model
    initial_completion = initial.goal_completion

    model.fail_goal = True
    blocked = runtime.clarify(
        initial.session_id,
        PlanningSessionTextRequest(text="\u4e3a\u4e86web\u5f00\u53d1"),
    )

    assert blocked.status == "MODEL_UNAVAILABLE"
    assert blocked.business_status == "goal_clarification"
    assert blocked.runtime_status == "blocked_model"
    assert blocked.goal_model == initial_goal
    assert blocked.goal_completion == initial_completion
    assert blocked.cognitive_metadata
    assert blocked.cognitive_metadata.current_stage == "goal_intelligence"
    assert blocked.model_failure
    assert blocked.model_failure.stage == "goal_intelligence"
    assert blocked.model_failure.resume_node == "goal_intelligence"
    assert blocked.model_failure.retryable is True
    assert blocked.model_failure.attempts[0].model_dump(by_alias=True, exclude_none=True) == {
        "provider": "deepseek",
        "status": "error",
        "errorType": "auth_error",
    }
    failure_payload = blocked.model_failure.model_dump(by_alias=True, exclude_none=True)
    assert "model" not in failure_payload["attempts"][0]
    assert "credentials are unavailable" not in json.dumps(failure_payload)
    assert blocked.pending_input
    assert blocked.pending_input.model_dump(by_alias=True) == {
        "text": "为了web开发",
        "applied": False,
    }
    assert model.attempts_by_task["planning_goal_model"] == 2
    assert model.attempts_by_task["planning_strategy"] == 0

    model.fail_goal = False
    recovered = runtime.continue_current_stage(blocked.session_id)

    assert recovered.status == "needs_goal_clarification"
    assert recovered.business_status == "goal_clarification"
    assert recovered.runtime_status == "idle"
    assert recovered.goal_completion and recovered.goal_completion["complete"] is False
    assert recovered.goal_completion != initial_completion
    assert recovered.goal_model and recovered.goal_model != initial_goal
    assert recovered.goal_model["goalStatement"] == "Go Web development"
    assert recovered.goal_model["decisionRelevantUnknowns"][0]["key"] == "desired_outcomes"
    assert recovered.model_failure is None
    assert recovered.pending_input is None
    assert model.attempts_by_task["planning_goal_model"] == 3
    assert model.attempts_by_task["planning_reality"] == 0
    assert model.attempts_by_task["planning_evidence"] == 0
    assert model.attempts_by_task["planning_strategy"] == 0


def test_phase73_retry_deep_planning_control_resumes_without_entering_goal_history(isolated_db) -> None:
    model = SemanticGoProgressionModel()
    runtime = CognitiveOSRuntime(model_client=model)
    initial = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-retry-control",
            userInput="Learn Go",
        )
    )
    model.fail_goal = True
    blocked = runtime.clarify(
        initial.session_id,
        PlanningSessionTextRequest(text="为了web开发"),
    )
    input_before = blocked.user_input
    with get_conn() as conn:
        history_before = conn.execute(
            "SELECT conversation_history_json FROM planning_sessions WHERE id = ?",
            (blocked.session_id,),
        ).fetchone()["conversation_history_json"]

    model.fail_goal = False
    recovered = runtime.clarify(
        blocked.session_id,
        PlanningSessionTextRequest(text="请重试当前深度规划"),
    )

    assert recovered.session_id == blocked.session_id
    assert recovered.user_input == input_before
    assert "请重试当前深度规划" not in recovered.user_input
    with get_conn() as conn:
        history_after = conn.execute(
            "SELECT conversation_history_json FROM planning_sessions WHERE id = ?",
            (blocked.session_id,),
        ).fetchone()["conversation_history_json"]
    user_turns_before = [
        item for item in json.loads(history_before) if item.get("role") == "user"
    ]
    user_turns_after = [
        item for item in json.loads(history_after) if item.get("role") == "user"
    ]
    assert user_turns_after == user_turns_before
    assert "请重试当前深度规划" not in history_after
    assert recovered.goal_model and recovered.goal_model["goalStatement"] == "Go Web development"


def test_phase73_next_step_control_does_not_become_goal_evidence(isolated_db) -> None:
    model = SemanticGoProgressionModel()
    runtime = CognitiveOSRuntime(model_client=model)
    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-control-next",
            userInput="我要学go语言",
        )
    )
    calls_before = Counter(model.attempts_by_task)
    artifacts_before = [(item.id, item.artifact_type, item.version) for item in blocked.artifacts]
    input_before = blocked.user_input

    unchanged = runtime.clarify(
        blocked.session_id,
        PlanningSessionTextRequest(text="下一步"),
    )

    assert unchanged.session_id == blocked.session_id
    assert unchanged.status == "needs_goal_clarification"
    assert unchanged.business_status == "goal_clarification"
    assert unchanged.runtime_status == "idle"
    assert unchanged.user_input == input_before
    assert "下一步" not in unchanged.user_input
    assert Counter(model.attempts_by_task) == calls_before
    assert [(item.id, item.artifact_type, item.version) for item in unchanged.artifacts] == artifacts_before
    assert unchanged.goal_completion == blocked.goal_completion
    assert unchanged.pending_question == blocked.pending_question


def test_phase73_skip_goal_clarification_uses_saved_context_and_advances(isolated_db) -> None:
    model = SemanticGoProgressionModel()
    runtime = CognitiveOSRuntime(model_client=model)
    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase73-control-skip",
            userInput="Learn Go",
        )
    )
    assert blocked.goal_completion and blocked.goal_completion["complete"] is False
    calls_before = Counter(model.attempts_by_task)
    input_before = blocked.user_input
    goal_artifacts_before = _artifact_ids(blocked, "user_goal_model")
    completion_artifacts_before = _artifact_ids(blocked, "goal_completion")

    advanced = runtime.clarify(
        blocked.session_id,
        PlanningSessionTextRequest(
            text="Skip this step and continue with the information already provided"
        ),
    )

    assert advanced.session_id == blocked.session_id
    assert advanced.status == "waiting_design_approval"
    assert advanced.business_status == "strategy_pending"
    assert advanced.runtime_status == "idle"
    assert advanced.user_input == input_before
    assert "Skip this step" not in advanced.user_input
    assert advanced.goal_completion
    assert advanced.goal_completion["complete"] is True
    assert advanced.goal_completion["blockingUnknowns"] == []
    assert advanced.goal_completion["nextStage"] == "strategy"
    skipped_description = blocked.goal_model["decisionRelevantUnknowns"][0]["description"]
    assert skipped_description in advanced.goal_completion["optionalUnknowns"]
    assert all(
        item["priority"] != "blocking"
        for item in advanced.goal_model["decisionRelevantUnknowns"]
    )
    assert skipped_description in {
        item["statement"] for item in advanced.goal_model["assumptions"]
    }
    assert advanced.goal_model["uncertainties"] == []
    assert _artifact_ids(advanced, "user_goal_model") == goal_artifacts_before
    assert len(_artifact_ids(advanced, "goal_completion")) == len(completion_artifacts_before) + 1
    assert model.attempts_by_task["planning_goal_model"] == calls_before["planning_goal_model"]
    assert model.attempts_by_task["planning_reality"] == 1
    assert model.attempts_by_task["planning_evidence"] == 1
    assert model.attempts_by_task["planning_strategy"] == 1
    assert any(
        item.agent == "Goal Completion Judge"
        and item.decision == "approve"
        and "explicitly skipped" in item.reason
        for item in advanced.decisions
    )
    calls_after_advance = Counter(model.attempts_by_task)
    strategy_after_advance = advanced.strategy_portfolio

    with pytest.raises(HTTPException) as exc_info:
        runtime.clarify(
            advanced.session_id,
            PlanningSessionTextRequest(text="跳过这一步"),
        )

    assert exc_info.value.status_code == 409
    still_waiting = runtime.get_session(advanced.session_id)
    assert still_waiting.status == "waiting_design_approval"
    assert still_waiting.strategy_portfolio == strategy_after_advance
    assert Counter(model.attempts_by_task) == calls_after_advance


@pytest.mark.parametrize("critical_mode", ["consistency", "safety", "feasibility"])
def test_phase73_skip_rejects_critical_goal_blockers(
    isolated_db,
    critical_mode: str,
) -> None:
    model = CriticalGoalProgressionModel(critical_mode)
    runtime = CognitiveOSRuntime(model_client=model)
    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId=f"phase73-control-skip-{critical_mode}",
            userInput="Learn Go",
        )
    )
    calls_before = Counter(model.attempts_by_task)
    artifacts_before = [(item.id, item.artifact_type, item.version) for item in blocked.artifacts]
    decisions_before = [(item.id, item.decision) for item in blocked.decisions]

    with pytest.raises(HTTPException) as exc_info:
        runtime.clarify(
            blocked.session_id,
            PlanningSessionTextRequest(text="跳过这一步"),
        )

    assert exc_info.value.status_code == 409
    unchanged = runtime.get_session(blocked.session_id)
    assert unchanged.user_input == blocked.user_input
    assert unchanged.goal_model == blocked.goal_model
    assert unchanged.goal_completion == blocked.goal_completion
    assert [(item.id, item.artifact_type, item.version) for item in unchanged.artifacts] == artifacts_before
    assert [(item.id, item.decision) for item in unchanged.decisions] == decisions_before
    assert Counter(model.attempts_by_task) == calls_before


def test_phase73_command_skip_control_does_not_become_goal_evidence(client, monkeypatch) -> None:
    model = SemanticGoProgressionModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    started = client.post(
        "/api/command/chat",
        json={"message": "我要学Go语言", "mode": "auto", "permission": "low"},
    )
    assert started.status_code == 200
    started_events = [json.loads(line) for line in started.text.splitlines() if line.strip()]
    thread_id = started_events[-1]["threadId"]
    started_status = next(
        item for item in started_events if item.get("type") == "planning_session_status"
    )
    assert started_status["status"] == "needs_goal_clarification"

    skipped = client.post(
        "/api/command/chat",
        json={
            "message": "跳过这一步，根据现有内容直接继续下一步",
            "mode": "auto",
            "threadId": thread_id,
            "permission": "low",
        },
    )

    assert skipped.status_code == 200
    skipped_events = [json.loads(line) for line in skipped.text.splitlines() if line.strip()]
    skipped_status = next(
        item for item in skipped_events if item.get("type") == "planning_session_status"
    )
    assert skipped_status["status"] == "waiting_design_approval"
    assert skipped_status["goalCompletion"]["complete"] is True
    assert not any(item.get("type") == "planning_session_started" for item in skipped_events)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT user_input, conversation_history_json
            FROM planning_sessions
            WHERE thread_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
    assert row is not None
    assert "跳过这一步" not in row["user_input"]
    assert "跳过这一步" not in row["conversation_history_json"]


def test_phase73_modify_control_does_not_become_revision_evidence(client, monkeypatch) -> None:
    model = SemanticGoProgressionModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    started = client.post(
        "/api/command/chat",
        json={"message": _full_go_request(), "mode": "auto", "permission": "low"},
    )
    assert started.status_code == 200
    started_events = [json.loads(line) for line in started.text.splitlines() if line.strip()]
    thread_id = started_events[-1]["threadId"]
    status = next(item for item in started_events if item.get("type") == "planning_session_status")
    assert status["status"] == "waiting_design_approval"

    with get_conn() as conn:
        before = conn.execute(
            """
            SELECT id, user_input, conversation_history_json
            FROM planning_sessions
            WHERE thread_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
    assert before is not None
    calls_before = Counter(model.attempts_by_task)

    modified = client.post(
        "/api/command/chat",
        json={
            "message": "修改",
            "mode": "auto",
            "threadId": thread_id,
            "permission": "low",
        },
    )

    assert modified.status_code == 200
    modified_events = [json.loads(line) for line in modified.text.splitlines() if line.strip()]
    assert not any(item.get("type") == "planning_session_started" for item in modified_events)
    modified_status = next(
        item for item in modified_events if item.get("type") == "planning_session_status"
    )
    assert modified_status["status"] == "waiting_design_approval"
    with get_conn() as conn:
        after = conn.execute(
            """
            SELECT id, user_input, conversation_history_json
            FROM planning_sessions
            WHERE id = ?
            """,
            (before["id"],),
        ).fetchone()
    assert after is not None
    assert after["user_input"] == before["user_input"]
    assert after["conversation_history_json"] == before["conversation_history_json"]
    assert Counter(model.attempts_by_task) == calls_before


def test_phase73_restart_control_reuses_saved_goal_not_control_text(client, monkeypatch) -> None:
    model = SemanticGoProgressionModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    started = client.post(
        "/api/command/chat",
        json={"message": _full_go_request(), "mode": "auto", "permission": "low"},
    )
    assert started.status_code == 200
    started_events = [json.loads(line) for line in started.text.splitlines() if line.strip()]
    thread_id = started_events[-1]["threadId"]
    with get_conn() as conn:
        original = conn.execute(
            """
            SELECT id, user_input
            FROM planning_sessions
            WHERE thread_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
    assert original is not None

    restarted = client.post(
        "/api/command/chat",
        json={
            "message": "重新开始",
            "mode": "auto",
            "threadId": thread_id,
            "permission": "low",
        },
    )

    assert restarted.status_code == 200
    restarted_events = [json.loads(line) for line in restarted.text.splitlines() if line.strip()]
    assert any(item.get("type") == "planning_session_started" for item in restarted_events)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, status, user_input
            FROM planning_sessions
            WHERE thread_id = ?
            ORDER BY created_at, rowid
            """,
            (thread_id,),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] == original["id"]
    assert rows[0]["status"] == "cancelled"
    assert rows[1]["user_input"] == original["user_input"]
    assert "重新开始" not in rows[1]["user_input"]


def test_phase73_stream_exposes_goal_completion_and_separate_statuses(client, monkeypatch) -> None:
    model = SemanticGoProgressionModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    response = client.post(
        "/api/command/chat",
        json={"message": _full_go_request(), "mode": "auto", "permission": "low"},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    completion = next(item for item in events if item.get("type") == "goal_completion_updated")
    assert completion["data"]["complete"] is True
    assert completion["data"]["blockingUnknowns"] == []
    assert completion["data"]["nextStage"] == "strategy"

    status = next(item for item in events if item.get("type") == "planning_session_status")
    assert status["status"] == "waiting_design_approval"
    assert status["businessStatus"] == "strategy_pending"
    assert status["runtimeStatus"] == "idle"
    assert status["goalCompletion"]["complete"] is True

    thread_id = events[-1]["threadId"]
    replay = client.get(f"/api/command/thread/{thread_id}")
    assert replay.status_code == 200
    assert "goal_completion_updated" in {
        item.get("kind") for item in replay.json()["messages"]
    }


def test_phase73_command_goal_failure_stream_replay_and_retry_preserve_pending_answer(client, monkeypatch) -> None:
    model = SemanticGoProgressionModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    started = client.post(
        "/api/command/chat",
        json={"message": "Learn Go", "mode": "auto", "permission": "low"},
    )
    assert started.status_code == 200
    started_events = [json.loads(line) for line in started.text.splitlines() if line.strip()]
    thread_id = started_events[-1]["threadId"]
    session_id = next(
        item["sessionId"] for item in started_events if item.get("type") == "planning_session_started"
    )
    initial_goal = next(
        dict(item["data"]) for item in started_events if item.get("type") == "goal_model_updated"
    )
    initial_completion = next(
        dict(item["data"]) for item in started_events if item.get("type") == "goal_completion_updated"
    )
    assert initial_goal.pop("artifactState") == "current"
    assert initial_completion.pop("artifactState") == "current"

    model.fail_goal = True
    blocked = client.post(
        "/api/command/chat",
        json={
            "message": "为了web开发",
            "mode": "auto",
            "threadId": thread_id,
            "permission": "low",
        },
    )
    assert blocked.status_code == 200
    blocked_events = [json.loads(line) for line in blocked.text.splitlines() if line.strip()]
    blocked_status = next(
        item for item in blocked_events if item.get("type") == "planning_session_status"
    )
    assert blocked_status["sessionId"] == session_id
    assert blocked_status["status"] == "MODEL_UNAVAILABLE"
    assert blocked_status["modelFailure"]["stage"] == "goal_intelligence"
    assert blocked_status["modelFailure"]["resumeNode"] == "goal_intelligence"
    assert blocked_status["modelFailure"]["attempts"] == [
        {"provider": "deepseek", "status": "error", "errorType": "auth_error"}
    ]
    assert "reason" not in blocked_status["modelFailure"]
    assert "detail" not in blocked_status["modelFailure"]
    assert blocked_status["pendingInput"] == {"text": "为了web开发", "applied": False}
    stale_goal = next(
        dict(item["data"]) for item in blocked_events if item.get("type") == "goal_model_updated"
    )
    stale_completion = next(
        dict(item["data"])
        for item in blocked_events
        if item.get("type") == "goal_completion_updated"
    )
    assert stale_goal.pop("artifactState") == "last_confirmed"
    assert stale_completion.pop("artifactState") == "last_confirmed"
    assert stale_goal == initial_goal
    assert stale_completion == initial_completion
    assert not any(item.get("type") == "strategy_portfolio_ready" for item in blocked_events)

    replay = client.get(f"/api/command/thread/{thread_id}")
    assert replay.status_code == 200
    replay_status = next(
        item["payload"]
        for item in reversed(replay.json()["messages"])
        if item.get("kind") == "planning_session_status"
        and item.get("payload", {}).get("status") == "MODEL_UNAVAILABLE"
    )
    assert replay_status["modelFailure"] == blocked_status["modelFailure"]
    assert replay_status["pendingInput"] == blocked_status["pendingInput"]

    model.fail_goal = False
    recovered = client.post(
        "/api/command/chat",
        json={
            "message": "请重试当前深度规划",
            "mode": "auto",
            "threadId": thread_id,
            "permission": "low",
        },
    )
    assert recovered.status_code == 200
    recovered_events = [json.loads(line) for line in recovered.text.splitlines() if line.strip()]
    recovered_status = next(
        item for item in recovered_events if item.get("type") == "planning_session_status"
    )
    assert recovered_status["sessionId"] == session_id
    assert "modelFailure" not in recovered_status
    assert "pendingInput" not in recovered_status
    recovered_goal = next(
        item["data"] for item in recovered_events if item.get("type") == "goal_model_updated"
    )
    assert recovered_goal["artifactState"] == "current"
    assert recovered_goal["goalStatement"] == "Go Web development"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_input, conversation_history_json FROM planning_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    assert row is not None
    assert "为了web开发" in row["user_input"]
    assert "请重试当前深度规划" not in row["user_input"]
    assert "请重试当前深度规划" not in row["conversation_history_json"]
