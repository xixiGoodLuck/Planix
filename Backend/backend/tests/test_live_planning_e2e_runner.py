from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from scripts.live_planning_e2e import (
    INDEPENDENT_GOAL,
    RETRY_MESSAGE,
    SCENARIOS,
    STRATEGY_APPROVAL_MESSAGE,
    HttpAuditTransport,
    PlanningScenarioRunner,
    Scenario,
    ScenarioResult,
    audit_manifest_batch,
    build_report,
    load_manifest_source_fingerprint,
    load_thread_manifest,
)


THREAD_ID = "new-thread-001"
SESSION_ID = "new-session-001"
GOAL_ID = "goal-artifact-1"
REALITY_ID = "reality-artifact-1"
EVIDENCE_ID = "evidence-artifact-1"
STRATEGY_ID = "strategy-artifact-1"
EXECUTION_ID = "execution-artifact-1"
CRITIQUE_ID = "critique-artifact-1"


class FakeCommandTransport:
    def __init__(self, responses: list[list[dict[str, Any]]], replay: dict[str, Any]) -> None:
        self.responses = deepcopy(responses)
        self.replay = deepcopy(replay)
        self.requests: list[dict[str, Any]] = []
        self.replay_requests: list[str] = []

    def chat(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.requests.append(deepcopy(payload))
        if not self.responses:
            raise AssertionError("unexpected Command chat call")
        return self.responses.pop(0)

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        self.replay_requests.append(thread_id)
        return deepcopy(self.replay)


def _scenario(
    *,
    requires_clarification: bool = False,
    time_capacity_minutes: int | None = None,
    spending_limit_cny: int | None = None,
    keyword_groups: tuple[tuple[str, ...], ...] | None = None,
    supporting_keyword_groups: tuple[tuple[str, ...], ...] = (),
    forbidden_keyword_groups: tuple[tuple[str, ...], ...] = (),
) -> Scenario:
    return Scenario(
        key="mock",
        direction="Mock",
        initial_message="我想完成一个目标",
        persona_response=f"{INDEPENDENT_GOAL} 固定事实只有：目标交付一个 REST API。",
        requires_clarification=requires_clarification,
        keyword_groups=(
            keyword_groups
            if keyword_groups is not None
            else (("rest api", "接口"),)
        ),
        supporting_keyword_groups=supporting_keyword_groups,
        forbidden_keyword_groups=forbidden_keyword_groups,
        time_capacity_minutes=time_capacity_minutes,
        spending_limit_cny=spending_limit_cny,
    )


def _stream(*events: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "thread", "threadId": THREAD_ID},
        *events,
        {"type": "done", "threadId": THREAD_ID},
    ]


def _model_usage(
    stage: str,
    *,
    provider: str = "deepseek",
    model: str = "deepseek-v4-flash",
    mode: str = "llm",
    successful_attempt_provider: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "mode": mode,
        "taskType": stage,
        "localFallbackAllowed": False,
        "attempts": [
            {
                "provider": successful_attempt_provider or provider,
                "model": model,
                "status": "success",
            }
        ],
    }


def _decision(
    *,
    agent: str,
    decision: str,
    input_ids: list[str] | None = None,
    output_ids: list[str] | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f"decision-{agent}-{decision}-{'-'.join(output_ids or input_ids or ['none'])}",
        "agent": agent,
        "decision": decision,
        "inputArtifactIds": list(input_ids or []),
        "outputArtifactIds": list(output_ids or []),
    }
    if stage:
        data["modelUsage"] = _model_usage(stage)
    return data


def _decision_event(data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "agent_decision", "sessionId": SESSION_ID, "data": deepcopy(data)}


def _strategy() -> dict[str, Any]:
    return {
        "recommendedStrategyId": "strategy-a",
        "strategies": [
            {
                "id": "strategy-a",
                "name": "Incremental delivery",
                "coreIdea": "Deliver a small REST API in stages.",
                "rationale": {"whyItFitsUser": "fixed facts", "evidenceUsed": [], "assumptions": []},
                "phases": [],
                "tradeoffs": ["scope"],
                "majorRisks": ["time"],
                "expectedResults": ["REST API"],
                "estimatedEffort": "two weeks",
            }
        ],
        "recommendationReason": "Best fit",
        "userDecision": {
            "question": "Confirm?",
            "options": ["strategy-a"],
            "defaultRecommendation": "strategy-a",
        },
        "status": "waiting_user_approval",
    }


def _execution() -> dict[str, Any]:
    return {
        "narrative": {
            "executionLogic": "Build and verify a REST API.",
            "dependencyExplanation": "The only task has no dependency.",
            "weeklyOrStageRhythm": "One stage",
            "workloadReasoning": "Fits the fixed facts.",
            "riskHandling": "Use the fallback if blocked.",
        },
        "tasks": [
            {
                "id": "task-1",
                "title": "Deliver REST API",
                "purpose": "Create the agreed deliverable.",
                "whyNow": "It is the first milestone.",
                "dependencies": [],
                "actionSteps": ["Implement one endpoint", "Run its test"],
                "estimatedMinutes": 90,
                "difficulty": "medium",
                "scheduleWindow": "week 1",
                "completionEvidence": ["Passing test output"],
                "deliverable": "A tested REST API endpoint",
                "resources": [
                    {
                        "title": "Official documentation",
                        "type": "documentation",
                        "exactUsage": "Check endpoint semantics.",
                        "expectedContribution": "Correct implementation.",
                    }
                ],
                "prerequisites": [],
                "risks": ["The endpoint may fail its first test."],
                "fallbackAction": "Reduce the endpoint scope and rerun the test.",
                "domainExtensions": {},
            }
        ],
        "checkpoints": [],
        "assumptions": [],
        "resourceCoverage": "strong",
        "status": "draft",
    }


def _critique() -> dict[str, Any]:
    return {
        "status": "passed",
        "score": 93,
        "dimensions": {
            "userFit": 93,
            "goalAlignment": 93,
            "domainCorrectness": 93,
            "feasibility": 93,
            "safety": 93,
            "taskSpecificity": 93,
            "resourceActionability": 93,
            "scheduleFit": 93,
            "adaptability": 93,
        },
        "strengths": ["Concrete"],
        "issues": [],
        "repairRequests": [],
        "simulationSummary": "The task is feasible.",
        "remainingRisks": [],
        "calendarWritable": True,
        "confidence": 0.93,
    }


