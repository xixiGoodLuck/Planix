from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import CognitiveContract


class ConversationTurn(CognitiveContract):
    role: Literal["user", "assistant", "system"]
    content: str


class MemoryHint(CognitiveContract):
    source_id: str | None = None
    kind: str
    statement: str
    confidence: float = Field(default=0.5, ge=0, le=1)


class Constraint(CognitiveContract):
    statement: str
    source_text: str = ""
    category: str = "general"


class Preference(CognitiveContract):
    statement: str
    source_text: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class KnownFact(CognitiveContract):
    key: str
    statement: str
    source_text: str = ""
    confidence: float = Field(default=1, ge=0, le=1)


class DecisionRelevantUnknown(CognitiveContract):
    key: str
    description: str
    why_it_changes_the_plan: str
    impact: Literal[
        "strategy",
        "safety",
        "feasibility",
        "schedule",
        "resources",
        "success_criteria",
    ]
    priority: Literal["blocking", "important", "optional"]


class GoalAssumption(CognitiveContract):
    statement: str
    confidence: float = Field(ge=0, le=1)
    needs_user_confirmation: bool = False


class GoalSuccessModel(CognitiveContract):
    definition: str
    measurable_signals: list[str] = Field(default_factory=list)
    intermediate_milestones: list[str] = Field(default_factory=list)


class FeasibilityJudgment(CognitiveContract):
    summary: str
    risks: list[str] = Field(default_factory=list)
    unrealistic_parts: list[str] = Field(default_factory=list)


