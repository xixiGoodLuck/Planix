from __future__ import annotations

from dataclasses import dataclass
import logging
from time import monotonic
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

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
    LearningGenerationError,
    LearningModelOutputError,
    LearningModelResponse,
    LearningSemanticModel,
    RouterLearningModel,
    artifact_ref,
    generated_id,
    require_index,
)
from ..validators import LearningArtifactValidationError, LearningArtifactValidator


logger = logging.getLogger("planix.learning.knowledge")


KNOWLEDGE_SYSTEM = """
You organize the knowledge needed for the supplied capabilities. Knowledge is a concept, mechanism, or technique that
supports an ability; explain why it supports the referenced capabilities and give observable mastery indicators.
Do not reteach skills explicitly listed as known in currentLevel. Keep the graph bounded to the current outcomes:
required is directly necessary, important improves the result, and optional is only a future extension. Do not add
deployment, distributed systems, or advanced engineering unless the scope or capability explicitly requires it.
Do not introduce setup, language fundamentals, data schemas, persistence, testing, or adjacent features unless a
referenced capability explicitly requires them. A required knowledge node must be necessary for a required
capability; useful background that is not necessary must not be marked required.
Refer to capabilities only by their zero-based input indexes and knowledge nodes only by their zero-based output
indexes. Order knowledge from foundations to later concepts. A prerequisite edge must point from an earlier node to
a later node: sourceIndex must be strictly less than targetIndex. Do not use prerequisite for containment or general
semantic support; use part_of or supports for those meanings. Return semantic fields and index relationships only.
Never return ids, artifact references, versions, timestamps, outcome references, or source references. Return JSON
only and do not reveal hidden reasoning.
""".strip()


KNOWLEDGE_CONTRACT_REPAIR_SYSTEM = f"""
{KNOWLEDGE_SYSTEM}

The previous response failed the output contract. Regenerate the complete draft once. Every edge index must exist in
the returned knowledge list. For prerequisite edges sourceIndex must be strictly less than targetIndex. Do not copy
invalid indexes from the prior response.
""".strip()


