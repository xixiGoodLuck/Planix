from __future__ import annotations

import re

from ...contracts import KnowledgeGraph
from ..coverage import CoverageReport
from .contracts import RetrievalGapPlan


class RetrievalPlanValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class RetrievalPlanValidator:
    _FORBIDDEN_URL = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|cn|org|net)/", re.I)
    _FORBIDDEN_VIDEO_ID = re.compile(r"(?<![A-Za-z0-9])BV[0-9A-Za-z]{10}(?![A-Za-z0-9])|\bav\d+\b", re.I)
    _FORBIDDEN_TIMESTAMP = re.compile(
        r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b|\b\d+(?:\.\d+)?\s*(?:seconds?|secs?|s|秒|分钟|mins?|minutes?)\b",
        re.I,
    )
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

    def validate(
        self,
        knowledge_graph: KnowledgeGraph,
        coverage_report: CoverageReport,
        plans: list[RetrievalGapPlan],
    ) -> None:
        if (
            coverage_report.knowledge_graph_ref.artifact_id != knowledge_graph.artifact_id
            or coverage_report.knowledge_graph_ref.version != knowledge_graph.version
        ):
            self._fail(
                "retrieval_knowledge_graph_reference",
                "coverageReport.knowledgeGraphRef",
                "coverage report references another knowledge graph version",
            )
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        coverage = {item.knowledge_id: item for item in coverage_report.knowledge_coverage}
        if len(coverage_report.knowledge_coverage) != len(knowledge) or set(coverage) != set(knowledge):
            self._fail(
                "retrieval_coverage_reference",
                "coverageReport.knowledgeCoverage",
                "coverage report must contain every knowledge node exactly once",
            )
        version_conflicts = {item.knowledge_id for item in coverage_report.conflicts}
        context_required = {
            item.knowledge_id
            for item in coverage_report.redundancy
            if item.classification == "CONTEXT_REQUIRED"
        }
        seen: set[tuple[str, str]] = set()
        seen_plan_ids: set[str] = set()
        for index, plan in enumerate(plans):
            path = f"retrievalPlans.{index}"
            if plan.retrieval_plan_id in seen_plan_ids:
                self._fail(
                    "duplicate_retrieval_plan_id",
                    f"{path}.retrievalPlanId",
                    "retrieval plan id is duplicated",
                )
            seen_plan_ids.add(plan.retrieval_plan_id)
            node = knowledge.get(plan.knowledge_id)
            if node is None:
                self._fail(
                    "retrieval_knowledge_reference",
                    f"{path}.knowledgeId",
                    "retrieval gap references missing knowledge",
                )
            identity = (plan.knowledge_id, plan.gap_type)
            if identity in seen:
                self._fail("duplicate_retrieval_gap", path, "retrieval gap is duplicated")
            seen.add(identity)
            expected_priority = self._PRIORITY[node.importance]
            if plan.priority != expected_priority:
                self._fail(
                    "retrieval_priority",
                    f"{path}.priority",
                    f"{node.importance} knowledge requires priority {expected_priority}",
                )
            item = coverage[plan.knowledge_id]
            expected_gap = self._STRENGTH_GAP.get(item.coverage_strength)
            if plan.gap_type in self._STRENGTH_GAP.values():
                if item.coverage_strength == "FULL" or plan.gap_type != expected_gap:
                    self._fail(
                        "retrieval_gap_strength",
                        f"{path}.gapType",
                        "evidence gap does not match the computed coverage strength",
                    )
            elif plan.gap_type == "VERSION_CONFLICT":
                if plan.knowledge_id not in version_conflicts:
                    self._fail(
                        "retrieval_version_conflict",
                        f"{path}.gapType",
                        "knowledge has no reported version conflict",
                    )
            elif plan.gap_type == "INSUFFICIENT_CONTEXT":
                if plan.knowledge_id not in context_required:
                    self._fail(
                        "retrieval_context_gap",
                        f"{path}.gapType",
                        "knowledge has no reported context requirement",
                    )
            expected_level = self._EVIDENCE_LEVEL[plan.gap_type]
            if plan.required_evidence_level != expected_level:
                self._fail(
                    "retrieval_evidence_level",
                    f"{path}.requiredEvidenceLevel",
                    f"gap requires evidence level {expected_level}",
                )
            self._validate_hints(path, plan.query_hints)
        expected = {
            (knowledge_id, self._STRENGTH_GAP[item.coverage_strength])
            for knowledge_id, item in coverage.items()
            if item.coverage_strength in self._STRENGTH_GAP
        }
        expected.update((knowledge_id, "VERSION_CONFLICT") for knowledge_id in version_conflicts)
        expected.update(
            (knowledge_id, "INSUFFICIENT_CONTEXT") for knowledge_id in context_required
        )
        if seen != expected:
            self._fail(
                "retrieval_plan_completeness",
                "retrievalPlans",
                "retrieval plans do not exactly cover the reported evidence gaps",
            )

    def _validate_hints(self, path: str, hints: list[str]) -> None:
        if not hints:
            self._fail("retrieval_query_hint", f"{path}.queryHints", "query hints are required")
        seen: set[str] = set()
        for hint in hints:
            normalized = " ".join(hint.split()).strip()
            if not normalized or normalized.casefold() in seen:
                self._fail(
                    "retrieval_query_hint",
                    f"{path}.queryHints",
                    "query hints must be non-empty and unique",
                )
            seen.add(normalized.casefold())
            if len(normalized) > 160:
                self._fail(
                    "retrieval_query_hint",
                    f"{path}.queryHints",
                    "query hints must not exceed 160 characters",
                )
            if self._FORBIDDEN_URL.search(normalized):
                self._fail(
                    "retrieval_query_url",
                    f"{path}.queryHints",
                    "query hints must not contain URLs",
                )
            if self._FORBIDDEN_VIDEO_ID.search(normalized):
                self._fail(
                    "retrieval_query_video_id",
                    f"{path}.queryHints",
                    "query hints must not contain video identifiers",
                )
            if self._FORBIDDEN_TIMESTAMP.search(normalized):
                self._fail(
                    "retrieval_query_timestamp",
                    f"{path}.queryHints",
                    "query hints must not contain timestamps or durations",
                )

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise RetrievalPlanValidationError(rule, path, message)


__all__ = ["RetrievalPlanValidationError", "RetrievalPlanValidator"]
