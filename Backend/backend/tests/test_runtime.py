import json

from app.db import get_conn
from app.services.runtime import decide_model_knowledge_enrichment, _merge_preference_memory


def _runtime_payload() -> dict:
    return {
        "input": "Plan one week of AI application internship preparation",
        "date": "2026-07-03",
        "preferences": "Deep work in the morning",
        "materials": "RAG FastAPI Agent Runtime",
        "data": {},
    }


def _stream_events(client, payload: dict | None = None) -> list[dict]:
    with client.stream("POST", "/api/runtime/run", json=payload or _runtime_payload()) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        text = "".join(response.iter_text())
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_runtime_run_returns_valid_ndjson_sequence(client):
    events = _stream_events(client)

    assert events
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "node"
    assert events[0]["nodeType"] == "input"
    assert events[-1]["type"] == "final"

    node_types = [event.get("nodeType") for event in events if event["type"] == "node"]
    assert node_types[:3] == ["input", "reasoning", "tool"]
    assert "observation" in node_types
    assert "output" in node_types

    tool_names = [event["toolCall"]["name"] for event in events if event["type"] == "tool"]
    assert tool_names == ["get_memory", "get_today_plans", "search_materials", "enrich_with_model_knowledge", "propose_tasks"]


def test_runtime_preference_memory_merges_fields_without_overwriting_saved_values():
    saved = json.dumps(
        {
            "preferenceMemory": {
                "learningStyle": "项目驱动",
                "dailyAvailableMinutes": 120,
                "outputLanguage": "zh",
            }
        },
        ensure_ascii=False,
    )
    payload = json.dumps({"dailyAvailableMinutes": 60}, ensure_ascii=False)

    merged = _merge_preference_memory(payload, saved)

    assert merged.learning_style == "项目驱动"
    assert merged.daily_available_minutes == 60
    assert merged.output_language == "zh"


def test_runtime_plain_text_preferences_extract_only_explicit_preferences():
    merged = _merge_preference_memory("我每天只有 1 小时，喜欢项目驱动", "")

    assert merged.daily_available_minutes == 60
    assert merged.learning_style == "项目驱动"
    assert merged.raw_preference_text == "我每天只有 1 小时，喜欢项目驱动"
    assert merged.career_direction == ""
    assert merged.project_preference == ""


def test_runtime_get_memory_returns_preference_and_history_context(client):
    payload = _runtime_payload()
    payload["preferences"] = json.dumps({"dailyAvailableMinutes": 60}, ensure_ascii=False)

    events = _stream_events(client, payload)

    memory_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "get_memory"
    )
    output = memory_event["toolCall"]["output"]
    assert "preferenceMemory" in output
    assert "historyMemory" in output
    assert output["preferenceMemory"]["dailyAvailableMinutes"] == 60
    assert output["preferenceMemory"]["learningStyle"] == "项目驱动"