KNOWLEDGE_SEMANTIC_REPAIR_SYSTEM = """
You perform one bounded append-only repair of an already valid Learning KnowledgeGraph. Return only new knowledge
nodes needed to cover the explicitly listed missing required capabilities. Never rewrite or delete existing nodes or
edges, change importance, alter outcomes, or change the LearningScope. capabilityIndexes refer to the supplied full
capability list. prerequisiteIndexes refer to the combined topology: existing nodes first, then earlier additions;
each prerequisite index must be strictly earlier than the new node. If the reported problem cannot be fixed by
append-only additions, return an empty additions list. Return JSON only and do not reveal hidden reasoning.
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

    @model_validator(mode="after")
    def validate_edge_indexes(self) -> "KnowledgeDrafts":
        node_count = len(self.knowledge)
        for index, edge in enumerate(self.edges):
            if edge.source_index >= node_count:
                raise ValueError(
                    f"edges[{index}].sourceIndex references {edge.source_index}; "
                    f"available indexes are 0..{node_count - 1}"
                )
            if edge.target_index >= node_count:
                raise ValueError(
                    f"edges[{index}].targetIndex references {edge.target_index}; "
                    f"available indexes are 0..{node_count - 1}"
                )
            if edge.relation == "prerequisite" and edge.source_index >= edge.target_index:
                raise ValueError(
                    f"edges[{index}] violates prior-index-only prerequisite ordering: "
                    f"sourceIndex {edge.source_index} must be less than "
                    f"targetIndex {edge.target_index}"
                )
        return self


class KnowledgeRepairDraft(KnowledgeDraft):
    prerequisite_indexes: list[NonNegativeIndex] = Field(default_factory=list)


class KnowledgeRepairDrafts(LearningContract):
    additions: list[KnowledgeRepairDraft] = Field(default_factory=list, max_length=8)


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
        payload = self._generation_payload(scope, capability_graph)
        traces: list[dict[str, Any]] = []
        try:
            response = self._complete(
                feature="learning_knowledge_generation",
                repair_type="initial",
                attempt=1,
                system=KNOWLEDGE_SYSTEM,
                payload=payload,
                response_type=KnowledgeDrafts,
                traces=traces,
            )
        except (LearningModelOutputError, ValidationError) as exc:
            if not self._is_contract_failure(exc):
                raise
            response = self._complete(
                feature="learning_knowledge_contract_repair",
                repair_type="contract",
                attempt=2,
                system=KNOWLEDGE_CONTRACT_REPAIR_SYSTEM,
                payload={
                    **payload,
                    "contractFailure": self._safe_contract_failure(exc),
                },
                response_type=KnowledgeDrafts,
                traces=traces,
            )
        graph = self._build_graph(scope, capability_graph, response.value)
        usage = dict(response.model_usage)
        usage["calls"] = traces
        usage["contractRepairs"] = sum(
            item["repairType"] == "contract" for item in traces
        )
        usage["graphRepairs"] = 0
        return KnowledgeGenerationResult(knowledge_graph=graph, model_usage=usage)

    def generate_validated(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        validator: LearningArtifactValidator,
    ) -> KnowledgeGenerationResult:
        result = self.generate(scope, capability_graph)
        try:
            validator.validate_knowledge_graph(
                scope,
                capability_graph,
                result.knowledge_graph,
            )
            return result
        except LearningArtifactValidationError as exc:
            if exc.rule not in {
                "knowledge_cycle_prerequisite",
                "knowledge_cycle_containment",
                "required_capability_coverage",
            }:
                raise
            repair_issue = exc
        repaired = self._repair_once(
            scope,
            capability_graph,
            result,
            repair_issue,
        )
        validator.validate_knowledge_graph(
            scope,
            capability_graph,
            repaired.knowledge_graph,
        )
        return repaired

    def _repair_once(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        current: KnowledgeGenerationResult,
        issue: LearningArtifactValidationError,
    ) -> KnowledgeGenerationResult:
        graph = current.knowledge_graph
        required_capability_ids = {
            item.id
            for item in capability_graph.capabilities
            if item.importance == "required"
        }
        covered_capability_ids = {
            capability_id
            for node in graph.nodes
            if node.importance == "required"
            for capability_id in node.capability_refs
        }
        missing_ids = required_capability_ids - covered_capability_ids
        missing_indexes = [
            index
            for index, capability in enumerate(capability_graph.capabilities)
            if capability.id in missing_ids
        ]
        traces = list(current.model_usage.get("calls", []))
        response = self._complete(
            feature="learning_knowledge_graph_repair",
            repair_type="semantic_graph",
            attempt=len(traces) + 1,
            system=KNOWLEDGE_SEMANTIC_REPAIR_SYSTEM,
            payload={
                "issue": {
                    "rule": issue.rule,
                    "target": issue.path,
                    "path": issue.path,
                },
                "missingRequiredCapabilities": [
                    {
                        "index": index,
                        "name": capability_graph.capabilities[index].name,
                        "outcomeStatements": [
                            outcome.statement
                            for outcome in capability_graph.outcomes
                            if outcome.id
                            in capability_graph.capabilities[index].outcome_refs
                        ],
                    }
                    for index in missing_indexes
                ],
                "existingKnowledge": [
                    {
                        "index": index,
                        "name": node.name,
                        "importance": node.importance,
                    }
                    for index, node in enumerate(graph.nodes)
                ],
                "topology": [
                    {
                        "sourceIndex": self._node_index(
                            graph, edge.source_knowledge_id
                        ),
                        "targetIndex": self._node_index(
                            graph, edge.target_knowledge_id
                        ),
                        "relation": edge.relation,
                    }
                    for edge in graph.edges
                ],
                "capabilities": [
                    {
                        "index": index,
                        "name": capability.name,
                        "importance": capability.importance,
                    }
                    for index, capability in enumerate(
                        capability_graph.capabilities
                    )
                ],
            },
            response_type=KnowledgeRepairDrafts,
            max_tokens=1800,
            traces=traces,
        )
        nodes = list(graph.nodes)
        edges = list(graph.edges)
        for addition_index, draft in enumerate(response.value.additions):
            combined_index = len(nodes)
            referenced_capabilities = [
                capability_graph.capabilities[
                    require_index(
                        capability_index,
                        len(capability_graph.capabilities),
                        stage="learning_knowledge_graph_repair",
                        field=(
                            f"additions[{addition_index}].capabilityIndexes"
                        ),
                    )
                ]
                for capability_index in draft.capability_indexes
            ]
            capability_refs = list(
                dict.fromkeys(item.id for item in referenced_capabilities)
            )
            outcome_refs = list(
                dict.fromkeys(
                    outcome_ref
                    for capability in referenced_capabilities
                    for outcome_ref in capability.outcome_refs
                )
            )
            node = KnowledgeNode(
                id=generated_id(
                    "knowledge",
                    graph.artifact_id,
                    combined_index,
                    draft.name,
                ),
                name=draft.name,
                explanation=draft.explanation,
                whyRequired=draft.why_required,
                capabilityRefs=capability_refs,
                outcomeRefs=outcome_refs,
                importance=draft.importance,
                masteryIndicators=draft.mastery_indicators,
            )
            for prerequisite_index in draft.prerequisite_indexes:
                if prerequisite_index >= combined_index:
                    raise LearningGenerationError(
                        "learning_knowledge_graph_repair",
                        f"additions[{addition_index}].prerequisiteIndexes must "
                        "reference an existing or earlier appended node",
                    )
                edges.append(
                    KnowledgeEdge(
                        sourceKnowledgeId=nodes[prerequisite_index].id,
                        targetKnowledgeId=node.id,
                        relation="prerequisite",
                        reason="Append-only knowledge coverage repair prerequisite.",
                    )
                )
            nodes.append(node)

        if issue.rule == "required_capability_coverage":
            repaired_coverage = {
                capability_id
                for node in nodes
                if node.importance == "required"
                for capability_id in node.capability_refs
            }
            if missing_ids - repaired_coverage:
                raise LearningGenerationError(
                    "learning_knowledge_graph_repair",
                    "bounded repair did not cover every missing required capability",
                )

        usage = dict(current.model_usage)
        usage["calls"] = traces
        usage["graphRepairs"] = 1
        usage["semanticRepair"] = response.model_usage
        return KnowledgeGenerationResult(
            knowledge_graph=graph.model_copy(update={"nodes": nodes, "edges": edges}),
            model_usage=usage,
        )

    def _build_graph(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        drafts: KnowledgeDrafts,
    ) -> KnowledgeGraph:
        graph_id = generated_id(
            "knowledge-graph",
            capability_graph.artifact_id,
            capability_graph.version,
            "|".join(item.id for item in capability_graph.capabilities),
        )
        nodes: list[KnowledgeNode] = []
        for index, draft in enumerate(drafts.knowledge):
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
            for index, edge in enumerate(drafts.edges)
        ]
        return KnowledgeGraph(
            artifactId=graph_id,
            scopeRef=artifact_ref("learning_scope", scope),
            capabilityGraphRef=artifact_ref("capability_graph", capability_graph),
            nodes=nodes,
            edges=edges,
        )

    def _generation_payload(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
    ) -> dict[str, Any]:
        return {
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
        }

    def _complete(
        self,
        *,
        feature: str,
        repair_type: str,
        attempt: int,
        system: str,
        payload: dict[str, Any],
        response_type: type[KnowledgeDrafts] | type[KnowledgeRepairDrafts],
        traces: list[dict[str, Any]],
        max_tokens: int = 3600,
    ) -> LearningModelResponse:
        started = monotonic()
        try:
            response = self.model.complete(
                stage="learning_knowledge",
                feature=feature,
                system=system,
                payload=payload,
                response_type=response_type,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            latency_ms = round((monotonic() - started) * 1000, 2)
            trace = {
                "feature": feature,
                "attempt": attempt,
                "repairType": repair_type,
                "latencyMs": latency_ms,
                "resultStatus": "failed",
                "errorType": type(exc).__name__,
            }
            traces.append(trace)
            logger.info("knowledge_model_call %s", trace)
            raise
        latency_ms = round((monotonic() - started) * 1000, 2)
        trace = {
            "feature": feature,
            "attempt": attempt,
            "repairType": repair_type,
            "latencyMs": latency_ms,
            "resultStatus": "completed",
        }
        traces.append(trace)
        logger.info("knowledge_model_call %s", trace)
        return response

    @staticmethod
    def _is_contract_failure(exc: Exception) -> bool:
        if isinstance(exc, ValidationError):
            return True
        message = str(getattr(exc, "message", exc)).casefold()
        return isinstance(exc, LearningModelOutputError) and (
            "contract validation" in message or "not one json object" in message
        )

    @staticmethod
    def _safe_contract_failure(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            errors = exc.errors()
            first = errors[0] if errors else {}
            location = ".".join(str(part) for part in first.get("loc", ()))
            return f"{location or 'root'}: {first.get('msg', 'invalid output')}"
        return str(getattr(exc, "message", "invalid model output"))[:500]

    @staticmethod
    def _node_index(graph: KnowledgeGraph, node_id: str) -> int:
        for index, node in enumerate(graph.nodes):
            if node.id == node_id:
                return index
        raise LearningGenerationError(
            "learning_knowledge_graph_repair",
            f"existing edge references unknown knowledge node {node_id}",
        )


__all__ = [
    "KnowledgeDraft",
    "KnowledgeDrafts",
    "KnowledgeRepairDraft",
    "KnowledgeRepairDrafts",
    "KnowledgeGenerationResult",
    "KnowledgeGenerator",
]
