from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from ..contracts import Importance, LearningContract, LearningOutcome, LearningScope
from ..scope_anchor_semantics import text_matches_concept_anchor
from .base import (
    LearningModelOutputError,
    LearningSemanticModel,
    RouterLearningModel,
    generated_id,
    require_index,
)


OUTCOME_SYSTEM = """
You generate learning outcomes from one approved LearningScope. Decompose only the explicit userGoal and
targetResult when its provenance is explicit. Do not introduce deployment, distributed systems, advanced engineering, certification, or another
goal unless the scope explicitly asks for it; a genuinely useful future extension may only be optional. Every
outcome needs observable acceptance criteria. Use required for the direct goal, important for a useful enhancement,
and optional for a future extension. Every required or important outcome must be directly traceable to an explicit
phrase in userGoal or targetResult. Do not turn typical framework prerequisites, setup steps, examples, or adjacent
features into additional outcomes unless the scope names them. When a goal enumerates a bounded set of concepts,
treat that list as the boundary. When targetResult is narrower than userGoal, targetResult is the authoritative
planning boundary and the broader topic must not add required or important outcomes. For an ambiguous goal, stay
conservative and use only assumptions already present
in the scope. If the target asks only to learn, understand, explain, or distinguish named concepts, never convert
that into implementation work: do not require defining routes, writing handlers, processing parameters or request
bodies, building an app, or testing an API. In that case, acceptance criteria must stay at explain, identify,
compare, or distinguish. Return semantic fields only. Never return ids, artifact references, versions, timestamps, or source
references. Return JSON only and do not reveal hidden reasoning.
Every required outcome must cite at least one supplied scopeAnchorIndexes entry. Scope anchors are code-owned;
reference them only by their zero-based indexes and never invent anchor text or ids.
When concept anchors are supplied, every required outcome must cite a concept anchor; the broad user_goal anchor
does not authorize adjacent required content. A concept citation authorizes only an outcome directly about that
named concept, not a common implementation detail, example, parameter type, schema, or neighboring feature.
Repeat the cited concept anchor text verbatim in each required outcome statement so code can verify the semantic
binding without guessing synonyms or translations.
For a bounded enumerated concept list, each concept anchor may support at most one required outcome. Consolidate
examples and directly necessary details into that outcome instead of splitting adjacent required outcomes.
""".strip()


NonNegativeIndex = Annotated[int, Field(ge=0)]


class LearningOutcomeDraft(LearningContract):
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=6)
    importance: Importance
    scope_anchor_indexes: list[NonNegativeIndex] = Field(default_factory=list)

class LearningOutcomeDrafts(LearningContract):
    outcomes: list[LearningOutcomeDraft] = Field(min_length=1, max_length=5)


@dataclass(frozen=True)
class OutcomeGenerationResult:
    outcomes: list[LearningOutcome]
    model_usage: dict[str, Any]


class LearningOutcomeGenerator:
    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model or RouterLearningModel()

    def generate(self, scope: LearningScope) -> OutcomeGenerationResult:
        payload = {
                "userGoal": scope.user_goal,
                "targetResult": (
                    scope.target_result
                    if scope.target_result_status == "explicit"
                    else None
                ),
                "targetResultStatus": scope.target_result_status,
                "scopeAnchors": [
                    {"index": index, "kind": item.kind, "text": item.text}
                    for index, item in enumerate(scope.explicit_scope_anchors)
                ],
                "currentLevel": scope.current_level.model_dump(by_alias=True),
                "assumptions": [item.model_dump(by_alias=True) for item in scope.assumptions],
                "unknowns": [item.model_dump(by_alias=True) for item in scope.unknowns],
                "confirmed": scope.confirmed,
            }
        response = self.model.complete(
            stage="learning_outcomes",
            feature="learning_outcome_generation",
            system=OUTCOME_SYSTEM,
            payload=payload,
            response_type=LearningOutcomeDrafts,
            max_tokens=1800,
        )
        if self._missing_required_anchors(scope, response.value):
            response = self.model.complete(
                stage="learning_outcomes",
                feature="learning_outcome_anchor_repair",
                system=OUTCOME_SYSTEM + "\nThis is the single bounded contract regeneration. Return valid anchor indexes and repeat each cited concept anchor text verbatim.",
                payload=payload,
                response_type=LearningOutcomeDrafts,
                max_tokens=1800,
            )
        if self._missing_required_anchors(scope, response.value):
            raise LearningModelOutputError(
                "learning_outcomes",
                "required outcome must reference an explicit scope anchor",
            )
        outcomes = [
            LearningOutcome(
                id=generated_id("outcome", scope.artifact_id, index, draft.statement),
                statement=draft.statement,
                acceptanceCriteria=draft.acceptance_criteria,
                importance=draft.importance,
                sourceGoalRefs=[scope.artifact_id],
                scopeAnchorRefs=[
                    scope.explicit_scope_anchors[
                        require_index(
                            anchor_index,
                            len(scope.explicit_scope_anchors),
                            stage="learning_outcomes",
                            field=f"outcomes[{index}].scopeAnchorIndexes",
                        )
                    ].id
                    for anchor_index in draft.scope_anchor_indexes
                ],
            )
            for index, draft in enumerate(response.value.outcomes)
        ]
        return OutcomeGenerationResult(outcomes=outcomes, model_usage=response.model_usage)

    @staticmethod
    def _missing_required_anchors(
        scope: LearningScope,
        drafts: LearningOutcomeDrafts,
    ) -> bool:
        concept_indexes = {
            index
            for index, anchor in enumerate(scope.explicit_scope_anchors)
            if anchor.kind == "concept"
        }
        if not concept_indexes:
            return bool(scope.explicit_scope_anchors) and any(
                item.importance == "required"
                and not any(
                    anchor_index < len(scope.explicit_scope_anchors)
                    for anchor_index in item.scope_anchor_indexes
                )
                for item in drafts.outcomes
            )
        referenced = [
            anchor_index
            for item in drafts.outcomes
            if item.importance == "required"
            for anchor_index in set(item.scope_anchor_indexes) & concept_indexes
        ]
        return len(referenced) != len(set(referenced)) or any(
            item.importance == "required"
            and not any(
                anchor_index in concept_indexes
                and anchor_index < len(scope.explicit_scope_anchors)
                and text_matches_concept_anchor(
                    item.statement,
                    scope.explicit_scope_anchors[anchor_index].text,
                )
                for anchor_index in item.scope_anchor_indexes
            )
            for item in drafts.outcomes
        )


__all__ = [
    "LearningOutcomeDraft",
    "LearningOutcomeDrafts",
    "LearningOutcomeGenerator",
    "OutcomeGenerationResult",
]
