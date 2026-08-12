from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...contracts import CoverageEdge, EvidenceGraph, KnowledgeGraph, SegmentEvidence
from ...generators import LearningModelOutputError, LearningSemanticModel, RouterLearningModel
from ...generators.base import generated_id, require_index
from ...validators import LearningArtifactValidationError
from ..validators import EvidenceValidator
from .schemas import CoverageMappingResponse
from .validators import CoverageMappingValidationError, CoverageMappingValidator


COVERAGE_MAPPING_SYSTEM = """
Map the supplied verified transcript content to the supplied knowledge nodes. The response list is positional: each
response entry belongs to the input segment at the same list position. You may generate only knowledgeId,
coverageType, summary, confidence, reason, and supportedRequirementIndexes. A requirement index may be returned only
when the verified transcript directly supports that supplied requirement. Never generate or return a segment id, evidence id, timestamp,
duration, URL, external id, fingerprint, source range, artifact id, or version. Do not map knowledge unless the
transcript text directly supports it. Return JSON only and do not reveal hidden reasoning.
""".strip()


class CoverageMappingError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


class CoverageMapper:
    """LLM chooses semantics; code binds segment and evidence identities."""

    _CONTENT_EVIDENCE_KINDS = {
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "manual_verified",
    }

    def __init__(
        self,
        model: LearningSemanticModel | None = None,
        *,
        validator: CoverageMappingValidator | None = None,
        evidence_validator: EvidenceValidator | None = None,
    ):
        self.model = model or RouterLearningModel()
        self.evidence_validator = evidence_validator or EvidenceValidator()
        self.validator = validator or CoverageMappingValidator(self.evidence_validator)
        self.model_usage: dict[str, Any] = {}

    def map(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> list[CoverageEdge]:
        try:
            self.model_usage = {}
            self.validator.validate_source(knowledge_graph, evidence_graph)
            evidence = {item.id: item for item in evidence_graph.evidence}
            bound_evidence = [
                self._verified_content_evidence(segment.evidence_refs, evidence)
                for segment in evidence_graph.segments
            ]
            response = self.model.complete(
                stage="learning_coverage_mapping",
                feature="learning_evidence_generation",
                system=COVERAGE_MAPPING_SYSTEM,
                payload={
                    "knowledge": [
                        {
                            "knowledgeId": node.id,
                            "name": node.name,
                            "explanation": node.explanation,
                            "whyRequired": node.why_required,
                            "importance": node.importance,
                            "coverageRequirements": [
                                {"index": index, "statement": item.statement}
                                for index, item in enumerate(node.coverage_requirements)
                            ],
                        }
                        for node in knowledge_graph.nodes
                    ],
                    "segments": [
                        {
                            "transcriptEvidence": [
                                {
                                    "kind": item.kind,
                                    "text": item.source_excerpt or item.supported_claim,
                                    "verificationStatus": item.verification_status,
                                }
                                for item in items
                            ]
                        }
                        for items in bound_evidence
                    ],
                },
                response_type=CoverageMappingResponse,
                max_tokens=3200,
            )
            if len(response.value.segments) != len(evidence_graph.segments):
                raise CoverageMappingError(
                    "coverage_mapping",
                    "model response segment count does not match the supplied segment count",
                )
            edges = self._bind_edges(
                response.value,
                knowledge_graph,
                evidence_graph,
                bound_evidence,
            )
            self.validator.validate_edges(knowledge_graph, evidence_graph, edges)
            self.evidence_validator.validate_coverage(
                knowledge_graph,
                evidence_graph.segments,
                evidence_graph.evidence,
                edges,
            )
            self.model_usage = response.model_usage
            return edges
        except CoverageMappingError:
            raise
        except (
            CoverageMappingValidationError,
            LearningArtifactValidationError,
            LearningModelOutputError,
            ValidationError,
            ValueError,
        ) as exc:
            raise CoverageMappingError("coverage_mapping", str(exc)) from exc

    def _verified_content_evidence(
        self,
        evidence_refs: list[str],
        evidence: dict[str, SegmentEvidence],
    ) -> list[SegmentEvidence]:
        return [
            evidence[evidence_id]
            for evidence_id in evidence_refs
            if evidence_id in evidence
            and evidence[evidence_id].verification_status == "verified"
            and evidence[evidence_id].kind in self._CONTENT_EVIDENCE_KINDS
        ]

    @staticmethod
    def _bind_edges(
        response: CoverageMappingResponse,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        bound_evidence: list[list[SegmentEvidence]],
    ) -> list[CoverageEdge]:
        edges: list[CoverageEdge] = []
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        for segment, segment_response, evidence in zip(
            evidence_graph.segments,
            response.segments,
            bound_evidence,
            strict=True,
        ):
            evidence_refs = [item.id for item in evidence]
            for mapping_index, mapping in enumerate(segment_response.mappings):
                node = knowledge.get(mapping.knowledge_id)
                if node is None:
                    raise CoverageMappingError(
                        "coverage_knowledge_reference",
                        f"mapping references unknown knowledge {mapping.knowledge_id}",
                    )
                supported_requirement_refs = list(
                    dict.fromkeys(
                        node.coverage_requirements[
                            require_index(
                                requirement_index,
                                len(node.coverage_requirements),
                                stage="learning_coverage_mapping",
                                field="supportedRequirementIndexes",
                            )
                        ].id
                        for requirement_index in mapping.supported_requirement_indexes
                    )
                )
                if node.coverage_requirements:
                    all_requirement_refs = {
                        item.id for item in node.coverage_requirements
                    }
                    supported = set(supported_requirement_refs)
                    strength = (
                        "full"
                        if supported == all_requirement_refs
                        else "partial" if supported else "supplementary"
                    )
                else:
                    strength = CoverageMapper._strength(mapping.confidence)
                edges.append(
                    CoverageEdge(
                        id=generated_id(
                            "coverage",
                            segment.id,
                            mapping_index,
                            mapping.knowledge_id,
                        ),
                        knowledgeId=mapping.knowledge_id,
                        segmentId=segment.id,
                        evidenceRefs=evidence_refs,
                        coverageType=mapping.coverage_type,
                        coverageStrength=strength,
                        confidence=mapping.confidence,
                        summary=mapping.summary,
                        reason=mapping.reason,
                        supportedRequirementRefs=supported_requirement_refs,
                    )
                )
        return edges

    @staticmethod
    def _strength(confidence: float) -> str:
        if confidence >= 0.85:
            return "full"
        if confidence >= 0.6:
            return "partial"
        return "supplementary"


__all__ = ["CoverageMapper", "CoverageMappingError"]
