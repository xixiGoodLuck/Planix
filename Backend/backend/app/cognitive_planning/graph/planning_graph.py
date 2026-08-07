from __future__ import annotations

from langgraph.graph import END, StateGraph

from ...harness.scheduler import SchedulerAction, SchedulerDecision
from ...services.cognitive_planning.contracts import CognitivePlanningState


def _decision(
    next_node: str,
    reason: str,
    *,
    action: SchedulerAction = SchedulerAction.INVOKE_CONTROLLER,
    agent_id: str | None = None,
) -> SchedulerDecision:
    if next_node == "__end__":
        action = SchedulerAction.COMPLETE
    return SchedulerDecision(
        action=action,
        next_node=next_node,
        reason_code=reason,
        agent_id=agent_id,
    )


def _from_guard(state: CognitivePlanningState) -> SchedulerDecision:
    action = str(state.get("user_action") or "create")
    if action in {"create", "answer_question", "restart"}:
        return _decision("understanding", "understanding_input", action=SchedulerAction.INVOKE_AGENT, agent_id="goal_intelligence")
    if action == "confirm_understanding":
        return _decision("compile_constraints", "understanding_confirmed")
    if action == "give_feedback":
        return _decision("feedback_router", "final_feedback")
    if action == "write_calendar":
        return _decision("calendar_gate", "final_approval_verified")
    if action == "continue_current_stage" and state.get("status") == "final_revision":
        return _decision(
            "semantic_review",
            "final_review_recheck",
            action=SchedulerAction.RECOVER,
            agent_id="critic",
        )
    if action == "continue_current_stage" and state.get("status") == "MODEL_UNAVAILABLE":
        resume = str(state.get("resume_node") or "understanding")
        mapping = {
            "goal_intelligence": "understanding",
            "goal_completion": "understanding_readiness",
            "reality": "assess_context",
            "evidence": "synthesize_context",
            "strategy": "design_approach",
            "execution": "generate_plan",
            "critic": "semantic_review",
        }
        node = mapping.get(resume, "understanding")
        return _decision(node, "checkpoint_resume", action=SchedulerAction.RECOVER)
    return _decision("wait_for_understanding", "wait_current_phase", action=SchedulerAction.WAIT_USER)


