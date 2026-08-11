from __future__ import annotations

import pytest

from app.learning.evidence.coverage import (
    CoverageReport,
    KnowledgeCoverageResult,
    SegmentCoverageAnalysis,
    VersionConflict,
    VersionObservation,
)
from app.learning.evidence.retrieval import (
    RetrievalGapPlan,
    RetrievalPlanValidationError,
    RetrievalPlanValidator,
    RetrievalPlanner,
)
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


def _fixture():
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    coverage = {
        "knowledge-routing": ("sufficient", "FULL"),
        "knowledge-pydantic": ("insufficient", "PARTIAL"),
        "knowledge-database": ("missing", "MISSING"),
        "knowledge-crud": ("insufficient", "WEAK"),
    }
    report = CoverageReport(
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        evidenceGraphRef={
            "artifactType": "evidence_graph",
            "artifactId": "evidence-report-retrieval",
            "version": 1,
        },
        knowledgeCoverage=[
            KnowledgeCoverageResult(
                knowledgeId=node.id,
                status=coverage[node.id][0],
                coverageStrength=coverage[node.id][1],
                evidenceRefs=[] if coverage[node.id][1] == "MISSING" else [f"evidence-{node.id}"],
                segmentRefs=[] if coverage[node.id][1] == "MISSING" else [f"segment-{node.id}"],
            )
            for node in knowledge_graph.nodes
        ],
        gaps=[],
        conflicts=[
            VersionConflict(
                knowledgeId="knowledge-routing",
                technology="FastAPI",
                observations=[
                    VersionObservation(
                        version="0.110",
                        resourceRefs=["resource-old"],
                        segmentRefs=["segment-old"],
                    ),
                    VersionObservation(
                        version="0.115",
                        resourceRefs=["resource-new"],
                        segmentRefs=["segment-new"],
                    ),
                ],
                reason="Two FastAPI versions are present.",
            )
        ],
        redundancy=[],
    )
    return knowledge_graph, report


def _plans():
    knowledge_graph, report = _fixture()
    plans = RetrievalPlanner().plan(knowledge_graph, report)
    return knowledge_graph, report, plans


def _find(plans, knowledge_id: str, gap_type: str):
    return next(
        item
        for item in plans
        if item.knowledge_id == knowledge_id and item.gap_type == gap_type
    )


def test_missing_knowledge_generates_missing_evidence_plan() -> None:
    _, _, plans = _plans()
    plan = _find(plans, "knowledge-database", "MISSING_EVIDENCE")

    assert plan.priority == "HIGH"
    assert plan.required_evidence_level == "verified_transcript"


def test_partial_coverage_generates_partial_retrieval_plan() -> None:
    _, _, plans = _plans()
    plan = _find(plans, "knowledge-pydantic", "PARTIAL_COVERAGE")

    assert plan.priority == "HIGH"
    assert "partially covered" in plan.reason


def test_version_conflict_generates_metadata_retrieval_plan() -> None:
    _, _, plans = _plans()
    plan = _find(plans, "knowledge-routing", "VERSION_CONFLICT")

    assert plan.required_evidence_level == "updated_source_metadata"
    assert "FastAPI" in plan.reason


def test_full_coverage_does_not_generate_an_evidence_gap() -> None:
    _, _, plans = _plans()
    routing = [item for item in plans if item.knowledge_id == "knowledge-routing"]

    assert [item.gap_type for item in routing] == ["VERSION_CONFLICT"]


def test_query_hints_are_nonempty_search_phrases_only() -> None:
    _, _, plans = _plans()
    plan = _find(plans, "knowledge-pydantic", "PARTIAL_COVERAGE")

    assert len(plan.query_hints) >= 2
    assert all("Pydantic" in hint for hint in plan.query_hints)
    assert all("http" not in hint.casefold() and "BV" not in hint for hint in plan.query_hints)


def test_priority_is_code_owned_by_knowledge_importance() -> None:
    knowledge_graph, report = _fixture()
    nodes = [
        node.model_copy(update={"importance": "important"})
        if node.id == "knowledge-database"
        else node.model_copy(update={"importance": "optional"})
        if node.id == "knowledge-crud"
        else node
        for node in knowledge_graph.nodes
    ]
    knowledge_graph = knowledge_graph.model_copy(update={"nodes": nodes})
    plans = RetrievalPlanner().plan(knowledge_graph, report)

    assert _find(plans, "knowledge-database", "MISSING_EVIDENCE").priority == "MEDIUM"
    assert _find(plans, "knowledge-crud", "WEAK_EVIDENCE").priority == "LOW"


def test_context_requirement_generates_context_retrieval_plan() -> None:
    knowledge_graph, report = _fixture()
    relationship = SegmentCoverageAnalysis(
        knowledgeId="knowledge-pydantic",
        classification="CONTEXT_REQUIRED",
        segmentRefs=["segment-context", "segment-knowledge-pydantic"],
        evidenceRefs=["evidence-knowledge-pydantic"],
        reason="The mapped segment depends on prerequisite context.",
    )
    report = report.model_copy(update={"redundancy": [relationship]})
    plans = RetrievalPlanner().plan(knowledge_graph, report)
    plan = _find(plans, "knowledge-pydantic", "INSUFFICIENT_CONTEXT")

    assert plan.required_evidence_level == "additional_context_transcript"


def test_retrieval_plan_referencing_missing_knowledge_is_rejected() -> None:
    knowledge_graph, report = _fixture()
    invalid = RetrievalGapPlan(
        retrievalPlanId="retrieval-plan-missing-knowledge",
        knowledgeId="knowledge-missing",
        gapType="MISSING_EVIDENCE",
        priority="HIGH",
        reason="Invalid fixture",
        requiredEvidenceLevel="verified_transcript",
        queryHints=["Missing knowledge tutorial"],
        constraints=["Search hints only"],
    )

    with pytest.raises(RetrievalPlanValidationError, match="retrieval_knowledge_reference"):
        RetrievalPlanValidator().validate(knowledge_graph, report, [invalid])


def test_full_coverage_cannot_forge_missing_evidence_plan() -> None:
    knowledge_graph, report = _fixture()
    invalid = RetrievalGapPlan(
        retrievalPlanId="retrieval-plan-forged-missing",
        knowledgeId="knowledge-routing",
        gapType="MISSING_EVIDENCE",
        priority="HIGH",
        reason="Forged missing gap",
        requiredEvidenceLevel="verified_transcript",
        queryHints=["FastAPI Routing tutorial"],
        constraints=["Search hints only"],
    )

    with pytest.raises(RetrievalPlanValidationError, match="retrieval_gap_strength"):
        RetrievalPlanValidator().validate(knowledge_graph, report, [invalid])


class UnsafeModelQueryHintGenerator:
    def __init__(self, hint: str):
        self.hint = hint

    def generate(self, *_args) -> list[str]:
        return [self.hint]


@pytest.mark.parametrize(
    ("hint", "expected_rule"),
    [
        ("https://www.bilibili.com/video/example", "retrieval_query_url"),
        ("FastAPI BV1zV2QBtE39", "retrieval_query_video_id"),
        ("FastAPI Routing 10:30", "retrieval_query_timestamp"),
    ],
)
def test_model_generated_resource_locator_is_rejected(
    hint: str,
    expected_rule: str,
) -> None:
    knowledge_graph, report = _fixture()
    planner = RetrievalPlanner(
        query_hint_generator=UnsafeModelQueryHintGenerator(hint)
    )

    with pytest.raises(RetrievalPlanValidationError, match=expected_rule):
        planner.plan(knowledge_graph, report)
