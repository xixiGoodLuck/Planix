from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.learning.contracts import VideoResource
from app.learning.evidence.transcript import (
    MockTranscriptProvider,
    TranscriptBuildError,
    TranscriptDocument,
    TranscriptEvidencePipeline,
    TranscriptEvidencePipelineError,
    TranscriptSegment,
    TranscriptSegmentBuilder,
    TranscriptValidationError,
    TranscriptValidator,
)
from app.learning.generators import LearningModelResponse

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


class ScriptedTranscriptModel:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "stage": stage,
                "feature": feature,
                "system": system,
                "payload": payload,
                "maxTokens": max_tokens,
            }
        )
        return LearningModelResponse(
            value=response_type.model_validate(self.response),
            model_usage={"provider": "fixture", "model": "scripted-transcript"},
        )


def _fixture():
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    resource = VideoResource(
        id="video-fastapi-transcript",
        provider="fixture",
        externalId="fastapi-transcript-a",
        canonicalUrl="https://example.test/videos/fastapi-transcript-a",
        title="FastAPI Transcript Fixture",
        author="Planix",
        language="zh-CN",
        durationSeconds=120,
        contentFingerprint="sha256:fastapi-transcript-a-v1",
    )
    transcript = TranscriptDocument(
        resourceId=resource.id,
        fingerprint=resource.content_fingerprint,
        language="zh-CN",
        segments=[
            TranscriptSegment(
                id="cue-routing",
                startSeconds=10,
                endSeconds=20,
                text="定义 FastAPI 路由并将 GET 请求映射到处理函数。",
            ),
            TranscriptSegment(
                id="cue-pydantic",
                startSeconds=20,
                endSeconds=30,
                text="使用 Pydantic 模型校验请求数据和响应结构。",
            ),
            TranscriptSegment(
                id="cue-database",
                startSeconds=30,
                endSeconds=40,
                text="建立数据库连接并持久化 API 接收到的数据。",
            ),
            TranscriptSegment(
                id="cue-crud",
                startSeconds=40,
                endSeconds=50,
                text="实现创建、查询、更新和删除四类 CRUD 操作。",
            ),
        ],
    )
    semantic_response = {
        "segments": [
            {
                "mappings": [
                    {
                        "knowledgeId": "knowledge-routing",
                        "coverageType": "demonstration",
                        "summary": "演示 FastAPI 路由和 GET 请求映射。",
                        "confidence": 0.98,
                        "reason": "字幕直接说明路由和 GET 请求映射。",
                    },
                    {
                        "knowledgeId": "knowledge-pydantic",
                        "coverageType": "explanation",
                        "summary": "解释 Pydantic 输入输出校验。",
                        "confidence": 0.98,
                        "reason": "字幕直接说明 Pydantic 输入输出校验。",
                    },
                ]
            },
            {
                "mappings": [
                    {
                        "knowledgeId": "knowledge-database",
                        "coverageType": "implementation",
                        "summary": "演示数据库连接与持久化。",
                        "confidence": 0.96,
                        "reason": "字幕直接说明数据库连接与持久化。",
                    },
                    {
                        "knowledgeId": "knowledge-crud",
                        "coverageType": "implementation",
                        "summary": "实现完整 CRUD 操作。",
                        "confidence": 0.97,
                        "reason": "字幕直接列出 CRUD 操作。",
                    },
                ]
            },
        ]
    }
    return knowledge_graph, resource, transcript, semantic_response


def _generate(*, response: dict[str, Any] | None = None):
    knowledge_graph, resource, transcript, semantic_response = _fixture()
    provider = MockTranscriptProvider([transcript])
    model = ScriptedTranscriptModel(response or deepcopy(semantic_response))
    result = TranscriptEvidencePipeline(provider=provider, model=model).generate(
        knowledge_graph,
        [resource],
    )
    return resource, transcript, provider, model, result


def test_valid_transcript_generates_verified_evidence() -> None:
    resource, _, provider, model, result = _generate()
    graph = result.evidence_graph

    assert provider.fetch_calls == [resource.id]
    assert len(model.calls) == 1
    assert len(graph.resources) == 1
    assert len(graph.segments) == 2
    assert len(graph.evidence) == 4
    assert len(graph.coverage_edges) == 4
    assert all(item.kind == "transcript_span" for item in graph.evidence)
    assert all(item.verification_status == "verified" for item in graph.evidence)


