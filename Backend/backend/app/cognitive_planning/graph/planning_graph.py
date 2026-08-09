from __future__ import annotations

from langgraph.graph import END, StateGraph

from ...harness.scheduler import SchedulerAction, SchedulerDecision
from ..contracts import CognitivePlanningState


def _decision(
    next_node: str,
    reason: str,
    *,
    action: SchedulerAction = SchedulerAction.INVOKE_CONTROLLER,
    agent_id: str | None = None,
) -> SchedulerDecision:
    if next_node == "__end__":
        action = SchedulerAction.COMPLETE
    return SchedulerDecision(action=action, next_node=next_node, reason_code=reason, agent_id=agent_id)


def _from_guard(state: CognitivePlanningState) -> SchedulerDecision:
    action = str(state.get("user_action") or "create")
    if action in {"create", "answer_question", "restart"}:
        return _decision("understanding", "understanding_input", action=SchedulerAction.INVOKE_AGENT, agent_id="understanding_agent")
    if action == "confirm_understanding":
        return _decision("compile_constraints", "understanding_confirmed")
    if action == "give_feedback":
        return _decision("feedback_router", "final_feedback")
    if action == "write_calendar":
        return _decision("calendar_gate", "final_approval_verified")
    if action == "continue_current_stage" and state.get("status") == "MODEL_UNAVAILABLE":
        node = str(state.get("resume_node") or "understanding")
        allowed = {"understanding", "generate_plan", "semantic_review", "repair_plan", "record_learning"}
        if node not in allowed:
            node = "understanding"
        agent = {
            "understanding": "understanding_agent",
            "generate_plan": "plan_generator",
            "semantic_review": "plan_reviewer",
            "repair_plan": "plan_generator",
            "record_learning": "feedback_learning",
        }.get(node)
        return _decision(node, "checkpoint_resume", action=SchedulerAction.RECOVER, agent_id=agent)
    return _decision("wait_for_understanding", "wait_current_phase", action=SchedulerAction.WAIT_USER)


def _model_blocked(state: CognitivePlanningState) -> bool:
    return state.get("status") == "MODEL_UNAVAILABLE" or state.get("runtime_status") == "blocked_model"


