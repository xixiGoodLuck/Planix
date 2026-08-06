from __future__ import annotations

import os

from fastapi import HTTPException

from ..harness import HarnessRuntime
from ..schemas import PlanningSessionResponse
from ..services.cognitive_planning.contracts import (
    CognitivePlanningMetadata,
    CognitivePlanningState,
    GoalAssumption,
    GoalCompletionResult,
    GoalModelingInput,
    MemoryHint,
    RealityAssessment,
    RealityAssessmentInput,
    SafePlanningError,
    UserGoalModel,
)
from ..services.cognitive_planning.orchestration.persistence import json_object
from ..services.cognitive_planning.orchestration.runtime import (
    CognitivePlanningRuntime as Phase6Runtime,
)
from .agents import (
    CognitiveModelClient,
    CriticAgent,
    EvidenceAgent,
    ExecutionAgent,
    GoalCompletionJudge,
    GoalIntelligenceAgent,
    PlanningModelUnavailable,
    RealityAgent,
    StrategyAgent,
    extract_obvious_facts,
)
from .graph import build_cognitive_os_graph
from .memory import UserModelMemoryRepository


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def use_cognitive_os() -> bool:
    value = os.getenv("PLANIX_COGNITIVE_MODE")
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in FALSE_VALUES:
        return False
    return normalized in TRUE_VALUES


