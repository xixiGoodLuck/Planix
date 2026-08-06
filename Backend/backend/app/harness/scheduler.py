from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .quality import MIN_CRITIC_PASS_SCORE, critic_score, meets_critic_score_gate


class SchedulerAction(StrEnum):
    """The small set of lifecycle decisions the Harness may make."""

    INVOKE_AGENT = "invoke_agent"
    INVOKE_CONTROLLER = "invoke_controller"
    WAIT_USER = "wait_user"
    WAIT_APPROVAL = "wait_approval"
    REPAIR = "repair"
    RECOVER = "recover"
    BLOCK = "block"
    COMPLETE = "complete"


@dataclass(frozen=True)
class SchedulerDecision:
    action: SchedulerAction
    next_node: str
    reason_code: str
    agent_id: str | None = None


AGENT_BY_NODE: dict[str, str] = {
    "goal_intelligence": "goal_intelligence",
    "goal_completion": "goal_completion",
    "reality": "reality",
    "evidence": "evidence",
    "strategy": "strategy",
    "execution": "execution",
    "critic": "critic",
    "feedback_learning": "feedback_learning",
}

RESUMABLE_NODES = frozenset(AGENT_BY_NODE)
REPAIR_TARGET_NODES = frozenset(
    {
        "goal_intelligence",
        "reality",
        "evidence",
        "strategy",
        "execution",
        "critic",
    }
)


def _complete(value: Any) -> bool:
    return bool(value and getattr(value, "complete", False))


