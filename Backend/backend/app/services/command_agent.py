import json
from datetime import date as date_type, datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException

from ..cognitive_planning import get_planning_orchestrator
from ..cognitive_planning.control_intent import detect_planning_control_intent
from ..db import get_conn
from ..schemas import (
    CommandApproveRequest,
    CommandChatRequest,
    CommandMessageOut,
    CommandPermission,
    CommandThreadOut,
    CommandThreadSummaryOut,
    CreatePlanningSessionRequest,
    PlanCreate,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
    PlanUpdate,
)
from .plans import create_plan, update_plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date_type.today().isoformat()


def _ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_message(row) -> CommandMessageOut:
    return CommandMessageOut(
        id=row["id"],
        threadId=row["thread_id"],
        role=row["role"],
        content=row["content"],
        kind=row["kind"],
        payload=_json_object(row["payload_json"]),
        createdAt=row["created_at"],
    )


def _approval_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"确认", "确认理解", "确认当前理解", "同意", "批准", "approve", "confirm", "yes"}


def _calendar_write_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"写入日历", "同步到日历", "批准并写入日历", "批准最终计划并写入日历", "write calendar", "sync calendar", "approve the final plan and write it to calendar"}


def _restart_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"重新开始", "新建计划", "restart", "start over"}


def _event_time(value: Any) -> tuple[str, str, int]:
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.date().isoformat(), parsed.strftime("%H:%M"), 0
    except ValueError:
        return _today(), "09:00", 0


