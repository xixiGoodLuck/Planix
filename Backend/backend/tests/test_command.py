import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.db import get_conn, init_db
from app.schemas import (
    AgentDecision,
    AgentMessage,
    CommandDecision,
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


def test_calendar_write_keeps_distinct_schedule_sessions_with_distinct_source_keys(client):
    service = CommandAgentService()
    first_state, first = service._upsert_calendar_plan(
        {
            "title": "Repeated study session",
            "date": "2026-07-05",
            "time": "09:00",
            "estimatedMinutes": 60,
            "sourceKey": "planning-v2:plan-1:schedule-1:session-1",
        }
    )
    second_state, second = service._upsert_calendar_plan(
        {
            "title": "Repeated study session",
            "date": "2026-07-05",
            "time": "10:00",
            "estimatedMinutes": 60,
            "sourceKey": "planning-v2:plan-1:schedule-1:session-2",
        }
    )

    assert first_state == second_state == "created"
    assert first.id != second.id
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_key FROM plans WHERE content = ? ORDER BY source_key",
            ("Repeated study session",),
        ).fetchall()
    assert [row["source_key"] for row in rows] == [
        "planning-v2:plan-1:schedule-1:session-1",
        "planning-v2:plan-1:schedule-1:session-2",
    ]
