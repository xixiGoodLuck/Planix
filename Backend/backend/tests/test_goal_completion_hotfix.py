from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.cognitive_planning.agents import GoalCompletionJudge
from app.cognitive_planning.graph.planning_graph import _from_guard
from app.db import init_db
from app.schemas import CognitivePlanningMetadata
from app.services.command_agent import detect_planning_control_intent
from app.services.cognitive_planning.compatibility.session_api_adapter import _model_failure
from app.services.cognitive_planning.contracts import (
    DecisionRelevantUnknown,
    FeasibilityJudgment,
    GoalCompletionResult,
    GoalQuestion,
    GoalSuccessModel,
    UserGoalModel,
)
from app.services.cognitive_planning.orchestration.edges import route_from_guard


def _goal_with_unknown(
    unknown: DecisionRelevantUnknown,
    question: GoalQuestion,
    *,
    can_proceed: bool = False,
) -> UserGoalModel:
    return UserGoalModel(
        goalStatement="Build a reviewable Go web service",
        desiredChange="Use Go for web development and produce a working service",
        domain="go_web_development",
        uncertainties=[unknown.description],
        decisionRelevantUnknowns=[unknown],
        successModel=GoalSuccessModel(
            definition="A working Go web service can be demonstrated and reviewed.",
            measurableSignals=["The service passes its integration checks."],
        ),
        feasibilityJudgment=FeasibilityJudgment(summary="Feasible within the stated weekly time budget."),
        questions=[question],
        confidence=0.88,
        canProceedToEvidence=can_proceed,
    )


def test_goal_completion_result_serializes_the_public_contract() -> None:
    result = GoalCompletionResult(
        complete=True,
        blockingUnknowns=[],
        optionalUnknowns=["目标期限"],
        nextStage="strategy",
    )

    assert result.model_dump(by_alias=True) == {
        "complete": True,
        "blockingUnknowns": [],
        "optionalUnknowns": ["目标期限"],
        "nextStage": "strategy",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "complete": True,
            "blockingUnknowns": [{"question": "仍需回答？", "impact": "会改变策略"}],
            "optionalUnknowns": [],
            "nextStage": "strategy",
        },
        {
            "complete": False,
            "blockingUnknowns": [],
            "optionalUnknowns": [],
            "nextStage": "goal_clarification",
        },
        {
            "complete": True,
            "blockingUnknowns": [],
            "optionalUnknowns": [],
            "nextStage": "goal_clarification",
        },
        {
            "complete": False,
            "blockingUnknowns": [{"question": "用途是什么？", "impact": "会改变策略"}],
            "optionalUnknowns": [],
            "nextStage": "strategy",
        },
    ],
)
def test_goal_completion_result_rejects_inconsistent_progression(payload: dict) -> None:
    with pytest.raises(ValidationError):
        GoalCompletionResult.model_validate(payload)


def test_goal_completion_judge_allows_optional_unknowns_even_when_legacy_flag_is_false() -> None:
    optional = DecisionRelevantUnknown(
        key="deadline",
        description="希望何时完成第一版",
        whyItChangesThePlan="期限会调整节奏，但不会改变当前策略选择。",
        impact="schedule",
        priority="optional",
    )
    goal = _goal_with_unknown(
        optional,
        GoalQuestion(
            question="你希望何时完成第一版？",
            whyThisQuestionMatters="可用于优化节奏，但现在不必阻塞规划。",
            expectedDecisionImpact="schedule refinement",
        ),
        can_proceed=False,
    )

    result = GoalCompletionJudge().evaluate(goal)

    assert result.complete is True
    assert result.blocking_unknowns == []
    assert result.next_stage == "strategy"
    assert "希望何时完成第一版" in result.optional_unknowns
    assert "你希望何时完成第一版？" in result.optional_unknowns


def test_goal_completion_judge_fails_closed_when_a_proceeding_goal_still_asks() -> None:
    important = DecisionRelevantUnknown(
        key="purpose",
        description="Which outcome should determine the recommended direction",
        whyItChangesThePlan="The answer selects a materially different strategy.",
        impact="strategy",
        priority="important",
    )
    question = GoalQuestion(
        question="Which outcome should this plan optimize for?",
        whyThisQuestionMatters="The answer changes the recommended strategy.",
        expectedDecisionImpact="strategy selection",
        answerOptions=["Outcome A", "Outcome B"],
    )
    goal = _goal_with_unknown(important, question, can_proceed=True)

    result = GoalCompletionJudge().evaluate(goal)

    assert result.complete is False
    assert result.next_stage == "goal_clarification"
    assert result.blocking_unknowns[0].question == question.question
    assert result.blocking_unknowns[0].answer_options == question.answer_options