class CognitiveOSRuntime(Phase6Runtime):
    """AI-native planning runtime. Rules protect; templates never decide."""

    engine_version = "cognitive-os-v1"

    def __init__(
        self,
        *,
        model_client: CognitiveModelClient | None = None,
        persistence=None,
        user_model: UserModelMemoryRepository | None = None,
    ):
        model = model_client or CognitiveModelClient()
        super().__init__(model_client=model, persistence=persistence)
        self.harness = HarnessRuntime()
        self.user_model = user_model or UserModelMemoryRepository()
        self.goal_agent = GoalIntelligenceAgent(model)
        self.goal_completion_judge = GoalCompletionJudge()
        self.reality_agent = RealityAgent(model)
        self.evidence_agent = EvidenceAgent(model=model, user_model=self.user_model)
        self.strategy_agent = StrategyAgent(model)
        self.execution_agent = ExecutionAgent(model)
        self.critic_agent = CriticAgent(model)
        self.hypotheses = self.user_model

    def _metadata(
        self,
        *,
        mode: str,
        stage: str,
        confidence: float | None = None,
        rules: list[str] | None = None,
        repair_count: int = 0,
    ) -> CognitivePlanningMetadata:
        return CognitivePlanningMetadata(
            engineVersion="cognitive-os-v1",
            planningMode=mode,
            currentStage=stage,
            agentConfidence=confidence,
            appliedUserRules=rules or [],
            repairCount=repair_count,
        )

    def _state_from_row(self, row, *, action: str, user_input: str = "") -> CognitivePlanningState:
        state = super()._state_from_row(row, action=action, user_input=user_input)
        if "reality_assessment_json" in row.keys():
            raw = json_object(row["reality_assessment_json"])
            if raw:
                state["reality_assessment"] = RealityAssessment.model_validate(raw)
        state = self.harness.restore_graph_state(state)
        # Authority migration is a safety gate, so it has higher priority than
        # an older Harness checkpoint (for example pending_agent=strategy).
        if state.get("evidence_requires_authority_refresh"):
            state["user_action"] = "continue_current_stage"
            state["business_status"] = "evidence_pending"
            state["runtime_status"] = "running"
            state["resume_node"] = "evidence"
        return state

    def _invoke(self, state: CognitivePlanningState) -> PlanningSessionResponse:
        result = self.harness.invoke(
            adapter=self,
            graph_builder=build_cognitive_os_graph,
            state=state,
        )
        session_id = str(result.get("session_id") or state["session_id"])
        return self.get_session(session_id)

    def _block_model(
        self,
        state: CognitivePlanningState,
        *,
        agent: str,
        error: SafePlanningError,
    ) -> CognitivePlanningState:
        recovery = self.harness.decide_model_failure(state, error)
        business_status = recovery.business_status
        state["planning_mode"] = recovery.planning_mode
        state["errors"] = [*state.get("errors", []), error]
        state["status"] = recovery.compatibility_status
        state["business_status"] = business_status
        state["runtime_status"] = recovery.runtime_status
        state["resume_node"] = recovery.resume_node
        self.agent_runtime.record_message(
            state["session_id"],
            from_agent=agent,
            to_agent=agent,
            message_type="block",
            reason=error.message,
            payload={
                "errorType": error.error_type,
                "retryable": error.retryable,
                "attempts": error.attempts,
                "resumeNode": state["resume_node"],
                "recoveryAction": recovery.action.value,
                "allowReadOnly": recovery.allow_read_only,
            },
            resolved=False,
        )
        self.persistence.update(
            state["session_id"],
            status=recovery.compatibility_status,
            business_status=business_status,
            runtime_status=recovery.runtime_status,
            cognitive_metadata=self._metadata(
                mode=recovery.planning_mode,
                stage=error.stage,
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def skip_current_stage(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        state = self._state_from_row(row, action="skip_current_stage")
        goal = state.get("goal_model")
        completion = state.get("goal_completion")
        if (
            row["status"] != "needs_goal_clarification"
            or state.get("business_status") != "goal_clarification"
            or not goal
            or not completion
            or completion.complete
        ):
            raise HTTPException(
                status_code=409,
                detail={"message": "only an incomplete goal clarification step can be skipped"},
            )

        critical_unknowns = [
            item
            for item in goal.decision_relevant_unknowns
            if item.priority == "blocking" and item.impact in {"safety", "feasibility"}
        ]
        if goal.consistency_warnings or critical_unknowns:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "goal consistency, safety, and feasibility blockers must be resolved before planning can continue"
                    )
                },
            )

        skipped_unknowns = [
            item for item in goal.decision_relevant_unknowns if item.priority == "blocking"
        ]
        if not skipped_unknowns:
            raise HTTPException(
                status_code=409,
                detail={"message": "there are no ordinary goal-clarification blockers to skip"},
            )

        existing_assumptions = {item.statement.casefold() for item in goal.assumptions}
        assumptions = list(goal.assumptions)
        for item in skipped_unknowns:
            if item.description.casefold() in existing_assumptions:
                continue
            assumptions.append(
                GoalAssumption(
                    statement=item.description,
                    confidence=0.4,
                    needsUserConfirmation=False,
                )
            )
            existing_assumptions.add(item.description.casefold())

        goal = goal.model_copy(
            update={
                "decision_relevant_unknowns": [
                    item.model_copy(update={"priority": "optional"})
                    if item.priority == "blocking"
                    else item
                    for item in goal.decision_relevant_unknowns
                ],
                "assumptions": assumptions,
                # The explicit control accepts ordinary unresolved Goal-stage
                # information as best-effort assumptions. Its text remains
                # auditable below and in optionalUnknowns, but it is no longer
                # an unresolved uncertainty when Reality revalidates the Goal.
                "uncertainties": [],
                "questions": [],
                "can_proceed_to_evidence": True,
            }
        )
        skipped_questions = [item.question for item in completion.blocking_unknowns]
        completion = GoalCompletionResult(
            complete=True,
            blockingUnknowns=[],
            optionalUnknowns=_dedupe_text(
                [
                    *completion.optional_unknowns,
                    *(item.description for item in skipped_unknowns),
                    *skipped_questions,
                ]
            ),
            nextStage="strategy",
        )
        state["goal_model"] = goal
        state["goal_completion"] = completion
        state["business_status"] = "goal_understood"
        state["runtime_status"] = "running"
        state["planning_mode"] = "model_backed"
        self._record_artifact(
            state,
            agent=self.goal_completion_judge.name,
            artifact_type=self.goal_completion_judge.artifact_type,
            artifact=completion,
            model_usage={},
            decision="approve",
            reason="The user explicitly skipped ordinary goal clarification and accepted best-effort assumptions.",
            summary="Goal clarification was skipped using the saved goal and known facts; planning may continue.",
            status="approved",
            input_artifact_types=("user_goal_model", "goal_completion"),
        )
        self.persistence.update(
            session_id,
            business_status="goal_understood",
            runtime_status="running",
            goal_model=goal,
            goal_completion=completion,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_completion",
                confidence=goal.confidence,
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=(
                "reality_assessment",
                "evidence_pack",
                "strategy_portfolio",
                "execution_blueprint",
                "critique_report",
            ),
        )
        self._handoff(
            state,
            self.goal_completion_judge.name,
            self.reality_agent.name,
            "The user accepted best-effort assumptions and asked planning to continue with saved context.",
        )
        return self._invoke(state)

    def goal_modeling_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        previous = state.get("goal_model")
        domain = previous.domain if previous else ""
        user_model_memories = self.user_model.relevant(domain)
        pre_extracted_facts = extract_obvious_facts(state.get("user_input", ""))
        request_context = state.get("request_context", {})
        prior_understanding = request_context.get("goalUnderstanding") if isinstance(request_context, dict) else None
        if isinstance(prior_understanding, dict):
            allowed = {
                "intentState",
                "understoodIntent",
                "possibleDomains",
                "knownFacts",
                "uncertainties",
                "consistencyWarnings",
                "nextQuestion",
                "clarificationOptions",
                "confidence",
            }
            pre_extracted_facts["goalUnderstanding"] = {
                key: prior_understanding[key]
                for key in allowed
                if key in prior_understanding
            }
        payload = GoalModelingInput(
            conversationHistory=state.get("conversation_history", []),
            previousGoalModel=previous,
            preExtractedFacts=pre_extracted_facts,
            relevantMemoryHints=[
                MemoryHint(
                    sourceId=item.id,
                    kind=item.category,
                    statement=item.statement,
                    confidence=item.confidence,
                )
                for item in user_model_memories
            ],
        )
        try:
            result = self.goal_agent.run(payload)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.goal_agent.name, error=exc.error)
        goal = result.artifact
        state["goal_model"] = goal
        state["planning_mode"] = "model_backed"
        state["runtime_status"] = "running"
        self._record_artifact(
            state,
            agent=self.goal_agent.name,
            artifact_type=self.goal_agent.artifact_type,
            artifact=goal,
            model_usage=result.model_usage,
            decision="produce_artifact",
            reason=(goal.questions[0].why_this_question_matters if goal.questions else goal.desired_change),
            summary="Goal Intelligence updated the semantic goal model; completion is judged separately.",
            status="draft",
            input_artifact_types=("user_goal_model",),
        )
        self.persistence.update(
            state["session_id"],
            runtime_status="running",
            goal_model=goal,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_intelligence",
                confidence=goal.confidence,
                rules=[item.statement for item in user_model_memories],
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def goal_completion_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        goal = state.get("goal_model")
        if not goal:
            return state
        completion = self.goal_completion_judge.evaluate(goal)
        if goal.can_proceed_to_evidence != completion.complete:
            goal = goal.model_copy(update={"can_proceed_to_evidence": completion.complete})
            state["goal_model"] = goal
        state["goal_completion"] = completion
        status = "needs_goal_clarification" if not completion.complete else state.get("status", "needs_goal_clarification")
        business_status = "goal_understood" if completion.complete else "goal_clarification"
        runtime_status = "running" if completion.complete else "idle"
        state["status"] = status
        state["business_status"] = business_status
        state["runtime_status"] = runtime_status
        self._record_artifact(
            state,
            agent=self.goal_completion_judge.name,
            artifact_type=self.goal_completion_judge.artifact_type,
            artifact=completion,
            model_usage={},
            decision="approve" if completion.complete else "request_user_input",
            reason=(
                "Only non-blocking unknowns remain."
                if completion.complete
                else completion.blocking_unknowns[0].impact
            ),
            summary=(
                "Goal completion passed; planning may continue toward strategy."
                if completion.complete
                else "Goal completion is waiting only on decision-blocking information."
            ),
            status="approved" if completion.complete else "blocked",
            input_artifact_types=("user_goal_model",),
        )
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=business_status,
            runtime_status=runtime_status,
            goal_model=goal,
            goal_completion=completion,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_completion",
                confidence=goal.confidence,
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=("reality_assessment", "evidence_pack", "strategy_portfolio", "execution_blueprint", "critique_report")
            if not completion.complete
            else (),
        )
        if completion.complete:
            self._handoff(state, self.goal_agent.name, self.reality_agent.name, "Goal understanding is reliable enough for reality assessment.")
        elif completion.blocking_unknowns:
            state["conversation_history"] = self.persistence.append_assistant_turn(
                state["session_id"],
                "\n".join(item.question for item in completion.blocking_unknowns),
            )
        return state

    def reality_assessment_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        goal = state.get("goal_model")
        if not goal:
            return state
        memories = self.user_model.relevant(goal.domain)
        payload = RealityAssessmentInput(
            goalModel=goal,
            conversationHistory=state.get("conversation_history", []),
            userModelMemories=[
                MemoryHint(sourceId=item.id, kind=item.category, statement=item.statement, confidence=item.confidence)
                for item in memories
            ],
            requestContext=state.get("request_context", {}),
        )
        try:
            result = self.reality_agent.run(payload)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.reality_agent.name, error=exc.error)
        reality = result.artifact
        state["reality_assessment"] = reality
        state["status"] = "needs_goal_clarification" if not reality.can_proceed_to_evidence else state.get("status", "needs_goal_clarification")
        state["business_status"] = "evidence_pending"
        state["runtime_status"] = "running" if reality.can_proceed_to_evidence else "idle"
        self._record_artifact(
            state,
            agent=self.reality_agent.name,
            artifact_type=self.reality_agent.artifact_type,
            artifact=reality,
            model_usage=result.model_usage,
            decision="approve" if reality.can_proceed_to_evidence else "request_user_input",
            reason=reality.feasibility_summary,
            summary=(
                "The goal passed reality assessment."
                if reality.can_proceed_to_evidence
                else "Reality assessment found a decision that the user must resolve."
            ),
            status="approved" if reality.can_proceed_to_evidence else "blocked",
            input_artifact_types=("user_goal_model", "reality_assessment"),
        )
        self.persistence.update(
            state["session_id"],
            status=state["status"],
            business_status="evidence_pending",
            runtime_status=state["runtime_status"],
            reality_assessment=reality,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="reality_assessment",
                confidence=reality.confidence,
                rules=[item.statement for item in memories],
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=("evidence_pack", "strategy_portfolio", "execution_blueprint", "critique_report")
            if not reality.can_proceed_to_evidence
            else (),
        )
        if reality.can_proceed_to_evidence:
            self._handoff(state, self.reality_agent.name, self.evidence_agent.name, "The realistic scope is ready for evidence synthesis.")
        elif reality.important_questions:
            state["conversation_history"] = self.persistence.append_assistant_turn(
                state["session_id"],
                "\n".join(item.question for item in reality.important_questions),
            )
        return state

    def repair_router_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        state = super().repair_router_node(state)
        state["next_node"] = {
            "goal_modeling": "goal_intelligence",
            "context_evidence": "evidence",
            "strategy_architect": "strategy",
            "execution_designer": "execution",
            "independent_critic": "critic",
        }.get(str(state.get("next_node") or ""), state.get("next_node", "__end__"))
        return state

    def _persist_learning_hypothesis(self, state, update) -> None:
        # Long-term rules are evaluated independently from the Critic that
        # diagnosed the feedback. Rejection or evaluator failure never blocks
        # the current-plan repair.
        self.harness.evaluate_memory_candidate(
            state["session_id"],
            learning_update=update,
            memory_repository=self.user_model,
        )

__all__ = ["CognitiveOSRuntime", "use_cognitive_os"]