def _design_stream(*, include_start: bool = True) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if include_start:
        events.append(
            {
                "type": "planning_session_started",
                "sessionId": SESSION_ID,
                "status": "waiting_design_approval",
            }
        )
    events.extend(
        [
            _decision_event(
                _decision(
                    agent="Goal Intelligence Agent",
                    decision="produce_artifact",
                    output_ids=[GOAL_ID],
                    stage="planning_goal_model",
                )
            ),
            _decision_event(
                _decision(
                    agent="Reality Agent",
                    decision="approve",
                    input_ids=[GOAL_ID],
                    output_ids=[REALITY_ID],
                    stage="planning_reality",
                )
            ),
            _decision_event(
                _decision(
                    agent="Evidence Agent",
                    decision="approve",
                    input_ids=[GOAL_ID, REALITY_ID],
                    output_ids=[EVIDENCE_ID],
                    stage="planning_evidence",
                )
            ),
            _decision_event(
                _decision(
                    agent="Strategy Agent",
                    decision="request_user_input",
                    input_ids=[GOAL_ID, EVIDENCE_ID],
                    output_ids=[STRATEGY_ID],
                    stage="planning_strategy",
                )
            ),
            {"type": "goal_model_updated", "sessionId": SESSION_ID, "data": {"goalStatement": "REST API"}},
            {
                "type": "goal_completion_updated",
                "sessionId": SESSION_ID,
                "data": {"complete": True, "blockingUnknowns": [], "nextStage": "strategy"},
            },
            {"type": "reality_assessment_ready", "sessionId": SESSION_ID, "data": {"feasibilitySummary": "feasible"}},
            {"type": "evidence_pack_ready", "sessionId": SESSION_ID, "data": {"synthesis": "evidence"}},
            {"type": "strategy_portfolio_ready", "sessionId": SESSION_ID, "data": _strategy()},
            {
                "type": "planning_session_status",
                "sessionId": SESSION_ID,
                "status": "waiting_design_approval",
                "businessStatus": "strategy_pending",
                "runtimeStatus": "idle",
            },
        ]
    )
    return _stream(*events)


def _execution_stream() -> list[dict[str, Any]]:
    return _stream(
        _decision_event(
            _decision(
                agent="Strategy Agent",
                decision="approve",
                input_ids=[STRATEGY_ID],
            )
        ),
        _decision_event(
            _decision(
                agent="Execution Agent",
                decision="produce_artifact",
                input_ids=[GOAL_ID, EVIDENCE_ID, STRATEGY_ID],
                output_ids=[EXECUTION_ID],
                stage="planning_execution",
            )
        ),
        _decision_event(
            _decision(
                agent="Critic Agent",
                decision="approve",
                input_ids=[GOAL_ID, EVIDENCE_ID, STRATEGY_ID, EXECUTION_ID],
                output_ids=[CRITIQUE_ID],
                stage="planning_critique",
            )
        ),
        {"type": "execution_blueprint_ready", "sessionId": SESSION_ID, "data": _execution()},
        {"type": "critique_report_ready", "sessionId": SESSION_ID, "data": _critique()},
        {
            "type": "planning_session_status",
            "sessionId": SESSION_ID,
            "status": "waiting_execution_approval",
            "businessStatus": "execution_pending",
            "runtimeStatus": "idle",
        },
    )


