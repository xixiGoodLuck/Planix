from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..contracts import CognitivePlanningState
from .edges import (
    route_after_critic,
    route_after_evidence,
    route_after_execution,
    route_after_feedback,
    route_after_goal,
    route_after_repair,
    route_after_strategy,
    route_from_guard,
)


def build_cognitive_graph(runtime):
    graph = StateGraph(CognitivePlanningState)
    graph.add_node("session_guard", runtime.session_guard_node)
    graph.add_node("goal_modeling", runtime.goal_modeling_node)
    graph.add_node("wait_for_goal_answer", runtime.wait_for_goal_answer_node)
    graph.add_node("context_evidence", runtime.context_evidence_node)
    graph.add_node("strategy_architect", runtime.strategy_architect_node)
    graph.add_node("wait_for_strategy_approval", runtime.wait_for_strategy_approval_node)
    graph.add_node("execution_designer", runtime.execution_designer_node)
    graph.add_node("independent_critic", runtime.independent_critic_node)
    graph.add_node("repair_router", runtime.repair_router_node)
    graph.add_node("wait_for_execution_approval", runtime.wait_for_execution_approval_node)
    graph.add_node("feedback_learning", runtime.feedback_learning_node)
    graph.add_node("calendar_gate", runtime.calendar_gate_node)
    graph.set_entry_point("session_guard")
    graph.add_conditional_edges(
        "session_guard",
        route_from_guard,
        {
            "goal_modeling": "goal_modeling",
            "wait_for_goal_answer": "wait_for_goal_answer",
            "context_evidence": "context_evidence",
            "strategy_architect": "strategy_architect",
            "execution_designer": "execution_designer",
            "wait_for_execution_approval": "wait_for_execution_approval",
            "feedback_learning": "feedback_learning",
            "calendar_gate": "calendar_gate",
        },
    )
    graph.add_conditional_edges("goal_modeling", route_after_goal, {"context_evidence": "context_evidence", "wait_for_goal_answer": "wait_for_goal_answer"})
    graph.add_edge("wait_for_goal_answer", END)
    graph.add_conditional_edges("context_evidence", route_after_evidence, {"strategy_architect": "strategy_architect", "wait_for_goal_answer": "wait_for_goal_answer"})
    graph.add_conditional_edges("strategy_architect", route_after_strategy, {"execution_designer": "execution_designer", "wait_for_strategy_approval": "wait_for_strategy_approval"})
    graph.add_edge("wait_for_strategy_approval", END)
    graph.add_conditional_edges("execution_designer", route_after_execution, {"independent_critic": "independent_critic", "__end__": END})
    graph.add_conditional_edges("independent_critic", route_after_critic, {"repair_router": "repair_router", "wait_for_execution_approval": "wait_for_execution_approval"})
    graph.add_conditional_edges(
        "repair_router",
        route_after_repair,
        {
            "goal_modeling": "goal_modeling",
            "context_evidence": "context_evidence",
            "strategy_architect": "strategy_architect",
            "execution_designer": "execution_designer",
            "independent_critic": "independent_critic",
            "__end__": END,
        },
    )
    graph.add_edge("wait_for_execution_approval", END)
    graph.add_conditional_edges("feedback_learning", route_after_feedback, {"repair_router": "repair_router", "__end__": END})
    graph.add_edge("calendar_gate", END)
    return graph.compile()
