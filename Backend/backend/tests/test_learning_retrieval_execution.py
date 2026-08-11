from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.learning.contracts import EvidenceGraph
from app.learning.evidence.retrieval import (
    CandidateValidationError,
    CandidateValidator,
    RetrievalExecutionError,
    RetrievalExecutor,
    RetrievalGapPlan,
    RetrievalRequest,
)

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


def _plan() -> RetrievalGapPlan:
    return RetrievalGapPlan(
        retrievalPlanId="retrieval-plan-routing",
        knowledgeId="knowledge-routing",
        gapType="MISSING_EVIDENCE",
        priority="HIGH",
        reason="Routing requires verified transcript evidence.",
        requiredEvidenceLevel="verified_transcript",
        queryHints=["FastAPI Routing complete tutorial"],
        constraints=[
            "Search hints only; do not preselect a resource.",
            "Do not include a URL, video identifier, timestamp, or duration.",
        ],
    )


def _execute():
    fixture = build_fastapi_crud_evidence_fixture()
    plan = _plan()
    request = RetrievalRequest.from_plan(plan)
    executor = RetrievalExecutor(fixture.provider, [plan])
    candidates = executor.execute(request)
    return fixture, plan, request, candidates


def test_retrieval_gap_plan_creates_traceable_request() -> None:
    plan = _plan()
    request = RetrievalRequest.from_plan(plan)

    assert request.retrieval_plan_id == plan.retrieval_plan_id
    assert request.knowledge_id == plan.knowledge_id
    assert request.query == plan.query_hints[0]
    assert request.constraints == plan.constraints


def test_executor_calls_search_and_metadata_provider_only() -> None:
    fixture, _, request, candidates = _execute()

    assert len(candidates) == 1
    assert fixture.provider.search_calls == 1
    assert fixture.provider.search_queries[0].knowledge_terms == [request.query]
    assert fixture.provider.metadata_calls == ["fastapi-course-a"]
    assert fixture.provider.fetch_calls == []


def test_executor_generates_metadata_candidate_with_fingerprint() -> None:
    fixture, plan, request, candidates = _execute()
    candidate = candidates[0]

    assert candidate.status == "candidate"
    assert candidate.provider == fixture.document.metadata.provider
    assert candidate.external_id == fixture.document.metadata.external_id
    assert candidate.url == fixture.document.metadata.canonical_url
    assert candidate.duration_seconds == fixture.document.metadata.duration_seconds
    assert candidate.content_fingerprint == fixture.document.metadata.content_fingerprint
    assert candidate.content_fingerprint.startswith("sha256:")
    assert candidate.retrieval_source.retrieval_plan_id == plan.retrieval_plan_id
    assert candidate.retrieval_source.knowledge_id == request.knowledge_id


def test_candidate_validation_accepts_executor_output() -> None:
    _, _, request, candidates = _execute()

    CandidateValidator().validate(candidates[0], request=request)


def test_candidate_with_forged_url_is_rejected() -> None:
    _, _, request, candidates = _execute()
    forged = candidates[0].model_copy(update={"url": "javascript:alert(1)"})

    with pytest.raises(CandidateValidationError, match="candidate_url"):
        CandidateValidator().validate(forged, request=request)


def test_candidate_without_duration_is_rejected() -> None:
    _, _, request, candidates = _execute()
    payload = candidates[0].model_dump(mode="json", by_alias=True)
    payload.pop("durationSeconds")

    with pytest.raises(CandidateValidationError, match="durationSeconds"):
        CandidateValidator().validate_payload(payload, request=request)


@pytest.mark.parametrize("forbidden_field", ["timestamp", "segment", "coverage"])
def test_candidate_cannot_contain_evidence_fields(forbidden_field: str) -> None:
    _, _, request, candidates = _execute()
    payload = deepcopy(candidates[0].model_dump(mode="json", by_alias=True))
    payload[forbidden_field] = 10 if forbidden_field == "timestamp" else "forged"

    with pytest.raises(CandidateValidationError, match="candidate_evidence_field"):
        CandidateValidator().validate_payload(payload, request=request)


def test_request_referencing_missing_retrieval_plan_is_rejected_before_search() -> None:
    fixture = build_fastapi_crud_evidence_fixture()
    plan = _plan()
    request = RetrievalRequest.from_plan(plan).model_copy(
        update={"retrieval_plan_id": "retrieval-plan-missing"}
    )
    executor = RetrievalExecutor(fixture.provider, [plan])

    with pytest.raises(RetrievalExecutionError, match="missing RetrievalGapPlan"):
        executor.execute(request)

    assert fixture.provider.search_calls == 0
    assert fixture.provider.metadata_calls == []


def test_candidate_is_not_an_evidence_graph_contract() -> None:
    _, _, _, candidates = _execute()

    with pytest.raises(ValidationError):
        EvidenceGraph.model_validate(candidates[0].model_dump(by_alias=True))
