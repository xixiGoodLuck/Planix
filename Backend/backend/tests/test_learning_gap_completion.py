from __future__ import annotations

from typing import Any

import pytest

from app.learning.contracts import EvidenceGraph
from app.learning.evidence.coverage import CoverageAggregator
from app.learning.evidence.mapping import CoverageMapper
from app.learning.evidence.orchestration import (
    GapCompletionOrchestrator,
    GapCompletionRun,
    GapCompletionValidationError,
    GapCompletionValidator,
)
from app.learning.evidence.providers import (
    MockVideoProvider,
    ProviderEvidenceSource,
    ProviderSegmentSource,
    ProviderVideoDocument,
    ProviderVideoMetadata,
    VideoSourceProviderError,
)
from app.learning.evidence.supplement import EvidenceSupplementer
from app.learning.evidence.transcript import TranscriptDocument, TranscriptSegment
from app.learning.generators import LearningModelResponse
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


class SemanticCoverageModel:
    def __init__(self, *, full: bool = True):
        self.full = full
        self.calls = 0

    def complete(
        self,
        *,
        stage: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        response_type,
        max_tokens: int,
    ):
        self.calls += 1
        segments = []
        for segment in payload["segments"]:
            text = " ".join(
                item["text"] for item in segment["transcriptEvidence"]
            ).casefold()
            if "routing" in text:
                knowledge_id = "knowledge-routing"
            elif "database" in text:
                knowledge_id = "knowledge-database"
            else:
                knowledge_id = "knowledge-crud"
            segments.append(
                {
                    "mappings": [
                        {
                            "knowledgeId": knowledge_id,
                            "coverageType": (
                                "demonstration" if self.full else "introduction"
                            ),
                            "summary": f"Verified {knowledge_id} transcript.",
                            "confidence": 0.96 if self.full else 0.7,
                            "reason": "Verified transcript directly supports the mapping.",
                        }
                    ]
                }
            )
        return LearningModelResponse(
            value=response_type.model_validate({"segments": segments}),
            model_usage={"provider": "fixture", "model": "semantic-coverage"},
        )


class DynamicTranscriptProvider:
    def __init__(self, text_by_external_id: dict[str, str]):
        self.text_by_external_id = text_by_external_id
        self.fetch_calls: list[str] = []

    def fetch_transcript(self, resource):
        self.fetch_calls.append(resource.external_id)
        text = self.text_by_external_id[resource.external_id]
        return TranscriptDocument(
            resourceId=resource.id,
            fingerprint=resource.content_fingerprint,
            language="en",
            segments=[
                TranscriptSegment(
                    id=f"cue-{resource.external_id}",
                    startSeconds=10,
                    endSeconds=90,
                    text=text,
                )
            ],
        )


class FailingVideoProvider:
    def search(self, query):
        raise VideoSourceProviderError("fixture provider unavailable")

    def fetch_metadata(self, external_id):
        raise AssertionError("metadata must not be called after search failure")


def _knowledge_graph(knowledge_ids: list[str]):
    source = build_fastapi_crud_evidence_fixture().knowledge_graph
    included = set(knowledge_ids)
    return source.model_copy(
        update={
            "artifact_id": "knowledge-graph-gap-completion-" + "-".join(knowledge_ids),
            "nodes": [item for item in source.nodes if item.id in included],
            "edges": [
                item
                for item in source.edges
                if item.source_knowledge_id in included
                and item.target_knowledge_id in included
            ],
        }
    )


def _empty_graph(knowledge_graph):
    graph = EvidenceGraph(
        artifactId="evidence-graph-gap-completion",
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        resources=[],
        segments=[],
        evidence=[],
        coverageEdges=[],
    )
    return graph, CoverageAggregator().aggregate(knowledge_graph, graph)