class AgentScheduler:
    """Choose lifecycle work from typed state; LangGraph only executes it.

    This scheduler deliberately has no model dependency. Agent judgments live
    in their artifacts, while this boundary decides whether the next lifecycle
    action is an invocation, a wait, a repair, recovery, or completion.
    """

    def _decision(
        self,
        next_node: str,
        reason_code: str,
        *,
        action: SchedulerAction | None = None,
    ) -> SchedulerDecision:
        if action is None:
            if next_node == "__end__":
                action = SchedulerAction.COMPLETE
            elif next_node == "wait_for_goal_answer":
                action = SchedulerAction.WAIT_USER
            elif next_node in {"wait_for_strategy_approval", "wait_for_execution_approval"}:
                action = SchedulerAction.WAIT_APPROVAL
            elif next_node == "repair":
                action = SchedulerAction.REPAIR
            elif next_node == "calendar_gate":
                action = SchedulerAction.INVOKE_CONTROLLER
            else:
                action = SchedulerAction.INVOKE_AGENT
        return SchedulerDecision(
            action=action,
            next_node=next_node,
            reason_code=reason_code,
            agent_id=AGENT_BY_NODE.get(next_node),
        )

    def from_guard(self, state: Mapping[str, Any]) -> SchedulerDecision:
        user_action = state.get("user_action", "create")
        if user_action == "skip_current_stage":
            if state.get("goal_model") and _complete(state.get("goal_completion")):
                return self._decision("reality", "goal_skip_accepted")
            return self._decision("wait_for_goal_answer", "goal_skip_rejected")

        if user_action == "continue_current_stage":
            resume_node = str(state.get("resume_node") or "")
            if state.get("status") == "MODEL_UNAVAILABLE" and resume_node in RESUMABLE_NODES:
                return self._decision(
                    resume_node,
                    "resume_failed_agent",
                    action=SchedulerAction.RECOVER,
                )
            completion = state.get("goal_completion")
            if completion and not _complete(completion):
                return self._decision("wait_for_goal_answer", "goal_still_incomplete")
            if resume_node in RESUMABLE_NODES - {"goal_intelligence", "goal_completion"}:
                return self._decision(resume_node, "resume_checkpoint")
            next_node = {
                "goal_clarification": "wait_for_goal_answer",
                "goal_understood": "reality",
                "evidence_pending": "evidence",
                "strategy_pending": "strategy",
                "execution_pending": "wait_for_execution_approval",
                "calendar_pending": "calendar_gate",
            }.get(str(state.get("business_status") or ""), "wait_for_goal_answer")
            return self._decision(next_node, "resume_business_stage")

        next_node = {
            "create": "goal_intelligence",
            "answer_question": "goal_intelligence",
            "approve_strategy": "execution",
            "revise_strategy": "strategy",
            "approve_execution": "wait_for_execution_approval",
            "give_feedback": "feedback_learning",
            "write_calendar": "calendar_gate",
            "restart": "goal_intelligence",
        }.get(str(user_action), "goal_intelligence")
        return self._decision(next_node, "user_action")

    def after_goal(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        next_node = "goal_completion" if state.get("goal_model") else "wait_for_goal_answer"
        return self._decision(next_node, "goal_artifact_state")

    def after_goal_completion(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        next_node = "reality" if _complete(state.get("goal_completion")) else "wait_for_goal_answer"
        return self._decision(next_node, "goal_completion_judgment")

    def after_reality(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        reality = state.get("reality_assessment")
        next_node = (
            "evidence"
            if reality and getattr(reality, "can_proceed_to_evidence", False)
            else "wait_for_goal_answer"
        )
        return self._decision(next_node, "reality_judgment")

    def after_evidence(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        evidence = state.get("evidence_pack")
        next_node = (
            "strategy"
            if evidence and getattr(evidence, "can_proceed_to_strategy", False)
            else "wait_for_goal_answer"
        )
        return self._decision(next_node, "evidence_judgment")

    def after_strategy(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed" or not state.get("strategy_portfolio"):
            return self._decision("__end__", "strategy_unavailable")
        next_node = "execution" if state.get("repair_loop") else "wait_for_strategy_approval"
        return self._decision(next_node, "strategy_ready")

    def after_execution(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        # Every execution artifact is forced through the independent Critic.
        next_node = "critic" if state.get("execution_blueprint") else "__end__"
        return self._decision(next_node, "execution_requires_critic")

    def after_critic(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        critique = state.get("critique_report")
        if not critique:
            return self._decision("__end__", "critic_artifact_missing")
        status = getattr(critique, "status", "")
        issues = list(getattr(critique, "issues", []) or [])
        repair_requests = list(getattr(critique, "repair_requests", []) or [])
        has_high_severity_issue = any(
            getattr(issue, "severity", "") in {"major", "blocker"}
            for issue in issues
        )
        calendar_writable = bool(getattr(critique, "calendar_writable", False))
        score = critic_score(critique)
        if (
            status == "passed"
            and meets_critic_score_gate(critique)
            and calendar_writable
            and not has_high_severity_issue
            and not repair_requests
        ):
            return self._decision("wait_for_execution_approval", "critic_passed")
        if repair_requests and int(state.get("repair_count", 0)) < 2:
            return self._decision("repair", "critic_requested_repair")
        if (
            status == "passed"
            and score < MIN_CRITIC_PASS_SCORE
            and int(state.get("repair_count", 0)) < 2
        ):
            return self._decision("repair", "critic_score_below_threshold")
        if status == "passed":
            return self._decision(
                "wait_for_execution_approval",
                "critic_inconsistent_pass",
                action=SchedulerAction.WAIT_USER,
            )
        if status == "blocked":
            return self._decision(
                "wait_for_execution_approval",
                "critic_blocked",
                action=SchedulerAction.WAIT_USER,
            )
        if int(state.get("repair_count", 0)) < 2:
            return self._decision("repair", "critic_requested_repair")
        return self._decision(
            "wait_for_execution_approval",
            "critic_repair_budget_exhausted",
            action=SchedulerAction.WAIT_USER,
        )

    def after_repair(self, state: Mapping[str, Any]) -> SchedulerDecision:
        target = str(state.get("next_node") or "__end__")
        if target not in REPAIR_TARGET_NODES:
            target = "__end__"
        return self._decision(target, "repair_target")

    def after_feedback(self, state: Mapping[str, Any]) -> SchedulerDecision:
        if state.get("planning_mode") != "model_backed":
            return self._decision("__end__", "runtime_not_model_backed")
        update = state.get("learning_update")
        next_node = "repair" if update and getattr(update, "current_plan_patch", None) else "__end__"
        return self._decision(next_node, "feedback_scope")


DEFAULT_SCHEDULER = AgentScheduler()


__all__ = [
    "AGENT_BY_NODE",
    "DEFAULT_SCHEDULER",
    "AgentScheduler",
    "SchedulerAction",
    "SchedulerDecision",
]