def test_goal_completion_judge_never_labels_a_blocker_as_optional() -> None:
    blocker = DecisionRelevantUnknown(
        key="purpose",
        description="Go 学习最终用于哪类可验证结果",
        whyItChangesThePlan="用途会改变技术重点、项目范围和成功标准。",
        impact="strategy",
        priority="blocking",
    )
    goal = _goal_with_unknown(
        blocker,
        GoalQuestion(
            question="你希望 Go 最终用于什么结果？",
            whyThisQuestionMatters="用途会改变整个策略。",
            expectedDecisionImpact="strategy",
            answerOptions=["找工作", "个人项目", "后端服务", "系统学习"],
        ),
    )

    result = GoalCompletionJudge().evaluate(goal)

    assert result.complete is False
    assert result.next_stage == "goal_clarification"
    assert result.blocking_unknowns[0].question == "你希望 Go 最终用于什么结果？"
    assert result.blocking_unknowns[0].answer_options == ["找工作", "个人项目", "后端服务", "系统学习"]
    assert result.optional_unknowns == []


def test_goal_completion_judge_matches_questions_by_semantics_not_list_position() -> None:
    optional = DecisionRelevantUnknown(
        key="deadline",
        description="Preferred deadline for the first reviewable version",
        whyItChangesThePlan="The deadline refines pacing only.",
        impact="schedule",
        priority="optional",
    )
    blocker = DecisionRelevantUnknown(
        key="purpose",
        description="Which outcome the Go project must support",
        whyItChangesThePlan="The purpose changes project scope and success criteria.",
        impact="strategy",
        priority="blocking",
    )
    optional_question = GoalQuestion(
        question="When would you prefer to finish the first version?",
        whyThisQuestionMatters="This refines the schedule but does not block strategy.",
        expectedDecisionImpact="schedule",
    )
    blocking_question = GoalQuestion(
        question="What outcome must the Go project support?",
        whyThisQuestionMatters="The purpose determines the project strategy.",
        expectedDecisionImpact="strategy",
    )
    goal = UserGoalModel(
        goalStatement="Build a Go web project",
        desiredChange="Produce a useful Go web project",
        domain="go_web_development",
        uncertainties=[optional.description, blocker.description],
        decisionRelevantUnknowns=[optional, blocker],
        successModel=GoalSuccessModel(definition="A useful Go project can be reviewed."),
        feasibilityJudgment=FeasibilityJudgment(summary="Feasible."),
        questions=[optional_question, blocking_question],
        confidence=0.7,
        canProceedToEvidence=False,
    )

    result = GoalCompletionJudge().evaluate(goal)

    assert result.complete is False
    assert result.blocking_unknowns[0].question == blocking_question.question
    assert optional_question.question in result.optional_unknowns
    assert blocking_question.question not in result.optional_unknowns


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("跳过这一步", "skip_current_stage"),
        (
            "Skip this step and continue with the information already provided",
            "skip_current_stage",
        ),
        ("下一步", "continue_current_stage"),
        ("继续", "continue_current_stage"),
        ("开始规划", "continue_current_stage"),
        ("请重试当前深度规划", "continue_current_stage"),
        ("重试深度规划", "continue_current_stage"),
        ("Retry the current deep planning session", "continue_current_stage"),
        ("Retry deep planning", "continue_current_stage"),
        ("确认", "approve_current_stage"),
        ("修改", "modify_current_stage"),
        ("重新开始", "restart_planning"),
        ("取消", "cancel_planning"),
        ("为了 Web 开发", "provide_goal_information"),
    ],
)
def test_planning_control_intent_is_detected_before_goal_modeling(text: str, expected: str) -> None:
    assert detect_planning_control_intent(text) == expected