def _video(external_id: str, text: str, fingerprint_character: str):
    return ProviderVideoDocument(
        metadata=ProviderVideoMetadata(
            provider="fixture",
            externalId=external_id,
            canonicalUrl=f"https://example.test/video/{external_id}",
            title=f"{external_id} tutorial",
            durationSeconds=300,
            contentFingerprint=f"sha256:{fingerprint_character * 64}",
        ),
        segments=[
            ProviderSegmentSource(
                sourceKey=f"source-{external_id}",
                timeRangeSeconds=(10, 90),
                evidence=[
                    ProviderEvidenceSource(
                        kind="transcript_span",
                        supportedClaim=text,
                        sourceRange={
                            "locatorType": "transcript_chars",
                            "startOffset": 0,
                            "endOffset": len(text),
                        },
                        sourceExcerpt=text,
                        verificationStatus="verified",
                    )
                ],
            )
        ],
    )


def _run(
    knowledge_ids: list[str],
    videos: list[ProviderVideoDocument],
    *,
    full: bool = True,
    max_rounds: int = 3,
):
    knowledge_graph = _knowledge_graph(knowledge_ids)
    graph, report = _empty_graph(knowledge_graph)
    transcript_provider = DynamicTranscriptProvider(
        {
            item.metadata.external_id: item.segments[0].evidence[0].supported_claim
            for item in videos
        }
    )
    model = SemanticCoverageModel(full=full)
    orchestrator = GapCompletionOrchestrator(
        transcript_provider,
        evidence_supplementer=EvidenceSupplementer(
            coverage_mapper=CoverageMapper(model=model)
        ),
        max_rounds=max_rounds,
    )
    result = orchestrator.run(
        knowledge_graph,
        graph,
        report,
        MockVideoProvider(videos),
    )
    return knowledge_graph, graph, report, transcript_provider, model, result


def test_one_round_completes_missing_required_knowledge() -> None:
    video = _video("routing-a", "FastAPI routing demonstration", "a")
    _, _, _, _, model, result = _run(["knowledge-routing"], [video])

    assert result.status == "COMPLETED"
    assert result.termination_reason == "REQUIRED_COVERAGE_FULL"
    assert len(result.rounds) == 1
    assert result.rounds[0].round_number == 1
    assert model.calls == 1


def test_two_rounds_complete_two_required_knowledge_nodes() -> None:
    videos = [
        _video("routing-a", "FastAPI routing demonstration", "a"),
        _video("database-a", "FastAPI database persistence demonstration", "b"),
    ]
    _, _, _, transcript_provider, _, result = _run(
        ["knowledge-routing", "knowledge-database"],
        videos,
    )

    assert result.status == "COMPLETED"
    assert [item.round_number for item in result.rounds] == [1, 2]
    assert result.rounds[0].status == "RUNNING"
    assert result.rounds[1].status == "COMPLETED"
    assert transcript_provider.fetch_calls == ["routing-a", "database-a"]


def test_completed_result_requires_all_required_coverage_full() -> None:
    video = _video("routing-a", "FastAPI routing demonstration", "a")
    knowledge_graph, _, _, _, _, result = _run(["knowledge-routing"], [video])

    coverage = {
        item.knowledge_id: item.coverage_strength
        for item in result.final_report.knowledge_coverage
    }
    assert all(
        coverage[item.id] == "FULL"
        for item in knowledge_graph.nodes
        if item.importance == "required"
    )


def test_max_rounds_stops_an_incomplete_run() -> None:
    videos = [
        _video("routing-a", "FastAPI routing demonstration", "a"),
        _video("database-a", "FastAPI database persistence demonstration", "b"),
    ]
    _, _, _, _, _, result = _run(
        ["knowledge-routing", "knowledge-database"],
        videos,
        max_rounds=1,
    )

    assert result.status == "INCOMPLETE"
    assert result.termination_reason == "MAX_ROUNDS_REACHED"
    assert len(result.rounds) == 1


def test_no_improvement_stops_without_infinite_loop() -> None:
    videos = [
        _video("routing-partial-a", "FastAPI routing introduction", "a"),
        _video("routing-partial-b", "More FastAPI routing introduction", "b"),
        _video("routing-partial-c", "Another FastAPI routing introduction", "c"),
    ]
    _, _, _, _, model, result = _run(
        ["knowledge-routing"],
        videos,
        full=False,
        max_rounds=3,
    )

    assert result.status == "INCOMPLETE"
    assert result.termination_reason == "NO_COVERAGE_IMPROVEMENT"
    assert len(result.rounds) == 2
    assert model.calls == 2