def _message(
    index: int,
    *,
    role: str,
    kind: str,
    content: str = "",
    data: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if role == "card" and kind != "goal_understanding":
        payload["sessionId"] = SESSION_ID
    if data is not None:
        if kind == "goal_understanding":
            payload.update(deepcopy(data))
        else:
            payload["data"] = deepcopy(data)
    if status:
        payload.update(status)
    return {
        "id": f"message-{index}",
        "threadId": THREAD_ID,
        "role": role,
        "content": content,
        "kind": kind,
        "payload": payload,
        "createdAt": "2026-07-12T00:00:00Z",
    }


def _replay(*, repair_count: int = 0) -> dict[str, Any]:
    messages = [
        _message(1, role="user", kind="text", content="我想完成一个目标"),
        _message(
            2,
            role="card",
            kind="goal_understanding",
            data={
                "intentState": "clear_goal",
                "source": "llm",
                "modelUsage": _model_usage("goal_understanding"),
            },
        ),
        _message(3, role="card", kind="planning_session_started", status={"status": "needs_goal_clarification"}),
        _message(
            4,
            role="card",
            kind="agent_decision",
            data=_decision(
                agent="Goal Intelligence Agent",
                decision="produce_artifact",
                output_ids=[GOAL_ID],
                stage="planning_goal_model",
            ),
        ),
        _message(
            5,
            role="card",
            kind="agent_decision",
            data=_decision(
                agent="Reality Agent",
                decision="approve",
                input_ids=[GOAL_ID],
                output_ids=[REALITY_ID],
                stage="planning_reality",
            ),
        ),
        _message(
            6,
            role="card",
            kind="agent_decision",
            data=_decision(
                agent="Evidence Agent",
                decision="approve",
                input_ids=[GOAL_ID, REALITY_ID],
                output_ids=[EVIDENCE_ID],
                stage="planning_evidence",
            ),
        ),
        _message(
            7,
            role="card",
            kind="agent_decision",
            data=_decision(
                agent="Strategy Agent",
                decision="request_user_input",
                input_ids=[GOAL_ID, EVIDENCE_ID],
                output_ids=[STRATEGY_ID],
                stage="planning_strategy",
            ),
        ),
        _message(8, role="card", kind="goal_model_updated", data={"goalStatement": "REST API"}),
        _message(9, role="card", kind="goal_completion_updated", data={"complete": True, "blockingUnknowns": []}),
        _message(10, role="card", kind="reality_assessment_ready", data={"feasibilitySummary": "feasible"}),
        _message(11, role="card", kind="evidence_pack_ready", data={"synthesis": "evidence"}),
        _message(12, role="card", kind="strategy_portfolio_ready", data=_strategy()),
        _message(13, role="user", kind="text", content=STRATEGY_APPROVAL_MESSAGE),
        _message(
            14,
            role="card",
            kind="agent_decision",
            data=_decision(agent="Strategy Agent", decision="approve", input_ids=[STRATEGY_ID]),
        ),
    ]
    index = 15
    previous_execution_id = ""
    for offset in range(repair_count + 1):
        execution_id = EXECUTION_ID if offset == 0 else f"execution-artifact-{offset + 1}"
        execution_inputs = [GOAL_ID, EVIDENCE_ID, STRATEGY_ID]
        if previous_execution_id:
            execution_inputs.append(previous_execution_id)
        messages.append(
            _message(
                index,
                role="card",
                kind="agent_decision",
                data=_decision(
                    agent="Execution Agent",
                    decision="produce_artifact",
                    input_ids=execution_inputs,
                    output_ids=[execution_id],
                    stage="planning_execution",
                ),
            )
        )
        index += 1
        final_iteration = offset == repair_count
        critique_id = CRITIQUE_ID if final_iteration else f"critique-repair-{offset + 1}"
        messages.append(
            _message(
                index,
                role="card",
                kind="agent_decision",
                data=_decision(
                    agent="Critic Agent",
                    decision="approve" if final_iteration else "request_agent_revision",
                    input_ids=[GOAL_ID, EVIDENCE_ID, STRATEGY_ID, execution_id],
                    output_ids=[critique_id],
                    stage="planning_critique",
                ),
            )
        )
        index += 1
        previous_execution_id = execution_id
    messages.extend(
        [
            _message(index, role="card", kind="execution_blueprint_ready", data=_execution()),
            _message(index + 1, role="card", kind="critique_report_ready", data=_critique()),
            _message(
                index + 2,
                role="card",
                kind="planning_session_status",
                status={
                    "status": "waiting_execution_approval",
                    "businessStatus": "execution_pending",
                    "runtimeStatus": "idle",
                },
            ),
        ]
    )
    return {
        "id": THREAD_ID,
        "title": "fresh test",
        "messages": messages,
        "currentDraft": None,
        "createdAt": "2026-07-12T00:00:00Z",
        "updatedAt": "2026-07-12T00:00:00Z",
    }


def _run_replay(
    replay: dict[str, Any],
    *,
    scenario: Scenario | None = None,
    required_provider: str | None = None,
) -> tuple[Any, FakeCommandTransport]:
    transport = FakeCommandTransport([_design_stream(), _execution_stream()], replay)
    result = PlanningScenarioRunner(
        transport,
        required_provider=required_provider,
    ).run(scenario or _scenario())
    return result, transport


def _redesign_stream(strategy_artifact_id: str) -> list[dict[str, Any]]:
    return _stream(
        _decision_event(
            _decision(
                agent="Strategy Agent",
                decision="request_user_input",
                input_ids=[GOAL_ID, EVIDENCE_ID],
                output_ids=[strategy_artifact_id],
                stage="planning_strategy",
            )
        ),
        {"type": "strategy_portfolio_ready", "sessionId": SESSION_ID, "data": _strategy()},
        {
            "type": "planning_session_status",
            "sessionId": SESSION_ID,
            "status": "waiting_design_approval",
            "businessStatus": "strategy_pending",
            "runtimeStatus": "idle",
        },
    )


def test_runner_handles_clarification_approval_repair_and_replay() -> None:
    clarification = _stream(
        {
            "type": "goal_understanding",
            "intentState": "ambiguous_goal",
            "nextQuestion": "What is the concrete deliverable?",
        }
    )
    transport = FakeCommandTransport(
        [clarification, _design_stream(), _execution_stream()],
        _replay(repair_count=1),
    )

    result = PlanningScenarioRunner(transport).run(_scenario(requires_clarification=True))

    assert result.passed is True
    assert result.clarification_rounds == 1
    assert result.repair_count == 1
    assert result.final_status == "waiting_execution_approval"
    assert result.task_count == 1
    assert result.total_estimated_minutes == 90
    assert result.replay_verified is True
    assert result.strategy_approval_count == 1
    assert [item["stage"] for item in result.model_stage_summary] == [
        "goal_understanding",
        "planning_goal_model",
        "planning_reality",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
    ]
    assert all(
        set(item) == {"stage", "provider", "model", "proofCount"}
        and item["provider"] == "deepseek"
        for item in result.model_stage_summary
    )
    assert "threadId" not in transport.requests[0]
    assert all(request.get("threadId") == THREAD_ID for request in transport.requests[1:])
    assert INDEPENDENT_GOAL in transport.requests[1]["message"]
    assert transport.requests[2]["message"] == STRATEGY_APPROVAL_MESSAGE
    assert transport.replay_requests == [THREAD_ID]


def test_runner_recovers_model_failure_from_resume_node() -> None:
    unavailable = _stream(
        {
            "type": "planning_session_started",
            "sessionId": SESSION_ID,
            "status": "MODEL_UNAVAILABLE",
        },
        {
            "type": "planning_session_status",
            "sessionId": SESSION_ID,
            "status": "MODEL_UNAVAILABLE",
            "businessStatus": "strategy_pending",
            "runtimeStatus": "blocked_model",
            "modelFailure": {
                "stage": "strategy_design",
                "resumeNode": "strategy",
                "retryable": True,
                "automaticRetryAttempted": True,
                "attempts": [
                    {"provider": "deepseek", "status": "error", "errorType": "timeout"}
                ],
                "summary": {"zh": "暂时失败", "en": "Temporary failure"},
                "action": {"zh": "重试", "en": "Retry"},
            },
        },
    )
    transport = FakeCommandTransport(
        [unavailable, _design_stream(include_start=False), _execution_stream()],
        _replay(),
    )

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is True
    assert transport.requests[1]["message"] == RETRY_MESSAGE
    assert transport.requests[1]["threadId"] == THREAD_ID
    assert result.model_failures == [
        {
            "stage": "strategy_design",
            "resumeNode": "strategy",
            "retryable": True,
            "automaticRetryAttempted": True,
            "attempts": [
                {"provider": "deepseek", "status": "error", "errorType": "timeout"}
            ],
        }
    ]


def test_runner_retries_goal_understanding_before_session_creation() -> None:
    unavailable = _stream(
        {
            "type": "model_usage",
            "feature": "goal_understanding",
            "source": "model_unavailable",
            "usage": {"provider": "deepseek", "mode": "model_unavailable"},
        },
        {"type": "assistant_delta", "text": "Safe retry guidance"},
    )
    transport = FakeCommandTransport(
        [unavailable, _design_stream(), _execution_stream()],
        _replay(),
    )

    scenario = _scenario()
    result = PlanningScenarioRunner(transport).run(scenario)

    assert result.passed is True
    assert "threadId" not in transport.requests[0]
    assert transport.requests[1]["threadId"] == THREAD_ID
    assert transport.requests[1]["message"] == scenario.initial_message
    assert result.session_id == SESSION_ID


def test_runner_fails_when_sparse_first_turn_does_not_clarify() -> None:
    transport = FakeCommandTransport(
        [_design_stream(), _execution_stream()],
        _replay(),
    )

    result = PlanningScenarioRunner(transport).run(_scenario(requires_clarification=True))

    assert result.passed is False
    assert any("did not trigger real model clarification" in failure for failure in result.failures)
    assert transport.replay_requests == []


def test_runner_fails_when_critic_repairs_are_exhausted() -> None:
    revision = _stream(
        {"type": "execution_blueprint_ready", "sessionId": SESSION_ID, "data": _execution()},
        {
            "type": "critique_report_ready",
            "sessionId": SESSION_ID,
            "data": {**_critique(), "status": "blocked", "calendarWritable": False},
        },
        {
            "type": "planning_session_status",
            "sessionId": SESSION_ID,
            "status": "execution_revision",
            "businessStatus": "execution_pending",
            "runtimeStatus": "idle",
        },
    )
    transport = FakeCommandTransport([_design_stream(), revision], _replay())

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is False
    assert any("repair loop ended" in failure for failure in result.failures)
    assert transport.replay_requests == []


def test_runner_fails_closed_on_non_retryable_model_failure() -> None:
    unavailable = _stream(
        {
            "type": "planning_session_started",
            "sessionId": SESSION_ID,
            "status": "MODEL_UNAVAILABLE",
        },
        {
            "type": "planning_session_status",
            "sessionId": SESSION_ID,
            "status": "MODEL_UNAVAILABLE",
            "businessStatus": "goal_clarification",
            "runtimeStatus": "blocked_model",
            "modelFailure": {
                "stage": "goal_modeling",
                "resumeNode": "goal_intelligence",
                "retryable": False,
                "automaticRetryAttempted": False,
                "attempts": [
                    {"provider": "deepseek", "status": "error", "errorType": "auth_error"}
                ],
                "summary": {"zh": "不可用", "en": "Unavailable"},
                "action": {"zh": "检查设置", "en": "Check settings"},
            },
        },
    )
    transport = FakeCommandTransport([unavailable], _replay())

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is False
    assert len(transport.requests) == 1
    assert result.model_failures[0]["attempts"][0] == {
        "provider": "deepseek",
        "status": "error",
        "errorType": "auth_error",
    }


def test_runner_replay_requires_every_artifact_and_forbids_calendar() -> None:
    missing = _replay()
    missing["messages"] = [
        message for message in missing["messages"] if message["kind"] != "evidence_pack_ready"
    ]
    transport = FakeCommandTransport([_design_stream(), _execution_stream()], missing)

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is False
    assert any("evidence_pack_ready" in failure for failure in result.failures)

    calendar = _replay()
    calendar["messages"].append(
        _message(99, role="card", kind="calendar_plan_preview", data={"tasks": []})
    )
    transport = FakeCommandTransport([_design_stream(), _execution_stream()], calendar)

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is False
    assert any("calendar_plan_preview" in failure for failure in result.failures)


def test_runner_requires_exact_opening_as_first_persisted_user_message() -> None:
    replay = _replay()
    replay["messages"][0]["content"] = "A different opening"

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("first persisted user message" in failure for failure in result.failures)


def test_runner_requires_strict_artifact_stage_order() -> None:
    replay = _replay()
    reality_index = next(
        index
        for index, message in enumerate(replay["messages"])
        if message["kind"] == "reality_assessment_ready"
    )
    evidence_index = next(
        index
        for index, message in enumerate(replay["messages"])
        if message["kind"] == "evidence_pack_ready"
    )
    replay["messages"][reality_index], replay["messages"][evidence_index] = (
        replay["messages"][evidence_index],
        replay["messages"][reality_index],
    )

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("strict order" in failure for failure in result.failures)


def test_read_only_manifest_audit_uses_only_replay_get_and_discovers_session() -> None:
    transport = FakeCommandTransport([], _replay())
    runner = PlanningScenarioRunner(transport, required_provider="deepseek")

    results = audit_manifest_batch(
        [_scenario()],
        runner,
        {"mock": THREAD_ID},
    )

    assert results[0].passed is True
    assert results[0].thread_id == THREAD_ID
    assert results[0].session_id == SESSION_ID
    assert transport.requests == []
    assert transport.replay_requests == [THREAD_ID]


def test_read_only_manifest_audit_rejects_multiple_sessions() -> None:
    replay = _replay()
    goal_card = next(
        message for message in replay["messages"] if message["kind"] == "goal_model_updated"
    )
    goal_card["payload"]["sessionId"] = "foreign-session"
    transport = FakeCommandTransport([], replay)

    result = PlanningScenarioRunner(transport).audit_thread(_scenario(), THREAD_ID)

    assert result.passed is False
    assert any("exactly one Planning Session" in failure for failure in result.failures)
    assert transport.requests == []


def test_thread_manifest_loader_accepts_direct_and_wrapped_mapping(tmp_path: Any) -> None:
    direct_path = tmp_path / "direct.json"
    direct_path.write_text(json.dumps({"travel": "thread-travel"}), encoding="utf-8")
    wrapped_path = tmp_path / "wrapped.json"
    wrapped_path.write_text(
        json.dumps({
            "schemaVersion": 1,
            "sourceFingerprint": "a" * 64,
            "threads": {"go": "thread-go"},
        }),
        encoding="utf-8",
    )

    assert load_thread_manifest(direct_path) == {"travel": "thread-travel"}
    assert load_thread_manifest(wrapped_path) == {"go": "thread-go"}
    assert load_manifest_source_fingerprint(wrapped_path) == "a" * 64
    assert load_manifest_source_fingerprint(direct_path) == ""


def test_thread_manifest_loader_rejects_unknown_or_reused_threads(tmp_path: Any) -> None:
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(json.dumps({"unknown": "thread-1"}), encoding="utf-8")
    reused_path = tmp_path / "reused.json"
    reused_path.write_text(
        json.dumps({"travel": "thread-1", "go": "thread-1"}),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="unknown scenarios"):
        load_thread_manifest(unknown_path)
    with pytest.raises(Exception, match="reuses a Thread"):
        load_thread_manifest(reused_path)


def test_runner_does_not_accept_execution_or_calendar_control_messages() -> None:
    replay = _replay()
    replay["messages"].append(
        _message(100, role="user", kind="text", content="确认执行计划")
    )
    transport = FakeCommandTransport([_design_stream(), _execution_stream()], replay)

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is False
    assert any("approval boundary" in failure for failure in result.failures)


@pytest.mark.parametrize(
    ("target", "field", "value", "expected"),
    [
        ("goal_understanding", "mode", "local_fallback", "not produced in llm mode"),
        ("planning_evidence", "provider", "mock", "real provider and model"),
        ("planning_execution", "model", "local-model", "real provider and model"),
        ("planning_reality", "localFallbackAllowed", True, "disable local fallback"),
    ],
)
def test_runner_rejects_non_real_model_provenance(
    target: str,
    field: str,
    value: Any,
    expected: str,
) -> None:
    replay = _replay()
    if target == "goal_understanding":
        message = next(item for item in replay["messages"] if item["kind"] == "goal_understanding")
        message["payload"]["modelUsage"][field] = value
    else:
        message = next(
            item
            for item in replay["messages"]
            if item["kind"] == "agent_decision"
            and item["payload"]["data"].get("modelUsage", {}).get("taskType") == target
        )
        message["payload"]["data"]["modelUsage"][field] = value

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any(expected in failure for failure in result.failures)


def test_runner_rejects_missing_local_fallback_proof() -> None:
    replay = _replay()
    strategy_decision = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("modelUsage", {}).get("taskType") == "planning_strategy"
    )
    strategy_decision["payload"]["data"]["modelUsage"].pop("localFallbackAllowed")

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("disable local fallback" in failure for failure in result.failures)


