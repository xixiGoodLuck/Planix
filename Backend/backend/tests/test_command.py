import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.db import get_conn, init_db
from app.schemas import (
    AgentDecision,
    AgentMessage,
    CommandDecision,
    GoalUnderstandingResult,
    MemoryCreate,
    ModelRouteAttempt,
    ModelUsage,
    PlanningSessionResponse,
    RefinedTask,
)
from app.services.command_agent import CommandAgentService, detect_command_intent, resolve_command_intent
from app.services.llm import LlmResult
from app.services.memory_store import MemoryService


def _events(response):
    lines = response.text.strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _approve_required_action(client, response, *, permission="low"):
    events = _events(response)
    approval = next(event for event in events if event["type"] == "approval_required")
    thread_id = events[-1]["threadId"]
    return client.post(
        "/api/command/approve",
        json={
            "threadId": thread_id,
            "actionId": approval["actionId"],
            "decision": "approve",
            "permission": permission,
        },
    )


def _valid_structured_plan():
    return {
        "goalTitle": "AI internship prep",
        "goalDescription": "Prepare portfolio material for interviews.",
        "durationDays": 5,
        "milestones": [
            {
                "title": "Project story",
                "description": "Prepare Planix talking points.",
                "tasks": [
                    {
                        "title": "Draft Planix project intro",
                        "description": "Write a concise project overview.",
                        "estimatedMinutes": 60,
                        "dueDate": "2026-07-05",
                        "priority": "high",
                    }
                ],
            },
            {
                "title": "Interview practice",
                "description": "Practice answers.",
                "tasks": [
                    {
                        "title": "Practice Agent Runtime explanation",
                        "description": "Explain Runtime event flow.",
                        "estimatedMinutes": 45,
                        "dueDate": "2026-07-06",
                        "priority": "medium",
                    }
                ],
            },
        ],
        "reviewPlan": {"frequency": "daily", "questions": ["What improved?"]},
    }


class FakeRuntime:
    calls = 0
    last_input = ""

    def __init__(self, structured_plan=None):
        self.structured_plan = structured_plan if structured_plan is not None else _valid_structured_plan()

    def run(self, payload):
        FakeRuntime.calls += 1
        FakeRuntime.last_input = payload.input
        run_id = f"run_fake_{FakeRuntime.calls}"
        for name in ("get_memory", "get_today_plans", "search_materials"):
            yield json.dumps({"runId": run_id, "type": "node", "title": name, "status": "running"}) + "\n"
            yield json.dumps({"runId": run_id, "type": "tool", "toolCall": {"name": name, "input": {}, "output": {}}}) + "\n"
        yield json.dumps({"runId": run_id, "type": "node", "title": "propose_tasks", "status": "running"}) + "\n"
        yield json.dumps({
            "runId": run_id,
            "type": "tool",
            "toolCall": {
                "name": "propose_tasks",
                "input": {"goal": payload.input},
                "output": {
                    "mode": "local_fallback",
                    "structuredPlan": self.structured_plan,
                    "tasks": [],
                    "sources": [],
                    "fallbackReason": "test",
                    "planHorizon": {
                        "rawText": "AI internship prep",
                        "durationDays": 5,
                        "horizonType": "weekly",
                        "startDate": "2026-07-05",
                        "endDate": "2026-07-09",
                        "expectedMilestoneCount": 1,
                        "expectedMinTaskCount": 3,
                        "expectedWeekCount": 1,
                    },
                    "qualityReport": {
                        "ok": True,
                        "score": 94,
                        "totalTasks": 2,
                        "milestoneCount": 2,
                        "coveredWeekCount": 1,
                        "dateSpanDays": 2,
                        "issues": [],
                        "metrics": {
                            "durationDays": 5,
                            "totalTasks": 2,
                            "milestoneCount": 2,
                            "coveredWeekCount": 1,
                            "dateSpanDays": 2,
                            "weakTaskCount": 0,
                            "missingDueDateCount": 0,
                            "outOfRangeDueDateCount": 0,
                            "repairAttempted": False,
                            "fallbackUsed": True,
                            "qualityStatus": "local_fallback",
                            "sourceType": "local_fallback",
                            "localRelevance": "low",
                        },
                    },
                    "qualityStatus": "local_fallback",
                    "sourceType": "local_fallback",
                    "localRelevance": "low",
                },
            },
        }) + "\n"


class ExplodingRuntime:
    calls = 0

    def run(self, payload):
        ExplodingRuntime.calls += 1
        raise AssertionError("Runtime should not be called")


def _patch_runtime(monkeypatch, runtime_factory):
    monkeypatch.setattr("app.services.command_agent.RuntimeOrchestrator", runtime_factory)


def _patch_decision(monkeypatch, decision: CommandDecision | None = None, *, error: str = ""):
    class FixedDecisionService:
        def decide(self, *args, **kwargs):
            task_type = kwargs.get("task_type", "command_decision")
            usage = ModelUsage(
                provider="test",
                model="router",
                promptTokens=10,
                completionTokens=5,
                totalTokens=15,
                latencyMs=7,
                mode="llm" if decision else "local_fallback",
                taskType=task_type,
            )
            return SimpleNamespace(
                decision=decision,
                usage=usage,
                source="llm" if decision else "local_fallback",
                error=error,
            )

    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: FixedDecisionService())


def _patch_goal_understanding(monkeypatch, *results: GoalUnderstandingResult):
    queued_results = list(results)

    class FixedGoalUnderstandingService:
        calls = []

        def understand(self, *args, **kwargs):
            FixedGoalUnderstandingService.calls.append((args, kwargs))
            if not queued_results:
                raise AssertionError("GoalUnderstandingService received more calls than expected")
            return SimpleNamespace(
                result=queued_results.pop(0),
                usage=ModelUsage(
                    provider="test",
                    model="goal-understanding",
                    promptTokens=12,
                    completionTokens=8,
                    totalTokens=20,
                    latencyMs=9,
                    mode="llm",
                    taskType="goal_understanding",
                ),
                source="llm",
                error="",
            )

    monkeypatch.setattr(
        "app.services.command_agent.GoalUnderstandingService",
        lambda: FixedGoalUnderstandingService(),
    )
    return FixedGoalUnderstandingService


def _patch_decision_must_not_run(monkeypatch):
    class ExplodingDecisionService:
        def decide(self, *args, **kwargs):
            raise AssertionError("ambiguous goal understanding must stop before CommandDecision")

    monkeypatch.setattr(
        "app.services.command_agent.CommandDecisionService",
        lambda: ExplodingDecisionService(),
    )


def _goal_understanding_payload(item: dict) -> dict:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data


def _assert_goal_understanding_stops_routing(client, response, *, location: str | None = None) -> dict:
    assert response.status_code == 200
    events = _events(response)
    understanding_events = [event for event in events if event["type"] == "goal_understanding"]
    assert len(understanding_events) == 1, response.text
    payload = _goal_understanding_payload(understanding_events[0])

    if location:
        assert payload["knownFacts"]["location"] == location

    forbidden_event_types = {
        "command_decision",
        "planning_session_started",
        "user_need_contract",
        "goal_model_updated",
        "reality_assessment_ready",
        "evidence_pack_ready",
        "strategy_portfolio_ready",
        "plan_design_proposal",
        "execution_blueprint_ready",
        "execution_plan_draft",
        "runtime_started",
        "runtime_event",
        "draft_created",
    }
    assert forbidden_event_types.isdisjoint({event["type"] for event in events})
    assert '"targetType":"unknown"' not in response.text
    assert "local_fallback" not in response.text

    thread_id = events[-1]["threadId"]
    replay = client.get(f"/api/command/thread/{thread_id}")
    assert replay.status_code == 200
    understanding_cards = [
        message for message in replay.json()["messages"]
        if message.get("kind") == "goal_understanding"
    ]
    assert len(understanding_cards) == 1
    replay_payload = _goal_understanding_payload(understanding_cards[0])
    assert replay_payload["intentState"] == payload["intentState"]
    assert replay_payload.get("clarificationOptions", []) == payload.get("clarificationOptions", [])

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0
    return payload


def _ambiguous_destination_result(location: str) -> GoalUnderstandingResult:
    return GoalUnderstandingResult.model_validate(
        {
            "intentState": "ambiguous_goal",
            "understoodIntent": f"用户计划前往{location}，但目的尚不明确。",
            "possibleDomains": ["travel", "career", "relocation", "other"],
            "knownFacts": {"location": location},
            "uncertainties": [
                {
                    "field": "purpose",
                    "impact": "不同目的会改变规划策略、约束和下一步行动。",
                }
            ],
            "consistencyWarnings": [],
            "nextQuestion": f"你去{location}主要是旅游、工作、学习、长期居住，还是其他目的？",
            "clarificationOptions": ["旅游", "工作", "学习", "长期居住"],
            "confidence": 0.56,
        }
    )


DEFAULT_PLANNING_MESSAGE = "\u5e2e\u6211\u89c4\u5212\u672c\u5468 AI \u5b9e\u4e60\u51c6\u5907"


def _create_workbench_draft(client, message: str = DEFAULT_PLANNING_MESSAGE):
    return client.post("/api/command/chat", json={"mode": "workbench", "message": message})


class FakePlanningService:
    calls = []

    def refine_task(self, payload):
        FakePlanningService.calls.append(payload)
        return RefinedTask(
            title=payload.task_title,
            objective=f"Refine {payload.task_title}",
            estimatedMinutes=payload.available_minutes or 60,
            steps=["Step one", "Step two", "Step three"],
            checklist=["Check one", "Check two"],
            acceptanceCriteria=["Done"],
            deliverable="A concrete output",
            risks=[],
            fallbackTips=[],
            mode="local_fallback",
        )