def build_planning_graph(runtime, *, scheduler=None):
    harness = runtime.harness

    def route(decide):
        return lambda state: harness.record_scheduler_decision(state, decide(state))

    graph = StateGraph(CognitivePlanningState)
    graph.add_node("session_guard", harness.wrap_session_guard(runtime.session_guard_node))
    graph.add_node("understanding", harness.wrap_agent_node("goal_intelligence", runtime.understanding_node))
    graph.add_node("understanding_readiness", harness.wrap_agent_node("goal_completion", runtime.understanding_readiness_node))
    graph.add_node("wait_for_understanding", harness.wrap_wait_node("wait_for_understanding", runtime.wait_for_understanding_node))
    graph.add_node("compile_constraints", harness.wrap_controller_node("compile_constraints", runtime.compile_constraints_node))
    graph.add_node("assess_context", harness.wrap_agent_node("reality", runtime.assess_context_node))
    graph.add_node("synthesize_context", harness.wrap_agent_node("evidence", runtime.synthesize_context_node))
    graph.add_node("build_context", harness.wrap_controller_node("build_context", runtime.build_context_node))
    graph.add_node("design_approach", harness.wrap_agent_node("strategy", runtime.design_approach_node))
    graph.add_node("select_approach", harness.wrap_controller_node("select_approach", runtime.select_approach_node))
    graph.add_node("generate_plan", harness.wrap_agent_node("execution", runtime.generate_plan_node))
    graph.add_node("validate_plan", harness.wrap_controller_node("validate_plan", runtime.validate_plan_node))
    graph.add_node("prepare_plan_repair", harness.wrap_controller_node("prepare_plan_repair", runtime.prepare_plan_repair_node))
    graph.add_node("semantic_review", harness.wrap_agent_node("critic", runtime.semantic_review_node))
    graph.add_node("repair_plan", harness.wrap_controller_node("repair_plan", runtime.repair_plan_node))
    graph.add_node("review_plan", harness.wrap_controller_node("review_plan", runtime.review_plan_node))
    graph.add_node("generate_schedule", harness.wrap_controller_node("generate_schedule", runtime.generate_schedule_node))
    graph.add_node("validate_schedule", harness.wrap_controller_node("validate_schedule", runtime.validate_schedule_node))
    graph.add_node("repair_schedule", harness.wrap_controller_node("repair_schedule", runtime.repair_schedule_node))
    graph.add_node("materialize_calendar", harness.wrap_controller_node("materialize_calendar", runtime.materialize_calendar_node))
    graph.add_node("wait_for_final_review", harness.wrap_wait_node("wait_for_final_review", runtime.wait_for_final_review_node))
    graph.add_node("feedback_router", harness.wrap_controller_node("feedback_router", runtime.feedback_router_node))
    graph.add_node("record_learning", harness.wrap_agent_node("feedback_learning", runtime.record_learning_node))
    graph.add_node("calendar_gate", harness.wrap_controller_node("calendar_gate", runtime.calendar_gate_node))

    graph.set_entry_point("session_guard")
    graph.add_conditional_edges("session_guard", route(_from_guard), {
        "understanding": "understanding",
        "understanding_readiness": "understanding_readiness",
        "wait_for_understanding": "wait_for_understanding",
        "compile_constraints": "compile_constraints",
        "assess_context": "assess_context",
        "synthesize_context": "synthesize_context",
        "design_approach": "design_approach",
        "generate_plan": "generate_plan",
        "semantic_review": "semantic_review",
        "review_plan": "review_plan",
        "feedback_router": "feedback_router",
        "calendar_gate": "calendar_gate",
        "__end__": END,
    })
    graph.add_conditional_edges("understanding", route(lambda state: _decision(
        "understanding_readiness" if state.get("goal_model") else "wait_for_understanding",
        "understanding_updated",
        action=SchedulerAction.INVOKE_AGENT if state.get("goal_model") else SchedulerAction.WAIT_USER,
        agent_id="goal_completion" if state.get("goal_model") else None,
    )), {"understanding_readiness": "understanding_readiness", "wait_for_understanding": "wait_for_understanding", "__end__": END})
    graph.add_edge("understanding_readiness", "wait_for_understanding")
    graph.add_edge("wait_for_understanding", END)
    graph.add_edge("compile_constraints", "assess_context")
    graph.add_conditional_edges("assess_context", route(lambda state: _decision(
        "synthesize_context" if state.get("reality_assessment") and state["reality_assessment"].can_proceed_to_evidence else "wait_for_understanding",
        "context_assessment_judgment",
        action=SchedulerAction.INVOKE_AGENT if state.get("reality_assessment") and state["reality_assessment"].can_proceed_to_evidence else SchedulerAction.WAIT_USER,
        agent_id="evidence" if state.get("reality_assessment") and state["reality_assessment"].can_proceed_to_evidence else None,
    )), {"synthesize_context": "synthesize_context", "wait_for_understanding": "wait_for_understanding", "__end__": END})
    graph.add_conditional_edges("synthesize_context", route(lambda state: _decision(
        "build_context" if state.get("evidence_pack") and state["evidence_pack"].can_proceed_to_strategy else "wait_for_understanding",
        "context_evidence_judgment",
        action=SchedulerAction.INVOKE_CONTROLLER if state.get("evidence_pack") and state["evidence_pack"].can_proceed_to_strategy else SchedulerAction.WAIT_USER,
    )), {"build_context": "build_context", "wait_for_understanding": "wait_for_understanding", "__end__": END})
    graph.add_edge("build_context", "design_approach")
    graph.add_edge("design_approach", "select_approach")
    graph.add_edge("select_approach", "generate_plan")
    graph.add_edge("generate_plan", "validate_plan")
    graph.add_conditional_edges("validate_plan", route(lambda state: _decision(
        "prepare_plan_repair" if state.get("plan_quality_report") and not state["plan_quality_report"].hard_rules_passed and int(state.get("repair_count", 0)) < 2
        else "semantic_review" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else "wait_for_final_review",
        "plan_hard_validation",
        action=SchedulerAction.REPAIR if state.get("plan_quality_report") and not state["plan_quality_report"].hard_rules_passed and int(state.get("repair_count", 0)) < 2
        else SchedulerAction.INVOKE_AGENT if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed
        else SchedulerAction.WAIT_USER,
        agent_id="critic" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed else None,
    )), {"prepare_plan_repair": "prepare_plan_repair", "semantic_review": "semantic_review", "wait_for_final_review": "wait_for_final_review", "__end__": END})
    graph.add_conditional_edges("prepare_plan_repair", route(lambda state: _decision(
        "semantic_review" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed else "generate_plan",
        "plan_repair_result",
        action=SchedulerAction.INVOKE_AGENT,
        agent_id="critic" if state.get("plan_quality_report") and state["plan_quality_report"].hard_rules_passed else "execution",
    )), {"semantic_review": "semantic_review", "generate_plan": "generate_plan", "__end__": END})
    graph.add_conditional_edges("semantic_review", route(lambda state: _decision(
        "repair_plan" if state.get("critique_report") and state["critique_report"].repair_requests and int(state.get("repair_count", 0)) < 2 else "review_plan",
        "semantic_plan_review",
        action=SchedulerAction.REPAIR if state.get("critique_report") and state["critique_report"].repair_requests and int(state.get("repair_count", 0)) < 2 else SchedulerAction.INVOKE_CONTROLLER,
    )), {"repair_plan": "repair_plan", "review_plan": "review_plan", "__end__": END})
    graph.add_conditional_edges("repair_plan", route(lambda state: _decision(
        str(state.get("next_node") or "generate_plan"), "repair_target", action=SchedulerAction.REPAIR
    )), {"understanding": "understanding", "assess_context": "assess_context", "synthesize_context": "synthesize_context", "design_approach": "design_approach", "generate_plan": "generate_plan", "semantic_review": "semantic_review", "__end__": END})
    graph.add_edge("review_plan", "generate_schedule")
    graph.add_edge("generate_schedule", "validate_schedule")
    graph.add_conditional_edges("validate_schedule", route(lambda state: _decision(
        "materialize_calendar" if state.get("schedule_quality_report") and state["schedule_quality_report"].passed
        else "repair_schedule" if int(state.get("schedule_repair_count", 0)) < 2
        else "wait_for_final_review",
        "schedule_validation",
        action=SchedulerAction.REPAIR if state.get("schedule_quality_report") and not state["schedule_quality_report"].passed and int(state.get("schedule_repair_count", 0)) < 2 else SchedulerAction.INVOKE_CONTROLLER,
    )), {
        "materialize_calendar": "materialize_calendar",
        "repair_schedule": "repair_schedule",
        "wait_for_final_review": "wait_for_final_review",
        "__end__": END,
    })
    graph.add_edge("repair_schedule", "validate_schedule")
    graph.add_edge("materialize_calendar", "wait_for_final_review")
    graph.add_edge("wait_for_final_review", END)
    graph.add_conditional_edges("feedback_router", route(lambda state: _decision(
        str(state.get("next_node") or "wait_for_final_review"), "feedback_target",
        action=SchedulerAction.WAIT_USER if state.get("next_node") == "wait_for_final_review" else SchedulerAction.INVOKE_CONTROLLER,
    )), {
        "understanding": "understanding",
        "record_learning": "record_learning",
        "generate_schedule": "generate_schedule",
        "materialize_calendar": "materialize_calendar",
        "wait_for_final_review": "wait_for_final_review",
        "__end__": END,
    })
    graph.add_edge("record_learning", "repair_plan")
    graph.add_edge("calendar_gate", END)
    return graph.compile()


__all__ = ["build_planning_graph"]