def test_model_failure_projection_sanitizes_attempts_and_marks_automatic_retry() -> None:
    failure = _model_failure(
        status="MODEL_UNAVAILABLE",
        metadata=CognitivePlanningMetadata(
            engineVersion="cognitive-os-v1",
            planningMode="blocked_model_unavailable",
            currentStage="goal_intelligence",
        ),
        messages=[
            SimpleNamespace(
                message_type="block",
                resolved=False,
                payload_json={
                    "errorType": "auth_error",
                    "retryable": True,
                    "resumeNode": "goal_intelligence",
                    "detail": "secret provider response",
                    "apiKey": "sk-secret",
                    "attempts": [
                        {
                            "provider": "deepseek",
                            "model": "private-model-name",
                            "status": "error",
                            "errorType": "model_output_truncated",
                            "detail": "raw response",
                        },
                        {
                            "provider": "deepseek",
                            "model": "private-model-name",
                            "status": "error",
                            "errorType": "model_output_truncated",
                            "automaticRetry": True,
                        },
                        {
                            "provider": "zhipu_glm",
                            "status": "error",
                            "errorType": "auth_error",
                        },
                        {
                            "provider": "kimi",
                            "status": "skipped",
                            "errorType": "missing_api_key",
                        },
                        {
                            "provider": "untrusted-provider",
                            "status": "error",
                            "errorType": "internal_exception",
                        },
                    ],
                },
            )
        ],
    )

    assert failure is not None
    payload = failure.model_dump(by_alias=True, exclude_none=True)
    assert payload["automaticRetryAttempted"] is True
    assert payload["attempts"] == [
        {"provider": "deepseek", "status": "error", "errorType": "model_output_truncated"},
        {"provider": "deepseek", "status": "error", "errorType": "model_output_truncated"},
        {"provider": "zhipu_glm", "status": "error", "errorType": "auth_error"},
        {"provider": "kimi", "status": "skipped", "errorType": "missing_api_key"},
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private-model-name" not in serialized
    assert "secret provider response" not in serialized
    assert "raw response" not in serialized
    assert "sk-secret" not in serialized
    assert "untrusted-provider" not in serialized
    assert "系统已自动重试一次" in payload["action"]["zh"]
    assert "already retried once automatically" in payload["action"]["en"]


def test_model_failure_projection_keeps_post_route_contract_error_provider_neutral() -> None:
    failure = _model_failure(
        status="MODEL_UNAVAILABLE",
        metadata=CognitivePlanningMetadata(
            engineVersion="cognitive-os-v1",
            planningMode="blocked_model_unavailable",
            currentStage="goal_intelligence",
        ),
        messages=[
            SimpleNamespace(
                message_type="block",
                resolved=False,
                payload_json={
                    "errorType": "invalid_model_output",
                    "resumeNode": "goal_intelligence",
                    "attempts": [
                        {
                            "provider": "deepseek",
                            "status": "error",
                            "errorType": "model_output_truncated",
                        },
                        {
                            "provider": "deepseek",
                            "status": "success",
                            "automaticRetry": True,
                        },
                    ],
                },
            )
        ],
    )

    assert failure is not None
    summary = failure.summary.en
    assert "DeepSeek: the model output was truncated" in summary
    assert "the output did not satisfy the structured contract" in summary
    assert "DeepSeek: the output did not satisfy the structured contract" not in summary


def test_model_failure_projection_does_not_append_unknown_for_unsupported_fallback() -> None:
    failure = _model_failure(
        status="MODEL_UNAVAILABLE",
        metadata=CognitivePlanningMetadata(
            engineVersion="cognitive-os-v1",
            planningMode="blocked_model_unavailable",
            currentStage="goal_intelligence",
        ),
        messages=[
            SimpleNamespace(
                message_type="block",
                resolved=False,
                payload_json={
                    "errorType": "model_unavailable",
                    "attempts": [
                        {
                            "provider": "deepseek",
                            "status": "error",
                            "errorType": "auth_error",
                        }
                    ],
                },
            )
        ],
    )

    assert failure is not None
    assert "DeepSeek: the API Key was invalid or expired" in failure.summary.en
    assert "did not return a usable result" not in failure.summary.en


@pytest.mark.parametrize("resume_node", ["goal_intelligence", "goal_completion"])
def test_blocked_model_resume_precedes_stale_incomplete_completion(resume_node: str) -> None:
    completion = GoalCompletionResult(
        complete=False,
        blockingUnknowns=[{"question": "Need one answer", "impact": "Changes strategy"}],
        optionalUnknowns=[],
        nextStage="goal_clarification",
    )
    state = {
        "user_action": "continue_current_stage",
        "status": "MODEL_UNAVAILABLE",
        "business_status": "goal_clarification",
        "runtime_status": "running",
        "planning_mode": "model_backed",
        "resume_node": resume_node,
        "goal_completion": completion,
    }

    assert _from_guard(state) == resume_node
    assert route_from_guard(state) == "goal_modeling"


def _create_legacy_planning_sessions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE planning_sessions (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL DEFAULT '',
          entry_point TEXT NOT NULL DEFAULT 'p_mode',
          status TEXT NOT NULL,
          user_input TEXT NOT NULL,
          user_need_contract_json TEXT NOT NULL DEFAULT '{}',
          slot_state_json TEXT NOT NULL DEFAULT '{}',
          pending_question_json TEXT NOT NULL DEFAULT '{}',
          memory_insight_json TEXT NOT NULL DEFAULT '{}',
          resource_brief_json TEXT NOT NULL DEFAULT '{}',
          design_proposal_json TEXT NOT NULL DEFAULT '{}',
          execution_draft_json TEXT NOT NULL DEFAULT '{}',
          latest_learning_patch_json TEXT NOT NULL DEFAULT '{}',
          cognitive_metadata_json TEXT NOT NULL DEFAULT '{}',
          goal_model_json TEXT NOT NULL DEFAULT '{}',
          reality_assessment_json TEXT NOT NULL DEFAULT '{}',
          evidence_pack_json TEXT NOT NULL DEFAULT '{}',
          strategy_portfolio_json TEXT NOT NULL DEFAULT '{}',
          execution_blueprint_json TEXT NOT NULL DEFAULT '{}',
          critique_report_json TEXT NOT NULL DEFAULT '{}',
          planning_learning_update_json TEXT NOT NULL DEFAULT '{}',
          conversation_history_json TEXT NOT NULL DEFAULT '[]',
          request_context_json TEXT NOT NULL DEFAULT '{}',
          approved_strategy_id TEXT NOT NULL DEFAULT '',
          repair_count INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def test_planning_status_migration_backfills_business_and_runtime_state() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_legacy_planning_sessions_table(conn)
    conn.executemany(
        """
        INSERT INTO planning_sessions(
          id, status, user_input, evidence_pack_json, strategy_portfolio_json
        )
        VALUES (?, ?, 'legacy input', ?, ?)
        """,
        [
            ("design", "waiting_design_approval", "{}", "{}"),
            ("execution", "waiting_execution_approval", "{}", "{}"),
            (
                "blocked-strategy",
                "MODEL_UNAVAILABLE",
                '{"canProceedToStrategy":true}',
                "{}",
            ),
            (
                "blocked-strategy-revision",
                "MODEL_UNAVAILABLE",
                "{}",
                '{"recommendedStrategyId":"strategy-a"}',
            ),
        ],
    )

    init_db(conn)

    rows = {
        row["id"]: (row["business_status"], row["runtime_status"])
        for row in conn.execute(
            "SELECT id, business_status, runtime_status FROM planning_sessions"
        ).fetchall()
    }
    assert rows["design"] == ("strategy_pending", "idle")
    assert rows["execution"] == ("execution_pending", "idle")
    assert rows["blocked-strategy"] == ("strategy_pending", "blocked_model")
    assert rows["blocked-strategy-revision"] == (
        "strategy_pending",
        "blocked_model",
    )
    conn.close()


def test_planning_status_migration_preserves_populated_model_failure_state() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO planning_sessions(
          id, status, user_input, strategy_portfolio_json,
          business_status, runtime_status
        ) VALUES (
          'blocked-strategy', 'MODEL_UNAVAILABLE', 'existing input',
          '{"recommendedStrategyId":"strategy-a"}',
          'strategy_pending', 'blocked_model'
        )
        """
    )

    init_db(conn)
    init_db(conn)

    row = conn.execute(
        """
        SELECT business_status, runtime_status
        FROM planning_sessions WHERE id = 'blocked-strategy'
        """
    ).fetchone()
    assert row is not None
    assert (row["business_status"], row["runtime_status"]) == (
        "strategy_pending",
        "blocked_model",
    )
    conn.close()
