from __future__ import annotations

import re
from typing import Any

from ..contracts import GoalModelingInput, UserGoalModel
from .base import AgentResult, CognitiveModelClient


GOAL_MODEL_SYSTEM = """
You are Planix Goal Modeling Agent. Build a precise, user-centered goal model from the full conversation.
You are not filling a universal form and must not use fixed learning or travel question templates.
Identify only decision-relevant unknowns: information whose answer can materially change strategy, safety,
feasibility, schedule, resources, or success criteria. Ask at most three highest-information-value questions
and explain why each matters. Preserve the user's exact hard constraints and important wording. Detect mixed,
unrealistic, unsafe, or unmeasurable goals and challenge them politely. Stop asking when evidence is sufficient.
The questions array explicitly interrupts planning for a user answer. Never return questions while also setting
canProceedToEvidence=true. If an answer is important enough to ask now, mark its matching unknown blocking and stop
before Evidence; keep non-blocking refinements out of questions.
Do not declare a sparse goal evidence-ready while it still has unresolved uncertainties, at most two directly
sourced known facts, and no meaningful hard constraint or soft preference. Important or optional decision-relevant
unknowns do not resolve those uncertainties or make the sparse goal complete. When at least one uncertainty
materially changes the plan, convert it into a matching blocking unknown and an answerable user question, then stop
before Evidence. Never erase uncertainty or invent facts merely to make the goal appear ready.
Return only the requested JSON. Provide concise decision summaries, never hidden chain-of-thought.
""".strip()


def extract_obvious_facts(text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {"rawUserStatement": text.strip()}
    duration = re.search(r"(\d+\s*(?:天|周|个月|月|days?|weeks?|months?))", text, re.I)
    time_budget = re.search(r"((?:每天|每周|daily|weekly)?\s*\d+(?:\.\d+)?\s*(?:小时|分钟|hours?|minutes?))", text, re.I)
    money = re.search(r"(\d+(?:\.\d+)?\s*(?:万|千)?\s*(?:元|人民币|rmb|cny))", text, re.I)
    date_hint = re.search(r"(20\d{2}[年\-/]\d{1,2}(?:[月\-/]\d{1,2}日?)?|\d{1,2}月)", text)
    if duration:
        facts["durationExpression"] = duration.group(1)
    if time_budget:
        facts["timeCommitmentExpression"] = time_budget.group(1).strip()
    if money:
        facts["budgetExpression"] = money.group(1)
    if date_hint:
        facts["dateExpression"] = date_hint.group(1)
    return facts


class GoalModelingAgent:
    name = "Goal Modeling Agent"
    artifact_type = "user_goal_model"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(self, payload: GoalModelingInput) -> AgentResult[UserGoalModel]:
        result = self.model.complete_contract(
            stage="goal_modeling",
            task_type="planning_goal_model",
            feature="cognitive_goal_modeling",
            system=GOAL_MODEL_SYSTEM,
            payload=payload.model_dump(by_alias=True),
            contract_type=UserGoalModel,
            temperature=0.15,
            validation_context={"goalModelingInput": payload.model_dump(by_alias=True)},
        )
        goal = result.artifact
        questions = goal.questions[:3]
        blocking = [item for item in goal.decision_relevant_unknowns if item.priority == "blocking"]
        if blocking and goal.can_proceed_to_evidence:
            goal = goal.model_copy(update={"can_proceed_to_evidence": False, "questions": questions})
        elif len(goal.questions) > 3:
            goal = goal.model_copy(update={"questions": questions})
        return AgentResult(goal, result.model_usage)
