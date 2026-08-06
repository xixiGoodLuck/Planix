from app.db import get_conn
from app.schemas import PlanQualityMetrics, PlanQualityReport
from app.services import planning as planning_module
from app.services.ai_settings import EffectiveAiSettings
from app.services.llm import LlmError, LlmResult
import json


def _goal_payload() -> dict:
    return {
        "goal": "Build a strong AI application internship portfolio",
        "deadline": "2026-09-30",
        "dailyHours": 3,
        "materials": "RAG FastAPI Agent evaluation",
        "preferences": "Deep work in the morning",
        "date": "2026-07-01",
    }


def _refine_payload() -> dict:
    return {
        "goal": "Learn Python for AI applications",
        "taskTitle": "Practice Python conditionals and loops",
        "taskDescription": "Build a small command-line exercise with if/else and for loops.",
        "date": "2026-07-04",
        "availableMinutes": 60,
        "userConstraints": ["project-driven"],
        "retrievedSources": [],
        "outputLanguage": "en",
    }


def _settings(
    *,
    provider: str = "deepseek",
    api_key: str = "sk-test-local",
    base_url: str = "https://api.deepseek.com/v1?token=secret",
    model: str = "deepseek-v4-flash",
    timeout_seconds: int = 10,
) -> EffectiveAiSettings:
    return EffectiveAiSettings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=0.1,
        timeout_seconds=timeout_seconds,
        updated_at="",
    )


def _insert_ski_command_draft(draft_id: str = "draft_ski_context") -> str:
    payload = {
        "structuredPlan": {
            "goalTitle": "14天滑雪入门",
            "goalDescription": "掌握滑雪基本站姿、平衡、安全摔倒、犁式刹车和入门转弯。",
            "durationDays": 14,
            "milestones": [
                {
                    "title": "滑雪基础与安全",
                    "description": "学习滑雪装备、基本站姿、平衡、安全摔倒和犁式刹车。",
                    "tasks": [
                        {
                            "title": "了解滑雪装备与安全知识",
                            "description": "熟悉雪板、雪鞋、头盔等使用。",
                            "estimatedMinutes": 30,
                            "dueDate": "2026-07-07",
                            "priority": "high",
                        },
                        {
                            "title": "练习基本站姿与平衡",
                            "description": "在平地上模拟滑雪站姿，双腿微曲，重心居中，练习原地踏步和平衡。",
                            "estimatedMinutes": 45,
                            "dueDate": "2026-07-08",
                            "priority": "high",
                        },
                        {
                            "title": "学习犁式刹车原理与分解动作",
                            "description": "学习雪板呈八字形、内刃切入雪面的刹车动作。",
                            "estimatedMinutes": 40,
                            "dueDate": "2026-07-09",
                            "priority": "high",
                        },
                    ],
                }
            ],
        },
        "sources": [],
        "planHorizon": {"durationDays": 14},
        "qualityStatus": "passed",
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO command_threads(id, title) VALUES (?, ?)",
            ("thread_ski_context", "ski thread"),
        )
        conn.execute(
            """
            INSERT INTO command_drafts(id, thread_id, kind, version, status, title, summary, payload_json)
            VALUES (?, ?, 'calendar_plan', 1, 'current', ?, '', ?)
            """,
            (draft_id, "thread_ski_context", "14天滑雪入门", json.dumps(payload, ensure_ascii=False)),
        )
    return f"command-draft:{draft_id}:m0:t1"


def _structured_plan_json(
    *,
    goal_title: str = "Python 90 day plan",
    duration_days: int = 90,
    task_count: int = 24,
    spacing_days: int = 3,
) -> str:
    milestones = []
    counter = 0
    for milestone_index in range(3):
        tasks = []
        tasks_for_milestone = task_count // 3 + (1 if milestone_index < task_count % 3 else 0)
        for _ in range(tasks_for_milestone):
            counter += 1
            tasks.append(
                {
                    "title": f"Build Python artifact {counter}",
                    "description": "Create a concrete learning output.",
                    "estimatedMinutes": 60,
                    "dueDate": f"2026-07-{1 + min(27, counter * spacing_days):02d}" if spacing_days <= 1 else None,
                    "priority": "medium",
                }
            )
        milestones.append({"title": f"Month {milestone_index + 1}", "description": "Milestone", "tasks": tasks})
    if spacing_days > 1:
        from datetime import date, timedelta

        start = date.fromisoformat("2026-07-01")
        counter = 0
        for milestone in milestones:
            for task in milestone["tasks"]:
                counter += 1
                task["dueDate"] = (start + timedelta(days=counter * spacing_days)).isoformat()
    return json.dumps(
        {
            "summary": "Live plan",
            "structuredPlan": {
                "goalTitle": goal_title,
                "goalDescription": "Live goal description",
                "durationDays": duration_days,
                "milestones": milestones,
                "reviewPlan": {"frequency": "daily", "questions": ["What improved today?"]},
            },
        },
        ensure_ascii=False,
    )


