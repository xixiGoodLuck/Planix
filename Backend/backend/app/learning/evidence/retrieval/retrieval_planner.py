from __future__ import annotations

from typing import Protocol

from ...contracts import KnowledgeGraph, KnowledgeNode
from ...generators.base import generated_id
from ..coverage import CoverageReport
from .contracts import RetrievalGapPlan, RetrievalGapType
from .validators import RetrievalPlanValidator


class QueryHintGenerator(Protocol):
    def generate(self, knowledge: KnowledgeNode, gap_type: RetrievalGapType) -> list[str]: ...


class DeterministicQueryHintGenerator:
    _INTENT = {
        "MISSING_EVIDENCE": ("complete tutorial", "完整教程"),
        "WEAK_EVIDENCE": ("in-depth tutorial", "深入讲解"),
        "PARTIAL_COVERAGE": ("implementation tutorial", "完整实现教程"),
        "VERSION_CONFLICT": ("latest version tutorial", "最新版本教程"),
        "INSUFFICIENT_CONTEXT": ("prerequisites tutorial", "前置知识教程"),
    }

    def generate(self, knowledge: KnowledgeNode, gap_type: RetrievalGapType) -> list[str]:
        english_intent, chinese_intent = self._INTENT[gap_type]
        hints = [
            f"{knowledge.name} {english_intent}",
            f"{knowledge.name} {chinese_intent}",
        ]
        if knowledge.mastery_indicators:
            hints.append(f"{knowledge.name} {knowledge.mastery_indicators[0]} tutorial")
        return hints


class RetrievalPlanner:
    _PRIORITY = {"required": "HIGH", "important": "MEDIUM", "optional": "LOW"}
    _STRENGTH_GAP = {
        "MISSING": "MISSING_EVIDENCE",
        "WEAK": "WEAK_EVIDENCE",
        "PARTIAL": "PARTIAL_COVERAGE",
    }
    _EVIDENCE_LEVEL = {
        "MISSING_EVIDENCE": "verified_transcript",
        "WEAK_EVIDENCE": "verified_transcript",
        "PARTIAL_COVERAGE": "verified_transcript",
        "VERSION_CONFLICT": "updated_source_metadata",
        "INSUFFICIENT_CONTEXT": "additional_context_transcript",
    }

    def __init__(
        self,
        query_hint_generator: QueryHintGenerator | None = None,
        *,
        validator: RetrievalPlanValidator | None = None,
    ):
        self.query_hint_generator = query_hint_generator or DeterministicQueryHintGenerator()
        self.validator = validator or RetrievalPlanValidator()

    def plan(
        self,
        knowledge_graph: KnowledgeGraph,
        coverage_report: CoverageReport,
    ) -> list[RetrievalGapPlan]:
        coverage = {item.knowledge_id: item for item in coverage_report.knowledge_coverage}
        conflicts = {item.knowledge_id: item for item in coverage_report.conflicts}
        context_required = {
            item.knowledge_id
            for item in coverage_report.redundancy
            if item.classification == "CONTEXT_REQUIRED"
        }
        plans: list[RetrievalGapPlan] = []
        for node in knowledge_graph.nodes:
            item = coverage.get(node.id)
            if item is not None and item.coverage_strength in self._STRENGTH_GAP:
                gap_type = self._STRENGTH_GAP[item.coverage_strength]
                plans.append(
                    self._build(
                        node,
                        gap_type,
                        reason=self._strength_reason(node, item.coverage_strength),
                    )
                )
            if node.id in conflicts:
                technologies = ", ".join(
                    sorted({item.technology for item in coverage_report.conflicts if item.knowledge_id == node.id})
                )
                plans.append(
                    self._build(
                        node,
                        "VERSION_CONFLICT",
                        reason=f"Evidence uses conflicting {technologies} versions; current metadata is required.",
                    )
                )
            if node.id in context_required:
                plans.append(
                    self._build(
                        node,
                        "INSUFFICIENT_CONTEXT",
                        reason="Covered segments depend on additional context evidence.",
                    )
                )
        self.validator.validate(knowledge_graph, coverage_report, plans)
        return plans

    def _build(
        self,
        knowledge: KnowledgeNode,
        gap_type: RetrievalGapType,
        *,
        reason: str,
    ) -> RetrievalGapPlan:
        return RetrievalGapPlan(
            retrievalPlanId=generated_id("retrieval-plan", knowledge.id, 0, gap_type),
            knowledgeId=knowledge.id,
            gapType=gap_type,
            priority=self._PRIORITY[knowledge.importance],
            reason=reason,
            requiredEvidenceLevel=self._EVIDENCE_LEVEL[gap_type],
            queryHints=self.query_hint_generator.generate(knowledge, gap_type),
            constraints=self._constraints(gap_type),
        )

    @staticmethod
    def _strength_reason(knowledge: KnowledgeNode, strength: str) -> str:
        if strength == "MISSING":
            return f"{knowledge.name} has no supporting evidence."
        if strength == "WEAK":
            return f"{knowledge.name} has only weak evidence and needs verified transcript support."
        return f"{knowledge.name} is only partially covered and needs evidence for the remaining concepts."

    @staticmethod
    def _constraints(gap_type: RetrievalGapType) -> list[str]:
        constraints = [
            "Search hints only; do not preselect a resource.",
            "Do not include a URL, video identifier, timestamp, or duration.",
        ]
        if gap_type == "VERSION_CONFLICT":
            constraints.append("Prefer current, explicitly versioned source metadata.")
        elif gap_type == "INSUFFICIENT_CONTEXT":
            constraints.append("Evidence must explain the missing prerequisite context.")
        else:
            constraints.append("Evidence must contain a verified transcript span.")
        return constraints


__all__ = [
    "DeterministicQueryHintGenerator",
    "QueryHintGenerator",
    "RetrievalPlanner",
]