class GoalQuestion(CognitiveContract):
    question: str = Field(min_length=1)
    why_this_question_matters: str = Field(min_length=1)
    expected_decision_impact: str = Field(min_length=1)
    answer_options: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("answer_options", mode="before")
    @classmethod
    def normalize_answer_options(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = str(item or "").strip()
            key = cleaned.casefold()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
            if len(result) == 4:
                break
        return result


_GOAL_INPUT_UNIT = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|\d+(?:\.\d+)?")
_GOAL_INPUT_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_GOAL_INPUT_CLAUSE = re.compile(r"[，,。.!！？?；;:\n]+")


def _initial_sparse_input_requires_clarification(info: ValidationInfo) -> bool:
    """Bind readiness to typed input evidence, not only model-authored labels."""

    context = info.context if isinstance(info.context, dict) else {}
    raw_input = context.get("goalModelingInput")
    if not isinstance(raw_input, dict):
        return False
    if raw_input.get("previousGoalModel") or raw_input.get("previous_goal_model"):
        return False

    history = raw_input.get("conversationHistory") or raw_input.get("conversation_history") or []
    if not isinstance(history, list):
        return False
    user_turns = [
        str(turn.get("content") or "").strip()
        for turn in history
        if isinstance(turn, dict) and turn.get("role") == "user" and str(turn.get("content") or "").strip()
    ]
    if len(user_turns) != 1:
        return False

    pre_extracted = raw_input.get("preExtractedFacts") or raw_input.get("pre_extracted_facts") or {}
    if not isinstance(pre_extracted, dict):
        return False
    understanding = pre_extracted.get("goalUnderstanding") or pre_extracted.get("goal_understanding") or {}
    if not isinstance(understanding, dict):
        return False
    warnings = understanding.get("consistencyWarnings") or understanding.get("consistency_warnings") or []
    if warnings:
        return True
    intent_state = str(
        understanding.get("intentState") or understanding.get("intent_state") or ""
    ).strip()
    if intent_state not in {"clear_goal", "ambiguous_goal"}:
        return False

    # This is intentionally derived from the user-authored surface, not from
    # model-authored facts, constraints, preferences, or uncertainties. A
    # model can split one short sentence into several facts or mislabel one
    # phrase as a constraint, but it cannot thereby add independent user
    # evidence. Two numeric commitments or several explicit clauses are a
    # conservative signal that the first turn has real planning shape.
    text = user_turns[0]
    unit_count = len(_GOAL_INPUT_UNIT.findall(text))
    number_count = len(_GOAL_INPUT_NUMBER.findall(text))
    clause_count = len([part for part in _GOAL_INPUT_CLAUSE.split(text) if part.strip()])
    has_independent_planning_shape = (
        unit_count > 24
        or clause_count > 2
        or number_count >= 3
        or (number_count >= 2 and clause_count >= 2)
    )
    return not has_independent_planning_shape


class UserGoalModel(CognitiveContract):
    goal_statement: str
    desired_change: str
    domain: str
    subdomain: str | None = None
    possible_intents: list[str] = Field(default_factory=list)
    current_knowledge: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    consistency_warnings: list[str] = Field(default_factory=list)
    user_language: list[str] = Field(default_factory=list)
    hard_constraints: list[Constraint] = Field(default_factory=list)
    soft_preferences: list[Preference] = Field(default_factory=list)
    known_facts: list[KnownFact] = Field(default_factory=list)
    decision_relevant_unknowns: list[DecisionRelevantUnknown] = Field(default_factory=list)
    assumptions: list[GoalAssumption] = Field(default_factory=list)
    success_model: GoalSuccessModel
    feasibility_judgment: FeasibilityJudgment = Field(
        default_factory=lambda: FeasibilityJudgment(summary="Deferred to Reality Agent")
    )
    questions: list[GoalQuestion] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    can_proceed_to_evidence: bool

    @model_validator(mode="after")
    def blocked_goal_requires_a_user_question(self, info: ValidationInfo) -> "UserGoalModel":
        blocking_unknowns = [item for item in self.decision_relevant_unknowns if item.priority == "blocking"]
        unresolved_uncertainties = [item.strip() for item in self.uncertainties if item.strip()]
        directly_sourced_facts = [
            item for item in self.known_facts if item.source_text.strip()
        ]
        distinct_fact_sources = {
            item.source_text.strip().casefold()
            for item in directly_sourced_facts
        }
        unsupported_fact_fragments_claim_readiness = bool(
            self.known_facts
            and len(distinct_fact_sources) <= 1
            and not self.hard_constraints
            and not self.soft_preferences
        )
        unresolved_sparse_goal_claims_readiness = bool(
            unresolved_uncertainties
            and len(directly_sourced_facts) <= 2
            and not self.hard_constraints
            and not self.soft_preferences
        )
        sparse_goal_claims_readiness = (
            self.can_proceed_to_evidence
            and not self.questions
            and (
                (
                    unsupported_fact_fragments_claim_readiness
                    and not self.decision_relevant_unknowns
                )
                or unresolved_sparse_goal_claims_readiness
            )
        )
        input_requires_clarification = _initial_sparse_input_requires_clarification(info)
        input_clarification_is_incomplete = (
            self.can_proceed_to_evidence
            or (
                not self.consistency_warnings
                and (not self.questions or not blocking_unknowns)
            )
        )
        if input_requires_clarification and input_clarification_is_incomplete:
            raise ValueError(
                "initial single-turn input lacks enough independent user-authored planning commitments to "
                "claim evidence readiness; splitting one user turn into fact fragments or relabeling one "
                "phrase as a hard constraint does not add evidence, so add a matching blocking "
                "decision-relevant unknown, ask at least one decision-shaping question, and set "
                "canProceedToEvidence=false"
            )
        if sparse_goal_claims_readiness:
            raise ValueError(
                "a sparse goal without enough decision-shaping evidence cannot claim evidence readiness; "
                "preserve any uncertainties, convert at least one material uncertainty into a blocking "
                "decision-relevant unknown with an answerable user question, and set "
                "canProceedToEvidence=false without inventing facts"
            )
        if blocking_unknowns and self.can_proceed_to_evidence and not self.consistency_warnings:
            raise ValueError("blocking decision-relevant unknowns must stop evidence planning")
        if not self.can_proceed_to_evidence and not self.questions and not self.consistency_warnings:
            raise ValueError("a blocked goal model must ask at least one decision-relevant question")
        return self


class GoalModelingInput(CognitiveContract):
    conversation_history: list[ConversationTurn]
    previous_goal_model: UserGoalModel | None = None
    pre_extracted_facts: dict[str, Any] = Field(default_factory=dict)
    relevant_memory_hints: list[MemoryHint] = Field(default_factory=list)
