from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field

from ..contracts import (
    CapabilityGraph,
    Importance,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    LearningContract,
    LearningScope,
)
from .base import (
    LearningSemanticModel,
    RouterLearningModel,
    artifact_ref,
    generated_id,
    require_index,
)


KNOWLEDGE_SYSTEM = """
You organize the knowledge needed for the supplied capabilities. Knowledge is a concept, mechanism, or technique that
supports an ability; explain why it supports the referenced capabilities and give observable mastery indicators.
Do not reteach skills explicitly listed as known in currentLevel. Keep the graph bounded to the current outcomes:
required is directly necessary, important improves the result, and optional is only a future extension. Do not add
deployment, distributed systems, or advanced engineering unless the scope or capability explicitly requires it.
Refer to capabilities only by their zero-based input indexes and knowledge nodes only by their zero-based output
indexes. Return semantic fields and index relationships only. Never return ids, artifact references, versions,
timestamps, outcome references, or source references. Return JSON only and do not reveal hidden reasoning.
""".strip()


NonNegativeIndex = Annotated[int, Field(ge=0)]


class KnowledgeDraft(LearningContract):
    name: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    capability_indexes: list[NonNegativeIndex] = Field(min_length=1)
    importance: Importance
    mastery_indicators: list[str] = Field(min_length=1, max_length=6)


class KnowledgeEdgeDraft(LearningContract):
    source_index: NonNegativeIndex
    target_index: NonNegativeIndex
    relation: Literal["prerequisite", "supports", "part_of", "optional_extension"]
    reason: str = Field(min_length=1)


class KnowledgeDrafts(LearningContract):
    knowledge: list[KnowledgeDraft] = Field(min_length=1, max_length=24)
    edges: list[KnowledgeEdgeDraft] = Field(default_factory=list, max_length=60)


@dataclass(frozen=True)
class KnowledgeGenerationResult:
    knowledge_graph: KnowledgeGraph
    model_usage: dict[str, Any]


class KnowledgeGenerator:
    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model or RouterLearningModel()

    def generate(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
    ) -> KnowledgeGenerationResult:
        response = self.model.complete(
            stage="learning_knowledge",
            feature="learning_knowledge_generation",
            system=KNOWLEDGE_SYSTEM,
            payload={
                "scope": {
                    "userGoal": scope.user_goal,
                    "targetResult": scope.target_result,
                    "currentLevel": scope.current_level.model_dump(by_alias=True),
                    "assumptions": [
                        item.model_dump(by_alias=True) for item in scope.assumptions
                    ],
                },
                "outcomes": [
                    {
                        "statement": outcome.statement,
                        "importance": outcome.importance,
                    }
                    for outcome in capability_graph.outcomes
                ],
                "capabilities": [
                    {
                        "index": index,
                        "name": capability.name,
                        "description": capability.description,
                        "whyRequired": capability.why_required,
                        "importance": capability.importance,
                    }
                    for index, capability in enumerate(capability_graph.capabilities)
                ],
            },
            response_type=KnowledgeDrafts,
            max_tokens=3600,
        )
        graph_id = generated_id(
            "knowledge-graph",
            capability_graph.artifact_id,
            capability_graph.version,
            "|".join(item.id for item in capability_graph.capabilities),
        )
        nodes: list[KnowledgeNode] = []
        for index, draft in enumerate(response.value.knowledge):
            referenced_capabilities = [
                capability_graph.capabilities[
                    require_index(
                        capability_index,
                        len(capability_graph.capabilities),
                        stage="learning_knowledge",
                        field=f"knowledge[{index}].capabilityIndexes",
                    )
                ]
                for capability_index in draft.capability_indexes
            ]
            capability_refs = list(dict.fromkeys(item.id for item in referenced_capabilities))
            outcome_refs = list(
                dict.fromkeys(
                    outcome_ref
                    for capability in referenced_capabilities
                    for outcome_ref in capability.outcome_refs
                )
            )
            nodes.append(
                KnowledgeNode(
                    id=generated_id("knowledge", graph_id, index, draft.name),
                    name=draft.name,
                    explanation=draft.explanation,
                    whyRequired=draft.why_required,
                    capabilityRefs=capability_refs,
                    outcomeRefs=outcome_refs,
                    importance=draft.importance,
                    masteryIndicators=draft.mastery_indicators,
                )
            )

        edges = [
            KnowledgeEdge(
                sourceKnowledgeId=nodes[
                    require_index(
                        edge.source_index,
                        len(nodes),
                        stage="learning_knowledge",
                        field=f"edges[{index}].sourceIndex",
                    )
                ].id,
                targetKnowledgeId=nodes[
                    require_index(
                        edge.target_index,
                        len(nodes),
                        stage="learning_knowledge",
                        field=f"edges[{index}].targetIndex",
                    )
                ].id,
                relation=edge.relation,
                reason=edge.reason,
            )
            for index, edge in enumerate(response.value.edges)
        ]
        graph = KnowledgeGraph(
            artifactId=graph_id,
            scopeRef=artifact_ref("learning_scope", scope),
            capabilityGraphRef=artifact_ref("capability_graph", capability_graph),
            nodes=nodes,
            edges=edges,
        )
        return KnowledgeGenerationResult(
            knowledge_graph=graph,
            model_usage=response.model_usage,
        )


__all__ = [
    "KnowledgeDraft",
    "KnowledgeDrafts",
    "KnowledgeGenerationResult",
    "KnowledgeGenerator",
]