def _count_table(table: str) -> int:
    with get_conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def test_goal_plan_returns_and_persists_structured_mock_result(client):
    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["mode"] == "mock"
    assert body["summary"]
    assert len(body["phases"]) >= 1
    assert len(body["tasks"]) >= 1
    assert body["structuredPlan"]["goalTitle"]
    assert body["structuredPlan"]["durationDays"] >= 1
    assert body["structuredPlan"]["milestones"][0]["tasks"][0]["priority"] in {"low", "medium", "high"}
    assert body["fallbackReason"] == "missing_api_key"
    assert body["baseUrlHost"] == "api.deepseek.com"

    with get_conn() as conn:
        row = conn.execute(
            "SELECT structured_plan_json, sources_json FROM planning_goals WHERE id = ?",
            (body["id"],),
        ).fetchone()

    assert row is not None
    assert "goalTitle" in row["structured_plan_json"]
    assert row["sources_json"].startswith("[")


def test_goal_plan_python_goal_has_structured_milestones(client):
    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "帮我规划一个 Python 学习计划",
            "deadline": "2026-07-29",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    structured = body["structuredPlan"]
    assert "Python" in structured["goalTitle"]
    assert structured["durationDays"] == 28
    assert structured["milestones"]
    assert structured["milestones"][0]["tasks"]
    assert structured["reviewPlan"]["questions"]