def test_runner_requires_selected_provider_and_matching_success_attempt() -> None:
    result, _transport = _run_replay(_replay(), required_provider="deepseek")
    assert result.passed is True

    result, _transport = _run_replay(_replay(), required_provider="openai")
    assert result.passed is False
    assert any("required provider" in failure for failure in result.failures)

    replay = _replay()
    execution_decision = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("modelUsage", {}).get("taskType") == "planning_execution"
    )
    execution_decision["payload"]["data"]["modelUsage"]["attempts"][0]["provider"] = "openai"
    result, _transport = _run_replay(replay, required_provider="deepseek")
    assert result.passed is False
    assert any("successful attempt" in failure for failure in result.failures)


def test_report_model_summary_is_allowlisted_and_never_copies_credentials() -> None:
    replay = _replay()
    understanding = next(item for item in replay["messages"] if item["kind"] == "goal_understanding")
    understanding["payload"]["modelUsage"]["apiKey"] = "never-copy-this-secret"
    understanding["payload"]["modelUsage"]["attempts"][0]["apiKey"] = "never-copy-this-secret"

    result, _transport = _run_replay(replay, required_provider="deepseek")
    report = build_report(
        [result],
        base_url="http://127.0.0.1:8000",
        smoke_only=True,
        required_provider="deepseek",
    )
    rendered = json.dumps(report)
    unsafe_label_report = build_report(
        [result],
        base_url="http://127.0.0.1:8000",
        smoke_only=True,
        required_provider="sk-never-copy-this-secret",
    )

    assert result.passed is True
    assert "never-copy-this-secret" not in rendered
    assert "never-copy-this-secret" not in json.dumps(unsafe_label_report)
    assert unsafe_label_report["requiredProvider"] is None
    assert report["requiredProvider"] == "deepseek"
    assert all(
        set(item) == {"stage", "provider", "model", "proofCount"}
        for item in report["results"][0]["model_stage_summary"]
    )


