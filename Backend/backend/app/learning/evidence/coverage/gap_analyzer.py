from __future__ import annotations

from ...contracts import KnowledgeGraph
from .coverage_report import EvidenceCoverageGap, KnowledgeCoverageResult


class GapAnalyzer:
    def analyze(
        self,
        knowledge_graph: KnowledgeGraph,
        coverage: list[KnowledgeCoverageResult],
    ) -> list[EvidenceCoverageGap]:
        coverage_by_id = {item.knowledge_id: item for item in coverage}
        gaps: list[EvidenceCoverageGap] = []
        for node in knowledge_graph.nodes:
            item = coverage_by_id[node.id]
            if item.coverage_strength == "MISSING":
                gaps.append(
                    EvidenceCoverageGap(
                        knowledgeId=node.id,
                        gapType="missing_knowledge",
                        currentStrength=item.coverage_strength,
                        reason="No valid evidence covers this knowledge node.",
                    )
                )
            elif item.coverage_strength == "WEAK":
                gaps.append(
                    EvidenceCoverageGap(
                        knowledgeId=node.id,
                        gapType="weak_coverage",
                        currentStrength=item.coverage_strength,
                        reason="Only low-level or unverified evidence is available.",
                    )
                )
            if node.importance == "required" and item.coverage_strength != "FULL":
                gaps.append(
                    EvidenceCoverageGap(
                        knowledgeId=node.id,
                        gapType="unsupported_required",
                        currentStrength=item.coverage_strength,
                        reason="Required knowledge must have FULL verified transcript coverage.",
                    )
                )
        return gaps


__all__ = ["GapAnalyzer"]