def test_planning_session_unclear_goal_stops_before_design(client):
    response = client.post(
        "/api/planning/sessions",
        json={"entryPoint": "p_mode", "userInput": "I want to learn Python", "context": {"date": "2026-07-05"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_goal_clarification"
    assert body["userNeedContract"]["canMoveToDesign"] is False
    assert body["userNeedContract"]["clarificationQuestions"]
    assert body["designProposal"] is None
    assert body["executionDraft"] is None


def test_planning_session_gates_feedback_and_memory_reuse(client):
    payload = {
        "entryPoint": "p_mode",
        "threadId": "thread-session-flow",
        "userInput": "Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
        "context": {"date": "2026-07-05"},
    }
    created = client.post("/api/planning/sessions", json=payload)

    assert created.status_code == 200
    body = created.json()
    session_id = body["sessionId"]
    assert body["status"] == "waiting_design_approval"
    assert body["memoryInsight"]
    assert body["resourceBrief"]
    assert body["designProposal"]
    assert body["executionDraft"] is None

    blocked_write = client.post(f"/api/planning/sessions/{session_id}/prepare-calendar-write", json={})
    assert blocked_write.status_code == 409

    execution = client.post(f"/api/planning/sessions/{session_id}/approve-design")
    assert execution.status_code == 200
    body = execution.json()
    assert body["status"] == "waiting_execution_approval"
    first_task = body["executionDraft"]["tasks"][0]
    assert first_task["acceptanceCriteria"]
    assert first_task["deliverable"]
    assert first_task["resourceBundle"]["primary"] or first_task["resourceBundle"]["practice"]

    feedback = client.post(f"/api/planning/sessions/{session_id}/feedback", json={"text": "\u8d44\u6e90\u592a\u96be"})
    assert feedback.status_code == 200
    body = feedback.json()
    assert body["learningPatch"]["feedbackType"] == "resource_feedback"
    assert body["learningPatch"]["immediatePatch"]["action"] == "replace_resource"
    assert body["learningPatch"]["reflection"]["howToAvoidNextTime"]
    assert body["learningPatch"]["longTermLearning"]["newRule"]

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM memories WHERE kind = 'preference'").fetchone()["count"] >= 1
        assert conn.execute("SELECT COUNT(*) AS count FROM memories WHERE kind = 'review'").fetchone()["count"] >= 1

    next_session = client.post("/api/planning/sessions", json={**payload, "threadId": "thread-session-flow-next"})
    assert next_session.status_code == 200
    next_body = next_session.json()
    assert next_body["memoryInsight"]["memoryHits"]["preferences"]

    approved = client.post(f"/api/planning/sessions/{session_id}/approve-execution", json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ready_to_write_calendar"
    prepared = client.post(f"/api/planning/sessions/{session_id}/prepare-calendar-write", json={})
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "waiting_calendar_write_approval"


def test_goal_plan_llm_error_returns_readable_local_structured_result(client, monkeypatch):
    class TimeoutClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return None, LlmError("timeout", "timeout", 0)

    monkeypatch.setattr(planning_module, "LlmClient", lambda: TimeoutClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "timeout"
    assert body["baseUrlHost"] == "api.deepseek.com"
    assert body["structuredPlan"]["goalTitle"]
    combined = " ".join(
        [
            body["summary"],
            body["structuredPlan"]["goalTitle"],
            body["structuredPlan"]["goalDescription"],
            *[phase["title"] for phase in body["phases"]],
            *[phase["detail"] for phase in body["phases"]],
            *[task["title"] for task in body["tasks"]],
            *[task["reason"] for task in body["tasks"]],
        ]
    )
    assert "�" not in combined
    assert "乱码" not in combined
    assert "ui-mock" not in combined
    assert "静态 UI 模拟" not in combined


def test_goal_plan_invalid_llm_structured_plan_is_safely_completed(client, monkeypatch):
    class InvalidStructuredClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content='{"summary":"LLM partial","structuredPlan":{"goalTitle":"Broken","durationDays":"14","milestones":[{"title":"M","tasks":[{"title":"Task","priority":"urgent","estimatedMinutes":"bad"}]}],"reviewPlan":{"frequency":"sometimes"}}}',
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: InvalidStructuredClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    structured = body["structuredPlan"]
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "quality_gate_failed"
    assert body["errorType"] == "plan_quality_failed"
    assert body["qualityStatus"] == "local_fallback"
    assert structured["goalTitle"]
    assert structured["durationDays"] >= 1
    assert structured["milestones"][0]["tasks"][0]["priority"] in {"low", "medium", "high"}
    assert isinstance(structured["milestones"][0]["tasks"][0]["estimatedMinutes"], int)
    assert structured["reviewPlan"]["frequency"] in {"daily", "weekly"}


def test_goal_plan_uses_smart_llm_timeout(client, monkeypatch):
    captured = {}

    class CaptureClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            captured.update(kwargs)
            return None, None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: CaptureClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    assert captured["timeout_seconds"] == planning_module.GOAL_PLAN_MIN_LLM_TIMEOUT_SECONDS
    assert captured["max_tokens"] == planning_module.GOAL_PLAN_DEFAULT_MAX_TOKENS
    assert captured["max_token_cap"] == planning_module.GOAL_PLAN_MAX_TOKEN_LIMIT
    assert captured["response_format_json"] is True


def test_goal_plan_max_tokens_env_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, raising=False)
    assert planning_module._goal_plan_max_tokens() == 4096

    monkeypatch.setenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, "6000")
    assert planning_module._goal_plan_max_tokens() == 6000

    monkeypatch.setenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, "8000")
    assert planning_module._goal_plan_max_tokens() == 8000

    monkeypatch.setenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, "99999")
    assert planning_module._goal_plan_max_tokens() == 8000

    monkeypatch.setenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, "abc")
    assert planning_module._goal_plan_max_tokens() == 4096

    monkeypatch.setenv(planning_module.GOAL_PLAN_MAX_TOKENS_ENV, "")
    assert planning_module._goal_plan_max_tokens() == 4096


def test_goal_plan_caps_smart_llm_timeout(client, monkeypatch):
    captured = {}

    class SlowSettingsClient:
        settings = _settings(timeout_seconds=120)

        def complete(self, *args, **kwargs):
            captured.update(kwargs)
            return None, None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: SlowSettingsClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    assert captured["timeout_seconds"] == planning_module.GOAL_PLAN_MAX_LLM_TIMEOUT_SECONDS