def test_no_candidates_stops_as_no_executable_gap() -> None:
    knowledge_graph = _knowledge_graph(["knowledge-routing"])
    graph, report = _empty_graph(knowledge_graph)
    orchestrator = GapCompletionOrchestrator(DynamicTranscriptProvider({}))

    result = orchestrator.run(
        knowledge_graph,
        graph,
        report,
        MockVideoProvider([]),
    )

    assert result.status == "INCOMPLETE"
    assert result.termination_reason == "NO_EXECUTABLE_GAP"
    assert len(result.rounds) == 1


def test_provider_failure_never_returns_completed() -> None:
    knowledge_graph = _knowledge_graph(["knowledge-routing"])
    graph, report = _empty_graph(knowledge_graph)
    orchestrator = GapCompletionOrchestrator(DynamicTranscriptProvider({}))

    result = orchestrator.run(
        knowledge_graph,
        graph,
        report,
        FailingVideoProvider(),
    )

    assert result.status == "FAILED"
    assert result.termination_reason == "FAILED"
    assert result.error
    assert result.rounds[-1].status != "COMPLETED"


def test_validator_rejects_forged_resolved_gap() -> None:
    video = _video("routing-a", "FastAPI routing demonstration", "a")
    knowledge_graph, graph, _, _, _, result = _run(
        ["knowledge-routing"],
        [video],
    )
    forged_round = result.rounds[0].model_copy(update={"resolved_gaps": []})
    forged = result.model_copy(update={"rounds": [forged_round]})

    with pytest.raises(GapCompletionValidationError, match="resolved_gap"):
        GapCompletionValidator().validate_result(knowledge_graph, graph, forged)


def test_validator_rejects_rounds_beyond_maximum() -> None:
    videos = [
        _video("routing-a", "FastAPI routing demonstration", "a"),
        _video("database-a", "FastAPI database persistence demonstration", "b"),
    ]
    knowledge_graph, graph, _, _, _, result = _run(
        ["knowledge-routing", "knowledge-database"],
        videos,
    )
    forged = result.model_copy(update={"max_rounds": 1})

    with pytest.raises(GapCompletionValidationError, match="exceeded MAX_ROUNDS"):
        GapCompletionValidator().validate_result(knowledge_graph, graph, forged)


def test_validator_rejects_out_of_order_round_number() -> None:
    video = _video("routing-a", "FastAPI routing demonstration", "a")
    knowledge_graph, graph, _, _, _, result = _run(
        ["knowledge-routing"],
        [video],
    )
    forged_round = result.rounds[0].model_copy(update={"round_number": 2})
    forged = result.model_copy(update={"rounds": [forged_round]})

    with pytest.raises(GapCompletionValidationError, match="round_sequence"):
        GapCompletionValidator().validate_result(knowledge_graph, graph, forged)


def test_validator_rejects_continuing_after_no_improvement() -> None:
    videos = [
        _video("routing-partial-a", "FastAPI routing introduction", "a"),
        _video("routing-partial-b", "More FastAPI routing introduction", "b"),
    ]
    knowledge_graph, graph, _, _, _, result = _run(
        ["knowledge-routing"],
        videos,
        full=False,
        max_rounds=3,
    )
    no_improvement = result.rounds[-1]
    continued = no_improvement.model_copy(
        update={"status": "RUNNING", "termination_reason": None}
    )
    forged_final = GapCompletionRun(
        runId=result.run_id,
        roundNumber=3,
        status="INCOMPLETE",
        beforeReport=no_improvement.after_report,
        afterReport=no_improvement.after_report,
        resolvedGaps=[],
        remainingGaps=no_improvement.after_report.gaps,
        terminationReason="NO_COVERAGE_IMPROVEMENT",
    )
    forged = result.model_copy(
        update={"rounds": [result.rounds[0], continued, forged_final]}
    )

    with pytest.raises(
        GapCompletionValidationError,
        match="coverage_no_improvement_continue",
    ):
        GapCompletionValidator().validate_result(knowledge_graph, graph, forged)