PY_STUDY = "Python \u5b66\u4e60"
MSG_TODAY_PLANS = "\u6211\u4eca\u5929\u6709\u4ec0\u4e48\u5b89\u6392\uff1f"
MSG_FIND_PYTHON = "\u5e2e\u6211\u627e Python \u8ba1\u5212"
MSG_PATCH_TO_DAY_AFTER = "\u628a\u660e\u5929\u7684 Python \u5b66\u4e60\u6539\u5230\u540e\u5929"
MSG_PATCH_TO_DAY_AFTER_30 = "\u628a\u660e\u5929\u7684 Python \u5b66\u4e60\u6539\u5230\u540e\u5929\uff0c\u6539\u6210 30 \u5206\u949f"
MSG_DELETE_TOMORROW = "\u5220\u9664\u660e\u5929\u7684 Python \u5b66\u4e60"
MSG_PATCH_AMBIGUOUS = "\u628a\u660e\u5929\u7684\u4efb\u52a1\u6539\u6210 30 \u5206\u949f"
MSG_PATCH_FIRST = "\u628a\u7b2c\u4e00\u4e2a\u6539\u6210 30 \u5206\u949f"
MSG_FIND_PYTHON_AI = "\u627e\u4e00\u4e0b Python AI \u5b9e\u4e60\u8d44\u6599\u548c\u8ba1\u5212"


def test_command_chat_streams_and_replays_thread(client):
    response = client.post("/api/command/chat", json={"message": "Planix 这个项目怎么介绍？"})

    assert response.status_code == 200
    events = _events(response)
    assert events[0]["type"] == "thread"
    assert any(event["type"] == "assistant_delta" for event in events)
    assert events[-1]["type"] == "done"
    thread_id = events[-1]["threadId"]

    thread = client.get(f"/api/command/thread/{thread_id}")
    assert thread.status_code == 200
    body = thread.json()
    assert body["id"] == thread_id
    assert len(body["messages"]) >= 2
    assert body["messages"][0]["role"] == "user"
    assert any(message["role"] == "assistant" for message in body["messages"])
    assert body["currentDraft"] is None
    assert "drafts" not in body
    assert "actions" not in body
    assert "outputs" not in body


def test_command_schema_migrations_include_phase_44_columns(client):
    client.get("/api/health")
    with get_conn() as conn:
        draft_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_drafts)").fetchall()}
        message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_messages)").fetchall()}
        action_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_actions)").fetchall()}
        approval_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_approvals)").fetchall()}
        assert "source_run_id" in draft_columns
        assert {"kind", "payload_json"} <= message_columns
        assert {"draft_id", "error_message"} <= action_columns
        assert "decision" in approval_columns
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'command_actions'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'command_approvals'"
        ).fetchone()