def test_goal_plan_passes_chinese_output_language_to_llm(client, monkeypatch):
    captured = {}

    class CaptureLanguageClient:
        settings = _settings()

        def complete(self, feature, system, user, **kwargs):
            captured["system"] = system
            captured["user"] = json.loads(user)
            return None, None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: CaptureLanguageClient())

    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "帮我规划一个 Python 学习计划",
            "deadline": "2026-07-29",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
        },
    )

    assert response.status_code == 200
    assert captured["user"]["outputLanguage"] == "zh-CN"
    assert "outputLanguage" in captured["system"]
    assert "Simplified Chinese" in captured["system"]


def test_goal_plan_prefers_frontend_output_language_over_goal_text(client, monkeypatch):
    captured = {}

    class CaptureLanguageClient:
        settings = _settings()

        def complete(self, feature, system, user, **kwargs):
            captured["user"] = json.loads(user)
            return None, None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: CaptureLanguageClient())

    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "帮我规划一个 Python 学习计划",
            "deadline": "2026-07-29",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
            "outputLanguage": "en-US",
        },
    )

    assert response.status_code == 200
    assert captured["user"]["outputLanguage"] == "en-US"


def test_goal_plan_truncated_llm_json_reports_invalid_model_output(client, monkeypatch):
    class TruncatedJsonClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content='{"summary":"Live plan","structuredPlan":{"goalTitle":"Broken"',
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: TruncatedJsonClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "invalid_model_output"
    assert body["structuredPlan"]["goalTitle"]