def test_model_provenance_rejects_credential_shaped_labels_without_reporting_them() -> None:
    replay = _replay()
    understanding = next(item for item in replay["messages"] if item["kind"] == "goal_understanding")
    understanding["payload"]["modelUsage"]["model"] = "sk-never-copy-this-secret"

    result, _transport = _run_replay(replay, required_provider="deepseek")
    report = build_report(
        [result],
        base_url="http://127.0.0.1:8000",
        smoke_only=True,
        required_provider="deepseek",
    )

    assert result.passed is False
    assert "never-copy-this-secret" not in json.dumps(report)


@pytest.mark.parametrize("capacity", [80, 121])
def test_runner_rejects_execution_outside_fixed_capacity(capacity: int) -> None:
    result, _transport = _run_replay(
        _replay(),
        scenario=_scenario(time_capacity_minutes=capacity),
    )

    assert result.passed is False
    assert any("75%-100%" in failure for failure in result.failures)


def test_runner_accepts_execution_at_capacity_lower_bound() -> None:
    result, _transport = _run_replay(
        _replay(),
        scenario=_scenario(time_capacity_minutes=120),
    )

    assert result.passed is True
    assert result.total_estimated_minutes == 90


def test_runner_accepts_budget_summary_and_reports_only_allocated_total() -> None:
    replay = _replay()
    execution = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )["payload"]["data"]
    execution["budgetSummary"] = {
        "spendingLimitCny": 100,
        "allocations": [
            {"category": "Transport", "amountCny": 60},
            {"category": "Lodging", "amountCny": 30},
        ],
    }

    result, _transport = _run_replay(
        replay,
        scenario=_scenario(spending_limit_cny=100),
    )
    report = build_report([result], base_url="http://127.0.0.1:8000", smoke_only=True)
    rendered = json.dumps(report)

    assert result.passed is True
    assert result.budget_allocated_cny == 90
    assert report["results"][0]["budgetAllocatedCny"] == 90
    assert "budget_allocated_cny" not in report["results"][0]
    assert "Transport" not in rendered
    assert "Lodging" not in rendered
    assert "allocations" not in rendered


@pytest.mark.parametrize(
    ("budget_summary", "expected"),
    [
        (None, "missing the required budgetSummary"),
        (
            {
                "spendingLimitCny": 100,
                "allocations": [
                    {"category": "Transport", "amountCny": 70},
                    {"category": "Lodging", "amountCny": 31},
                ],
            },
            "exceed the fixed spending limit",
        ),
        (
            {
                "spendingLimitCny": 100,
                "allocations": [
                    {"category": "Lodging", "amountCny": 40},
                    {"category": "lodging", "amountCny": 30},
                ],
            },
            "duplicate allocation categories",
        ),
    ],
)
def test_runner_rejects_missing_excess_or_duplicate_budget_allocations(
    budget_summary: dict[str, Any] | None,
    expected: str,
) -> None:
    replay = _replay()
    execution = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )["payload"]["data"]
    if budget_summary is not None:
        execution["budgetSummary"] = budget_summary

    result, _transport = _run_replay(
        replay,
        scenario=_scenario(spending_limit_cny=100),
    )

    assert result.passed is False
    assert any(expected in failure for failure in result.failures)


def test_domain_keywords_are_limited_to_deliverable_fields_and_forbidden_is_any_hit() -> None:
    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    task = execution_card["payload"]["data"]["tasks"][0]
    task.update(
        {
            "title": "Deliver feature",
            "purpose": "Build a REST API, but do not claim it as a delivered result.",
            "actionSteps": ["Implement one endpoint", "Run its test"],
            "deliverable": "A tested endpoint",
            "completionEvidence": ["Passing output"],
        }
    )
    result, _transport = _run_replay(replay)
    assert result.passed is False
    assert any("domain requirements" in failure for failure in result.failures)

    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    execution_card["payload"]["data"]["tasks"][0]["title"] += " with Docker"
    result, _transport = _run_replay(
        replay,
        scenario=_scenario(forbidden_keyword_groups=(("docker", "github"),)),
    )
    assert result.passed is False
    assert any("cross-domain contamination" in failure for failure in result.failures)


