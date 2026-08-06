from __future__ import annotations

from ..deep_planning import DeepPlanningService
from .edges import route_after_feedback, route_after_session_guard, route_after_user_advocate
from .nodes import PlanningGraphNodes
from .state import PlanningGraphState


class LangGraphUnavailable(RuntimeError):
    pass


def build_graph(service: DeepPlanningService):
    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - depends on optional package.
        raise LangGraphUnavailable("LangGraph is not installed") from exc

    nodes = PlanningGraphNodes(service)
    graph = StateGraph(PlanningGraphState)
    graph.add_node("session_guard", nodes.session_guard_node)
    graph.add_node("user_advocate", nodes.user_advocate_node)
    graph.add_node("memory_insight", nodes.memory_insight_node)
    graph.add_node("resource_intelligence", nodes.resource_intelligence_node)
    graph.add_node("plan_codesigner", nodes.plan_codesigner_node)
    graph.add_node("execution_planner", nodes.execution_planner_node)
    graph.add_node("wait_execution_approval", nodes.wait_execution_approval_node)
    graph.add_node("feedback_evolution", nodes.feedback_evolution_node)
    graph.add_node("calendar_write_gate", nodes.calendar_write_gate_node)

    graph.set_entry_point("session_guard")
    graph.add_conditional_edges(
        "session_guard",
        route_after_session_guard,
        {
            "user_advocate": "user_advocate",
            "plan_codesigner": "plan_codesigner",
            "execution_planner": "execution_planner",
            "wait_execution_approval": "wait_execution_approval",
            "feedback_evolution": "feedback_evolution",
            "calendar_write_gate": "calendar_write_gate",
        },
    )
    graph.add_conditional_edges(
        "user_advocate",
        route_after_user_advocate,
        {"memory_insight": "memory_insight", "__end__": END},
    )
    graph.add_edge("memory_insight", "resource_intelligence")
    graph.add_edge("resource_intelligence", "plan_codesigner")
    graph.add_edge("plan_codesigner", END)
    graph.add_edge("execution_planner", END)
    graph.add_edge("wait_execution_approval", END)
    graph.add_conditional_edges(
        "feedback_evolution",
        route_after_feedback,
        {
            "resource_intelligence": "resource_intelligence",
            "execution_planner": "execution_planner",
            "plan_codesigner": "plan_codesigner",
        },
    )
    graph.add_edge("calendar_write_gate", END)
    return graph.compile()
