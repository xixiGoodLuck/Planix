from __future__ import annotations

from typing import Any, Literal, TypedDict

from ...schemas import PlanningSessionResponse


PlanningGraphAction = Literal[
    "create",
    "clarify",
    "revise_design",
    "approve_design",
    "approve_execution",
    "revise_execution",
    "submit_feedback",
    "prepare_calendar_write",
]


class PlanningGraphState(TypedDict, total=False):
    session_id: str
    thread_id: str
    user_input: str
    status: str
    action: PlanningGraphAction
    context: dict[str, Any]
    accept_missing_resources: bool

    slot_state: dict[str, Any]
    user_need_contract: dict[str, Any]
    memory_insight: dict[str, Any]
    resource_brief: dict[str, Any]
    design_proposal: dict[str, Any]
    execution_draft: dict[str, Any]
    learning_patch: dict[str, Any]

    last_user_action: str
    next_action: str
    restart_requested: bool

    artifacts: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    messages: list[dict[str, Any]]

    response: PlanningSessionResponse
    errors: list[dict[str, Any]]


def response_to_state(response: PlanningSessionResponse) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": response.session_id,
        "thread_id": response.thread_id,
        "status": response.status,
        "response": response,
        "artifacts": [item.model_dump(by_alias=True) for item in response.artifacts],
        "decisions": [item.model_dump(by_alias=True) for item in response.decisions],
        "messages": [item.model_dump(by_alias=True) for item in response.messages],
    }
    if response.slot_state:
        payload["slot_state"] = response.slot_state.model_dump(by_alias=True)
    if response.user_need_contract:
        payload["user_need_contract"] = response.user_need_contract.model_dump(by_alias=True)
    if response.memory_insight:
        payload["memory_insight"] = response.memory_insight.model_dump(by_alias=True)
    if response.resource_brief:
        payload["resource_brief"] = response.resource_brief.model_dump(by_alias=True)
    if response.design_proposal:
        payload["design_proposal"] = response.design_proposal.model_dump(by_alias=True)
    if response.execution_draft:
        payload["execution_draft"] = response.execution_draft.model_dump(by_alias=True)
    if response.learning_patch:
        payload["learning_patch"] = response.learning_patch.model_dump(by_alias=True)
    return payload