def _event_minutes(start: Any, end: Any) -> int:
    try:
        start_at = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(1, int((end_at - start_at).total_seconds() // 60))
    except (TypeError, ValueError):
        return 30


class CommandAgentService:
    """Thin thread/NDJSON adapter for the canonical Planix V2 runtime."""

    def ensure_thread(self, thread_id: str | None = None, title: str = "") -> str:
        if thread_id:
            with get_conn() as conn:
                row = conn.execute("SELECT id FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
                if row:
                    conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (_now(), thread_id))
                    return thread_id
        new_id = thread_id or str(uuid4())
        now = _now()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO command_threads(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (new_id, (title or "Planix Planning").strip()[:160], now, now),
            )
        return new_id

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        kind: str = "text",
        payload: dict[str, Any] | None = None,
    ) -> CommandMessageOut:
        message_id = str(uuid4())
        created_at = _now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_messages(id, thread_id, role, content, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, thread_id, role, content, kind, json.dumps(payload or {}, ensure_ascii=False), created_at),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (created_at, thread_id))
        return CommandMessageOut(
            id=message_id,
            threadId=thread_id,
            role=role,
            content=content,
            kind=kind,
            payload=payload or {},
            createdAt=created_at,
        )

    def list_threads(self, limit: int = 50) -> list[CommandThreadSummaryOut]:
        safe_limit = min(max(int(limit or 50), 1), 100)
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.title, t.created_at, t.updated_at, COUNT(m.id) AS message_count
                FROM command_threads t
                LEFT JOIN command_messages m ON m.thread_id = t.id
                GROUP BY t.id
                ORDER BY t.updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            CommandThreadSummaryOut(
                id=row["id"],
                title=row["title"],
                messageCount=int(row["message_count"] or 0),
                createdAt=row["created_at"],
                updatedAt=row["updated_at"],
            )
            for row in rows
        ]

    def delete_thread(self, thread_id: str) -> dict[str, int]:
        with get_conn() as conn:
            existing = conn.execute("SELECT id FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Thread not found")
            conn.execute("DELETE FROM command_approvals WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_actions WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_drafts WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_messages WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_threads WHERE id = ?", (thread_id,))
        return {"deleted": 1}

    def get_thread(self, thread_id: str) -> CommandThreadOut:
        with get_conn() as conn:
            thread = conn.execute("SELECT * FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
            if not thread:
                raise HTTPException(status_code=404, detail="Thread not found")
            messages = [
                _row_to_message(row)
                for row in conn.execute(
                    "SELECT * FROM command_messages WHERE thread_id = ? ORDER BY created_at ASC",
                    (thread_id,),
                ).fetchall()
            ]
        return CommandThreadOut(
            id=thread["id"],
            title=thread["title"],
            messages=messages,
            createdAt=thread["created_at"],
            updatedAt=thread["updated_at"],
        )

    def _planning_event(
        self,
        thread_id: str,
        kind: str,
        session_id: str,
        *,
        data: dict[str, Any] | None = None,
        status: str | None = None,
        business_status: str | None = None,
        runtime_status: str | None = None,
        model_failure: dict[str, Any] | None = None,
        pending_input: dict[str, Any] | None = None,
        content: str = "",
    ) -> str:
        event: dict[str, Any] = {"type": kind, "sessionId": session_id}
        if status is not None:
            event["status"] = status
        if business_status is not None:
            event["businessStatus"] = business_status
        if runtime_status is not None:
            event["runtimeStatus"] = runtime_status
        if model_failure is not None:
            event["modelFailure"] = model_failure
        if pending_input is not None:
            event["pendingInput"] = pending_input
        if data is not None:
            event["data"] = data
        payload = {key: value for key, value in event.items() if key != "type"}
        self.add_message(thread_id, "card", content or status or session_id, kind=kind, payload=payload)
        return _ndjson(event)

    def _seen_trace_ids(self, thread_id: str, session_id: str) -> set[tuple[str, str]]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT kind, payload_json FROM command_messages
                WHERE thread_id = ? AND role = 'card' AND kind IN ('agent_decision', 'agent_message')
                """,
                (thread_id,),
            ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row in rows:
            payload = _json_object(row["payload_json"])
            data = payload.get("data")
            if str(payload.get("sessionId") or "") != session_id or not isinstance(data, dict):
                continue
            trace_id = str(data.get("id") or "")
            if trace_id:
                seen.add((row["kind"], trace_id))
        return seen

    def _stream_snapshot(
        self,
        thread_id: str,
        session: PlanningSessionResponse,
        *,
        include_start: bool = False,
    ) -> Iterator[str]:
        if include_start:
            yield self._planning_event(
                thread_id,
                "planning_session_started",
                session.session_id,
                status=session.status,
                content=f"Planning session {session.status}",
            )
        seen = self._seen_trace_ids(thread_id, session.session_id)
        for decision in session.decisions:
            key = ("agent_decision", decision.id)
            if key in seen:
                continue
            data = decision.model_dump(by_alias=True, exclude_none=True)
            yield self._planning_event(
                thread_id,
                "agent_decision",
                session.session_id,
                data=data,
                content=decision.user_visible_summary or decision.reason or decision.decision,
            )
            seen.add(key)
        for message in session.messages:
            key = ("agent_message", message.id)
            if key in seen:
                continue
            data = message.model_dump(by_alias=True)
            yield self._planning_event(
                thread_id,
                "agent_message",
                session.session_id,
                data=data,
                content=message.reason or message.message_type,
            )
            seen.add(key)
        yield self._planning_event(
            thread_id,
            "planning_session_status",
            session.session_id,
            status=session.status,
            business_status=session.business_status,
            runtime_status=session.runtime_status,
            model_failure=session.model_failure.model_dump(by_alias=True, exclude_none=True) if session.model_failure else None,
            pending_input=session.pending_input.model_dump(by_alias=True) if session.pending_input else None,
            data={
                "planningPhase": session.planning_phase,
                "planningStep": session.planning_step,
                "cognitiveMetadata": session.cognitive_metadata.model_dump(by_alias=True, exclude_none=True) if session.cognitive_metadata else None,
                "understandingSnapshot": session.understanding_snapshot,
                "constraintSet": session.constraint_set,
                "contextPack": session.context_pack,
                "planBlueprint": session.plan_blueprint,
                "planQualityReport": session.plan_quality_report,
                "scheduleBlueprint": session.schedule_blueprint,
                "scheduleQualityReport": session.schedule_quality_report,
                "calendarProposal": session.calendar_proposal,
                "finalApprovalBundle": session.final_approval_bundle,
            },
            content=session.status,
        )

    def _stream_start(self, thread_id: str, payload: CommandChatRequest) -> Iterator[str]:
        session = get_planning_orchestrator().create_session(
            CreatePlanningSessionRequest(
                entryPoint="p_mode",
                threadId=thread_id,
                userInput=payload.message,
                context=dict(payload.context),
            )
        )
        yield from self._stream_snapshot(thread_id, session, include_start=True)

    def _followup_action(self, thread_id: str, payload: CommandChatRequest) -> tuple[str, PlanningSessionResponse] | None:
        session = get_planning_orchestrator().latest_for_thread(thread_id)
        if not session:
            return None
        text = payload.message
        control = detect_planning_control_intent(text)
        if control == "cancel_planning":
            return "cancel", session
        if control == "restart_planning" or _restart_text(text):
            return "restart", session
        if control == "continue_current_stage":
            return "continue", session
        if control == "skip_current_stage":
            return "skip", session
        if session.status == "needs_goal_clarification":
            return "answer_understanding", session
        if session.status == "MODEL_UNAVAILABLE":
            if session.model_failure and session.model_failure.resume_node == "understanding":
                return "answer_understanding", session
            return "continue", session
        if session.status == "waiting_understanding_confirmation":
            return ("confirm_understanding" if control == "approve_current_stage" or _approval_text(text) else "revise_understanding"), session
        if session.status in {"final_revision", "waiting_final_review"}:
            if session.status == "waiting_final_review" and _calendar_write_text(text):
                return "approve_final", session
            return "revise_final", session
        if session.status == "waiting_calendar_write_approval":
            return "calendar_status", session
        if session.status == "learning_from_feedback":
            return "revise_final", session
        return None

    def _stream_followup(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        action: str,
        session: PlanningSessionResponse,
    ) -> Iterator[str]:
        service = get_planning_orchestrator()
        request = PlanningSessionTextRequest(text=payload.message)
        if action == "cancel":
            updated = service.cancel(session.session_id)
        elif action == "restart":
            service.cancel(session.session_id)
            yield from self._stream_start(thread_id, payload)
            return
        elif action == "continue":
            updated = service.continue_current_stage(session.session_id)
        elif action == "skip":
            updated = service.skip_current_stage(session.session_id)
        elif action == "answer_understanding":
            updated = service.answer_understanding(session.session_id, request)
        elif action == "confirm_understanding":
            updated = service.confirm_understanding(session.session_id)
        elif action == "revise_understanding":
            updated = service.revise_understanding(session.session_id, request)
        elif action == "revise_final":
            updated = service.revise_final(session.session_id, request)
        elif action == "approve_final":
            yield from self._stream_calendar_preview(thread_id, payload, session)
            return
        else:
            updated = service.get_session(session.session_id)
        yield from self._stream_snapshot(thread_id, updated)

    def _calendar_items(self, session: PlanningSessionResponse) -> list[dict[str, Any]]:
        proposal = session.calendar_proposal if isinstance(session.calendar_proposal, dict) else {}
        events = proposal.get("events") if isinstance(proposal.get("events"), list) else []
        items: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            title = str(event.get("title") or "").strip()
            source_key = str(event.get("sourceKey") or "").strip()
            if not title or not source_key:
                continue
            target_date, target_time, _ = _event_time(event.get("start"))
            items.append(
                {
                    "title": title,
                    "date": target_date,
                    "time": target_time,
                    "estimatedMinutes": _event_minutes(event.get("start"), event.get("end")),
                    "priority": "medium",
                    "sourceKey": source_key,
                    "sourceTaskId": str(event.get("sourceTaskId") or ""),
                    "sourceSessionId": str(event.get("sourceSessionId") or ""),
                    "description": str(event.get("description") or ""),
                }
            )
        return items

    def _artifact_refs(self, session: PlanningSessionResponse) -> dict[str, dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}
        for kind in ("final_approval_bundle", "calendar_proposal"):
            artifact = max(
                (item for item in session.artifacts if item.artifact_type == kind),
                key=lambda item: item.version,
                default=None,
            )
            if artifact is None:
                raise RuntimeError(f"Calendar preview requires a versioned {kind} artifact")
            refs[kind] = {
                "id": artifact.id,
                "sessionId": artifact.session_id,
                "kind": artifact.artifact_type,
                "version": artifact.version,
                "owner": artifact.owner_agent,
                "status": artifact.status,
            }
        return refs

    def _create_calendar_action(
        self,
        thread_id: str,
        session: PlanningSessionResponse,
        items: list[dict[str, Any]],
        refs: dict[str, dict[str, Any]],
    ) -> tuple[str, str]:
        draft_id = str(uuid4())
        action_id = str(uuid4())
        now = _now()
        anchor = {
            "planningSessionId": session.session_id,
            "finalApprovalRef": refs["final_approval_bundle"],
            "calendarProposalRef": refs["calendar_proposal"],
        }
        payload = {**anchor, "plans": items}
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_drafts(
                  id, thread_id, kind, version, status, title, summary, payload_json,
                  source_run_id, created_at, updated_at
                ) VALUES (?, ?, 'calendar_plan', 1, 'current', ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    thread_id,
                    "Planix V2 Calendar Proposal",
                    "Version-bound Calendar approval transport",
                    json.dumps(anchor, ensure_ascii=False),
                    session.session_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO command_actions(
                  id, thread_id, draft_id, target, operation, risk, status, reason,
                  payload_json, result_json, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, 'calendar', 'create_or_update_plans', 'write', 'waiting_approval', ?, ?, '{}', '', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    draft_id,
                    f"Write {len(items)} approved Planix V2 events to Calendar",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return action_id, draft_id

    def _stream_calendar_preview(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        session: PlanningSessionResponse,
    ) -> Iterator[str]:
        updated = get_planning_orchestrator().approve_final(session.session_id)
        items = self._calendar_items(updated)
        if not items:
            raise RuntimeError("Calendar Proposal contains no writable events")
        refs = self._artifact_refs(updated)
        action_id, draft_id = self._create_calendar_action(thread_id, updated, items, refs)
        self._record_approval(thread_id, action_id, payload.permission, "pending")
        preview = {
            "actionId": action_id,
            "draftId": draft_id,
            "title": "Planix V2 Calendar Proposal",
            "plans": items,
        }
        self.add_message(thread_id, "card", "Calendar proposal ready", kind="calendar_plan_preview", payload=preview)
        yield from self._stream_snapshot(thread_id, updated)
        yield _ndjson({"type": "calendar_plan_preview", **preview})
        approval = {
            "actionId": action_id,
            "draftId": draft_id,
            "permission": payload.permission,
            "risk": "write",
            "target": "calendar",
            "operation": "create_or_update_plans",
            "summary": f"Write {len(items)} approved events to Calendar?",
        }
        self.add_message(thread_id, "card", approval["summary"], kind="approval_request", payload=approval)
        yield _ndjson({"type": "approval_required", **approval})

    def stream_chat(self, payload: CommandChatRequest) -> Iterator[str]:
        thread_id = ""
        try:
            thread_id = self.ensure_thread(payload.thread_id, title=payload.message)
            self.add_message(thread_id, "user", payload.message)
            yield _ndjson({"type": "thread", "threadId": thread_id})
            followup = self._followup_action(thread_id, payload)
            if followup:
                yield from self._stream_followup(thread_id, payload, *followup)
            else:
                yield from self._stream_start(thread_id, payload)
            yield _ndjson({"type": "done", "threadId": thread_id})
        except AssertionError:
            raise
        except Exception as exc:
            if thread_id:
                self.add_message(thread_id, "card", "Planning request failed", kind="error", payload={"error": str(exc)})
            yield _ndjson({"type": "error", "error": str(exc)})
            if thread_id:
                yield _ndjson({"type": "done", "threadId": thread_id})

    def _load_action(self, action_id: str):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM command_actions WHERE id = ?", (action_id,)).fetchone()

    def _update_action(self, action_id: str, *, status: str, result: dict[str, Any] | None = None, error: str = "") -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE command_actions
                SET status = ?, result_json = COALESCE(?, result_json), error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, _now(), action_id),
            )

    def _record_approval(self, thread_id: str, action_id: str, permission: CommandPermission, decision: str) -> None:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO command_approvals(id, thread_id, action_id, permission, decision, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), thread_id, action_id, permission, decision, _now()),
            )

    def _artifact_ref_is_current(self, session_id: str, raw_ref: Any, expected_kind: str) -> bool:
        if not isinstance(raw_ref, dict) or raw_ref.get("kind") != expected_kind:
            return False
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, version FROM planning_artifacts
                WHERE session_id = ? AND artifact_type = ?
                ORDER BY version DESC, created_at DESC, id DESC LIMIT 1
                """,
                (session_id, expected_kind),
            ).fetchone()
        return bool(
            row
            and str(raw_ref.get("sessionId") or "") == session_id
            and str(raw_ref.get("id") or "") == row["id"]
            and int(raw_ref.get("version") or 0) == int(row["version"] or 0)
        )

    def _calendar_action_is_approved(self, action_id: str) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT decision FROM command_approvals WHERE action_id = ? ORDER BY rowid DESC LIMIT 1",
                (action_id,),
            ).fetchone()
        return bool(row and row["decision"] == "approve")

    def _upsert_calendar_plan(self, item: dict[str, Any]):
        title = str(item.get("title") or "").strip()
        source_key = str(item.get("sourceKey") or "").strip()
        if not title or not source_key:
            raise ValueError("Calendar event requires title and sourceKey")
        target_date = str(item.get("date") or _today())
        target_time = str(item.get("time") or "09:00")
        description = str(item.get("description") or "")
        estimated_minutes = max(1, int(item.get("estimatedMinutes") or 30))
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM plans WHERE source_key = ? LIMIT 1", (source_key,)).fetchone()
        if row:
            plan = update_plan(
                row["id"],
                PlanUpdate(
                    date=target_date,
                    time=target_time,
                    content=title,
                    result=description,
                    priority="medium",
                    estimatedMinutes=estimated_minutes,
                    sourceKey=source_key,
                ),
            )
            return "updated", plan
        plan = create_plan(
            PlanCreate(
                date=target_date,
                time=target_time,
                content=title,
                result=description,
                priority="medium",
                estimatedMinutes=estimated_minutes,
                source="ai",
                sourceKey=source_key,
            )
        )
        return "created", plan

    def _execute_calendar_action(self, action_id: str) -> dict[str, Any]:
        action = self._load_action(action_id)
        if not action:
            raise HTTPException(status_code=404, detail="Calendar action not found")
        if action["target"] != "calendar" or action["operation"] != "create_or_update_plans":
            raise HTTPException(status_code=409, detail="Only Planix V2 Calendar actions are supported")
        if not self._calendar_action_is_approved(action_id):
            raise HTTPException(status_code=409, detail="Calendar action requires explicit approval")
        payload = _json_object(action["payload_json"])
        session_id = str(payload.get("planningSessionId") or "")
        final_ref = payload.get("finalApprovalRef")
        proposal_ref = payload.get("calendarProposalRef")
        if not self._artifact_ref_is_current(session_id, final_ref, "final_approval_bundle") or not self._artifact_ref_is_current(
            session_id, proposal_ref, "calendar_proposal"
        ):
            raise HTTPException(status_code=409, detail="Calendar approval is stale")
        orchestrator = get_planning_orchestrator()
        orchestrator.assert_calendar_write_allowed(session_id, final_approval_ref=final_ref)
        self._update_action(action_id, status="running")
        created = 0
        updated = 0
        plans: list[dict[str, Any]] = []
        try:
            for item in payload.get("plans") or []:
                if not isinstance(item, dict):
                    continue
                state, plan = self._upsert_calendar_plan(item)
                created += int(state == "created")
                updated += int(state == "updated")
                plans.append(
                    {
                        "id": plan.id,
                        "date": plan.date,
                        "time": plan.time,
                        "title": plan.content,
                        "sourceKey": plan.source_key,
                        "sourceTaskId": item.get("sourceTaskId"),
                        "sourceSessionId": item.get("sourceSessionId"),
                        "state": state,
                    }
                )
            result = {
                "actionId": action_id,
                "created": created,
                "updated": updated,
                "failed": 0,
                "affectedDates": sorted({item["date"] for item in plans}),
                "errors": [],
                "plans": plans,
            }
            orchestrator.mark_calendar_written(session_id, final_approval_ref=final_ref)
            self._update_action(action_id, status="success", result=result)
            return result
        except Exception as exc:
            self._update_action(action_id, status="failed", error=str(exc))
            raise

    def stream_approve(self, payload: CommandApproveRequest) -> Iterator[str]:
        action = self._load_action(payload.action_id)
        if not action:
            yield _ndjson({"type": "error", "error": "Calendar action not found"})
            return
        thread_id = payload.thread_id or action["thread_id"]
        yield _ndjson({"type": "thread", "threadId": thread_id})
        decision = "approve" if payload.approved is True else "reject" if payload.approved is False else payload.decision
        self._record_approval(thread_id, payload.action_id, payload.permission, decision)
        if decision == "reject":
            self._update_action(payload.action_id, status="rejected", result={"decision": "reject"})
            yield _ndjson({"type": "execution_result", "actionId": payload.action_id, "status": "rejected", "text": "Calendar write cancelled."})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return
        try:
            action_payload = _json_object(action["payload_json"])
            session_id = str(action_payload.get("planningSessionId") or "")
            final_ref = action_payload.get("finalApprovalRef")
            if not self._artifact_ref_is_current(session_id, final_ref, "final_approval_bundle"):
                raise HTTPException(status_code=409, detail="Calendar approval is stale")
            get_planning_orchestrator().approve_calendar_write(session_id, final_approval_ref=final_ref)
            result = self._execute_calendar_action(payload.action_id)
            self.add_message(thread_id, "card", "Calendar write completed", kind="calendar_write_result", payload=result)
            yield _ndjson({"type": "calendar_write_result", **result})
            yield _ndjson({"type": "execution_result", "actionId": payload.action_id, "status": "success", "text": "Calendar write completed."})
        except Exception as exc:
            yield _ndjson({"type": "error", "error": str(exc)})
        yield _ndjson({"type": "done", "threadId": thread_id})
