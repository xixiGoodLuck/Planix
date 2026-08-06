from __future__ import annotations

from ....harness.quality import meets_critic_score_gate
from ..contracts import CognitivePlanningState


def route_from_guard(state: CognitivePlanningState) -> str:
    if state.get("user_action") == "skip_current_stage":
        completion = state.get("goal_completion")
        return "context_evidence" if state.get("goal_model") and completion and completion.complete else "wait_for_goal_answer"
    if state.get("user_action") == "continue_current_stage":
        resume_node = {
            "goal_intelligence": "goal_modeling",
            "goal_completion": "goal_modeling",
            "reality": "context_evidence",
            "evidence": "context_evidence",
            "strategy": "strategy_architect",
            "execution": "execution_designer",
            "critic": "independent_critic",
            "feedback_learning": "feedback_learning",
        }.get(str(state.get("resume_node") or ""))
        if state.get("status") == "MODEL_UNAVAILABLE" and resume_node:
            return resume_node
        completion = state.get("goal_completion")
        if completion and not completion.complete:
            return "wait_for_goal_answer"
        if resume_node not in {"goal_modeling", None}:
            return resume_node
        return {
            "goal_clarification": "wait_for_goal_answer",
            "goal_understood": "context_evidence",
            "evidence_pending": "context_evidence",
            "strategy_pending": "strategy_architect",
            "execution_pending": "wait_for_execution_approval",
            "calendar_pending": "calendar_gate",
        }.get(str(state.get("business_status") or ""), "wait_for_goal_answer")
    return {
        "create": "goal_modeling",
        "answer_question": "goal_modeling",
        "approve_strategy": "execution_designer",
        "revise_strategy": "strategy_architect",
        "approve_execution": "wait_for_execution_approval",
        "give_feedback": "feedback_learning",
        "write_calendar": "calendar_gate",
        "restart": "goal_modeling",
    }.get(state.get("user_action", "create"), "goal_modeling")


def route_after_goal(state: CognitivePlanningState) -> str:
    goal = state.get("goal_model")
    if state.get("planning_mode") != "model_backed" or not goal or not goal.can_proceed_to_evidence:
        return "wait_for_goal_answer"
    return "context_evidence"


def route_after_evidence(state: CognitivePlanningState) -> str:
    evidence = state.get("evidence_pack")
    if state.get("planning_mode") != "model_backed" or not evidence or not evidence.can_proceed_to_strategy:
        return "wait_for_goal_answer"
    return "strategy_architect"


def route_after_execution(state: CognitivePlanningState) -> str:
    return "independent_critic" if state.get("execution_blueprint") else "__end__"


def route_after_strategy(state: CognitivePlanningState) -> str:
    return "execution_designer" if state.get("repair_loop") else "wait_for_strategy_approval"


def route_after_critic(state: CognitivePlanningState) -> str:
    critique = state.get("critique_report")
    if not critique:
        return "wait_for_execution_approval"
    if critique.status == "passed" and meets_critic_score_gate(critique):
        return "wait_for_execution_approval"
    if int(state.get("repair_count", 0)) >= 2:
        return "wait_for_execution_approval"
    return (
        "repair_router"
        if critique.repair_requests or critique.status == "passed"
        else "wait_for_execution_approval"
    )


def route_after_repair(state: CognitivePlanningState) -> str:
    return str(state.get("next_node") or "__end__")


def route_after_feedback(state: CognitivePlanningState) -> str:
    update = state.get("learning_update")
    if not update or not update.current_plan_patch:
        return "__end__"
    return "repair_router"