def test_command_schema_migrates_legacy_action_and_approval_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE command_actions (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          target TEXT NOT NULL,
          operation TEXT NOT NULL,
          risk TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'proposed',
          reason TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE command_approvals (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          action_id TEXT NOT NULL,
          permission TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO command_actions(
          id, thread_id, target, operation, risk, error
        ) VALUES (
          'action_legacy', 'thread_legacy', 'calendar', 'create_or_update_plans', 'write', 'legacy failure'
        );
        """
    )

    init_db(conn)

    action_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_actions)").fetchall()}
    approval_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_approvals)").fetchall()}
    assert {"draft_id", "error_message"} <= action_columns
    assert "decision" in approval_columns
    row = conn.execute("SELECT draft_id, error_message FROM command_actions WHERE id = 'action_legacy'").fetchone()
    assert row["draft_id"] == ""
    assert row["error_message"] == "legacy failure"


def test_auto_normal_chat_does_not_run_runtime_or_create_draft(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "Planix 这个项目怎么介绍？"})

    assert response.status_code == 200
    assert ExplodingRuntime.calls == 0
    events = _events(response)
    assert not any(event["type"] == "runtime_started" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_chat_mode_planning_request_does_not_run_runtime(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    model_calls = []

    def complete(*args, **kwargs):
        model_calls.append((args, kwargs))
        return LlmResult("这是纯聊天回复。", "deepseek", "deepseek-chat"), None

    monkeypatch.setattr("app.services.command_agent.LlmClient.complete", complete)

    response = client.post("/api/command/chat", json={"mode": "chat", "message": "帮我规划本周 AI 实习准备"})

    assert response.status_code == 200
    assert ExplodingRuntime.calls == 0
    events = _events(response)
    text = "".join(event.get("text", "") for event in events if event["type"] == "assistant_delta")
    assert text == "这是纯聊天回复。"
    assert len(model_calls) == 1
    assert model_calls[0][1]["routing_enabled"] is False
    assert not any(event["type"] == "runtime_started" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_goal_understanding_beijing_asks_purpose_without_unknown_routing(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision_must_not_run(monkeypatch)
    service = _patch_goal_understanding(monkeypatch, _ambiguous_destination_result("北京"))

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "我要去北京"})

    payload = _assert_goal_understanding_stops_routing(client, response, location="北京")
    assert payload["intentState"] == "ambiguous_goal"
    assert payload["possibleDomains"] == ["travel", "career", "relocation", "other"]
    assert payload["uncertainties"] == [
        {
            "field": "purpose",
            "impact": "不同目的会改变规划策略、约束和下一步行动。",
        }
    ]
    assert "目的" in payload["nextQuestion"]
    assert "旅游" in payload["nextQuestion"]
    assert payload["clarificationOptions"] == ["旅游", "工作", "学习", "长期居住"]
    assert len(service.calls) == 1


def test_goal_understanding_urumqi_asks_purpose_without_travel_template(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision_must_not_run(monkeypatch)
    service = _patch_goal_understanding(monkeypatch, _ambiguous_destination_result("乌鲁木齐"))

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "我要去乌鲁木齐"})

    payload = _assert_goal_understanding_stops_routing(client, response, location="乌鲁木齐")
    assert payload["intentState"] == "ambiguous_goal"
    assert payload["understoodIntent"] == "用户计划前往乌鲁木齐，但目的尚不明确。"
    assert payload["knownFacts"] == {"location": "乌鲁木齐"}
    assert payload["uncertainties"][0]["field"] == "purpose"
    assert "目的" in payload["nextQuestion"]
    assert len(service.calls) == 1


def test_failed_goal_understanding_does_not_fall_through_to_planning(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision_must_not_run(monkeypatch)

    class FailedGoalUnderstandingService:
        def understand(self, *args, **kwargs):
            return SimpleNamespace(
                result=None,
                usage=ModelUsage(
                    provider="test",
                    model="goal-understanding",
                    mode="model_unavailable",
                    taskType="goal_understanding",
                    fallbackUsed=False,
                    localFallbackAllowed=False,
                    attempts=[
                        ModelRouteAttempt(
                            provider="deepseek",
                            model="deepseek-v4-flash",
                            status="error",
                            errorType="auth_error",
                            latencyMs=1,
                        ),
                        ModelRouteAttempt(
                            provider="kimi",
                            model="kimi-k2.7-code",
                            status="skipped",
                            errorType="missing_api_key",
                            latencyMs=0,
                        ),
                        ModelRouteAttempt(
                            provider="zhipu_glm",
                            model="glm-4-flash",
                            status="error",
                            errorType="auth_error",
                            latencyMs=1,
                        ),
                    ],
                ),
                source="model_unavailable",
                error="goal understanding model unavailable",
            )

    monkeypatch.setattr(
        "app.services.command_agent.GoalUnderstandingService",
        lambda: FailedGoalUnderstandingService(),
    )

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "我要去北京"})

    assert response.status_code == 200
    events = _events(response)
    assert any(
        event["type"] == "model_usage"
        and event["feature"] == "goal_understanding"
        and event["source"] == "model_unavailable"
        for event in events
    )
    assistant = next(event["text"] for event in events if event["type"] == "assistant_delta")
    assert "没有启动规划" in assistant
    assert "DeepSeek、GLM 的 API Key 无效或已过期" in assistant
    assert "Kimi 尚未配置 Key" in assistant
    assert "无法可靠理解" not in assistant
    assert not any(event["type"] == "command_decision" for event in events)
    assert not any(event["type"] == "planning_session_started" for event in events)
    assert not any(event["type"] == "goal_understanding" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_goal_understanding_destination_followup_reuses_thread_context_and_starts_planning(client, monkeypatch):
    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision_must_not_run(monkeypatch)
    clear_result = GoalUnderstandingResult.model_validate(
        {
            "intentState": "clear_goal",
            "understoodIntent": "用户想去北京旅游。",
            "possibleDomains": ["travel"],
            "knownFacts": {"location": "北京", "purpose": "旅游"},
            "uncertainties": [],
            "consistencyWarnings": [],
            "nextQuestion": None,
            "confidence": 0.9,
        }
    )
    service = _patch_goal_understanding(
        monkeypatch,
        _ambiguous_destination_result("北京"),
        clear_result,
    )

    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要去北京"})
    start_events = _events(start)
    thread_id = start_events[-1]["threadId"]
    assert _goal_understanding_payload(
        next(event for event in start_events if event["type"] == "goal_understanding")
    )["intentState"] == "ambiguous_goal"

    followup = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "旅游"},
    )

    assert followup.status_code == 200
    followup_events = _events(followup)
    followup_payload = _goal_understanding_payload(
        next(event for event in followup_events if event["type"] == "goal_understanding")
    )
    assert followup_payload["intentState"] == "clear_goal"
    assert followup_payload["knownFacts"] == {"location": "北京", "purpose": "旅游"}
    assert any(event["type"] == "planning_session_started" for event in followup_events)
    assert not any(event["type"] == "command_decision" for event in followup_events)
    assert not any(event["type"] in {"runtime_started", "runtime_event", "draft_created"} for event in followup_events)
    assert len(service.calls) == 2
    second_args, second_kwargs = service.calls[1]
    assert second_args == ("旅游",)
    assert "我要去北京" in second_kwargs["thread_context"]
    assert second_kwargs["prior_understanding"]["knownFacts"] == {"location": "北京"}
    assert "旅游、工作、学习" in second_kwargs["prior_understanding"]["nextQuestion"]
    with get_conn() as conn:
        planning_row = conn.execute(
            "SELECT user_input, request_context_json FROM planning_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert planning_row["user_input"] == "旅游"
    assert json.loads(planning_row["request_context_json"])["goalUnderstanding"]["knownFacts"] == {
        "location": "北京",
        "purpose": "旅游",
    }

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_goal_understanding_ski_project_mismatch_warns_and_stops_planning(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision_must_not_run(monkeypatch)
    result = GoalUnderstandingResult.model_validate(
        {
            "intentState": "ambiguous_goal",
            "understoodIntent": "用户想从零开始学习滑雪，但目标用途仍需确认。",
            "possibleDomains": ["sports_skill", "content_creation", "competition", "other"],
            "knownFacts": {
                "skill": "滑雪",
                "currentLevel": "零基础",
                "availableTime": "2小时",
            },
            "uncertainties": [
                {
                    "field": "purpose",
                    "impact": "技能提升、内容记录或比赛目标需要不同训练路线。",
                }
            ],
            "consistencyWarnings": [
                "“做项目”通常描述技术或创作目标，与滑雪技能学习并不直接一致。"
            ],
            "nextQuestion": "你是想提升滑雪技能、记录学习过程制作内容、参加比赛挑战，还是有其他目标？",
            "confidence": 0.48,
        }
    )
    service = _patch_goal_understanding(monkeypatch, result)

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": "我要学滑雪\n零基础 2小时 做项目"},
    )

    payload = _assert_goal_understanding_stops_routing(client, response)
    assert payload["intentState"] == "ambiguous_goal"
    assert payload["knownFacts"]["skill"] == "滑雪"
    assert "purpose" in {item["field"] for item in payload["uncertainties"]}
    assert payload["consistencyWarnings"]
    assert "不直接一致" in payload["consistencyWarnings"][0]
    assert "提升滑雪技能" in payload["nextQuestion"]
    assert "项目" not in payload["understoodIntent"]
    assert "purpose" not in payload["knownFacts"]
    accepted_output = json.dumps(
        {
            "understoodIntent": payload["understoodIntent"],
            "knownFacts": payload["knownFacts"],
            "nextQuestion": payload["nextQuestion"],
        },
        ensure_ascii=False,
    )
    assert "作品集" not in accepted_output
    assert "README" not in accepted_output
    assert len(service.calls) == 1


def test_auto_planning_request_starts_deep_planning_session_without_runtime_or_draft(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="create_plan",
            confidence=0.91,
            targetType="unknown",
            action="create",
            decisionSummary="我理解你想创建一个学习规划。",
        ),
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert FakeRuntime.calls == 0
    assert not any(event["type"] == "command_decision" for event in events)
    assert any(event["type"] == "planning_session_started" for event in events)
    assert any(event["type"] == "agent_decision" and event["data"]["agent"] == "User Advocate Agent" for event in events)
    assert any(event["type"] == "agent_message" and event["data"]["messageType"] == "handoff" for event in events)
    assert any(event["type"] == "user_need_contract" and event["data"]["canMoveToDesign"] is True for event in events)
    assert any(event["type"] == "memory_insight_brief" for event in events)
    assert any(event["type"] == "resource_brief" for event in events)
    assert any(event["type"] == "plan_design_proposal" for event in events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_design_approval"
        for event in events
    )
    assert not any(event["type"] == "runtime_event" for event in events)
    assert not any(event["type"] == "runtime_started" for event in events)
    assert not any(event["type"] == "draft_created" for event in events)
    assert any(
        event["type"] == "goal_understanding"
        and event["modelUsage"]["taskType"] == "goal_understanding"
        for event in events
    )
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_actions").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


def test_deep_planning_unclear_goal_asks_clarification_without_design(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="create_plan",
            confidence=0.9,
            targetType="unknown",
            action="create",
            decisionSummary="create plan",
        ),
    )

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "I want to learn Python"})

    assert response.status_code == 200
    events = _events(response)
    assert ExplodingRuntime.calls == 0
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert contract["data"]["canMoveToDesign"] is False
    assert contract["data"]["clarificationQuestions"]
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "needs_goal_clarification"
        for event in events
    )
    assert not any(event["type"] == "plan_design_proposal" for event in events)
    assert not any(event["type"] == "execution_plan_draft" for event in events)
    assert not any(event["type"] == "draft_created" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_planning_like_clarify_decision_is_guarded_into_deep_planning(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="clarify",
            confidence=0.81,
            targetType="unknown",
            action="answer",
            needsClarification=True,
            clarificationQuestion="Please provide more details.",
        ),
    )

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "\u6211\u8981\u5b66go"})

    assert response.status_code == 200
    events = _events(response)
    assert ExplodingRuntime.calls == 0
    assert any(event["type"] == "planning_session_started" for event in events)
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert contract["data"]["canMoveToDesign"] is False
    questions = "\n".join(contract["data"]["clarificationQuestions"])
    assert "\u65f6\u95f4" in questions
    assert "\u9879\u76ee" in questions or "\u5b9e\u4e60" in questions
    assert not any(event["type"] == "clarify_question" for event in events)
    assert not any(event["type"] == "runtime_started" for event in events)
    assert not any(event["type"] == "draft_created" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_deep_planning_clarification_followup_skips_command_decision(client, monkeypatch):
    class ExplodingDecisionService:
        def decide(self, *args, **kwargs):
            raise AssertionError("active planning session follow-up should bypass LLM decision")

    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "\u6211\u8981\u5b66go"})
    thread_id = _events(start)[-1]["threadId"]
    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: ExplodingDecisionService())

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "\u7cbe\u901ago"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert not any(event["type"] == "command_decision" for event in events)
    assert any(event["type"] == "user_need_contract" for event in events)
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert "currentLevel" not in contract["data"]["missingInformation"]
    assert "availableTime" in contract["data"]["missingInformation"]
    assert "desiredOutcome" in contract["data"]["missingInformation"]
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1


def test_deep_planning_learning_goal_guardrail_overrides_chat_decision(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="chat", confidence=0.5, targetType="unknown", action="answer"),
        error="API key is invalid or expired.",
    )

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "我要学python"})

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "planning_session_started" for event in events)
    assert any(event["type"] == "user_need_contract" for event in events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "needs_goal_clarification"
        for event in events
    )
    assert not any(event["type"] == "plan_search_results" for event in events)
    assert not any(event["type"] == "runtime_started" for event in events)
    assert not any(event["type"] == "draft_created" for event in events)


def test_deep_planning_clarification_answer_preempts_query_plan_fallback(client, monkeypatch):
    class AuthErrorDecisionService:
        def decide(self, *args, **kwargs):
            raise AssertionError("auth_error should not be consulted during active planning continuation")

    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要学go"})
    start_events = _events(start)
    thread_id = start_events[-1]["threadId"]
    start_session_id = next(event["sessionId"] for event in start_events if event["type"] == "planning_session_started")
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "needs_goal_clarification"
        for event in start_events
    )

    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: AuthErrorDecisionService())
    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "零基础，每天3小时，找实习"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert not any(event["type"] == "command_decision" for event in events)
    assert not any(event["type"] == "plan_search_results" for event in events)
    assert not any(event["type"] == "runtime_started" for event in events)
    assert not any(event["type"] == "draft_created" for event in events)
    assert "查询结果 0" not in response.text
    assert "query_plan" not in response.text
    assert any(event["type"] == "memory_insight_brief" for event in events)
    assert any(event["type"] == "resource_brief" for event in events)
    assert any(event["type"] == "plan_design_proposal" for event in events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_design_approval"
        for event in events
    )
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert contract["sessionId"] == start_session_id
    learning = contract["data"]["slotState"]["learning"]
    assert learning["currentLevel"] == "zero_beginner"
    assert "零基础" in learning["currentLevelText"]
    assert "3" in learning["dailyTime"]
    assert learning["purpose"] == "internship"
    assert "实习" in learning["purposeText"]
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_actions").fetchone()["count"] == 0


def test_deep_planning_learning_short_clarification_normalizes_slots_without_goal_pollution(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要学python"})
    start_events = _events(start)
    thread_id = start_events[-1]["threadId"]
    session_id = next(event["sessionId"] for event in start_events if event["type"] == "planning_session_started")

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "零基础 4小时 提升能力"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert not any(event["type"] == "command_decision" for event in events)
    assert not any(event["type"] == "plan_search_results" for event in events)
    assert "query_plan" not in response.text
    assert any(event["type"] == "memory_insight_brief" for event in events)
    assert any(event["type"] == "resource_brief" for event in events)
    assert any(event["type"] == "plan_design_proposal" for event in events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_design_approval"
        for event in events
    )
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert contract["sessionId"] == session_id
    assert "补充信息" not in contract["data"]["interpretedGoal"]
    learning = contract["data"]["slotState"]["learning"]
    assert learning["currentLevel"] == "zero_beginner"
    assert learning["currentLevelText"] == "零基础"
    assert learning["dailyTime"] == "4小时"
    assert learning["availableTimeScope"] == "unknown"
    assert learning["purpose"] == "skill_improvement"
    assert "提升" in learning["purposeText"]
    resource_brief = next(event for event in events if event["type"] == "resource_brief")
    candidate_titles = [item["title"] for item in resource_brief["data"]["resourceCandidates"]]
    assert all("补充信息" not in title for title in candidate_titles)


def test_deep_planning_weekly_time_clarification_stays_in_active_session(client, monkeypatch):
    class ExplodingDecisionService:
        def decide(self, *args, **kwargs):
            raise AssertionError("active planning continuation must preempt command decision")

    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要学python"})
    start_events = _events(start)
    thread_id = start_events[-1]["threadId"]
    session_id = next(event["sessionId"] for event in start_events if event["type"] == "planning_session_started")
    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: ExplodingDecisionService())

    level = client.post("/api/command/chat", json={"mode": "auto", "threadId": thread_id, "message": "零基础"})
    weekly = client.post("/api/command/chat", json={"mode": "auto", "threadId": thread_id, "message": "每周4小时"})
    purpose = client.post("/api/command/chat", json={"mode": "auto", "threadId": thread_id, "message": "找实习"})

    for response in (level, weekly, purpose):
        assert response.status_code == 200
        events = _events(response)
        assert not any(event["type"] == "command_decision" for event in events)
        assert not any(event["type"] == "plan_search_results" for event in events)
        assert "query_plan" not in response.text
        assert "modify_plan" not in response.text
    weekly_contract = next(event for event in _events(weekly) if event["type"] == "user_need_contract")
    assert weekly_contract["sessionId"] == session_id
    learning = weekly_contract["data"]["slotState"]["learning"]
    assert learning["dailyTime"] == "每周4小时"
    assert learning["availableTimeScope"] == "weekly"
    final_events = _events(purpose)
    assert any(event["type"] == "plan_design_proposal" for event in final_events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_design_approval"
        for event in final_events
    )


def test_deep_planning_travel_slot_state_accumulates_until_design(client, monkeypatch):
    class ExplodingDecisionService:
        def decide(self, *args, **kwargs):
            raise AssertionError("active travel planning session should bypass LLM decision")

    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要去新疆"})
    start_events = _events(start)
    thread_id = start_events[-1]["threadId"]
    start_contract = next(event for event in start_events if event["type"] == "user_need_contract")
    assert start_contract["data"]["slotState"]["domain"] == "travel"
    assert start_contract["data"]["canMoveToDesign"] is False

    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: ExplodingDecisionService())
    followup = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "旅游，赛里木湖，我要去两个星期"},
    )
    followup_events = _events(followup)
    assert not any(event["type"] == "command_decision" for event in followup_events)
    contract = next(event for event in followup_events if event["type"] == "user_need_contract")
    travel = contract["data"]["slotState"]["travel"]
    assert travel["durationDays"] == 14
    assert "赛里木湖" in travel["places"]
    assert contract["data"]["canMoveToDesign"] is False

    complete = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "九月，飞机，1万元，喀纳斯，体能很好"},
    )
    complete_events = _events(complete)
    assert not any(event["type"] == "command_decision" for event in complete_events)
    assert any(event["type"] == "plan_design_proposal" for event in complete_events)
    complete_contract = next(event for event in complete_events if event["type"] == "user_need_contract")
    travel = complete_contract["data"]["slotState"]["travel"]
    assert travel["month"] == "9月"
    assert travel["transport"] == "飞机"
    assert "喀纳斯" in travel["places"]


def test_deep_planning_chinese_confirmation_approves_current_stage(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "可以", "context": {"date": "2026-07-05"}},
    )

    events = _events(response)
    assert not any(event["type"] == "command_decision" for event in events)
    assert any(event["type"] == "execution_plan_draft" for event in events)


def test_deep_planning_execution_confirmation_gate_and_ready_noop(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]
    client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "确认方向", "context": {"date": "2026-07-05"}},
    )

    approve_execution = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "确认执行计划", "context": {"date": "2026-07-05"}},
    )

    events = _events(approve_execution)
    assert not any(event["type"] == "command_decision" for event in events)
    assert not any(event["type"] == "learning_update" for event in events)
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "ready_to_write_calendar"
        for event in events
    )
    draft = next(event for event in events if event["type"] == "execution_plan_draft")
    assert draft["data"]["status"] == "approved"

    repeat = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "确认执行计划", "context": {"date": "2026-07-05"}},
    )
    repeat_events = _events(repeat)
    repeat_types = [event["type"] for event in repeat_events]
    assert not any(event["type"] == "command_decision" for event in repeat_events)
    assert not any(event["type"] == "learning_update" for event in repeat_events)
    assert not any(event["type"] == "planning_session_started" for event in repeat_events)
    assert "agent_decision" not in repeat_types
    assert "agent_message" not in repeat_types
    assert "user_need_contract" not in repeat_types
    assert "memory_insight_brief" not in repeat_types
    assert "resource_brief" not in repeat_types
    assert "plan_design_proposal" not in repeat_types
    assert "execution_plan_draft" not in repeat_types
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "ready_to_write_calendar"
        for event in repeat_events
    )

    positive_feedback = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "这个计划很适合", "context": {"date": "2026-07-05"}},
    )
    feedback_events = _events(positive_feedback)
    assert any(event["type"] == "learning_update" for event in feedback_events)


def test_planning_trace_snapshot_is_incremental_after_model_recovery(client):
    service = CommandAgentService()
    thread_id = service.ensure_thread(title="incremental planning trace")
    session_id = "trace-session"
    timestamp = "2026-07-12T08:00:00Z"
    failed_decision = AgentDecision(
        id="decision-auth-failure",
        sessionId=session_id,
        agent="Evidence Agent",
        decision="block",
        reason="Provider authentication failed.",
        userVisibleSummary="Evidence stopped while provider settings were unavailable.",
        createdAt=timestamp,
    )
    failed_message = AgentMessage(
        id="message-auth-failure",
        sessionId=session_id,
        fromAgent="Evidence Agent",
        toAgent="Evidence Agent",
        messageType="block",
        reason="Provider authentication failed.",
        payloadJson={"errorType": "auth_error"},
        createdAt=timestamp,
    )
    failed = PlanningSessionResponse(
        sessionId=session_id,
        threadId=thread_id,
        entryPoint="p_mode",
        status="MODEL_UNAVAILABLE",
        businessStatus="evidence_pending",
        runtimeStatus="blocked_model",
        userInput="Learn Python for data analysis",
        decisions=[failed_decision],
        messages=[failed_message],
        version=1,
        createdAt=timestamp,
        updatedAt=timestamp,
    )

    first_events = [json.loads(line) for line in service._stream_planning_session_snapshot(thread_id, failed)]
    assert [event["data"]["id"] for event in first_events if event["type"] == "agent_decision"] == [
        failed_decision.id
    ]
    assert [event["data"]["id"] for event in first_events if event["type"] == "agent_message"] == [
        failed_message.id
    ]

    success_decision = AgentDecision(
        id="decision-evidence-success",
        sessionId=session_id,
        agent="Evidence Agent",
        decision="approve",
        reason="Current evidence is sufficient for strategy design.",
        userVisibleSummary="Evidence recovered successfully.",
        createdAt=timestamp,
    )
    success_message = AgentMessage(
        id="message-evidence-handoff",
        sessionId=session_id,
        fromAgent="Evidence Agent",
        toAgent="Strategy Agent",
        messageType="handoff",
        reason="Evidence is sufficient to compare strategies.",
        resolved=True,
        createdAt=timestamp,
    )
    recovered = failed.model_copy(
        update={
            "status": "waiting_design_approval",
            "business_status": "strategy_pending",
            "runtime_status": "idle",
            "decisions": [failed_decision, success_decision],
            "messages": [failed_message, success_message],
            "version": 2,
        }
    )

    recovered_events = [
        json.loads(line) for line in service._stream_planning_session_snapshot(thread_id, recovered)
    ]
    assert [
        event["data"]["id"] for event in recovered_events if event["type"] == "agent_decision"
    ] == [success_decision.id]
    assert [
        event["data"]["id"] for event in recovered_events if event["type"] == "agent_message"
    ] == [success_message.id]
    recovered_payload = json.dumps(recovered_events)
    assert "auth-failure" not in recovered_payload
    assert "auth_error" not in recovered_payload
    assert any(event["type"] == "planning_session_status" for event in recovered_events)

    with get_conn() as conn:
        trace_cards_after_recovery = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM command_messages
            WHERE thread_id = ? AND kind IN ('agent_decision', 'agent_message')
            """,
            (thread_id,),
        ).fetchone()["count"]
    assert trace_cards_after_recovery == 4

    repeated_events = [
        json.loads(line) for line in service._stream_planning_session_snapshot(thread_id, recovered)
    ]
    assert not any(event["type"] in {"agent_decision", "agent_message"} for event in repeated_events)
    assert any(event["type"] == "planning_session_status" for event in repeated_events)
    with get_conn() as conn:
        trace_cards_after_repeat = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM command_messages
            WHERE thread_id = ? AND kind IN ('agent_decision', 'agent_message')
            """,
            (thread_id,),
        ).fetchone()["count"]
    assert trace_cards_after_repeat == trace_cards_after_recovery


def test_deep_planning_topic_switch_requires_restart_confirmation(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post("/api/command/chat", json={"mode": "auto", "message": "我要学go"})
    thread_id = _events(start)[-1]["threadId"]

    switch = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "我要去新疆旅行"},
    )

    events = _events(switch)
    contract = next(event for event in events if event["type"] == "user_need_contract")
    assert contract["data"]["missingInformation"] == ["topicSwitchConfirmation"]
    assert contract["data"]["pendingQuestion"]["askedFields"] == ["topicSwitchConfirmation"]
    assert not any(event["type"] == "plan_design_proposal" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1


def test_deep_planning_approve_design_generates_execution_draft_with_resources(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]

    approve = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "approve design", "context": {"date": "2026-07-05"}},
    )

    assert approve.status_code == 200
    events = _events(approve)
    draft = next(event for event in events if event["type"] == "execution_plan_draft")
    assert draft["data"]["status"] == "waiting_user_approval"
    assert draft["data"]["tasks"]
    first_task = draft["data"]["tasks"][0]
    assert first_task["acceptanceCriteria"]
    assert first_task["deliverable"]
    assert first_task["resourceBundle"]["primary"] or first_task["resourceBundle"]["practice"]
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_execution_approval"
        for event in events
    )
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_deep_planning_python_internship_draft_passes_semantic_quality_gate(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 3 hours, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]

    approve = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "approve design", "context": {"date": "2026-07-05"}},
    )

    assert approve.status_code == 200
    draft = next(event for event in _events(approve) if event["type"] == "execution_plan_draft")["data"]
    tasks = draft["tasks"]
    assert draft["qualityStatus"] == "passed"
    assert draft["qualityReport"]["status"] == "passed"
    assert draft["qualityReport"]["checks"]["internshipFit"] is True
    assert draft["qualityReport"]["checks"]["timeFit"] is True
    assert len(tasks) >= 12
    assert sum(task["estimatedMinutes"] for task in tasks) >= 900
    titles = " ".join(task["title"] for task in tasks).lower()
    assert "readme" in titles
    assert "github" in titles
    assert "resume" in titles
    assert "interview" in titles
    assert "learn and reproduce" not in titles
    assert "checkable output" not in titles
    primary_titles = [
        task["resourceBundle"]["primary"]["title"]
        for task in tasks
        if task.get("resourceBundle", {}).get("primary")
    ]
    most_repeated = max(primary_titles.count(title) for title in set(primary_titles))
    assert most_repeated <= len(tasks) // 2


def test_deep_planning_calendar_write_blocks_failed_quality_report(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 3 hours, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    events = _events(start)
    thread_id = events[-1]["threadId"]
    session_id = next(event for event in events if event["type"] == "planning_session_started")["sessionId"]
    approve = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "approve design", "context": {"date": "2026-07-05"}},
    )
    draft = next(event for event in _events(approve) if event["type"] == "execution_plan_draft")["data"]
    draft["qualityStatus"] = "needs_repair"
    draft["qualityReport"] = {
        "status": "needs_repair",
        "score": 42,
        "blockers": ["The plan is too sparse for the user's time horizon and availability."],
        "warnings": [],
        "repairSuggestions": ["Increase task count and total planned minutes."],
        "checks": {
            "goalAlignment": True,
            "timeFit": False,
            "taskSpecificity": True,
            "resourceDiversity": True,
            "deliverableQuality": True,
            "internshipFit": True,
            "calendarWritable": False,
        },
    }
    with get_conn() as conn:
        conn.execute(
            "UPDATE planning_sessions SET status = 'ready_to_write_calendar', execution_draft_json = ? WHERE id = ?",
            (json.dumps(draft), session_id),
        )

    response = client.post(f"/api/planning/sessions/{session_id}/prepare-calendar-write", json={})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["qualityStatus"] == "needs_repair"
    assert "too sparse" in detail["blockers"][0]


def test_deep_planning_resource_feedback_updates_plan_and_memory(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]
    client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "approve design", "context": {"date": "2026-07-05"}},
    )

    feedback = client.post(
        "/api/command/chat",
        json={"mode": "auto", "threadId": thread_id, "message": "\u8d44\u6e90\u592a\u96be", "context": {"date": "2026-07-05"}},
    )

    assert feedback.status_code == 200
    events = _events(feedback)
    learning = next(event for event in events if event["type"] == "learning_update")
    assert learning["data"]["feedbackType"] == "resource_feedback"
    assert learning["data"]["immediatePatch"]["action"] == "replace_resource"
    assert learning["data"]["longTermLearning"]["newRule"]
    revised_draft = next(event for event in events if event["type"] == "execution_plan_draft")
    assert revised_draft["data"]["status"] == "waiting_user_approval"
    assert revised_draft["data"]["tasks"][0]["resourceBundle"]["primary"]["sourceType"] == "practice_bank"
    assert any(
        event["type"] == "planning_session_status" and event["status"] == "waiting_execution_approval"
        for event in events
    )
    with get_conn() as conn:
        preference_count = conn.execute("SELECT COUNT(*) AS count FROM memories WHERE kind = 'preference'").fetchone()["count"]
        review_count = conn.execute("SELECT COUNT(*) AS count FROM memories WHERE kind = 'review'").fetchone()["count"]
    assert preference_count >= 1
    assert review_count >= 1


def test_deep_planning_active_session_continues_unless_restart_requested(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]

    revise_same_session = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "threadId": thread_id,
            "message": "Plan it with more backend project practice",
            "context": {"date": "2026-07-05"},
        },
    )

    assert revise_same_session.status_code == 200
    same_events = _events(revise_same_session)
    assert any(event["type"] == "plan_design_proposal" for event in same_events)
    assert not any(event["type"] == "planning_session_started" for event in same_events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 1

    restart = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "threadId": thread_id,
            "message": "start over with a new plan for React, 30 days, project driven",
            "context": {"date": "2026-07-05"},
        },
    )

    assert restart.status_code == 200
    restart_events = _events(restart)
    assert any(event["type"] == "planning_session_started" for event in restart_events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM planning_sessions").fetchone()["count"] == 2


def test_deep_planning_calendar_preview_uses_planning_session_source_keys(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(intent="create_plan", confidence=0.9, targetType="unknown", action="create"),
    )
    start = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "low",
            "message": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
            "context": {"date": "2026-07-05"},
        },
    )
    thread_id = _events(start)[-1]["threadId"]
    client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "threadId": thread_id, "message": "approve design", "context": {"date": "2026-07-05"}},
    )
    client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "threadId": thread_id, "message": "confirm execution plan", "context": {"date": "2026-07-05"}},
    )

    write = client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "threadId": thread_id, "message": "write to calendar", "context": {"date": "2026-07-05"}},
    )

    assert write.status_code == 200
    events = _events(write)
    preview = next(event for event in events if event["type"] == "calendar_plan_preview")
    assert preview["plans"]
    assert preview["plans"][0]["sourceKey"].startswith("planning-session:")
    assert ":t0" in preview["plans"][0]["sourceKey"]
    assert any(event["type"] == "approval_required" for event in events)


def test_workbench_mode_creates_hidden_draft(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())

    response = _create_workbench_draft(client, "Planix 这个项目怎么介绍？")

    assert response.status_code == 200
    events = _events(response)
    assert FakeRuntime.calls == 1
    assert [event["type"] for event in events].count("runtime_started") == 1
    assert any(event["type"] == "runtime_event" and event["name"] == "propose_tasks" for event in events)
    draft_event = next(event for event in events if event["type"] == "draft_created")
    summary_event = next(event for event in events if event["type"] == "summary")
    summary_index = next(index for index, event in enumerate(events) if event["type"] == "summary")
    detail_event = events[summary_index + 1]
    assert detail_event["type"] == "plan_detail"
    assert detail_event["planHorizon"]["durationDays"] == 5
    assert detail_event["qualityStatus"] == "local_fallback"
    assert detail_event["qualityReport"]["metrics"]["fallbackUsed"] is True
    assert detail_event["sourceType"] == "local_fallback"
    assert draft_event["kind"] == "calendar_plan"
    assert "已生成计划草稿" in summary_event["text"]

    with get_conn() as conn:
        draft = conn.execute("SELECT * FROM command_drafts WHERE id = ?", (draft_event["draftId"],)).fetchone()
        assert draft is not None
        assert draft["status"] == "current"
        assert draft["kind"] == "calendar_plan"
        payload = json.loads(draft["payload_json"])
        assert payload["structuredPlan"]["goalTitle"] == "AI internship prep"
        assert payload["runtimeRunId"].startswith("run_fake_")
        assert payload["qualityReport"]["metrics"]["qualityStatus"] == "local_fallback"
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0

    thread_id = events[-1]["threadId"]
    thread = client.get(f"/api/command/thread/{thread_id}").json()
    assert thread["currentDraft"]["id"] == draft_event["draftId"]


def test_command_threads_list_and_delete_do_not_delete_plans(client):
    from app.schemas import PlanCreate
    from app.services.plans import create_plan

    service = CommandAgentService()
    first_thread_id = service.ensure_thread(None, title="第一个对话")
    second_thread_id = service.ensure_thread(None, title="第二个对话")
    service.add_message(first_thread_id, "user", "第一个对话")
    service.add_message(first_thread_id, "assistant", "回复")
    service.add_message(second_thread_id, "user", "第二个对话")
    service.add_message(second_thread_id, "assistant", "回复")
    create_plan(PlanCreate(date="2026-07-05", time="09:00", content="Keep calendar plan"))

    listed = client.get("/api/command/threads").json()
    ids = [thread["id"] for thread in listed]
    assert second_thread_id in ids
    assert first_thread_id in ids
    assert listed[ids.index(second_thread_id)]["messageCount"] >= 2

    deleted = client.delete(f"/api/command/thread/{first_thread_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1
    assert client.get(f"/api/command/thread/{first_thread_id}").status_code == 404

    listed_after = client.get("/api/command/threads").json()
    assert first_thread_id not in [thread["id"] for thread in listed_after]
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 1


def test_second_generation_supersedes_previous_draft(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())

    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]
    second = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "workbench", "message": "帮我规划下周 AI 实习准备"},
    )

    assert second.status_code == 200
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT version, status FROM command_drafts WHERE thread_id = ? ORDER BY version ASC",
            (thread_id,),
        ).fetchall()
    assert [(row["version"], row["status"]) for row in rows] == [(1, "superseded"), (2, "current")]


def test_runtime_handoff_uses_current_thread_context_only(client, monkeypatch):
    FakeRuntime.calls = 0
    FakeRuntime.last_input = ""
    _patch_runtime(monkeypatch, lambda: FakeRuntime())

    service = CommandAgentService()
    thread_id = service.ensure_thread(None, title="我要去赛里木湖")
    service.add_message(thread_id, "user", "我要去赛里木湖")
    second = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "workbench", "message": "帮我做个规划"},
    )

    assert second.status_code == 200
    assert FakeRuntime.calls == 1
    assert "赛里木湖" in FakeRuntime.last_input

    client.post("/api/command/chat", json={"mode": "workbench", "message": "帮我做个规划"})
    assert FakeRuntime.calls == 2
    assert "赛里木湖" not in FakeRuntime.last_input


def test_invalid_structured_plan_does_not_create_draft_but_saves_message(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime(structured_plan={"goalTitle": "Bad", "milestones": []}))

    response = _create_workbench_draft(client)

    assert response.status_code == 200
    events = _events(response)
    assert not any(event["type"] == "draft_created" for event in events)
    text = "".join(event.get("text", "") for event in events if event["type"] == "assistant_delta")
    assert "没有返回合法的 structuredPlan" in text
    thread_id = events[-1]["threadId"]
    thread = client.get(f"/api/command/thread/{thread_id}").json()
    assert len(thread["messages"]) == 2
    assert thread["messages"][1]["role"] == "assistant"
    assert thread["currentDraft"] is None
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_draft_save_error_returns_error_event_and_done(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())

    def fail_create(*args, **kwargs):
        raise RuntimeError("migration missing")

    monkeypatch.setattr("app.services.command_agent.CommandAgentService._create_calendar_draft", fail_create)

    response = _create_workbench_draft(client)

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "error" and "计划草稿保存失败" in event["error"] for event in events)
    assert "migration missing" not in response.text
    assert all("detail" not in event for event in events if event["type"] == "error")
    assert events[-1]["type"] == "done"
    thread_id = events[-1]["threadId"]
    thread = client.get(f"/api/command/thread/{thread_id}").json()
    assert thread["messages"][-1]["role"] == "assistant"
    assert "计划草稿保存失败" in thread["messages"][-1]["content"]
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_calendar_write_error_uses_calendar_specific_message(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client, "Plan my skating practice")
    thread_id = _events(first)[-1]["threadId"]

    def fail_execute(self, action_id):
        raise RuntimeError("write exploded")

    monkeypatch.setattr("app.services.command_agent.CommandAgentService._execute_calendar_action", fail_execute)

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "medium", "message": "write this plan to calendar"},
    )

    assert response.status_code == 200
    approved = _approve_required_action(client, response, permission="medium")
    events = _events(approved)
    error = next(event for event in events if event["type"] == "error")
    assert "写入日历失败" in error["error"]
    assert "计划草稿保存失败" not in error["error"]


def test_phase_43_generation_does_not_create_write_actions(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())

    response = _create_workbench_draft(client)

    assert response.status_code == 200
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_actions").fetchone()["count"] == 0
        assert conn.execute("SELECT COUNT(*) AS count FROM command_approvals").fetchone()["count"] == 0
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'command_outputs'"
        ).fetchone()
        assert exists is None


def test_intent_router_rules():
    assert detect_command_intent("帮我规划本周 AI 实习准备") == "planning_request"
    assert detect_command_intent(MSG_TODAY_PLANS) == "query_plan"
    assert detect_command_intent(MSG_FIND_PYTHON) == "query_plan"
    assert detect_command_intent(MSG_PATCH_TO_DAY_AFTER) == "patch_calendar_plan"
    assert detect_command_intent("修改我的计划") == "patch_calendar_plan"
    assert detect_command_intent("删除周五的计划") == "patch_calendar_plan"
    assert detect_command_intent("重新生成一个轻松版本") == "regenerate_draft"
    assert detect_command_intent("展开看看完整计划") == "show_current_plan"
    assert detect_command_intent("写入日历") == "sync_to_calendar"
    assert detect_command_intent("帮我写入日历") == "sync_to_calendar"
    assert detect_command_intent("写入计划") == "sync_to_calendar"
    assert detect_command_intent("保存计划") == "sync_to_calendar"
    assert detect_command_intent("确认写入") == "sync_to_calendar"
    assert detect_command_intent("保存到日程") == "sync_to_calendar"
    assert detect_command_intent("把这个计划写进日历") == "sync_to_calendar"
    assert detect_command_intent("查一下 Python 笔记") == "query_memory"
    assert detect_command_intent("把这段保存成笔记") == "save_memory"
    assert resolve_command_intent(
        "保存",
        detect_command_intent("保存"),
        has_current_draft=True,
    ) == "sync_to_calendar"
    assert resolve_command_intent(
        "保存",
        detect_command_intent("保存"),
        has_current_draft=False,
    ) == "unsupported_command"
    assert detect_command_intent("打开日历") == "navigate_ui"
    assert detect_command_intent("Planix 这个项目怎么介绍？") == "normal_chat"
    assert detect_command_intent("帮我细化全部任务") == "refine_current_plan"
    assert detect_command_intent("refine all tasks") == "refine_current_plan"


def test_auto_decision_uses_task_specific_routing_for_patch_and_memory(client, monkeypatch):
    calls = []

    class RecordingDecisionService:
        def decide(self, message, **kwargs):
            task_type = kwargs.get("task_type", "command_decision")
            calls.append((message, task_type))
            return SimpleNamespace(
                decision=None,
                usage=ModelUsage(
                    provider="test",
                    model="router",
                    totalTokens=1,
                    mode="local_fallback",
                    taskType=task_type,
                ),
                source="local_fallback",
                error="",
            )

    monkeypatch.setattr("app.services.command_agent.CommandDecisionService", lambda: RecordingDecisionService())
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())

    memory_query = client.post("/api/command/chat", json={"mode": "auto", "message": "查一下我的记忆"})
    memory_write = client.post("/api/command/chat", json={"mode": "auto", "message": "记住：我晚上 8 点后适合学习"})
    calendar_patch = client.post("/api/command/chat", json={"mode": "auto", "message": "modify my plan"})

    assert memory_query.status_code == 200
    assert memory_write.status_code == 200
    assert calendar_patch.status_code == 200
    assert [task for _, task in calls] == ["memory_query", "memory_write", "calendar_patch"]
    assert any(event["type"] == "model_usage" and event["usage"]["taskType"] == "memory_query" for event in _events(memory_query))
    assert any(event["type"] == "model_usage" and event["usage"]["taskType"] == "memory_write" for event in _events(memory_write))
    assert any(event["type"] == "model_usage" and event["usage"]["taskType"] == "calendar_patch" for event in _events(calendar_patch))


def test_query_plan_searches_calendar_without_runtime_or_draft(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    client.post(
        "/api/plans",
        json={"date": "2026-07-05", "time": "09:00", "content": PY_STUDY, "source": "manual"},
    )

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": MSG_TODAY_PLANS, "context": {"date": "2026-07-05"}},
    )

    assert response.status_code == 200
    events = _events(response)
    result = next(event for event in events if event["type"] == "plan_search_results")
    assert ExplodingRuntime.calls == 0
    assert result["calendarPlans"][0]["title"] == PY_STUDY
    assert result["dateRange"]["startDate"] == "2026-07-05"
    assert not any(event["type"] == "runtime_started" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_drafts").fetchone()["count"] == 0


def test_llm_decision_routes_query_plan_without_runtime(client, monkeypatch):
    ExplodingRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: ExplodingRuntime())
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="query_plan",
            confidence=0.88,
            targetType="calendar_date",
            action="query",
            decisionSummary="我理解你想查看今天的安排。",
        ),
    )
    client.post(
        "/api/plans",
        json={"date": "2026-07-05", "time": "09:00", "content": PY_STUDY, "source": "manual"},
    )

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": MSG_TODAY_PLANS, "context": {"date": "2026-07-05"}},
    )

    assert response.status_code == 200
    events = _events(response)
    assert ExplodingRuntime.calls == 0
    assert any(event["type"] == "command_decision" and event["source"] == "llm" for event in events)
    result = next(event for event in events if event["type"] == "plan_search_results")
    assert result["calendarPlans"][0]["title"] == PY_STUDY


def test_query_memory_searches_materials_history_and_notes(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="query_memory",
            confidence=0.9,
            targetType="memory",
            action="query",
            extractedParams={"query": "Python AI 实习"},
            decisionSummary="我理解你想查记忆。",
        ),
    )
    client.post(
        "/api/rag/documents",
        json={"title": "Python 面试资料", "content": "Python portfolio and AI internship notes.", "sourceType": "paste"},
    )
    MemoryService().create_memory(
        MemoryCreate(kind="planning_history", title="Python AI internship plan", content="History summary")
    )
    MemoryService().create_memory(
        MemoryCreate(kind="note", title="Python month note", content="Python 月度复盘和 AI 实习材料")
    )

    response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": MSG_FIND_PYTHON_AI, "context": {"date": "2026-07-05"}},
    )

    assert response.status_code == 200
    result = next(event for event in _events(response) if event["type"] == "memory_search_results")
    groups = {group["kind"]: group["items"] for group in result["groups"]}
    assert groups["material"]
    assert groups["planning_history"][0]["title"] == "Python AI internship plan"
    assert groups["note"][0]["title"] == "Python month note"


def test_llm_decision_query_notes_returns_note_results(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="query_notes",
            confidence=0.93,
            targetType="note",
            action="query",
            extractedParams={"query": "Python AI"},
            decisionSummary="我理解你想查找 Python AI 资料。",
        ),
    )
    MemoryService().create_memory(
        MemoryCreate(kind="note", title="Python AI note", content="Python AI 月笔记")
    )
    MemoryService().create_memory(
        MemoryCreate(kind="planning_history", title="Python AI history", content="Python AI planning history")
    )

    response = client.post("/api/command/chat", json={"mode": "auto", "message": "找资料", "context": {"date": "2026-07-05"}})

    assert response.status_code == 200
    result = next(event for event in _events(response) if event["type"] == "memory_search_results")
    assert [group["kind"] for group in result["groups"]] == ["note"]
    assert result["groups"][0]["items"][0]["title"] == "Python AI note"


def test_save_note_preview_reject_and_approve_writes_note_memory(client, monkeypatch):
    _patch_decision(
        monkeypatch,
        CommandDecision(
            intent="save_note",
            confidence=0.9,
            targetType="note",
            action="save",
            extractedParams={"noteText": "Python 面试重点是项目复盘", "date": "2026-07-05"},
            needsConfirmation=True,
            decisionSummary="我理解你想保存一条笔记。",
        ),
    )
    preview = client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "message": "保存笔记", "context": {"date": "2026-07-05"}},
    )
    assert preview.status_code == 200
    preview_events = _events(preview)
    note_preview = next(event for event in preview_events if event["type"] == "memory_write_preview")
    approval = next(event for event in preview_events if event["type"] == "approval_required")
    assert note_preview["kind"] == "note"
    assert "Python" in note_preview["content"]

    rejected = client.post("/api/command/approve", json={"actionId": approval["actionId"], "decision": "reject", "permission": "low"})
    assert rejected.status_code == 200
    assert MemoryService().search_memories("Python", kinds=["note"]) == []

    preview = client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "message": "保存笔记", "context": {"date": "2026-07-05"}},
    )
    action_id = next(event for event in _events(preview) if event["type"] == "approval_required")["actionId"]
    approved = client.post("/api/command/approve", json={"actionId": action_id, "decision": "approve", "permission": "low"})

    assert approved.status_code == 200
    assert any(event["type"] == "memory_write_result" and event["status"] == "success" for event in _events(approved))
    memories = MemoryService().search_memories("Python", kinds=["note"])
    assert memories and "Python" in memories[0].content


def test_patch_calendar_plan_low_permission_previews_and_requires_approval(client):
    client.post(
        "/api/plans",
        json={
            "date": "2026-07-06",
            "time": "09:00",
            "content": PY_STUDY,
            "source": "manual",
            "result": "keep completion",
        },
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "low",
            "message": MSG_PATCH_TO_DAY_AFTER,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    preview = next(event for event in events if event["type"] == "plan_patch_preview")
    approval = next(event for event in events if event["type"] == "approval_required")
    assert preview["changes"]["date"] == "2026-07-07"
    assert approval["risk"] == "write"
    with get_conn() as conn:
        plan = conn.execute("SELECT date, result FROM plans WHERE content = ?", (PY_STUDY,)).fetchone()
        action = conn.execute("SELECT operation, status FROM command_actions WHERE id = ?", (approval["actionId"],)).fetchone()
        assert plan["date"] == "2026-07-06"
        assert plan["result"] == "keep completion"
        assert action["operation"] == "update"
        assert action["status"] == "waiting_approval"


def test_reject_patch_calendar_plan_does_not_update_plan(client):
    client.post(
        "/api/plans",
        json={
            "date": "2026-07-06",
            "time": "09:00",
            "content": PY_STUDY,
            "source": "manual",
            "result": "keep completion",
        },
    )
    preview_response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "low",
            "message": MSG_PATCH_TO_DAY_AFTER,
            "context": {"date": "2026-07-05"},
        },
    )
    action_id = next(event for event in _events(preview_response) if event["type"] == "approval_required")["actionId"]

    response = client.post(
        "/api/command/approve",
        json={"actionId": action_id, "decision": "reject", "permission": "low"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "execution_result" and event["status"] == "rejected" for event in events)
    assert not any(event["type"] == "plan_patch_result" for event in events)
    with get_conn() as conn:
        plan = conn.execute("SELECT date, result FROM plans WHERE content = ?", (PY_STUDY,)).fetchone()
        action = conn.execute("SELECT status FROM command_actions WHERE id = ?", (action_id,)).fetchone()
        assert plan["date"] == "2026-07-06"
        assert plan["result"] == "keep completion"
        assert action["status"] == "rejected"


def test_patch_calendar_plan_medium_permission_requires_approval(client):
    client.post(
        "/api/plans",
        json={
            "date": "2026-07-06",
            "time": "09:00",
            "content": PY_STUDY,
            "source": "manual",
            "result": "keep completion",
        },
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "medium",
            "message": MSG_PATCH_TO_DAY_AFTER_30,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "approval_required" for event in events)
    approved = _approve_required_action(client, response, permission="medium")
    result = next(event for event in _events(approved) if event["type"] == "plan_patch_result")
    assert result["status"] == "success"
    assert result["after"]["date"] == "2026-07-07"
    assert result["after"]["estimatedMinutes"] == 30
    with get_conn() as conn:
        plan = conn.execute("SELECT date, estimated_minutes, result, done FROM plans WHERE content = ?", (PY_STUDY,)).fetchone()
        assert plan["date"] == "2026-07-07"
        assert plan["estimated_minutes"] == 30
        assert plan["result"] == "keep completion"
        assert plan["done"] == 0


def test_patch_calendar_delete_medium_permission_requires_approval(client):
    client.post(
        "/api/plans",
        json={"date": "2026-07-06", "time": "09:00", "content": PY_STUDY, "source": "ai"},
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "medium",
            "message": MSG_DELETE_TOMORROW,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "plan_patch_preview" and event["operation"] == "delete" for event in events)
    assert any(event["type"] == "approval_required" and event["risk"] == "delete" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 1


def test_patch_calendar_high_permission_still_requires_delete_approval(client):
    client.post(
        "/api/plans",
        json={"date": "2026-07-06", "time": "09:00", "content": PY_STUDY, "source": "ai"},
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "high",
            "message": MSG_DELETE_TOMORROW,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "approval_required" for event in events)
    approved = _approve_required_action(client, response, permission="high")
    result = next(event for event in _events(approved) if event["type"] == "plan_patch_result")
    assert result["status"] == "success"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


def test_patch_calendar_manual_delete_always_requires_approval(client):
    client.post(
        "/api/plans",
        json={"date": "2026-07-06", "time": "09:00", "content": PY_STUDY, "source": "manual"},
    )

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "high",
            "message": MSG_DELETE_TOMORROW,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "approval_required" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 1


def test_patch_calendar_multiple_candidates_returns_selection_card_without_action(client):
    client.post("/api/plans", json={"date": "2026-07-06", "time": "09:00", "content": PY_STUDY, "source": "manual"})
    client.post("/api/plans", json={"date": "2026-07-06", "time": "10:00", "content": "React \u5b66\u4e60", "source": "manual"})

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "medium",
            "message": MSG_PATCH_AMBIGUOUS,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    result = next(event for event in _events(response) if event["type"] == "plan_search_results")
    assert len(result["calendarPlans"]) == 2
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_actions").fetchone()["count"] == 0


def test_generic_modify_plan_without_context_clarifies_without_action(client, monkeypatch):
    _patch_decision(monkeypatch, None)

    response = client.post(
        "/api/command/chat",
        json={
            "mode": "auto",
            "permission": "medium",
            "message": "修改我的计划",
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    events = _events(response)
    clarify = next(event for event in events if event["type"] == "clarify_question")
    assert clarify["question"] == "你想修改哪一个计划？可以先说“查看今天计划”，再告诉我修改第几个。"
    assert not any(event["type"] == "plan_patch_preview" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM command_actions").fetchone()["count"] == 0


def test_patch_calendar_uses_last_query_ordinal_reference(client):
    client.post(
        "/api/plans",
        json={"date": "2026-07-05", "time": "09:00", "content": PY_STUDY, "source": "manual", "estimatedMinutes": 60},
    )
    client.post(
        "/api/plans",
        json={"date": "2026-07-05", "time": "10:00", "content": "React \u5b66\u4e60", "source": "manual", "estimatedMinutes": 45},
    )
    query = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": MSG_TODAY_PLANS, "context": {"date": "2026-07-05"}},
    )
    thread_id = _events(query)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={
            "threadId": thread_id,
            "mode": "auto",
            "permission": "medium",
            "message": MSG_PATCH_FIRST,
            "context": {"date": "2026-07-05"},
        },
    )

    assert response.status_code == 200
    approved = _approve_required_action(client, response, permission="medium")
    result = next(event for event in _events(approved) if event["type"] == "plan_patch_result")
    assert result["after"]["title"] == PY_STUDY
    assert result["after"]["estimatedMinutes"] == 30
    with get_conn() as conn:
        first = conn.execute("SELECT estimated_minutes FROM plans WHERE content = ?", (PY_STUDY,)).fetchone()
        second = conn.execute("SELECT estimated_minutes FROM plans WHERE content = ?", ("React \u5b66\u4e60",)).fetchone()
        assert first["estimated_minutes"] == 30
        assert second["estimated_minutes"] == 45


def test_command_drafts_route_stays_private(client):
    assert client.post("/api/command/drafts", json={}).status_code == 404


def test_show_current_plan_returns_inline_detail(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "message": "展开看看完整计划"},
    )

    assert response.status_code == 200
    events = _events(response)
    detail = next(event for event in events if event["type"] == "plan_detail")
    assert detail["title"] == "AI internship prep"
    assert detail["structuredPlan"]["milestones"][0]["tasks"][0]["title"] == "Draft Planix project intro"
    assert detail["planHorizon"]["durationDays"] == 5
    assert detail["qualityReport"]["metrics"]["totalTasks"] == 2
    assert detail["qualityStatus"] == "local_fallback"
    assert detail["sourceType"] == "local_fallback"


def test_regenerate_current_draft_supersedes_old_version(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "message": "重新生成一个更轻松的版本"},
    )

    assert response.status_code == 200
    assert FakeRuntime.calls == 2
    events = _events(response)
    assert any(event["type"] == "draft_created" and event["version"] == 2 for event in events)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT version, status FROM command_drafts WHERE thread_id = ? ORDER BY version ASC",
            (thread_id,),
        ).fetchall()
    assert [(row["version"], row["status"]) for row in rows] == [(1, "superseded"), (2, "current")]


def test_sync_calendar_low_requires_approval_and_does_not_write(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "把这个计划写进日历"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "calendar_plan_preview" for event in events)
    approval = next(event for event in events if event["type"] == "approval_required")
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0
        action = conn.execute("SELECT * FROM command_actions WHERE id = ?", (approval["actionId"],)).fetchone()
        assert action["status"] == "waiting_approval"
        assert conn.execute("SELECT COUNT(*) AS count FROM command_approvals WHERE action_id = ?", (approval["actionId"],)).fetchone()["count"] == 1


def test_write_plan_phrase_uses_current_draft_without_runtime(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]
    assert FakeRuntime.calls == 1

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "写入计划"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert FakeRuntime.calls == 1
    assert not any(event["type"] == "runtime_started" for event in events)
    assert any(event["type"] == "calendar_plan_preview" for event in events)
    assert any(event["type"] == "approval_required" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


def test_short_save_phrase_with_current_draft_means_calendar_write(client, monkeypatch):
    FakeRuntime.calls = 0
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "保存"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert FakeRuntime.calls == 1
    assert not any(event["type"] == "runtime_started" for event in events)
    assert any(event["type"] == "calendar_plan_preview" for event in events)


def test_approve_calendar_write_creates_plans(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]
    write = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "把这个计划写进日历"},
    )
    action_id = next(event for event in _events(write) if event["type"] == "approval_required")["actionId"]

    response = client.post(
        "/api/command/approve",
        json={"threadId": thread_id, "actionId": action_id, "decision": "approve", "permission": "low"},
    )

    assert response.status_code == 200
    events = _events(response)
    result = next(event for event in events if event["type"] == "calendar_write_result")
    assert result["created"] == 2
    assert result["failed"] == 0
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 2
        assert conn.execute("SELECT status FROM command_actions WHERE id = ?", (action_id,)).fetchone()["status"] == "success"
        first_plan = conn.execute(
            "SELECT result, priority, estimated_minutes FROM plans WHERE content = ?",
            ("Draft Planix project intro",),
        ).fetchone()
        assert first_plan["result"] == "Write a concise project overview."
        assert first_plan["priority"] == "high"
        assert first_plan["estimated_minutes"] == 60


def test_approve_calendar_write_error_uses_calendar_specific_message(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client, "Plan my skating practice")
    thread_id = _events(first)[-1]["threadId"]
    write = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "write this plan to calendar"},
    )
    action_id = next(event for event in _events(write) if event["type"] == "approval_required")["actionId"]

    def fail_execute(self, action_id):
        raise RuntimeError("approve write exploded")

    monkeypatch.setattr("app.services.command_agent.CommandAgentService._execute_calendar_action", fail_execute)

    response = client.post(
        "/api/command/approve",
        json={"threadId": thread_id, "actionId": action_id, "decision": "approve", "permission": "low"},
    )

    assert response.status_code == 200
    events = _events(response)
    error = next(event for event in events if event["type"] == "error")
    assert "写入日历失败" in error["error"]
    assert "计划草稿保存失败" not in error["error"]


def test_reject_calendar_write_does_not_create_plans(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]
    write = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "low", "message": "把这个计划写进日历"},
    )
    action_id = next(event for event in _events(write) if event["type"] == "approval_required")["actionId"]

    response = client.post(
        "/api/command/approve",
        json={"threadId": thread_id, "actionId": action_id, "decision": "reject", "permission": "low"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "execution_result" and event["status"] == "rejected" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0
        assert conn.execute("SELECT status FROM command_actions WHERE id = ?", (action_id,)).fetchone()["status"] == "rejected"


@pytest.mark.parametrize("permission", ["medium", "high"])
def test_elevated_permission_still_requires_calendar_approval(client, monkeypatch, permission):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": permission, "message": "把这个计划写进日历"},
    )

    assert response.status_code == 200
    events = _events(response)
    assert any(event["type"] == "approval_required" for event in events)
    assert not any(event["type"] == "calendar_write_result" for event in events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


def test_refine_all_tasks_updates_current_draft_and_calendar_write_uses_refinements(client, monkeypatch):
    FakePlanningService.calls = []
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    monkeypatch.setattr("app.services.command_agent.PlanningService", lambda: FakePlanningService())

    first = _create_workbench_draft(client, "Plan my skating practice")
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "message": "refine all tasks"},
    )

    assert response.status_code == 200
    events = _events(response)
    started = next(event for event in events if event["type"] == "refinement_started")
    result = next(event for event in events if event["type"] == "refined_tasks_result")
    assert started["total"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert len(FakePlanningService.calls) == 1
    assert FakePlanningService.calls[0].plan_context is not None
    assert FakePlanningService.calls[0].plan_context.plan_title == "AI internship prep"
    assert FakePlanningService.calls[0].plan_context.current_milestone["title"] == "Project story"
    assert FakePlanningService.calls[0].available_minutes == 60

    with get_conn() as conn:
        draft = conn.execute("SELECT payload_json FROM command_drafts WHERE id = ?", (started["draftId"],)).fetchone()
        payload = json.loads(draft["payload_json"])
        assert sorted(payload["refinements"].keys()) == ["m0:t0"]
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0

    write = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "medium", "message": "write this plan to calendar"},
    )

    assert write.status_code == 200
    _approve_required_action(client, write, permission="medium")
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans WHERE refined_task_json != ''").fetchone()["count"] == 1


def test_refine_all_tasks_caps_large_current_milestone_at_five(client, monkeypatch):
    FakePlanningService.calls = []
    structured_plan = _valid_structured_plan()
    structured_plan["milestones"][0]["tasks"] = [
        {
            "title": f"Task {index}",
            "description": f"Practice item {index}",
            "estimatedMinutes": 120,
            "dueDate": "2026-07-05",
            "priority": "high",
        }
        for index in range(1, 8)
    ]
    _patch_runtime(monkeypatch, lambda: FakeRuntime(structured_plan=structured_plan))
    monkeypatch.setattr("app.services.command_agent.PlanningService", lambda: FakePlanningService())

    first = _create_workbench_draft(client, "Plan my Python practice")
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "message": "refine all tasks"},
    )

    assert response.status_code == 200
    events = _events(response)
    started = next(event for event in events if event["type"] == "refinement_started")
    result = next(event for event in events if event["type"] == "refined_tasks_result")
    assert started["total"] == 5
    assert result["succeeded"] == 5
    assert len(FakePlanningService.calls) == 5
    assert all(call.available_minutes == 120 for call in FakePlanningService.calls)


def test_refine_without_current_draft_does_not_write(client):
    response = client.post("/api/command/chat", json={"mode": "auto", "message": "refine all tasks"})

    assert response.status_code == 200
    events = _events(response)
    assert not any(event["type"] == "refinement_started" for event in events)
    text = "".join(event.get("text", "") for event in events if event["type"] == "assistant_delta")
    assert "没有可细化的计划草稿" in text
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


def test_calendar_write_does_not_overwrite_manual_plan(client, monkeypatch):
    _patch_runtime(monkeypatch, lambda: FakeRuntime())
    client.post(
        "/api/plans",
        json={"date": "2026-07-05", "time": "08:00", "content": "Draft Planix project intro", "source": "manual", "result": "keep me"},
    )
    first = _create_workbench_draft(client)
    thread_id = _events(first)[-1]["threadId"]

    response = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "mode": "auto", "permission": "medium", "message": "把这个计划写进日历"},
    )

    assert response.status_code == 200
    _approve_required_action(client, response, permission="medium")
    with get_conn() as conn:
        manual = conn.execute("SELECT * FROM plans WHERE source = 'manual'").fetchone()
        assert manual["result"] == "keep me"
        assert conn.execute("SELECT COUNT(*) AS count FROM plans WHERE source = 'ai'").fetchone()["count"] == 2


def test_calendar_write_does_not_overwrite_existing_ai_result(client):
    source_key = "command-draft:draft_existing_ai:m0:t0"
    client.post(
        "/api/plans",
        json={
            "date": "2026-07-05",
            "time": "08:00",
            "content": "Draft Planix project intro",
            "source": "ai",
            "sourceKey": source_key,
            "result": "keep existing user note",
        },
    )

    state, plan = CommandAgentService()._upsert_calendar_plan(
        {
            "title": "Draft Planix project intro",
            "date": "2026-07-05",
            "time": "09:00",
            "description": "Write a concise project overview.",
            "priority": "high",
            "estimatedMinutes": 60,
            "sourceKey": source_key,
        }
    )

    assert state == "updated"
    assert plan.result == "keep existing user note"
    assert plan.priority == "high"
    assert plan.estimated_minutes == 60
    with get_conn() as conn:
        row = conn.execute("SELECT result, priority, estimated_minutes FROM plans WHERE source_key = ?", (source_key,)).fetchone()
        assert row["result"] == "keep existing user note"
        assert row["priority"] == "high"
        assert row["estimated_minutes"] == 60