def test_content_segments_bind_exact_transcript_ranges() -> None:
    _, _, _, _, result = _generate()
    graph = result.evidence_graph

    assert [(item.start_seconds, item.end_seconds) for item in graph.segments] == [
        (10, 30),
        (30, 50),
    ]
    first_evidence = [
        item for item in graph.evidence if item.segment_id == graph.segments[0].id
    ]
    assert [item.source_excerpt for item in first_evidence] == [
        "定义 FastAPI 路由并将 GET 请求映射到处理函数。",
        "使用 Pydantic 模型校验请求数据和响应结构。",
    ]
    assert first_evidence[0].source_range.start_offset == 0
    assert first_evidence[0].source_range.end_offset == len(first_evidence[0].source_excerpt)
    assert first_evidence[1].source_range.start_offset == len(first_evidence[0].source_excerpt) + 1


def test_fingerprint_is_preserved_across_transcript_evidence_graph() -> None:
    resource, transcript, _, _, result = _generate()
    graph = result.evidence_graph

    assert transcript.fingerprint == resource.content_fingerprint
    assert all(
        item.resource_fingerprint == resource.content_fingerprint
        for item in graph.segments
    )
    assert all(
        item.resource_fingerprint == resource.content_fingerprint
        for item in graph.evidence
    )


def test_transcript_over_video_duration_is_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    bad_cue = transcript.segments[-1].model_copy(update={"end_seconds": 121})
    bad_document = transcript.model_copy(
        update={"segments": [*transcript.segments[:-1], bad_cue]}
    )

    with pytest.raises(TranscriptValidationError, match="transcript_timestamp"):
        TranscriptValidator().validate(resource, bad_document)


def test_transcript_fingerprint_mismatch_is_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    bad_document = transcript.model_copy(update={"fingerprint": "sha256:stale"})

    with pytest.raises(TranscriptValidationError, match="transcript_fingerprint"):
        TranscriptValidator().validate(resource, bad_document)


def test_reversed_transcript_timestamp_is_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    bad_cue = TranscriptSegment.model_construct(
        id="cue-bad",
        start_seconds=25,
        end_seconds=24,
        text="bad",
    )
    bad_document = transcript.model_copy(update={"segments": [bad_cue]})

    with pytest.raises(TranscriptValidationError, match="transcript_timestamp"):
        TranscriptValidator().validate(resource, bad_document)


def test_empty_transcript_is_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    empty_document = transcript.model_copy(update={"segments": []})

    with pytest.raises(TranscriptValidationError, match="transcript_empty"):
        TranscriptValidator().validate(resource, empty_document)


def test_metadata_cannot_directly_create_timestamped_content() -> None:
    _, resource, _, _ = _fixture()

    with pytest.raises(TranscriptBuildError, match="TranscriptDocument"):
        TranscriptSegmentBuilder().build(resource, resource)  # type: ignore[arg-type]


def test_model_returned_timestamp_is_rejected() -> None:
    _, _, _, semantic_response = _fixture()
    response = deepcopy(semantic_response)
    response["segments"][0]["mappings"][0]["startSeconds"] = 10

    with pytest.raises(
        TranscriptEvidencePipelineError,
        match="Extra inputs are not permitted",
    ) as caught:
        _generate(response=response)

    assert caught.value.stage == "coverage_mapping"


def test_model_never_receives_provider_owned_timestamp_fields() -> None:
    _, _, _, model, _ = _generate()
    payload = json.dumps(model.calls[0]["payload"], ensure_ascii=False)

    assert all(
        forbidden not in payload
        for forbidden in (
            "canonicalUrl",
            "durationSeconds",
            "startSeconds",
            "endSeconds",
            "fingerprint",
            "externalId",
        )
    )


def test_overlapping_transcript_segments_are_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    overlapping = transcript.segments[1].model_copy(update={"start_seconds": 19})
    bad_document = transcript.model_copy(
        update={"segments": [transcript.segments[0], overlapping]}
    )

    with pytest.raises(TranscriptValidationError, match="transcript_overlap"):
        TranscriptValidator().validate(resource, bad_document)


def test_blank_transcript_text_is_rejected() -> None:
    _, resource, transcript, _ = _fixture()
    blank = transcript.segments[0].model_copy(update={"text": "   "})
    bad_document = transcript.model_copy(update={"segments": [blank]})

    with pytest.raises(TranscriptValidationError, match="transcript_text"):
        TranscriptValidator().validate(resource, bad_document)