def test_supporting_keywords_can_use_schedule_and_risks_but_hard_delivery_cannot_use_fallback() -> None:
    result, _transport = _run_replay(
        _replay(),
        scenario=_scenario(
            supporting_keyword_groups=(("week 1",), ("first test",)),
        ),
    )
    assert result.passed is True

    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    execution_card["payload"]["data"]["tasks"][0]["fallbackAction"] = (
        "Use Docker if the normal delivery path is blocked."
    )
    result, _transport = _run_replay(
        replay,
        scenario=_scenario(keyword_groups=(("docker",),)),
    )
    assert result.passed is False
    assert any("domain requirements" in failure for failure in result.failures)


def test_supporting_keywords_include_execution_narrative_and_plan_level_risk_context() -> None:
    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    execution_card["payload"]["data"]["narrative"]["riskHandling"] = (
        "Autumn weather risk is handled with an explicit disruption fallback."
    )

    result, _transport = _run_replay(
        replay,
        scenario=_scenario(
            supporting_keyword_groups=(("autumn",), ("disruption fallback",)),
        ),
    )

    assert result.passed is True


def test_travel_autumn_requirement_accepts_an_explicit_autumn_month() -> None:
    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    task = execution_card["payload"]["data"]["tasks"][0]
    task["scheduleWindow"] = "11月中下旬"

    result, _transport = _run_replay(
        replay,
        scenario=_scenario(supporting_keyword_groups=(("秋天", "11月", "november"),)),
    )

    assert result.passed is True


def test_forbidden_keyword_audit_distinguishes_boundary_from_positive_action() -> None:
    forbidden = (("买入股票", "trade securities"),)
    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    execution_card["payload"]["data"]["tasks"][0]["actionSteps"].append("不得买入股票")
    result, _transport = _run_replay(
        replay,
        scenario=_scenario(forbidden_keyword_groups=forbidden),
    )
    assert result.passed is True

    replay = _replay()
    execution_card = next(
        item for item in replay["messages"] if item["kind"] == "execution_blueprint_ready"
    )
    execution_card["payload"]["data"]["tasks"][0]["actionSteps"].append("买入股票")
    result, _transport = _run_replay(
        replay,
        scenario=_scenario(forbidden_keyword_groups=forbidden),
    )
    assert result.passed is False
    assert any("cross-domain contamination" in failure for failure in result.failures)


def test_runner_reapproves_each_new_strategy_artifact_and_binds_final_critic_version() -> None:
    second_strategy_id = "strategy-artifact-2"
    replay = _replay(repair_count=1)
    replay["messages"].extend(
        [
            _message(
                90,
                role="card",
                kind="agent_decision",
                data=_decision(
                    agent="Strategy Agent",
                    decision="request_user_input",
                    input_ids=[GOAL_ID, EVIDENCE_ID],
                    output_ids=[second_strategy_id],
                    stage="planning_strategy",
                ),
            ),
            _message(91, role="user", kind="text", content=STRATEGY_APPROVAL_MESSAGE),
            _message(
                92,
                role="card",
                kind="agent_decision",
                data=_decision(
                    agent="Strategy Agent",
                    decision="approve",
                    input_ids=[second_strategy_id],
                ),
            ),
        ]
    )
    for message in replay["messages"]:
        if message["kind"] != "agent_decision":
            continue
        data = message["payload"]["data"]
        if data.get("agent") == "Execution Agent" and data.get("outputArtifactIds") == ["execution-artifact-2"]:
            # An upstream Strategy repair can legitimately start a new Execution
            # branch without listing the old Execution/Critique artifacts.
            data["inputArtifactIds"] = [GOAL_ID, EVIDENCE_ID, second_strategy_id]
        if data.get("agent") == "Critic Agent" and data.get("decision") == "approve":
            data["inputArtifactIds"] = [GOAL_ID, EVIDENCE_ID, second_strategy_id, "execution-artifact-2"]

    final_stream = _execution_stream()
    approval_event = next(
        event
        for event in final_stream
        if event.get("type") == "agent_decision"
        and event.get("data", {}).get("agent") == "Strategy Agent"
        and event.get("data", {}).get("decision") == "approve"
    )
    approval_event["data"]["inputArtifactIds"] = [second_strategy_id]
    transport = FakeCommandTransport(
        [_design_stream(), _redesign_stream(second_strategy_id), final_stream],
        replay,
    )

    result = PlanningScenarioRunner(transport).run(_scenario())

    assert result.passed is True
    assert result.strategy_approval_count == 2
    assert result.repair_count == 1
    assert [request["message"] for request in transport.requests[1:]] == [
        STRATEGY_APPROVAL_MESSAGE,
        STRATEGY_APPROVAL_MESSAGE,
    ]


def test_runner_rejects_unbound_strategy_or_stale_critic_artifact() -> None:
    replay = _replay()
    strategy_approval = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Strategy Agent"
        and item["payload"]["data"].get("decision") == "approve"
    )
    strategy_approval["payload"]["data"]["inputArtifactIds"] = ["stale-strategy"]
    result, _transport = _run_replay(replay)
    assert result.passed is False
    assert any("uniquely paired" in failure for failure in result.failures)

    replay = _replay(repair_count=1)
    final_critic = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Critic Agent"
        and item["payload"]["data"].get("decision") == "approve"
    )
    final_critic["payload"]["data"]["inputArtifactIds"] = [EXECUTION_ID]
    result, _transport = _run_replay(replay)
    assert result.passed is False
    assert any("Critic decision" in failure or "Critic approval" in failure for failure in result.failures)


def test_runner_rejects_one_critic_decision_bound_to_multiple_executions() -> None:
    replay = _replay(repair_count=1)
    final_critic = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Critic Agent"
        and item["payload"]["data"].get("decision") == "approve"
    )
    final_critic["payload"]["data"]["inputArtifactIds"].append(EXECUTION_ID)

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("exactly one Execution" in failure for failure in result.failures)


def test_runner_rejects_reused_critique_artifact_output() -> None:
    replay = _replay(repair_count=1)
    critic_decisions = [
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Critic Agent"
    ]
    critic_decisions[-1]["payload"]["data"]["outputArtifactIds"] = list(
        critic_decisions[0]["payload"]["data"]["outputArtifactIds"]
    )

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("reused the same Critique" in failure for failure in result.failures)