def test_goal_plan_empty_llm_content_is_reported(client, monkeypatch):
    class EmptyContentClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return None, LlmError(
                "The model returned empty content. Increase max tokens or use a non-reasoning model.",
                "empty_content",
                0,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: EmptyContentClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "empty_content"
    assert body["structuredPlan"]["goalTitle"]


def test_goal_plan_model_output_truncated_is_reported(client, monkeypatch):
    class TruncatedOutputClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return None, LlmError(
                "The model output was truncated before completion.",
                "model_output_truncated",
                0,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: TruncatedOutputClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "model_output_truncated"
    assert body["structuredPlan"]["goalTitle"]


def test_goal_plan_mock_provider_reports_mock_fallback(client, monkeypatch):
    class MockProviderClient:
        settings = _settings(provider="mock", api_key="sk-test-local")

        def complete(self, *args, **kwargs):
            return None, None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: MockProviderClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "mock_provider"
    assert body["errorType"] is None


def test_goal_plan_llm_success_has_no_fallback_diagnostics(client, monkeypatch):
    class SuccessClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content=_structured_plan_json(goal_title="Live Goal", duration_days=91, task_count=36, spacing_days=2),
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: SuccessClient())

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "llm"
    assert body["fallbackReason"] is None
    assert body["errorType"] is None
    assert body["baseUrlHost"] is None
    assert body["qualityStatus"] == "passed"
    assert body["qualityReport"]["ok"] is True
    assert body["qualityReport"]["metrics"]["durationDays"] == body["planHorizon"]["durationDays"]
    assert body["qualityReport"]["metrics"]["qualityStatus"] == "passed"
    assert body["qualityReport"]["metrics"]["sourceType"] in {"local_context", "model_knowledge", "insufficient_context"}
    assert body["qualityReport"]["metrics"]["fallbackUsed"] is False
    assert body["phases"][0]["title"] == "Month 1"
    assert body["tasks"][0]["title"] == "Build Python artifact 1"


def test_goal_plan_sparse_llm_output_triggers_one_repair(client, monkeypatch):
    class RepairClient:
        settings = _settings()
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            content = (
                _structured_plan_json(goal_title="Sparse", duration_days=90, task_count=5, spacing_days=1)
                if self.calls == 1
                else _structured_plan_json(goal_title="Repaired", duration_days=90, task_count=24, spacing_days=3)
            )
            return LlmResult(content=content, provider="deepseek", model="deepseek-v4-flash"), None

    repair_client = RepairClient()
    monkeypatch.setattr(planning_module, "LlmClient", lambda: repair_client)

    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "三个月学习 Python",
            "deadline": "",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert repair_client.calls == 2
    assert body["mode"] == "llm"
    assert body["qualityStatus"] == "repaired"
    assert body["structuredPlan"]["goalTitle"] == "Repaired"
    assert body["qualityReport"]["ok"] is True
    assert body["qualityReport"]["totalTasks"] == 24
    assert body["qualityReport"]["metrics"]["repairAttempted"] is True
    assert body["qualityReport"]["metrics"]["fallbackUsed"] is False
    assert body["qualityReport"]["metrics"]["qualityStatus"] == "repaired"
    assert body["qualityReport"]["metrics"]["totalTasks"] == 24


def test_golden_demo_python_90_day_plan_quality(client, monkeypatch):
    class FailedRepairClient:
        settings = _settings()
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            return (
                LlmResult(
                    content=_structured_plan_json(goal_title="Still sparse", duration_days=90, task_count=5, spacing_days=1),
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    failed_client = FailedRepairClient()
    monkeypatch.setattr(planning_module, "LlmClient", lambda: failed_client)

    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "三个月学习 Python",
            "deadline": "",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    tasks = [
        task
        for milestone in body["structuredPlan"]["milestones"]
        for task in milestone["tasks"]
    ]
    dates = {task["dueDate"] for task in tasks}
    assert failed_client.calls == 2
    assert body["mode"] == "mock"
    assert body["fallbackReason"] == "quality_gate_failed"
    assert body["errorType"] == "plan_quality_failed"
    assert body["qualityStatus"] == "local_fallback"
    assert body["qualityReport"]["ok"] is True
    assert body["planHorizon"]["durationDays"] == 90
    assert len(body["structuredPlan"]["milestones"]) >= 3
    assert len(tasks) >= 24
    assert len(dates) > 1
    assert body["qualityReport"]["coveredWeekCount"] >= 10
    assert body["qualityReport"]["metrics"]["durationDays"] == 90
    assert body["qualityReport"]["metrics"]["totalTasks"] >= 24
    assert body["qualityReport"]["metrics"]["coveredWeekCount"] >= 10
    assert body["qualityReport"]["metrics"]["repairAttempted"] is True
    assert body["qualityReport"]["metrics"]["fallbackUsed"] is True
    assert body["qualityReport"]["metrics"]["qualityStatus"] == "local_fallback"
    assert body["qualityReport"]["metrics"]["sourceType"] == "local_fallback"


def test_quality_metrics_serialization():
    report = PlanQualityReport(
        ok=True,
        score=100,
        totalTasks=24,
        milestoneCount=3,
        coveredWeekCount=10,
        dateSpanDays=84,
        issues=[],
        metrics=PlanQualityMetrics(
            durationDays=90,
            totalTasks=24,
            milestoneCount=3,
            coveredWeekCount=10,
            dateSpanDays=84,
            weakTaskCount=0,
            missingDueDateCount=0,
            outOfRangeDueDateCount=0,
            repairAttempted=True,
            fallbackUsed=False,
            qualityStatus="repaired",
            sourceType="model_knowledge",
            localRelevance="low",
        ),
    )

    dumped = report.model_dump(by_alias=True)

    assert dumped["totalTasks"] == 24
    assert dumped["metrics"]["durationDays"] == 90
    assert dumped["metrics"]["coveredWeekCount"] == 10
    assert dumped["metrics"]["repairAttempted"] is True
    assert dumped["metrics"]["qualityStatus"] == "repaired"


def test_goal_plan_llm_error_types_are_reported_safely(client, monkeypatch):
    error_types = [
        "auth_error",
        "bad_model",
        "bad_base_url",
        "network_error",
        "timeout",
        "insufficient_balance",
        "invalid_key_format",
        "model_output_truncated",
        "empty_content",
    ]

    for error_type in error_types:
        class ErrorClient:
            settings = _settings()

            def complete(self, *args, **kwargs):
                return None, LlmError(f"{error_type} happened", error_type, 0, detail="Authorization: Bearer secret")

        monkeypatch.setattr(planning_module, "LlmClient", lambda: ErrorClient())

        response = client.post("/api/planning/goal-plan", json=_goal_payload())

        assert response.status_code == 200
        body = response.json()
        assert body["fallbackReason"] == "llm_error"
        assert body["errorType"] == error_type
        assert body["baseUrlHost"] == "api.deepseek.com"
        combined = " ".join(str(body.get(key) or "") for key in ("errorMessage", "baseUrlHost"))
        assert "Bearer" not in combined
        assert "secret" not in combined
        assert "token=" not in combined


def test_local_structured_goal_plan_template_is_readable_chinese(client):
    response = client.post(
        "/api/planning/goal-plan",
        json={
            "goal": "学会 Python",
            "deadline": "2026-07-29",
            "dailyHours": 2,
            "materials": "",
            "preferences": "",
            "date": "2026-07-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    combined = " ".join(
        [
            body["summary"],
            body["structuredPlan"]["goalTitle"],
            body["structuredPlan"]["goalDescription"],
            *[phase["title"] for phase in body["phases"]],
            *[phase["detail"] for phase in body["phases"]],
            *[task["title"] for task in body["tasks"]],
            *[task["reason"] for task in body["tasks"]],
        ]
    )
    assert "阶段" in combined
    assert "Python" in combined
    assert "锛" not in combined
    assert "鍒" not in combined
    assert "乱码" not in combined


def test_goal_plan_returns_rag_sources_when_documents_match(client):
    document = client.post(
        "/api/rag/documents",
        json={
            "title": "AI internship JD",
            "content": "AI application internships value RAG, FastAPI, React, Agent tools, and evaluation.",
        },
    ).json()

    response = client.post("/api/planning/goal-plan", json=_goal_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["sources"]
    assert body["sources"][0]["documentId"] == document["id"]
    assert body["structuredPlan"]["goalDescription"]


def test_daily_review_creates_replan_preview_without_writing_plans(client):
    created = client.post(
        "/api/plans",
        json={
            "date": "2026-07-01",
            "time": "09:00",
            "content": "Finish planning loop backend",
            "done": False,
        },
    ).json()

    response = client.post(
        "/api/planning/daily-review",
        json={
            "date": "2026-07-01",
            "goal": "Build portfolio-grade AI planner",
            "data": {
                "2026-07-01": {
                    "plans": [
                        {
                            "id": created["id"],
                            "time": "09:00",
                            "title": "Finish planning loop backend",
                            "done": False,
                            "completion": "",
                        }
                    ]
                }
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["doneCount"] == 0
    assert body["totalCount"] == 1
    assert body["targetDate"] == "2026-07-02"
    assert body["replanTasks"][0]["targetDate"] == "2026-07-02"

    tomorrow = client.get("/api/plans", params={"date": "2026-07-02"}).json()
    assert tomorrow == []

    saved = client.get("/api/planning/daily-review", params={"date": "2026-07-01"})
    assert saved.status_code == 200
    assert saved.json()["mode"] == "saved"


def test_missing_daily_review_returns_empty_saved_state(client):
    response = client.get("/api/planning/daily-review", params={"date": "2026-07-03"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "saved"
    assert body["date"] == "2026-07-03"
    assert body["summary"] == ""
    assert body["suggestions"] == []
    assert body["doneCount"] == 0
    assert body["totalCount"] == 0
    assert body["targetDate"] == "2026-07-04"
    assert body["replanTasks"] == []


def test_replan_apply_writes_ai_plans(client):
    response = client.post(
        "/api/planning/replan/apply",
        json={
            "tasks": [
                {
                    "targetDate": "2026-07-02",
                    "time": "10:00",
                    "title": "Continue backend tests",
                    "reason": "Moved from today's unfinished work",
                    "sourcePlanId": "local-plan-1",
                }
            ]
        },
    )
    assert response.status_code == 200
    created = response.json()
    assert created[0]["date"] == "2026-07-02"
    assert created[0]["content"] == "Continue backend tests"
    assert created[0]["source"] == "ai"

    listed = client.get("/api/plans", params={"date": "2026-07-02"}).json()
    assert [item["id"] for item in listed] == [created[0]["id"]]


def test_refine_task_returns_local_fallback_without_key(client):
    response = client.post("/api/planning/refine-task", json=_refine_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "local_fallback"
    assert body["fallbackReason"] == "missing_api_key"
    assert body["title"] == _refine_payload()["taskTitle"]
    assert body["estimatedMinutes"] == 60
    assert len(body["steps"]) >= 3
    assert len(body["checklist"]) >= 2
    assert len(body["acceptanceCriteria"]) >= 1
    assert body["deliverable"]
    assert sum(block["durationMinutes"] for block in body["timeBlocks"]) == 60
    assert all(block["durationMinutes"] <= 30 for block in body["timeBlocks"])
    assert body["budgetExplanation"]
    assert body["planFitCheck"]["hasCheckableOutput"] is True
    assert body["learningResources"]


def test_refine_task_requires_task_title(client):
    payload = _refine_payload()
    payload["taskTitle"] = "   "

    response = client.post("/api/planning/refine-task", json=payload)

    assert response.status_code == 422


def test_refine_task_llm_success_returns_valid_refinement(client, monkeypatch):
    class SuccessClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content=json.dumps(
                        {
                            "title": "Practice Python conditionals and loops",
                            "objective": "Build confidence using if/else and loops in a small CLI exercise.",
                            "estimatedMinutes": 55,
                            "steps": [
                                "Review if/else syntax with one tiny example.",
                                "Write a loop that checks each item in a list.",
                                "Combine both into a command-line scoring script.",
                            ],
                            "checklist": [
                                "The script runs without syntax errors.",
                                "At least two branches and one loop are used.",
                            ],
                            "acceptanceCriteria": [
                                "The user can run the script and see different outputs for different inputs.",
                            ],
                            "deliverable": "A saved python_practice.py file.",
                            "risks": ["Skipping the runnable example would make the task too abstract."],
                            "fallbackTips": ["If stuck, start with a hard-coded list before adding input."],
                            "timeBlocks": [
                                {"title": "Read a random tutorial", "durationMinutes": 45, "action": "Read and copy notes."},
                                {"title": "Practice", "durationMinutes": 15, "action": "Write a loop exercise."},
                            ],
                            "learningResources": [
                                {"title": "Python tutorial", "type": "official_doc", "url": "https://docs.python.org/3/tutorial/"},
                                {"title": "Random blog", "type": "library_doc", "url": "https://example.com/python-loops"},
                            ],
                            "budgetExplanation": "Use the available session budget.",
                            "planFitCheck": {
                                "fitsCurrentMilestone": True,
                                "advancesOverallGoal": True,
                                "hasCheckableOutput": True,
                                "note": "This supports the Python basics milestone.",
                            },
                        }
                    ),
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: SuccessClient())

    response = client.post("/api/planning/refine-task", json=_refine_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "llm"
    assert body["fallbackReason"] is None
    assert body["errorType"] is None
    assert body["estimatedMinutes"] == 60
    assert len(body["steps"]) == 3
    assert [block["durationMinutes"] for block in body["timeBlocks"]] == [30, 15, 15]
    assert body["learningResources"][0]["url"] == "https://docs.python.org/3/tutorial/"
    assert body["learningResources"][1]["url"] is None
    assert body["learningResources"][1]["type"] == "search_keyword"
    assert body["planFitCheck"]["fitsCurrentMilestone"] is True


def test_refine_task_plan_context_budget_takes_priority(client):
    payload = _refine_payload()
    payload["availableMinutes"] = 90
    payload["planContext"] = {
        "planTitle": "Three month Python plan",
        "durationDays": 90,
        "qualityStatus": "passed",
        "dailyLearningMinutes": 120,
        "currentMilestone": {"title": "Python basics"},
        "currentTask": {
            "title": payload["taskTitle"],
            "description": payload["taskDescription"],
            "estimatedMinutes": 120,
            "dueDate": "2026-07-04",
            "priority": "high",
        },
        "sameMilestoneTasks": ["Practice Python conditionals and loops"],
    }

    response = client.post("/api/planning/refine-task", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["estimatedMinutes"] == 120
    assert [block["durationMinutes"] for block in body["timeBlocks"]] == [30, 30, 30, 30]
    assert "120" in body["budgetExplanation"]


def test_resolve_refine_context_from_command_draft_source_key(client):
    source_key = _insert_ski_command_draft()

    context = planning_module.resolve_refine_plan_context_from_source_key(source_key, daily_learning_minutes=45)

    assert context is not None
    assert context["planTitle"] == "14天滑雪入门"
    assert context["currentMilestone"]["title"] == "滑雪基础与安全"
    assert context["currentTask"]["title"] == "练习基本站姿与平衡"
    assert "滑雪站姿" in context["currentTask"]["description"]
    assert context["previousTask"]["title"] == "了解滑雪装备与安全知识"
    assert context["nextTask"]["title"] == "学习犁式刹车原理与分解动作"


def test_refine_task_uses_command_draft_context_and_blocks_yoga_drift(client, monkeypatch):
    source_key = _insert_ski_command_draft()

    class YogaDriftClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content=json.dumps(
                        {
                            "title": "基本站姿与平衡练习",
                            "objective": "通过瑜伽山式和树式改善单腿平衡。",
                            "estimatedMinutes": 45,
                            "steps": ["练习山式", "练习树式", "记录瑜伽平衡感受"],
                            "checklist": ["完成山式", "完成树式"],
                            "acceptanceCriteria": ["能保持树式30秒"],
                            "deliverable": "瑜伽平衡记录",
                            "risks": [],
                            "fallbackTips": [],
                            "timeBlocks": [
                                {"title": "瑜伽热身", "durationMinutes": 45, "action": "练习山式和树式。"},
                            ],
                            "learningResources": [
                                {"title": "山式教程", "type": "search_keyword", "searchKeyword": "山式 Tadasana"},
                            ],
                            "budgetExplanation": "45分钟瑜伽练习。",
                            "planFitCheck": {
                                "fitsCurrentMilestone": True,
                                "advancesOverallGoal": True,
                                "hasCheckableOutput": True,
                                "note": "瑜伽平衡服务于当前任务。",
                            },
                        }
                    ),
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: YogaDriftClient())
    payload = {
        "goal": "练习平衡",
        "taskTitle": "练习基本站姿与平衡",
        "taskDescription": "",
        "date": "2026-07-08",
        "availableMinutes": 60,
        "sourceKey": source_key,
        "outputLanguage": "zh",
    }

    response = client.post("/api/planning/refine-task", json=payload)

    assert response.status_code == 200
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert body["mode"] == "local_fallback"
    assert body["errorType"] == "domain_mismatch"
    assert body["estimatedMinutes"] == 45
    assert "滑雪" in serialized
    assert "瑜伽" not in serialized
    assert "山式" not in serialized
    assert all(block["durationMinutes"] <= 30 for block in body["timeBlocks"])


def test_normalize_time_blocks_splits_long_blocks():
    cases = {
        40: [20, 20],
        45: [30, 15],
        60: [30, 30],
        90: [30, 30, 30],
        120: [30, 30, 30, 30],
    }
    for minutes, expected in cases.items():
        blocks = planning_module.normalize_time_blocks(
            [{"title": "Long block", "durationMinutes": minutes, "action": "Do the work"}],
            budget_minutes=minutes,
            task_title="Practice Python",
            output_language="en",
        )
        assert [block.duration_minutes for block in blocks] == expected
        assert all(block.duration_minutes <= 30 for block in blocks)


def test_validate_learning_resources_filters_non_allowlisted_urls():
    resources = planning_module.validate_learning_resources(
        [
            {"title": "Official Python tutorial", "type": "official_doc", "url": "https://docs.python.org/3/tutorial/"},
            {"title": "Random tutorial", "type": "library_doc", "url": "https://example.com/python"},
        ],
        task_title="Practice Python loops",
        output_language="en",
    )

    assert resources[0].url == "https://docs.python.org/3/tutorial/"
    assert resources[0].type == "official_doc"
    assert resources[1].url is None
    assert resources[1].type == "search_keyword"
    assert resources[1].search_keyword


def test_refine_task_invalid_json_falls_back(client, monkeypatch):
    class InvalidJsonClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return LlmResult(content="not json", provider="deepseek", model="deepseek-v4-flash"), None

    monkeypatch.setattr(planning_module, "LlmClient", lambda: InvalidJsonClient())

    response = client.post("/api/planning/refine-task", json=_refine_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "local_fallback"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "invalid_model_output"


def test_refine_task_missing_required_fields_falls_back(client, monkeypatch):
    class TooShortClient:
        settings = _settings()

        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content=json.dumps(
                        {
                            "title": "",
                            "objective": "",
                            "estimatedMinutes": -5,
                            "steps": ["Only one step"],
                            "checklist": ["Only one check"],
                            "acceptanceCriteria": [],
                            "deliverable": "",
                            "risks": [],
                            "fallbackTips": [],
                        }
                    ),
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(planning_module, "LlmClient", lambda: TooShortClient())

    response = client.post("/api/planning/refine-task", json=_refine_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "local_fallback"
    assert body["fallbackReason"] == "llm_error"
    assert body["errorType"] == "invalid_model_output"
    assert body["estimatedMinutes"] == 60
    assert len(body["steps"]) >= 3


def test_refine_task_does_not_write_formal_data(client):
    before = {
        "plans": _count_table("plans"),
        "planning_goals": _count_table("planning_goals"),
        "documents": _count_table("documents"),
    }

    response = client.post("/api/planning/refine-task", json=_refine_payload())

    after = {
        "plans": _count_table("plans"),
        "planning_goals": _count_table("planning_goals"),
        "documents": _count_table("documents"),
    }
    assert response.status_code == 200
    assert after == before
