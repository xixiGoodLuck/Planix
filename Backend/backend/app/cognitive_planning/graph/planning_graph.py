from __future__ import annotations

from langgraph.graph import END, StateGraph

from ...harness import DEFAULT_SCHEDULER, AgentScheduler
from ...services.cognitive_planning.contracts import CognitivePlanningState


def _from_guard(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.from_guard(state).next_node


def _after_goal(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_goal(state).next_node


def _after_goal_completion(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_goal_completion(state).next_node


def _after_reality(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_reality(state).next_node


def _after_evidence(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_evidence(state).next_node


def _after_strategy(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_strategy(state).next_node


def _after_execution(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_execution(state).next_node


def _after_critic(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_critic(state).next_node


def _after_repair(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_repair(state).next_node


def _after_feedback(state: CognitivePlanningState) -> str:
    return DEFAULT_SCHEDULER.after_feedback(state).next_node


def build_cognitive_os_graph(runtime, *, scheduler: AgentScheduler | None = None):
    active_scheduler = scheduler or DEFAULT_SCHEDULER
    harness = runtime.harness

    def route(decide):
        return lambda state: harness.record_scheduler_decision(state, decide(state))

    graph = StateGraph(CognitivePlanningState)
    graph.add_node("session_guard", harness.wrap_session_guard(runtime.session_guard_node))
    graph.add_node("goal_intelligence", harness.wrap_agent_node("goal_intelligence", runtime.goal_modeling_node))
    graph.add_node("goal_completion", harness.wrap_agent_node("goal_completion", runtime.goal_completion_node))
    graph.add_node("reality", harness.wrap_agent_node("reality", runtime.reality_assessment_node))
    graph.add_node("evidence", harness.wrap_agent_node("evidence", runtime.context_evidence_node))
    graph.add_node("strategy", harness.wrap_agent_node("strategy", runtime.strategy_architect_node))
    graph.add_node("execution", harness.wrap_agent_node("execution", runtime.execution_designer_node))
    graph.add_node("critic", harness.wrap_agent_node("critic", runtime.independent_critic_node))
    graph.add_node("repair", harness.wrap_controller_node("repair", runtime.repair_router_node))
    graph.add_node("feedback_learning", harness.wrap_agent_node("feedback_learning", runtime.feedback_learning_node))
    graph.add_node("calendar_gate", harness.wrap_controller_node("calendar_gate", runtime.calendar_gate_node))
    graph.add_node("wait_for_goal_answer", harness.wrap_wait_node("wait_for_goal_answer", runtime.wait_for_goal_answer_node))
    graph.add_node("wait_for_strategy_approval", harness.wrap_wait_node("wait_for_strategy_approval", runtime.wait_for_strategy_approval_node))
    graph.add_node("wait_for_execution_approval", harness.wrap_wait_node("wait_for_execution_approval", runtime.wait_for_execution_approval_node))
    graph.set_entry_point("session_guard")
    graph.add_conditional_edges("session_guard", route(active_scheduler.from_guard), {
        "goal_intelligence": "goal_intelligence",
        "goal_completion": "goal_completion",
        "reality": "reality",
        "evidence": "evidence",
        "strategy": "strategy",
        "execution": "execution",
        "critic": "critic",
        "feedback_learning": "feedback_learning",
        "wait_for_goal_answer": "wait_for_goal_answer",
        "wait_for_strategy_approval": "wait_for_strategy_approval",
        "wait_for_execution_approval": "wait_for_execution_approval",
        "calendar_gate": "calendar_gate",
    })
    graph.add_conditional_edges("goal_intelligence", route(active_scheduler.after_goal), {
        "goal_completion": "goal_completion", "wait_for_goal_answer": "wait_for_goal_answer", "__end__": END,
    })
    graph.add_conditional_edges("goal_completion", route(active_scheduler.after_goal_completion), {
        "reality": "reality", "wait_for_goal_answer": "wait_for_goal_answer", "__end__": END,
    })
    graph.add_conditional_edges("reality", route(active_scheduler.after_reality), {
        "evidence": "evidence", "wait_for_goal_answer": "wait_for_goal_answer", "__end__": END,
    })
    graph.add_conditional_edges("evidence", route(active_scheduler.after_evidence), {
        "strategy": "strategy", "wait_for_goal_answer": "wait_for_goal_answer", "__end__": END,
    })
    graph.add_conditional_edges("strategy", route(active_scheduler.after_strategy), {
        "execution": "execution", "wait_for_strategy_approval": "wait_for_strategy_approval", "__end__": END,
    })
    graph.add_conditional_edges("execution", route(active_scheduler.after_execution), {"critic": "critic", "__end__": END})
    graph.add_conditional_edges("critic", route(active_scheduler.after_critic), {
        "repair": "repair", "wait_for_execution_approval": "wait_for_execution_approval", "__end__": END,
    })
    graph.add_conditional_edges("repair", route(active_scheduler.after_repair), {
        "goal_intelligence": "goal_intelligence",
        "reality": "reality",
        "evidence": "evidence",
        "strategy": "strategy",
        "execution": "execution",
        "critic": "critic",
        "__end__": END,
    })
    graph.add_conditional_edges("feedback_learning", route(active_scheduler.after_feedback), {"repair": "repair", "__end__": END})
    graph.add_edge("wait_for_goal_answer", END)
    graph.add_edge("wait_for_strategy_approval", END)
    graph.add_edge("wait_for_execution_approval", END)
    graph.add_edge("calendar_gate", END)
    return graph.compile()
