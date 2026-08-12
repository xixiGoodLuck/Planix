from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field

from ..contracts import (
    CapabilityEdge,
    CapabilityGraph,
    CapabilityNode,
    Importance,
    LearningContract,
    LearningOutcome,
    LearningScope,
)
from ..scope_anchor_semantics import text_matches_concept_anchor
from .base import (
    LearningModelOutputError,
    LearningSemanticModel,
    RouterLearningModel,
    artifact_ref,
    generated_id,
    require_index,
)


CAPABILITY_SYSTEM = """
You derive capabilities from approved learning outcomes. A capability describes what the learner must be able to do,
not a knowledge topic, language feature, framework decorator, or SQL syntax item. Keep the set bounded to the stated
outcomes. Use required for abilities directly necessary for a required outcome, important for useful strengthening,
and optional only for future extension. Do not introduce environment setup, language basics, data modeling,
persistence, testing, or adjacent framework features unless an acceptance criterion explicitly requires them.
Refer to outcomes only by their zero-based input indexes. Return semantic fields and index relationships only. Never
return ids, artifact references, versions, timestamps, or source references. Return JSON only and do not reveal
hidden reasoning.
Every required capability must cite at least one supplied scopeAnchorIndexes entry. Scope anchors are code-owned;
reference them only by zero-based index and never invent anchor text or ids.
When concept anchors are supplied, required capabilities must cite them directly. The broad user_goal anchor cannot
authorize an adjacent implementation ability, example, parameter type, schema, or neighboring feature.
Repeat at least one cited concept anchor text verbatim in each required capability name or description.
For a bounded enumerated concept list, each concept anchor may support at most one required capability. Consolidate
examples and directly necessary details into that capability instead of splitting adjacent required capabilities.
""".strip()


NonNegativeIndex = Annotated[int, Field(ge=0)]


class CapabilityDraft(LearningContract):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    outcome_indexes: list[NonNegativeIndex] = Field(min_length=1)
    importance: Importance
    scope_anchor_indexes: list[NonNegativeIndex] = Field(default_factory=list)

class CapabilityEdgeDraft(LearningContract):
    source_index: NonNegativeIndex
    target_index: NonNegativeIndex
    relation: Literal["prerequisite", "supports"]


class CapabilityDrafts(LearningContract):
    capabilities: list[CapabilityDraft] = Field(min_length=1, max_length=12)
    edges: list[CapabilityEdgeDraft] = Field(default_factory=list, max_length=30)


@dataclass(frozen=True)
class CapabilityGenerationResult:
    capability_graph: CapabilityGraph
    model_usage: dict[str, Any]


class CapabilityGenerator:
    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model or RouterLearningModel()

    def generate(
        self,
        scope: LearningScope,
        outcomes: list[LearningOutcome],
    ) -> CapabilityGenerationResult:
        payload = {
                "scope": {
                    "userGoal": scope.user_goal,
                    "targetResult": (
                        scope.target_result
                        if scope.target_result_status == "explicit"
                        else None
                    ),
                    "targetResultStatus": scope.target_result_status,
                    "currentLevel": scope.current_level.model_dump(by_alias=True),
                },
                "scopeAnchors": [
                    {"index": index, "kind": item.kind, "text": item.text}
                    for index, item in enumerate(scope.explicit_scope_anchors)
                ],
                "outcomes": [
                    {
                        "index": index,
                        "statement": outcome.statement,
                        "acceptanceCriteria": outcome.acceptance_criteria,
                        "importance": outcome.importance,
                    }
                    for index, outcome in enumerate(outcomes)
                ],
            }
        response = self.model.complete(
            stage="learning_capabilities",
            feature="learning_capability_generation",
            system=CAPABILITY_SYSTEM,
            payload=payload,
            response_type=CapabilityDrafts,
            max_tokens=2600,
        )
        if self._missing_required_anchors(scope, response.value):
            response = self.model.complete(
                stage="learning_capabilities",
                feature="learning_capability_anchor_repair",
                system=CAPABILITY_SYSTEM + "\nThis is the single bounded contract regeneration. Return valid anchor indexes and repeat each cited concept anchor text verbatim.",
                payload=payload,
                response_type=CapabilityDrafts,
                max_tokens=2600,
            )
        if self._missing_required_anchors(scope, response.value):
            raise LearningModelOutputError(
                "learning_capabilities",
                "required capability must reference an explicit scope anchor",
            )
        graph_id = generated_id(
            "capability-graph",
            scope.artifact_id,
            scope.version,
            "|".join(item.id for item in outcomes),
        )
        capabilities: list[CapabilityNode] = []
        for index, draft in enumerate(response.value.capabilities):
            outcome_refs = list(
                dict.fromkeys(
                    outcomes[
                        require_index(
                            outcome_index,
                            len(outcomes),
                            stage="learning_capabilities",
                            field=f"capabilities[{index}].outcomeIndexes",
                        )
                    ].id
                    for outcome_index in draft.outcome_indexes
                )
            )
            capabilities.append(
                CapabilityNode(
                    id=generated_id("capability", graph_id, index, draft.name),
                    name=draft.name,
                    description=draft.description,
                    whyRequired=draft.why_required,
                    outcomeRefs=outcome_refs,
                    importance=draft.importance,
                    scopeAnchorRefs=[
                        scope.explicit_scope_anchors[
                            require_index(
                                anchor_index,
                                len(scope.explicit_scope_anchors),
                                stage="learning_capabilities",
                                field=f"capabilities[{index}].scopeAnchorIndexes",
                            )
                        ].id
                        for anchor_index in draft.scope_anchor_indexes
                    ],
                )
            )

        edges = [
            CapabilityEdge(
                sourceCapabilityId=capabilities[
                    require_index(
                        edge.source_index,
                        len(capabilities),
                        stage="learning_capabilities",
                        field=f"edges[{index}].sourceIndex",
                    )
                ].id,
                targetCapabilityId=capabilities[
                    require_index(
                        edge.target_index,
                        len(capabilities),
                        stage="learning_capabilities",
                        field=f"edges[{index}].targetIndex",
                    )
                ].id,
                relation=edge.relation,
            )
            for index, edge in enumerate(response.value.edges)
        ]
        graph = CapabilityGraph(
            artifactId=graph_id,
            scopeRef=artifact_ref("learning_scope", scope),
            outcomes=outcomes,
            capabilities=capabilities,
            edges=edges,
        )
        return CapabilityGenerationResult(
            capability_graph=graph,
            model_usage=response.model_usage,
        )

    @staticmethod
    def _missing_required_anchors(
        scope: LearningScope,
        drafts: CapabilityDrafts,
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
                for item in drafts.capabilities
            )
        referenced = [
            anchor_index
            for item in drafts.capabilities
            if item.importance == "required"
            for anchor_index in set(item.scope_anchor_indexes) & concept_indexes
        ]
        return len(referenced) != len(set(referenced)) or any(
            item.importance == "required"
            and not any(
                anchor_index in concept_indexes
                and anchor_index < len(scope.explicit_scope_anchors)
                and text_matches_concept_anchor(
                    item.name,
                    scope.explicit_scope_anchors[anchor_index].text,
                )
                for anchor_index in item.scope_anchor_indexes
            )
            for item in drafts.capabilities
        )


__all__ = [
    "CapabilityDraft",
    "CapabilityDrafts",
    "CapabilityGenerationResult",
    "CapabilityGenerator",
]
