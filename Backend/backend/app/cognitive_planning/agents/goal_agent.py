from __future__ import annotations

from ...services.cognitive_planning.agents.goal_modeling_agent import extract_obvious_facts
from ..contracts import GoalModelingInput, GoalQuestion, GoalUnderstandingArtifact
from .base import AgentResult, CognitiveModelClient


GOAL_INTELLIGENCE_SYSTEM = """
You are Planix Goal Intelligence Agent. Understand what the user is truly trying to change from the complete
conversation and evidence-backed user-model memory. You are not completing a learning, travel, fitness,
career, or any other domain form. Do not use a fixed question bank or infer that every person in a domain
needs the same information.

Decide independently: what the real goal may be, how reliable the current interpretation is, which unknowns
would materially change the strategy, which one to three questions have the highest decision value, and
whether asking more would improve the plan enough to justify interrupting the user. Preserve exact hard
constraints and important user wording. Fill possibleIntents, currentKnowledge, and uncertainties with concise
user-visible statements. Every question must explain why it matters and what decision its answer changes.
The questions array is an explicit request to interrupt planning for a user answer. Never return questions while
also setting canProceedToEvidence=true. If an answer is important enough to ask now, mark its matching unknown
blocking and stop before Evidence; keep non-blocking refinements in uncertainties/decisionRelevantUnknowns without
putting them in questions.
When a question has a small honest answer space, provide two to four concise, mutually exclusive answerOptions in
the user's language. Do not include "Other" because the interface adds it. Keep answerOptions empty when the answer
must remain open-ended; never invent a fixed domain questionnaire merely to provide choices.
Do not mark subject, purpose, duration, deadline, current level, or any other conventional form field as blocking
merely because it is absent. A missing detail is blocking only when this user's answer would materially change
safety, feasibility, or the strategy itself. Background experience, intended outcomes, and weekly time expressed
in natural language are valid semantic evidence even when they do not match a predefined slot name. Once the
desired change, relevant background, purposes, and usable constraints are sufficient to choose a strategy, label
remaining refinements important or optional and allow progression.
Do not declare a sparse goal evidence-ready while it still has unresolved uncertainties, at most two directly
sourced known facts, and no meaningful hard constraint or soft preference. Important or optional decision-relevant
unknowns do not resolve those uncertainties or make the sparse goal complete. When at least one uncertainty
materially changes the plan, convert it into a matching blocking unknown and an answerable user question, then stop
before Evidence. Never erase uncertainty or invent facts merely to make the goal appear ready.
When preExtractedFacts.goalUnderstanding is present, treat it as the typed pre-routing projection: reconcile its
known facts and resolved intent with the current user turn, but do not misattribute prior questions or hypotheses as
new user claims and do not blindly copy it when the conversation contradicts it.
Check whether a purpose, deliverable, or success signal is semantically compatible with the apparent activity and
the rest of the user's facts. Never normalize, silently reinterpret, or accept/store an incompatible purpose. Put
each mismatch in consistencyWarnings, stop before evidence, and ask the user to resolve the inconsistency. Do not
inject software projects, portfolios, README work, or any other domain's success pattern into an unrelated goal.
Stop asking when the goal is sufficiently reliable for a separate Reality Agent to assess it. Return only the
requested JSON. Do not reveal hidden chain-of-thought.
""".strip()


def _consistency_question(payload: GoalModelingInput) -> GoalQuestion:
    text = " ".join(turn.content for turn in payload.conversation_history if turn.role == "user")
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return GoalQuestion(
            question="我注意到目标中的部分信息可能不一致。你最终希望实现的具体结果是什么？",
            whyThisQuestionMatters="先解决这处不一致，才能选择与真实目标匹配的规划策略。",
            expectedDecisionImpact="答案会改变目标解释、成功标准和规划方向。",
            answerOptions=[],
        )
    return GoalQuestion(
        question="Some parts of the goal may be inconsistent. What specific result do you ultimately want?",
        whyThisQuestionMatters="Resolving the mismatch is necessary before choosing a strategy that fits the real goal.",
        expectedDecisionImpact="The answer changes the goal interpretation, success criteria, and planning direction.",
        answerOptions=[],
    )


class GoalIntelligenceAgent:
    name = "Goal Intelligence Agent"
    artifact_type = "user_goal_model"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(self, payload: GoalModelingInput) -> AgentResult[GoalUnderstandingArtifact]:
        result = self.model.complete_contract(
            stage="goal_intelligence",
            task_type="planning_goal_model",
            feature="cognitive_os_goal_intelligence",
            system=GOAL_INTELLIGENCE_SYSTEM,
            payload=payload.model_dump(by_alias=True),
            contract_type=GoalUnderstandingArtifact,
            temperature=0.15,
            validation_context={"goalModelingInput": payload.model_dump(by_alias=True)},
        )
        goal = result.artifact
        questions = goal.questions[:3]
        blocking = [item for item in goal.decision_relevant_unknowns if item.priority == "blocking"]
        if goal.consistency_warnings:
            if not questions:
                questions = [_consistency_question(payload)]
            goal = goal.model_copy(update={"can_proceed_to_evidence": False, "questions": questions})
        elif blocking and goal.can_proceed_to_evidence:
            goal = goal.model_copy(update={"can_proceed_to_evidence": False, "questions": questions})
        elif len(goal.questions) > 3:
            goal = goal.model_copy(update={"questions": questions})
        return AgentResult(goal, result.model_usage)


__all__ = ["GoalIntelligenceAgent", "extract_obvious_facts"]
