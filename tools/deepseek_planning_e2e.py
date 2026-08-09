#!/usr/bin/env python
"""Run live, approval-complete Planix planning scenarios against saved DeepSeek settings."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


BASE_URL = "http://127.0.0.1:8003"
PLANNING_TASKS = {
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
}


@dataclass(frozen=True)
class Scenario:
    key: str
    goal: str
    persona: dict[str, str] = field(default_factory=dict)
    followups: tuple[str, ...] = ()
    final_revision: str = ""
    calendar_audit: bool = False


SCENARIOS = (
    Scenario(
        "learning",
        "I am a programming beginner. In 30 days I want to learn Python basics, studying one hour on weekdays and two hours per weekend day, and independently deliver a command-line project.",
        persona={
            "current_level": "I am a programming beginner with no prior Python experience.",
            "project": "The command-line project will be a local to-do list with add, list, complete, and delete commands.",
        },
    ),
    Scenario(
        "job_search",
        "In three months I want general preparation for full-stack AI application internships, not a specific company, covering Python, FastAPI, React, REST APIs, LLM integration, RAG basics, testing, Git, and project explanation. I am intermediate in Python, FastAPI, and React and can build ordinary CRUD applications, but I have no prior RAG experience and only basic testing experience. I can dedicate 12 hours per week. I will start applying in week six and finish a locally runnable RAG question-answering Agent in the first five weeks; online deployment is not required and I have access to an OpenAI-compatible LLM API.",
        persona={
            "project": "The project is a locally runnable RAG Agent using an OpenAI-compatible API, FastAPI, React, and local documents; online deployment is not required.",
            "focus": "This is general preparation for full-stack AI internships, not one company; focus on LLM integration, APIs, and a usable React interface.",
            "current_level": "I am intermediate in Python, FastAPI, and React, comfortable with REST APIs and Git, new to RAG, and a beginner in automated testing.",
            "availability": "I can dedicate 12 hours per week, mostly on weekday evenings and weekends.",
        },
    ),
    Scenario(
        "software",
        "In six weeks I want to complete a personal expense-tracking Web application with add, edit, delete, category totals, and monthly summary. I know Python and React and can invest ten hours per week. It must run reliably locally and include a complete README.",
        persona={"project": "Use FastAPI, React, and SQLite; local single-user operation is sufficient."},
    ),
    Scenario(
        "exam",
        "I have a database exam in two weeks. I can study at most 90 minutes daily. Cover SQL, transactions, indexes, and database design, with mock exams during the final three days. I know basic SQL SELECT statements.",
        persona={"current_level": "I know basic SELECT, WHERE, JOIN, and simple table creation."},
    ),
    Scenario(
        "zero_budget",
        "In one month I want to learn FastAPI. My budget is exactly 0 CNY, I can use only free resources, and I have 45 minutes daily. The final deliverable is a working CRUD API. I know basic Python.",
        persona={"project": "The CRUD API manages a local task list with create, read, update, and delete endpoints using SQLite."},
    ),
    Scenario(
        "multi_turn",
        "I want to learn Python in two months, one hour per day.",
        persona={
            "current_level": "I already know Java and have programming experience.",
            "availability": "I have one hour on weekdays and three hours per weekend day.",
            "success": "I will independently finish a demonstrable Python automation project.",
            "purpose": "The purpose is to improve automation in my job.",
        },
        followups=(
            "I already know Java.",
            "I have one hour on weekdays and three hours per weekend day; the outcome is a demonstrable Python automation project.",
        ),
    ),
    Scenario(
        "plan_revision",
        "In eight weeks I want to improve Python engineering skills, investing eight hours per week, and deliver one complete demonstrable project. I already know Python basics.",
        persona={"project": "Build a tested FastAPI service with SQLite, authentication, and a clear README."},
        final_revision="Split the second task into two tasks, but do not change any other task or the eight-week duration.",
    ),
    Scenario(
        "schedule_revision",
        "In six weeks I want to learn data analysis, investing eight hours per week, and deliver a demonstrable analysis report. I know basic Python and spreadsheets.",
        persona={"project": "Use a public CSV dataset and deliver a notebook plus a concise PDF-style report."},
        final_revision="Do not schedule anything on Wednesday evenings and schedule more on weekends. Do not rewrite plan content; only reschedule it.",
    ),
    Scenario(
        "resource_limit",
        "In one month I want to learn RAG. I cannot buy paid courses and must prefer official documentation and existing local materials. I can invest eight hours weekly and will deliver a local RAG question-answering demonstration over a small document collection. I know Python basics.",
        persona={"project": "The demo ingests local Markdown files and answers questions with cited chunks."},
    ),
    Scenario(
        "complex",
        "In three months I want to progress from existing Python basics to independently completing and explaining a task-planning AI Agent with FastAPI and a simple React UI, while also preparing a resume, project presentation, and full-stack AI internship interviews. I can invest eight to ten hours per week.",
        persona={
            "project": "The Agent accepts a goal, creates an editable task plan, and exposes the flow through FastAPI and React.",
            "focus": "Target full-stack AI application roles emphasizing LLM integration, API design, and project explanation.",
        },
    ),
    Scenario(
        "calendar_audit",
        "Starting on 2026-08-10, spend two hours daily for two weeks preparing a small Python portfolio demo. I know Python basics and the deliverable is a runnable command-line project.",
        persona={"project": "The deliverable is a local command-line task tracker with a README."},
        calendar_audit=True,
    ),
)


def events(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def post_json(client: httpx.Client, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(path, json=payload or {})
    response.raise_for_status()
    return response.json()


def command_turn(client: httpx.Client, thread_id: str, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stream = events(
        client.post(
            "/api/command/chat",
            json={
                "threadId": thread_id,
                "message": message,
                "permission": "low",
                "context": {"timezone": "Asia/Shanghai"},
            },
        )
    )
    status = next((item for item in reversed(stream) if item.get("type") == "planning_session_status"), None)
    if not status:
        raise RuntimeError(f"Command did not return planning status: {stream[-3:]}")
    document = {
        **(status.get("data") or {}),
        "sessionId": status.get("sessionId"),
        "status": status.get("status"),
        "businessStatus": status.get("businessStatus"),
        "runtimeStatus": status.get("runtimeStatus"),
        "pendingInput": status.get("pendingInput"),
        "modelFailure": status.get("modelFailure"),
        "decisions": [item.get("data") for item in stream if item.get("type") == "agent_decision" and isinstance(item.get("data"), dict)],
        "messages": [item.get("data") for item in stream if item.get("type") == "agent_message" and isinstance(item.get("data"), dict)],
    }
    return document, stream


def semantic_answer(document: dict[str, Any], persona: dict[str, str]) -> str:
    question_context = {
        "understanding": document.get("understandingSnapshot"),
        "pendingInput": document.get("pendingInput"),
    }
    serialized = json.dumps(question_context, ensure_ascii=False).casefold()
    groups = (
        (("current", "level", "skill", "experience"), "current_level"),
        (("availability", "time", "schedule", "hours"), "availability"),
        (("success", "outcome", "deliverable"), "success"),
        (("purpose", "why"), "purpose"),
        (("project", "command-line", "application", "build"), "project"),
        (("focus", "internship", "target role", "requirement"), "focus"),
    )
    selected = [persona[key] for tokens, key in groups if key in persona and any(token in serialized for token in tokens)]
    return " ".join(selected or persona.values()) or "Keep the current goal and continue using only explicit, non-blocking assumptions."


def model_stats(document: dict[str, Any]) -> tuple[list[str], int, int, int, int]:
    providers: list[str] = []
    calls = retries = fallbacks = mocks = 0
    for decision in document.get("decisions") or []:
        usage = decision.get("modelUsage") if isinstance(decision, dict) else None
        if not isinstance(usage, dict) or not usage.get("provider"):
            continue
        calls += 1
        provider = str(usage.get("provider") or "")
        if provider:
            providers.append(provider)
        fallbacks += int(bool(usage.get("fallbackUsed")))
        mocks += int(provider.casefold() in {"mock", "test", "fake"})
        attempts = usage.get("attempts") if isinstance(usage.get("attempts"), list) else []
        retries += max(0, len([item for item in attempts if item.get("status") != "skipped"]) - 1)
    return providers, calls, retries, fallbacks, mocks


def persisted_plan_ids(client: httpx.Client, plans: list[dict[str, Any]]) -> set[str]:
    months = {
        (int(str(item["date"])[:4]), int(str(item["date"])[5:7]))
        for item in plans
    }
    expected = {str(item["id"]) for item in plans}
    found: set[str] = set()
    for year, month in months:
        response = client.get("/api/plans/month", params={"year": year, "month": month})
        response.raise_for_status()
        found.update(str(item["id"]) for item in response.json() if str(item.get("id")) in expected)
    return found


def assert_capacity_constraints(document: dict[str, Any]) -> None:
    core = (document.get("constraintSet") or {}).get("core") or {}
    sessions = (document.get("scheduleBlueprint") or {}).get("sessions") or []
    weekly: dict[tuple[int, int], int] = {}
    daily: dict[str, int] = {}
    for item in sessions:
        start = datetime.fromisoformat(item["start"])
        week = start.isocalendar()
        weekly[(week.year, week.week)] = weekly.get((week.year, week.week), 0) + int(item["durationMinutes"])
        daily[start.date().isoformat()] = daily.get(start.date().isoformat(), 0) + int(item["durationMinutes"])
    weekly_limit = core.get("weeklyCapacityMinutes")
    if weekly_limit is not None and any(value > int(weekly_limit) for value in weekly.values()):
        raise RuntimeError(f"weekly capacity exceeded: {weekly} > {weekly_limit}")
    for day, used in daily.items():
        weekday = datetime.fromisoformat(day).weekday()
        limit = core.get("weekendCapacityMinutes") if weekday >= 5 else core.get("weekdayCapacityMinutes")
        if limit is not None and used > int(limit):
            raise RuntimeError(f"daily capacity exceeded on {day}: {used} > {limit}")


def overlaps(start: str, end: str, busy_start: str, busy_end: str) -> bool:
    return datetime.fromisoformat(start) < datetime.fromisoformat(busy_end) and datetime.fromisoformat(end) > datetime.fromisoformat(busy_start)


def run_one(client: httpx.Client, scenario: Scenario, run_index: int, *, clean: bool) -> dict[str, Any]:
    started = time.monotonic()
    marker = f"[E2E-DEEPSEEK][{scenario.key.upper()}-R{run_index}]"
    thread_id = f"e2e-deepseek-{scenario.key}-{run_index}-{uuid4().hex[:8]}"
    goal = f"{marker} {scenario.goal} Every final task title must begin with {marker}."
    result: dict[str, Any] = {
        "case": scenario.key,
        "run": run_index,
        "marker": marker,
        "threadId": thread_id,
        "status": "started",
        "created": 0,
        "updated": 0,
        "calendarIds": [],
    }
    audit_plan_ids: list[str] = []
    if scenario.calendar_audit:
        busy = post_json(
            client,
            "/api/plans",
            {"date": "2026-08-12", "time": "10:00", "content": f"{marker} existing busy interval", "estimatedMinutes": 120},
        )
        audit_plan_ids.append(str(busy["id"]))
    document, _ = command_turn(client, thread_id, goal)
    all_decisions = list(document["decisions"])
    all_messages = list(document["messages"])
    result["sessionId"] = document["sessionId"]
    clarification_rounds = 0
    recovery_attempts = 0
    for followup in scenario.followups:
        document, stream = command_turn(client, thread_id, followup)
        all_decisions.extend(document["decisions"])
        all_messages.extend(document["messages"])
    for _ in range(8):
        status = document.get("status")
        if status == "waiting_understanding_confirmation":
            document, stream = command_turn(client, thread_id, "confirm")
            all_decisions.extend(document["decisions"])
            all_messages.extend(document["messages"])
            continue
        if status in {"waiting_understanding_input", "collecting_goal", "needs_goal_clarification"}:
            if clarification_rounds >= 3:
                raise RuntimeError("Understanding exceeded the three-round question budget")
            clarification_rounds += 1
            answer = semantic_answer(document, scenario.persona)
            document, stream = command_turn(client, thread_id, answer)
            all_decisions.extend(document["decisions"])
            all_messages.extend(document["messages"])
            continue
        if status == "MODEL_UNAVAILABLE" and document.get("modelFailure", {}).get("retryable"):
            if recovery_attempts >= 1:
                break
            recovery_attempts += 1
            document, stream = command_turn(client, thread_id, "continue")
            all_decisions.extend(document["decisions"])
            all_messages.extend(document["messages"])
            continue
        break
    if scenario.final_revision and document.get("status") == "waiting_final_review":
        before_plan = document.get("planBlueprint") or {}
        before_schedule = document.get("scheduleBlueprint") or {}
        before_calendar = document.get("calendarProposal") or {}
        document, stream = command_turn(client, thread_id, scenario.final_revision)
        all_decisions.extend(document["decisions"])
        all_messages.extend(document["messages"])
        after_plan = document.get("planBlueprint") or {}
        after_schedule = document.get("scheduleBlueprint") or {}
        after_calendar = document.get("calendarProposal") or {}
        if scenario.key == "plan_revision":
            before_tasks = before_plan.get("tasks") or []
            after_tasks = after_plan.get("tasks") or []
            if after_plan.get("version") != int(before_plan.get("version") or 0) + 1 or len(after_tasks) <= len(before_tasks):
                raise RuntimeError("plan revision did not structurally split the requested task")
            unchanged = {item["id"]: item for index, item in enumerate(before_tasks) if index != 1}
            after_by_id = {item["id"]: item for item in after_tasks}
            if any(after_by_id.get(task_id) != task for task_id, task in unchanged.items()):
                raise RuntimeError("plan revision changed an unrelated task or stable id")
            if len(before_tasks) > 1 and after_by_id.get(before_tasks[1]["id"]) == before_tasks[1]:
                raise RuntimeError("plan revision did not change the requested second task")
            preserved_lineage = set(before_tasks[1].get("sourceConstraintRefs") or []) if len(before_tasks) > 1 else set()
            revised_lineage = {ref for item in after_tasks if item["id"] not in unchanged for ref in item.get("sourceConstraintRefs") or []}
            if not preserved_lineage.issubset(revised_lineage):
                raise RuntimeError("plan revision lost constraint lineage")
            if not (document.get("planQualityReport") or {}).get("passed"):
                raise RuntimeError("plan quality did not rerun successfully")
            if after_schedule.get("version") == before_schedule.get("version") or after_calendar.get("version") == before_calendar.get("version"):
                raise RuntimeError("plan revision did not regenerate downstream artifacts")
        if scenario.key == "schedule_revision":
            if after_plan != before_plan:
                raise RuntimeError("schedule-only revision changed Plan semantics")
            if after_schedule.get("version") == before_schedule.get("version"):
                raise RuntimeError("schedule revision did not create a new Schedule artifact")
            if any(datetime.fromisoformat(item["start"]).weekday() == 2 for item in after_schedule.get("sessions") or []):
                raise RuntimeError("schedule revision still contains Wednesday sessions")
            before_weekend = sum(int(item["durationMinutes"]) for item in before_schedule.get("sessions") or [] if datetime.fromisoformat(item["start"]).weekday() >= 5)
            after_weekend = sum(int(item["durationMinutes"]) for item in after_schedule.get("sessions") or [] if datetime.fromisoformat(item["start"]).weekday() >= 5)
            if after_weekend <= before_weekend:
                raise RuntimeError(f"schedule revision did not increase weekend allocation: {before_weekend} -> {after_weekend}")
            if not (document.get("scheduleQualityReport") or {}).get("passed"):
                raise RuntimeError("schedule quality did not pass after revision")
            if after_calendar.get("version") == before_calendar.get("version"):
                raise RuntimeError("schedule revision did not regenerate CalendarProposal")
    if document.get("status") != "waiting_final_review":
        raise RuntimeError(f"{scenario.key} stopped at {document.get('status')}: {document.get('modelFailure')}")
    document["decisions"] = all_decisions
    document["messages"] = all_messages
    assert_capacity_constraints(document)
    providers, calls, retries, fallbacks, mocks = model_stats(document)
    result.update(
        understanding=bool(document.get("understandingSnapshot")),
        plan=bool(document.get("planBlueprint")),
        review=bool(document.get("planQualityReport")),
        repair=int(document.get("cognitiveMetadata", {}).get("repairCount") or 0),
        schedule=bool(document.get("scheduleBlueprint")),
        finalReview=True,
        providers=providers,
        modelCalls=calls,
        modelRetries=retries,
        fallbackCount=fallbacks,
        mockCount=mocks,
        reviewerContradictions=sum(
            1
            for message in document.get("messages") or []
            if "reviewer_contradiction" in json.dumps(message, ensure_ascii=False)
        ),
        clarificationRounds=clarification_rounds,
        recoveryAttempts=recovery_attempts,
    )
    if not providers or any(provider != "deepseek" for provider in providers):
        raise RuntimeError(f"non-DeepSeek provider observed: {providers}")
    if fallbacks or mocks:
        raise RuntimeError(f"fallback/mock observed: fallback={fallbacks}, mock={mocks}")
    stream = events(client.post("/api/command/chat", json={"threadId": thread_id, "message": "write calendar", "permission": "low", "context": {"timezone": "Asia/Shanghai"}}))
    approval = next((item for item in stream if item.get("type") == "approval_required"), None)
    if not approval:
        raise RuntimeError(f"Calendar approval was not requested: {stream[-3:]}")
    if scenario.calendar_audit:
        context = document.get("contextPack") or {}
        busy_start = "2026-08-12T10:00:00+08:00"
        busy_end = "2026-08-12T12:00:00+08:00"
        if not str(context.get("calendarSnapshotRef") or "").startswith("calendar:"):
            raise RuntimeError("ContextPack did not bind the production Calendar snapshot")
        day_sessions = [item for item in (document.get("scheduleBlueprint") or {}).get("sessions") or [] if item["start"].startswith("2026-08-12")]
        if any(overlaps(item["start"], item["end"], busy_start, busy_end) for item in day_sessions):
            raise RuntimeError("Schedule overlaps the real existing Calendar event")
        old_revision = int((document.get("finalApprovalBundle") or {}).get("calendarSnapshotVersion") or 0)
        changed = post_json(
            client,
            "/api/plans",
            {"date": "2026-08-13", "time": "15:00", "content": f"{marker} revision change", "estimatedMinutes": 30},
        )
        audit_plan_ids.append(str(changed["id"]))
        stale = events(client.post("/api/command/approve", json={"threadId": thread_id, "actionId": approval["actionId"], "decision": "approve", "permission": "low"}))
        errors = [str(item.get("error") or "") for item in stale if item.get("type") == "error"]
        if not errors or not any("stale" in error.casefold() for error in errors):
            raise RuntimeError(f"old Calendar approval was not rejected as stale: {stale[-3:]}")
        result.update(
            status="stale_calendar_rejected",
            calendarSnapshotRef=context.get("calendarSnapshotRef"),
            oldCalendarRevision=old_revision,
            newCalendarRevision=old_revision + 1,
            busyDaySessions=day_sessions,
            staleError=errors[0],
            idempotent=True,
        )
        for plan_id in audit_plan_ids:
            client.delete(f"/api/plans/{plan_id}").raise_for_status()
        result["elapsedSeconds"] = round(time.monotonic() - started, 2)
        return result
    written = events(
        client.post(
            "/api/command/approve",
            json={
                "threadId": thread_id,
                "actionId": approval["actionId"],
                "decision": "approve",
                "permission": "low",
            },
        )
    )
    calendar_result = next((item for item in written if item.get("type") == "calendar_write_result"), None)
    if not calendar_result or calendar_result.get("failed"):
        raise RuntimeError(f"Calendar write failed: {written[-3:]}")
    plans = calendar_result.get("plans") or []
    if not plans:
        raise RuntimeError("Calendar write returned no persisted events")
    if any(not str(plan.get("sourceKey") or "") or not str(plan.get("sourceTaskId") or "") or not str(plan.get("sourceSessionId") or "") for plan in plans):
        raise RuntimeError("Calendar persistence lost source lineage")
    ids = [str(plan["id"]) for plan in plans]
    result.update(
        status="written_to_calendar",
        calendarWrite=True,
        created=int(calendar_result.get("created") or 0),
        updated=int(calendar_result.get("updated") or 0),
        calendarIds=ids,
        sourceKeys=[str(plan.get("sourceKey") or "") for plan in plans],
    )
    before = persisted_plan_ids(client, plans)
    duplicate = events(
        client.post(
            "/api/command/approve",
            json={
                "threadId": thread_id,
                "actionId": approval["actionId"],
                "decision": "approve",
                "permission": "low",
            },
        )
    )
    after = persisted_plan_ids(client, plans)
    result["idempotent"] = before == after and len(before) == len(ids)
    result["duplicateResult"] = [item.get("type") for item in duplicate]
    if not result["idempotent"]:
        raise RuntimeError("duplicate Calendar approval changed persisted event identity")
    if clean:
        for plan_id in ids:
            response = client.delete(f"/api/plans/{plan_id}")
            response.raise_for_status()
        result["cleaned"] = True
    result["elapsedSeconds"] = round(time.monotonic() - started, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--only", action="append", choices=[item.key for item in SCENARIOS])
    parser.add_argument("--keep-calendar", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    selected = [item for item in SCENARIOS if not args.only or item.key in args.only]
    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "provider": "deepseek",
        "model": "deepseek-chat",
        "results": [],
        "failures": [],
    }
    path = args.report or Path("data/e2e-reports") / f"deepseek-planning-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url, timeout=600.0) as client:
        settings = client.get("/api/ai/settings").json()
        if settings.get("provider") != "deepseek" or settings.get("model") != "deepseek-chat" or not settings.get("hasApiKey"):
            raise RuntimeError("saved DeepSeek deepseek-chat settings are not ready")
        original_routing = {
            "routingRules": settings.get("routingRules") or [],
            "autoModelPolicy": settings.get("autoModelPolicy"),
        }
        pinned = []
        for rule in settings.get("routingRules") or []:
            item = {key: value for key, value in rule.items() if key != "updatedAt"}
            if item.get("taskType") in PLANNING_TASKS:
                item.update(primaryProvider="deepseek", fallbackProviders=[], localFallbackEnabled=False)
            pinned.append(item)
        response = client.put(
            "/api/ai/settings/routing",
            json={"routingRules": pinned, "autoModelPolicy": settings.get("autoModelPolicy")},
        )
        response.raise_for_status()
        try:
            for scenario in selected:
                for run_index in range(1, args.repeats + 1):
                    try:
                        item = run_one(client, scenario, run_index, clean=not args.keep_calendar)
                        report["results"].append(item)
                        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(json.dumps({"case": scenario.key, "run": run_index, "status": "passed", "seconds": item["elapsedSeconds"]}), flush=True)
                    except Exception as exc:
                        failure = {"case": scenario.key, "run": run_index, "error": str(exc)[:1000]}
                        report["failures"].append(failure)
                        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(json.dumps({"case": scenario.key, "run": run_index, "status": "failed", "error": str(exc)[:300]}), flush=True)
        finally:
            restore = {
                "routingRules": [
                    {key: value for key, value in rule.items() if key != "updatedAt"}
                    for rule in original_routing["routingRules"]
                ],
                "autoModelPolicy": original_routing["autoModelPolicy"],
            }
            client.put("/api/ai/settings/routing", json=restore).raise_for_status()
    report["passed"] = not report["failures"] and len(report["results"]) == len(selected) * args.repeats
    report["summary"] = {
        "runs": len(report["results"]) + len(report["failures"]),
        "passed": len(report["results"]),
        "failed": len(report["failures"]),
        "calendarEvents": sum(item["created"] + item["updated"] for item in report["results"]),
        "modelCalls": sum(item["modelCalls"] for item in report["results"]),
        "modelRetries": sum(item["modelRetries"] for item in report["results"]),
        "fallbackCount": sum(item["fallbackCount"] for item in report["results"]),
        "mockCount": sum(item["mockCount"] for item in report["results"]),
        "reviewerContradictions": sum(item["reviewerContradictions"] for item in report["results"]),
        "averageRepair": round(sum(item["repair"] for item in report["results"]) / max(1, len(report["results"])), 3),
        "averageSeconds": round(sum(item["elapsedSeconds"] for item in report["results"]) / max(1, len(report["results"])), 2),
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(path), "summary": report["summary"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