def test_runtime_tools_are_readonly_or_preview_and_do_not_write_plans(client):
    client.post(
        "/api/plans",
        json={
            "date": "2026-07-03",
            "time": "09:00",
            "content": "Existing task",
            "done": False,
        },
    )
    before = client.get("/api/plans", params={"date": "2026-07-03"}).json()

    events = _stream_events(client)

    tool_events = [event for event in events if event["type"] == "tool"]
    assert tool_events
    tool_names = {event["toolCall"]["name"] for event in tool_events}
    assert tool_names <= {"search_materials", "get_today_plans", "get_memory", "enrich_with_model_knowledge", "propose_tasks"}
    write_modes = {event["toolCall"]["writeMode"] for event in tool_events}
    assert write_modes <= {"readonly", "preview"}
    assert "preview" in write_modes

    proposal_event = next(
        event for event in tool_events
        if event["toolCall"]["name"] == "propose_tasks"
    )
    proposal = proposal_event["toolCall"]["output"]
    assert proposal["notice"] == "Preview only; no user data was modified."
    assert proposal["mode"] in {"llm", "local_fallback"}
    assert proposal["structuredPlan"]["goalTitle"]
    assert proposal["structuredPlan"]["milestones"]
    assert proposal["tasks"]
    assert proposal["memoryContextSummary"]
    assert proposal["planHorizon"]["durationDays"] >= 1
    assert proposal["qualityReport"]["ok"] is True
    assert proposal["qualityReport"]["metrics"]["durationDays"] == proposal["planHorizon"]["durationDays"]
    assert proposal["qualityReport"]["metrics"]["totalTasks"] == proposal["qualityReport"]["totalTasks"]
    assert proposal["qualityReport"]["metrics"]["qualityStatus"] in {"passed", "repaired", "local_fallback"}
    assert proposal["qualityReport"]["metrics"]["sourceType"] in {"local_context", "model_knowledge", "local_fallback", "insufficient_context"}
    assert proposal["qualityStatus"] in {"passed", "repaired", "local_fallback"}
    assert proposal["sourceType"] in {"local_context", "model_knowledge", "local_fallback", "insufficient_context"}
    assert proposal["localRelevance"] in {"high", "medium", "low"}

    after = client.get("/api/plans", params={"date": "2026-07-03"}).json()
    assert [item["id"] for item in after] == [item["id"] for item in before]


def test_runtime_skips_model_knowledge_when_local_sources_are_sufficient(client):
    for index in range(2):
        client.post(
            "/api/rag/documents",
            json={
                "title": f"AI internship source {index}",
                "content": "AI application internship preparation needs RAG retrieval, FastAPI, portfolio projects, and interview review.",
            },
        )

    events = _stream_events(client)
    tool_names = [event["toolCall"]["name"] for event in events if event["type"] == "tool"]

    assert "search_materials" in tool_names
    assert "enrich_with_model_knowledge" not in tool_names

    material_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "search_materials"
    )
    decision = material_event["toolCall"]["input"]["modelKnowledgeDecision"]
    assert decision["shouldEnrich"] is False
    assert decision["relevantSourceCount"] >= 2


def test_runtime_force_model_knowledge_runs_even_with_sufficient_local_sources(client):
    for index in range(2):
        client.post(
            "/api/rag/documents",
            json={
                "title": f"AI internship source {index}",
                "content": "AI application internship preparation needs RAG retrieval, FastAPI, portfolio projects, and interview review.",
            },
        )
    payload = _runtime_payload()
    payload["options"] = {"forceModelKnowledge": True}

    events = _stream_events(client, payload)
    enrich_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "enrich_with_model_knowledge"
    )

    assert enrich_event["title"] == "大模型知识补全"
    assert enrich_event["toolCall"]["input"]["triggerReason"] == "forced_by_user"
    assert enrich_event["toolCall"]["output"]["triggerReason"] == "forced_by_user"


def test_runtime_model_knowledge_runs_when_local_sources_are_insufficient(client):
    events = _stream_events(client)
    enrich_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "enrich_with_model_knowledge"
    )

    assert enrich_event["toolCall"]["input"]["triggerReason"] == "insufficient_local_sources"
    assert enrich_event["toolCall"]["writeMode"] == "readonly"

    material_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "search_materials"
    )
    assert all(
        source.get("documentId") != "model-knowledge"
        for source in material_event["toolCall"]["output"]
    )


def test_model_knowledge_decision_triggers_for_unrelated_local_sources():
    decision = decide_model_knowledge_enrichment(
        "我去新疆旅游",
        [
            {"title": "Python 数据类型", "chunk": "变量、列表、字典和函数"},
            {"title": "游泳换气训练", "chunk": "漂浮、换气和水性练习"},
            {"title": "化学反应基础", "chunk": "元素、分子和方程式"},
        ],
        False,
    )

    assert decision["shouldEnrich"] is True
    assert decision["triggerReason"] == "keyword_mismatch"
    assert decision["localSourceCount"] == 3
    assert "新疆" in decision["missingKeywords"]
    assert "旅游" in decision["missingKeywords"]