def test_runner_ignores_unreviewed_critic_safety_slot_until_model_review_succeeds() -> None:
    replay = _replay()
    final_critic_index = next(
        index
        for index, item in enumerate(replay["messages"])
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Critic Agent"
    )
    replay["messages"].insert(
        final_critic_index,
        _message(
            998,
            role="card",
            kind="agent_decision",
            data={
                "id": "decision-critic-unavailable",
                "agent": "Critic Agent",
                "decision": "block",
                "reason": "Independent Critic model unavailable",
                "inputArtifactIds": [EXECUTION_ID],
                "outputArtifactIds": [CRITIQUE_ID],
                "modelUsage": {},
            },
        ),
    )

    result, _transport = _run_replay(replay, required_provider="deepseek")

    assert result.passed is True
    critique_summary = next(
        item for item in result.model_stage_summary if item["stage"] == "planning_critique"
    )
    assert critique_summary["proofCount"] == 1


def test_runner_rejects_unique_critic_approval_of_a_non_latest_execution() -> None:
    replay = _replay(repair_count=1)
    critic_decisions = [
        item["payload"]["data"]
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("agent") == "Critic Agent"
    ]
    critic_decisions[0]["decision"] = "approve"
    critic_decisions[-1]["decision"] = "request_agent_revision"

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("stale Execution" in failure for failure in result.failures)


def test_runner_rejects_passed_critique_with_major_issue() -> None:
    replay = _replay()
    critique = next(
        item for item in replay["messages"] if item["kind"] == "critique_report_ready"
    )
    critique["payload"]["data"]["issues"] = [
        {"severity": "major", "description": "A required deliverable is missing."}
    ]

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("major issue" in failure for failure in result.failures)


def test_runner_rejects_a_nominal_pass_below_the_high_quality_score() -> None:
    replay = _replay()
    critique = next(
        item for item in replay["messages"] if item["kind"] == "critique_report_ready"
    )
    critique["payload"]["data"]["score"] = 89

    result, _transport = _run_replay(replay)

    assert result.passed is False
    assert any("high-quality threshold of 90" in failure for failure in result.failures)


def test_repair_count_accepts_block_with_followup_execution_and_public_status() -> None:
    replay = _replay(repair_count=1)
    revision = next(
        item
        for item in replay["messages"]
        if item["kind"] == "agent_decision"
        and item["payload"]["data"].get("decision") == "request_agent_revision"
    )
    revision["payload"]["data"]["decision"] = "block"
    status = next(
        item for item in reversed(replay["messages"])
        if item["kind"] == "planning_session_status"
    )
    status["payload"]["repairCount"] = 1

    result, _transport = _run_replay(replay)

    assert result.passed is True
    assert result.repair_count == 1


def test_full_acceptance_requires_all_ten_unique_directions_and_no_only_flag() -> None:
    results = [
        ScenarioResult(key=scenario.key, direction=scenario.direction, passed=True)
        for scenario in SCENARIOS
    ]
    results[0].started_at = "2026-07-12T00:00:00+00:00"
    results[0].completed_at = "2026-07-12T00:01:00+00:00"
    for result in results[1:]:
        result.started_at = "2026-07-12T00:02:00+00:00"
        result.completed_at = "2026-07-12T00:03:00+00:00"
    missing_provider = build_report(results, base_url="http://127.0.0.1:8000")
    wrong_provider = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        required_provider="openai",
    )
    full = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        required_provider="deepseek",
        read_only_audit=True,
        source_fingerprint="a" * 64,
        frozen_source_verified=True,
    )
    programmatic = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        required_provider="deepseek",
        source_fingerprint="a" * 64,
        frozen_source_verified=True,
    )
    smoke = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        smoke_only=True,
        required_provider="deepseek",
    )
    partial = build_report(
        results[:-1],
        base_url="http://127.0.0.1:8000",
        required_provider="deepseek",
    )
    duplicate_direction_results = deepcopy(results)
    duplicate_direction_results[-1].direction = duplicate_direction_results[0].direction
    duplicate = build_report(
        duplicate_direction_results,
        base_url="http://127.0.0.1:8000",
        required_provider="deepseek",
    )

    assert missing_provider["summary"]["fullAcceptancePassed"] is False
    assert wrong_provider["summary"]["fullAcceptancePassed"] is False
    assert full["summary"]["fullAcceptancePassed"] is True
    assert programmatic["summary"]["fullAcceptancePassed"] is False
    assert full["summary"]["canaryOrderPassed"] is True
    assert full["summary"]["frozenSourceVerified"] is True
    assert full["summary"]["performanceTargetPassed"] is False
    assert full["summary"]["smokeOnly"] is False
    assert smoke["summary"]["allPassed"] is True
    assert smoke["summary"]["smokeOnly"] is True
    assert smoke["summary"]["fullAcceptancePassed"] is False
    assert partial["summary"]["allPassed"] is True
    assert partial["summary"]["fullAcceptancePassed"] is False
    assert duplicate["summary"]["fullAcceptancePassed"] is False


def test_report_marks_two_lane_batch_inside_performance_target() -> None:
    results: list[ScenarioResult] = []
    for index, scenario in enumerate(SCENARIOS):
        if index == 0:
            start_minute, end_minute = 0, 4
        else:
            start_minute = 4 + ((index - 1) // 2) * 4
            end_minute = start_minute + 4
        results.append(
            ScenarioResult(
                key=scenario.key,
                direction=scenario.direction,
                passed=True,
                started_at=f"2026-07-12T00:{start_minute:02d}:00+00:00",
                completed_at=f"2026-07-12T00:{end_minute:02d}:00+00:00",
                wall_time_ms=4 * 60 * 1000,
                model_request_count=7,
                model_latency_ms=1000,
                generation_modes=["single_pass"],
            )
        )
    for result in results:
        result.request_intervals = [
            {
                "startedAt": result.started_at,
                "completedAt": result.completed_at,
            }
        ]

    report = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        required_provider="deepseek",
        read_only_audit=True,
        source_fingerprint="a" * 64,
        frozen_source_verified=True,
    )

    summary = report["summary"]
    assert report["schemaVersion"] == 3
    assert summary["fullAcceptancePassed"] is True
    assert summary["performanceTargetPassed"] is True
    assert summary["batchWallTimeMs"] == 24 * 60 * 1000
    assert summary["modelRequestCount"] == 70
    assert summary["maxObservedConcurrent"] == 2
    assert summary["concurrencyUtilization"] == 0.8333
    assert summary["generationModes"] == ["single_pass"]


