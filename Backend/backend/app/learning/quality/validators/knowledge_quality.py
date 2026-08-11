from __future__ import annotations

import re

from ...contracts import CapabilityGraph, KnowledgeGraph, LearningScope
from .base import QualityEvaluation


class KnowledgeQualityValidator:
    def evaluate(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
    ) -> QualityEvaluation:
        result = QualityEvaluation()
        owner_id = knowledge_graph.artifact_id
        required_outcomes = {
            item.id for item in capability_graph.outcomes if item.importance == "required"
        }
        required_capabilities = {
            item.id
            for item in capability_graph.capabilities
            if item.importance == "required"
        }
        covered_outcomes = {
            outcome_id
            for capability in capability_graph.capabilities
            if capability.importance == "required"
            for outcome_id in capability.outcome_refs
        }
        missing_outcomes = sorted(required_outcomes - covered_outcomes)
        result.add(
            rule="knowledge_coverage",
            passed=not missing_outcomes,
            evidence=missing_outcomes or sorted(covered_outcomes & required_outcomes),
            owner_id=owner_id,
            severity="blocker",
            target_type="learning_outcome",
            target_id=missing_outcomes[0] if missing_outcomes else owner_id,
            description="required outcomes must have required capability coverage",
        )

        covered_capabilities = {
            capability_id
            for node in knowledge_graph.nodes
            if node.importance == "required"
            for capability_id in node.capability_refs
        }
        missing_capabilities = sorted(required_capabilities - covered_capabilities)
        result.add(
            rule="knowledge_coverage",
            passed=not missing_capabilities,
            evidence=missing_capabilities or sorted(covered_capabilities & required_capabilities),
            owner_id=owner_id,
            severity="blocker",
            target_type="capability",
            target_id=missing_capabilities[0] if missing_capabilities else owner_id,
            description="required capabilities must have required knowledge coverage",
        )

        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        prerequisite_edges = [
            (edge.source_knowledge_id, edge.target_knowledge_id)
            for edge in knowledge_graph.edges
            if edge.source_knowledge_id in knowledge_ids
            and edge.target_knowledge_id in knowledge_ids
            and edge.relation == "prerequisite"
        ]
        prerequisite_is_acyclic = self._is_acyclic(
            knowledge_ids,
            prerequisite_edges,
        )
        result.add(
            rule="knowledge_coverage",
            passed=prerequisite_is_acyclic,
            evidence=[source for source, _target in prerequisite_edges],
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge_graph",
            target_id=owner_id,
            description="KnowledgeGraph prerequisite dependencies must be acyclic",
        )
        containment_edges = [
            (edge.source_knowledge_id, edge.target_knowledge_id)
            for edge in knowledge_graph.edges
            if edge.source_knowledge_id in knowledge_ids
            and edge.target_knowledge_id in knowledge_ids
            and edge.relation == "part_of"
        ]
        result.add(
            rule="knowledge_coverage",
            passed=self._is_acyclic(knowledge_ids, containment_edges),
            evidence=[source for source, _target in containment_edges],
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge_graph",
            target_id=owner_id,
            description="KnowledgeGraph containment relationships must be acyclic",
        )

        outcome_ids = {item.id for item in capability_graph.outcomes}
        capability_ids = {item.id for item in capability_graph.capabilities}
        missing_sources = sorted(
            node.id
            for node in knowledge_graph.nodes
            if node.importance == "required"
            and (
                not set(node.capability_refs) <= capability_ids
                or not set(node.outcome_refs) <= outcome_ids
                or not node.capability_refs
                or not node.outcome_refs
            )
        )
        result.add(
            rule="knowledge_coverage",
            passed=not missing_sources,
            evidence=missing_sources
            or sorted(node.id for node in knowledge_graph.nodes if node.importance == "required"),
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge",
            target_id=missing_sources[0] if missing_sources else owner_id,
            description="required knowledge must retain valid capability and outcome lineage",
        )

        known = {
            self._normalize(item)
            for item in [
                *scope.current_level.known_skills,
                *scope.current_level.known_technologies,
            ]
            if self._normalize(item)
        }
        duplicate_nodes = sorted(
            node.id
            for node in knowledge_graph.nodes
            if self._duplicates_known_skill(self._normalize(node.name), known)
        )
        required_count = sum(
            node.importance == "required" for node in knowledge_graph.nodes
        )
        excessive_duplicates = len(duplicate_nodes) > max(1, required_count // 2)
        result.add(
            rule="knowledge_coverage",
            passed=not excessive_duplicates,
            evidence=duplicate_nodes,
            owner_id=owner_id,
            severity="major",
            target_type="current_level",
            target_id=scope.artifact_id,
            description="KnowledgeGraph must not substantially reteach declared existing skills",
        )
        return result

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)

    @staticmethod
    def _duplicates_known_skill(name: str, known: set[str]) -> bool:
        if not name:
            return False
        return any(name == item or (len(name) >= 4 and name in item) for item in known)

    @staticmethod
    def _is_acyclic(nodes: set[str], edges: list[tuple[str, str]]) -> bool:
        outgoing = {node: [] for node in nodes}
        indegree = {node: 0 for node in nodes}
        for source, target in edges:
            outgoing[source].append(target)
            indegree[target] += 1
        ready = [node for node, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            node = ready.pop()
            visited += 1
            for target in outgoing[node]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        return visited == len(nodes)


__all__ = ["KnowledgeQualityValidator"]