def test_model_knowledge_decision_triggers_for_weak_generic_matches():
    decision = decide_model_knowledge_enrichment(
        "我去新疆旅游",
        [
            {"title": "安全计划", "chunk": "安全、预算、计划和时间安排"},
            {"title": "旅行准备泛用建议", "chunk": "建议提前安排时间和目标"},
        ],
        False,
    )

    assert decision["shouldEnrich"] is True
    assert decision["triggerReason"] == "low_local_relevance"
    assert decision["localRelevance"] == "low"
    assert decision["sourceType"] == "insufficient_context"
    assert "安全" in decision["matchedKeywords"]


def test_model_knowledge_decision_skips_relevant_xinjiang_travel_sources():
    decision = decide_model_knowledge_enrichment(
        "我去新疆旅游",
        [
            {"title": "新疆旅游 7 日行程", "chunk": "乌鲁木齐、喀纳斯、伊犁景点和交通安排"},
            {"title": "新疆旅行住宿预算", "chunk": "住宿、预算、安全和吐鲁番路线建议"},
        ],
        False,
    )

    assert decision["shouldEnrich"] is False
    assert decision["triggerReason"] is None
    assert decision["relevantSourceCount"] == 2
    assert "新疆" in decision["matchedKeywords"]
    assert "旅游" in decision["matchedKeywords"]


def test_model_knowledge_decision_force_override():
    decision = decide_model_knowledge_enrichment(
        "我去新疆旅游",
        [
            {"title": "新疆旅游 7 日行程", "chunk": "乌鲁木齐、喀纳斯、伊犁景点和交通安排"},
            {"title": "新疆旅行住宿预算", "chunk": "住宿、预算、安全和吐鲁番路线建议"},
        ],
        True,
    )

    assert decision["shouldEnrich"] is True
    assert decision["triggerReason"] == "forced_by_user"


def test_runtime_persists_agent_run_and_events(client):
    events = _stream_events(client)
    run_id = events[0]["runId"]

    with get_conn() as conn:
        run = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        stored_events = conn.execute(
            "SELECT COUNT(*) AS total FROM agent_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()["total"]

    assert run is not None
    assert run["status"] == "done"
    assert run["output_summary"]
    assert stored_events == len(events)


def test_runtime_search_materials_returns_sources(client):
    client.post(
        "/api/rag/documents",
        json={
            "title": "Runtime JD",
            "content": "Agent runtime roles need RAG retrieval, FastAPI streaming, and safe tool routing.",
        },
    )

    events = _stream_events(client)

    material_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "search_materials"
    )
    assert material_event["toolCall"]["writeMode"] == "readonly"
    assert material_event["toolCall"]["output"]
    source = material_event["toolCall"]["output"][0]
    assert {"documentId", "title", "chunk", "score", "chunkIndex"} <= set(source)


def test_runtime_search_materials_query_uses_context_pack(client):
    payload = _runtime_payload()
    payload["input"] = "学 Python 找 AI 应用实习"
    payload["preferences"] = json.dumps(
        {
            "learningStyle": "项目驱动",
            "careerDirection": "AI 应用实习",
        },
        ensure_ascii=False,
    )

    events = _stream_events(client, payload)

    material_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "search_materials"
    )
    query = material_event["toolCall"]["input"]["query"]
    assert "Python" in query
    assert "项目驱动" in query
    assert "AI 应用实习" in query