def test_replay_audit_collects_sanitized_model_performance_metrics() -> None:
    replay = _replay()
    replay["messages"][0]["createdAt"] = "2026-07-12T00:00:00Z"
    status_message = next(
        message
        for message in reversed(replay["messages"])
        if message["kind"] == "planning_session_status"
    )
    status_message["createdAt"] = "2026-07-12T00:05:00Z"

    usages: list[dict[str, Any]] = []
    for message in replay["messages"]:
        if message["kind"] == "goal_understanding":
            usages.append(message["payload"]["modelUsage"])
        elif message["kind"] == "agent_decision":
            usage = message["payload"].get("data", {}).get("modelUsage")
            if isinstance(usage, dict):
                usages.append(usage)
    for usage in usages:
        usage["latencyMs"] = 100
    execution_usage = next(
        usage for usage in usages if usage["taskType"] == "planning_execution"
    )
    execution_usage["generationMode"] = "single_pass"
    success = execution_usage["attempts"][-1]
    execution_usage["attempts"] = [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "status": "error",
            "errorType": "rate_limit",
            "latencyMs": 10,
        },
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "status": "error",
            "errorType": "model_output_truncated",
            "latencyMs": 15,
        },
        {
            "provider": "kimi",
            "model": "moonshot-v1",
            "status": "skipped",
            "errorType": "missing_api_key",
            "latencyMs": 999,
        },
        {
            **success,
            "latencyMs": 30,
            "automaticRetry": True,
            "retryReason": "contract_validation",
        },
    ]
    final_status_index = next(
        index
        for index, message in reversed(list(enumerate(replay["messages"])))
        if message["kind"] == "planning_session_status"
    )
    replay["messages"].insert(
        final_status_index,
        _message(
            999,
            role="card",
            kind="planning_session_status",
            status={
                "status": "MODEL_UNAVAILABLE",
                "modelFailure": {
                    "stage": "planning_evidence",
                    "resumeNode": "evidence",
                    "retryable": True,
                    "attempts": [
                        {
                            "provider": "deepseek",
                            "model": "deepseek-v4-flash",
                            "status": "error",
                            "errorType": "timeout",
                            "latencyMs": 40,
                        }
                    ],
                },
            },
        ),
    )

    transport = FakeCommandTransport([], replay)
    result = PlanningScenarioRunner(
        transport,
        required_provider="deepseek",
    ).audit_thread(_scenario(), THREAD_ID)

    assert result.passed is True
    assert result.wall_time_ms == 5 * 60 * 1000
    assert result.model_latency_ms == (len(usages) - 1) * 100 + 55 + 40
    assert result.model_request_count == len(usages) + 3
    assert result.rate_limit_count == 1
    assert result.truncation_count == 1
    assert result.automatic_retry_count == 1
    assert result.contract_repair_count == 1
    assert result.generation_modes == ["single_pass"]
    evidence_metrics = next(
        item for item in result.stage_performance if item["stage"] == "planning_evidence"
    )
    assert evidence_metrics["modelRequestCount"] == 2


def test_performance_concurrency_excludes_human_wait_time() -> None:
    results = [
        ScenarioResult(
            key="one",
            direction="One",
            passed=True,
            started_at="2026-07-12T00:00:00+00:00",
            completed_at="2026-07-12T00:10:00+00:00",
            request_intervals=[
                {
                    "startedAt": "2026-07-12T00:00:00+00:00",
                    "completedAt": "2026-07-12T00:01:00+00:00",
                }
            ],
        ),
        ScenarioResult(
            key="two",
            direction="Two",
            passed=True,
            started_at="2026-07-12T00:00:30+00:00",
            completed_at="2026-07-12T00:09:00+00:00",
            request_intervals=[
                {
                    "startedAt": "2026-07-12T00:02:00+00:00",
                    "completedAt": "2026-07-12T00:03:00+00:00",
                }
            ],
        ),
    ]

    summary = build_report(
        results,
        base_url="http://127.0.0.1:8000",
        smoke_only=True,
    )["summary"]

    assert summary["batchWallTimeMs"] == 10 * 60 * 1000
    assert summary["maxObservedConcurrent"] == 1
    assert summary["concurrencyUtilization"] == 0.1


def test_http_auditor_has_no_post_capability_and_report_redacts_origins() -> None:
    transport = HttpAuditTransport(
        "http://user:sk-never-copy-this-secret@127.0.0.1:8000/private?api_key=secret"
    )
    try:
        assert not hasattr(transport, "chat")
    finally:
        transport.close()

    report = build_report(
        [ScenarioResult(key="mock", direction="Mock")],
        base_url="http://user:sk-never-copy-this-secret@127.0.0.1:8000/private?api_key=secret",
        smoke_only=True,
    )
    rendered = json.dumps(report)

    assert report["baseUrl"] == "http://127.0.0.1:8000"
    assert "never-copy-this-secret" not in rendered
    assert "api_key" not in rendered


def test_scenarios_use_only_locked_capacity_and_persona_facts() -> None:
    by_key = {scenario.key: scenario for scenario in SCENARIOS}
    assert {
        key: scenario.time_capacity_minutes
        for key, scenario in by_key.items()
        if scenario.time_capacity_minutes is not None
    } == {
        "go": 7200,
        "python": 7200,
        "spoken_english": 5760,
        "job_search": 5760,
        "photography": 2400,
    }
    assert {
        key: scenario.spending_limit_cny
        for key, scenario in by_key.items()
        if scenario.spending_limit_cny is not None
    } == {"travel": 20000, "photography": 1000}
    assert any("秋天" in group for group in by_key["travel"].supporting_keyword_groups)
    assert any("风险" in group for group in by_key["travel"].supporting_keyword_groups)
    assert not any("秋天" in group for group in by_key["travel"].keyword_groups)
    for key in ("swimming", "skiing", "fitness"):
        assert any("停止" in group for group in by_key[key].supporting_keyword_groups)
        assert not any("停止" in group for group in by_key[key].keyword_groups)
    assert any("专业评估" in group for group in by_key["fitness"].supporting_keyword_groups)
    disallowed_facts = {
        "travel": ("护照", "签证", "步行", "饮食限制"),
        "go": ("Windows", "免费资源"),
        "swimming": ("短暂漂浮", "每次60分钟", "健康禁忌"),
        "skiing": ("每周两次体能", "健康禁忌", "眩晕"),
        "spoken_english": ("Windows", "免费资源", "录音作品"),
        "job_search": ("在职", "不自动投递", "不写入日历"),
        "fitness": ("没有已知运动禁忌",),
        "household_budget": ("没有债务",),
        "photography": ("现有手机",),
    }
    for key, phrases in disallowed_facts.items():
        combined = by_key[key].initial_message + by_key[key].persona_response
        assert not any(phrase in combined for phrase in phrases)