def build_planning_graph(runtime, *, scheduler=None):
    harness = runtime.harness

    def route(decide):
        return lambda state: harness.record_scheduler_decision(state, decide(state))

    graph = StateGraph(CognitivePlanningState)
    graph.add_node("session_guard", harness.wrap_session_guard(runtime.session_guard_node))
    graph.add_node("understanding", harness.wrap_agent_node("understanding", runtime.understanding_node))
    graph.add_node("understanding_readiness", harness.wrap_controller_node("understanding_readiness", runtime.understanding_readiness_node))
    graph.add_node("wait_for_understanding", harness.wrap_wait_node("wait_for_understanding", runtime.wait_for_understanding_node))
    graph.add_node("compile_constraints", harness.wrap_controller_node("compile_constraints", runtime.compile_constraints_node))
    graph.add_node("build_context", harness.wrap_controller_node("build_context", runtime.build_context_node))
    graph.add_node("generate_plan", harness.wrap_agent_node("generate_plan", runtime.generate_plan_node))
    graph.add_node("validate_plan", harness.wrap_controller_node("validate_plan", runtime.validate_plan_node))
    graph.add_node("semantic_review", harness.wrap_agent_node("semantic_review", runtime.semantic_review_node))
    graph.add_node("repair_plan", harness.wrap_agent_node("repair_plan", runtime.repair_plan_node))
    graph.add_node("validate_repaired_plan", harness.wrap_controller_node("validate_repaired_plan", runtime.validate_repaired_plan_node))
    graph.add_node("generate_schedule", harness.wrap_controller_node("generate_schedule", runtime.generate_schedule_node))
    graph.add_node("validate_schedule", harness.wrap_controller_node("validate_schedule", runtime.validate_schedule_node))
    graph.add_node("repair_schedule", harness.wrap_controller_node("repair_schedule", runtime.repair_schedule_node))
    graph.add_node("materialize_calendar", harness.wrap_controller_node("materialize_calendar", runtime.materialize_calendar_node))
    graph.add_node("wait_for_final_review", harness.wrap_wait_node("wait_for_final_review", runtime.wait_for_final_review_node))
    graph.add_node("feedback_router", harness.wrap_controller_node("feedback_router", runtime.feedback_router_node))
    graph.add_node("record_learning", harness.wrap_agent_node("record_learning", runtime.record_learning_node))
    graph.add_node("calendar_gate", harness.wrap_controller_node("calendar_gate", runtime.calendar_gate_node))

    graph.set_entry_point("session_guard")
    graph.add_conditional_edges("session_guard", route(_from_guard), {
        "understanding": "understanding",
        "compile_constraints": "compile_constraints",
        "generate_plan": "generate_plan",
        "semantic_review": "semantic_review",
        "repair_plan": "repair_plan",
        "record_learning": "record_learning",
        "wait_for_understanding": "wait_for_understanding",
        "feedback_router": "feedback_router",
        "calendar_gate": "calendar_gate",
        "__end__": END,
    })
    graph.add_conditional_edges("understanding", route(lambda state: _decision(
        "__end__" if _model_blocked(state) else "understanding_readiness",
        "model_failure_checkpoint" if _model_blocked(state) else "understanding_complete",
    )), {"understanding_readiness": "understanding_readiness", "__end__": END})
    graph.add_edge("understanding_readiness", "wait_for_understanding")
    graph.add_edge("wait_for_understanding", END)
    graph.add_edge("compile_constraints", "build_context")
    graph.add_edge("build_context", "generate_plan")
    graph.add_conditional_edges("generate_plan", route(lambda state: _decision(
        "__end__" if _model_blocked(state) else "validate_plan",
        "model_failure_checkpoint" if _model_blocked(state) else "plan_generated",
    )), {"validate_plan": "validate_plan", "__end__": END})
    graph.add_conditional_edges("validate_plan", route(lambda state: _decision(
        "semantic_review" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else "repair_plan" if int(state.get("repair_count", 0)) < 2
        else "wait_for_final_review",
        "plan_hard_validation",
        action=SchedulerAction.INVOKE_AGENT if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else SchedulerAction.REPAIR if int(state.get("repair_count", 0)) < 2
        else SchedulerAction.WAIT_USER,
        agent_id="plan_reviewer" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else "plan_generator" if int(state.get("repair_count", 0)) < 2 else None,
    )), {"semantic_review": "semantic_review", "repair_plan": "repair_plan", "wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_conditional_edges("semantic_review", route(lambda state: _decision(
        "__end__" if _model_blocked(state)
        else "generate_schedule" if state.get("plan_quality_report") and state["plan_quality_report"].passed
        else "repair_plan" if int(state.get("repair_count", 0)) < 2
        else "wait_for_final_review",
        "semantic_plan_review",
        action=SchedulerAction.INVOKE_CONTROLLER if state.get("plan_quality_report") and state["plan_quality_report"].passed
        else SchedulerAction.REPAIR if int(state.get("repair_count", 0)) < 2
        else SchedulerAction.WAIT_USER,
        agent_id="plan_generator" if state.get("plan_quality_report") and not state["plan_quality_report"].passed and int(state.get("repair_count", 0)) < 2 else None,
    )), {"generate_schedule": "generate_schedule", "repair_plan": "repair_plan", "wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_conditional_edges("repair_plan", route(lambda state: _decision(
        "__end__" if _model_blocked(state) else "validate_repaired_plan",
        "model_failure_checkpoint" if _model_blocked(state) else "plan_repair_complete",
    )), {"validate_repaired_plan": "validate_repaired_plan", "__end__": END})
    graph.add_conditional_edges("validate_repaired_plan", route(lambda state: _decision(
        "semantic_review" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else "repair_plan" if int(state.get("repair_count", 0)) < 2
        else "wait_for_final_review",
        "repair_regression_validation",
        action=SchedulerAction.INVOKE_AGENT if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else SchedulerAction.REPAIR if int(state.get("repair_count", 0)) < 2
        else SchedulerAction.WAIT_USER,
        agent_id="plan_reviewer" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed else "plan_generator",
    )), {"semantic_review": "semantic_review", "repair_plan": "repair_plan", "wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_edge("generate_schedule", "validate_schedule")
    graph.add_conditional_edges("validate_schedule", route(lambda state: _decision(
        "materialize_calendar" if state.get("schedule_quality_report") and state["schedule_quality_report"].passed
        else "repair_schedule" if int(state.get("schedule_repair_count", 0)) < 2
        else "wait_for_final_review",
        "schedule_validation",
        action=SchedulerAction.INVOKE_CONTROLLER if state.get("schedule_quality_report") and state["schedule_quality_report"].passed
        else SchedulerAction.REPAIR if int(state.get("schedule_repair_count", 0)) < 2
        else SchedulerAction.WAIT_USER,
    )), {"materialize_calendar": "materialize_calendar", "repair_schedule": "repair_schedule", "wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_edge("repair_schedule", "validate_schedule")
    graph.add_edge("materialize_calendar", "wait_for_final_review")
    graph.add_edge("wait_for_final_review", END)
    graph.add_conditional_edges("feedback_router", route(lambda state: _decision(
        str(state.get("next_node") or "wait_for_final_review"),
        "feedback_target",
        action=SchedulerAction.INVOKE_AGENT if state.get("next_node") in {"understanding", "repair_plan", "record_learning"} else SchedulerAction.INVOKE_CONTROLLER,
        agent_id={"understanding": "understanding_agent", "repair_plan": "plan_generator", "record_learning": "feedback_learning"}.get(state.get("next_node")),
    )), {
        "understanding": "understanding",
        "repair_plan": "repair_plan",
        "record_learning": "record_learning",
        "generate_schedule": "generate_schedule",
        "materialize_calendar": "materialize_calendar",
        "wait_for_final_review": "wait_for_final_review",
        "__end__": END,
    })
    graph.add_conditional_edges("record_learning", route(lambda state: _decision(
        "__end__" if _model_blocked(state) else "wait_for_final_review",
        "model_failure_checkpoint" if _model_blocked(state) else "learning_recorded",
    )), {"wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_edge("calendar_gate", END)
    return graph.compile()


__all__ = ["build_planning_graph"]