def test_runtime_cleans_history_before_material_search_and_planning(client):
    long_histories = [
        (
            "hist-python",
            "## Python 收支计算器长计划\n"
            "每天学习Python核心语法，并将所学应用于构建个人收支计算器，"
            "包含文件读写、异常处理、报表输出和命令行交互。" * 4,
        ),
        (
            "hist-ai",
            "## AI 应用实习长计划\n"
            "围绕 FastAPI、RAG、Agent Runtime、作品集 README 和部署链路展开，"
            "目标是准备 AI 应用开发实习。" * 4,
        ),
        (
            "hist-ski",
            "## 滑雪学习计划\n"
            "练习犁式刹车、连续犁式转弯和平行转弯，提升雪道控制能力。" * 4,
        ),
    ]
    with get_conn() as conn:
        for run_id, summary in long_histories:
            conn.execute(
                """
                INSERT INTO agent_runs(id, input, status, output_summary)
                VALUES (?, ?, 'done', ?)
                """,
                (run_id, run_id, summary),
            )

    events = _stream_events(
        client,
        {
            "input": "我要学游泳",
            "date": "2026-07-03",
            "preferences": json.dumps({"learningStyle": "项目驱动", "planningStyle": "具体可执行"}, ensure_ascii=False),
            "materials": "",
            "data": {},
        },
    )

    memory_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "get_memory"
    )
    recent = memory_event["toolCall"]["output"]["historyMemory"]["recentProgress"]
    assert recent
    assert {"title", "summary", "relevanceToGoal"} <= set(recent[0])
    assert all(len(item["summary"]) <= 121 for item in recent)

    material_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "search_materials"
    )
    query = material_event["toolCall"]["input"]["query"]
    assert len(query) <= 500
    for token in ("我要学游泳", "游泳入门", "蛙泳", "漂浮", "换气", "水性练习", "项目驱动", "具体可执行"):
        assert token in query
    for leaked in ("Python 收支计算器", "AI 应用实习长计划", "犁式刹车", "平行转弯"):
        assert leaked not in query

    proposal_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "propose_tasks"
    )
    summary = proposal_event["toolCall"]["output"]["memoryContextSummary"]
    assert len(summary) <= 500
    assert "近期有同类学习记录" in summary
    assert "Python 收支计算器" not in summary
    assert "AI 应用实习长计划" not in summary
    assert "犁式刹车" not in summary


def test_runtime_python_goal_returns_learning_plan(client):
    events = _stream_events(
        client,
        {
            "input": "帮我规划一个 python 学习计划",
            "date": "2026-07-03",
            "preferences": "",
            "materials": "",
            "data": {},
        },
    )

    final = next(event for event in events if event["type"] == "final")
    assert "Python" in final["content"]
    assert "语法" in final["content"]
    assert "项目" in final["content"]
    assert "复盘" in final["content"]

    proposal_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "propose_tasks"
    )
    structured = proposal_event["toolCall"]["output"]["structuredPlan"]
    assert structured["durationDays"] >= 1
    assert structured["milestones"][0]["tasks"][0]["priority"] in {"low", "medium", "high"}


def test_runtime_daily_available_minutes_limits_local_fallback_tasks(client):
    events = _stream_events(
        client,
        {
            "input": "帮我规划一个 Python 学习计划",
            "date": "2026-07-03",
            "preferences": json.dumps({"dailyAvailableMinutes": 60}, ensure_ascii=False),
            "materials": "",
            "data": {},
        },
    )

    proposal_event = next(
        event for event in events
        if event["type"] == "tool" and event["toolCall"]["name"] == "propose_tasks"
    )
    tasks = [
        task
        for milestone in proposal_event["toolCall"]["output"]["structuredPlan"]["milestones"]
        for task in milestone["tasks"]
    ]
    assert tasks
    assert max(task["estimatedMinutes"] for task in tasks) <= 60


def test_runtime_success_events_do_not_include_old_ui_mock_names(client):
    events = _stream_events(client)
    combined = json.dumps(events, ensure_ascii=False)

    assert "plan_context_lookup" not in combined
    assert "ui-mock" not in combined
    assert "静态 UI 模拟" not in combined
    assert "乱码" not in combined
