from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from app.learning.contracts import KnowledgeNode
from app.learning.evidence import SearchQueryGenerator
from app.learning.evidence.providers import (
    BilibiliProvider,
    VideoSearchQuery,
    VideoSourceProviderError,
)
from app.learning.evidence.search_query_generator import SearchQueryGenerationError
from app.learning.generators import LearningModelResponse

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


FIXTURE_DIR = Path(__file__).with_name("fixtures")


def _recorded(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class RecordedBilibiliTransport:
    def __init__(self, *, search=None, view=None):
        self.nav = _recorded("bilibili_nav_response.json")
        self.search = search or _recorded("bilibili_search_response.json")
        self.view = view or _recorded("bilibili_view_response.json")
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/nav"):
            payload = self.nav
        elif request.url.path.endswith("/wbi/search/type"):
            payload = self.search
        elif request.url.path.endswith("/view"):
            payload = self.view
        else:
            return httpx.Response(404, json={"code": -404}, request=request)
        return httpx.Response(200, json=payload, request=request)


def _provider(*, search=None, view=None):
    recorded = RecordedBilibiliTransport(search=search, view=view)
    client = httpx.Client(transport=httpx.MockTransport(recorded))
    provider = BilibiliProvider(client=client, clock=lambda: 1_700_000_000)
    return provider, client, recorded


def _query(maximum_results: int = 5) -> VideoSearchQuery:
    return VideoSearchQuery(
        knowledgeTerms=["FastAPI Routing 中文教程"],
        maximumResults=maximum_results,
    )


def test_bilibili_search_parses_recorded_real_response_shape() -> None:
    provider, client, recorded = _provider()
    try:
        hits = provider.search(_query())
    finally:
        client.close()

    assert [item.external_id for item in hits] == [
        "BV1zV2QBtE39",
        "BV18F4m1K7N3",
    ]
    assert hits[0].title == "黑马程序员PythonWeb开发：FastAPI从入门到实战"
    assert hits[0].canonical_url == (
        "https://www.bilibili.com/video/av115688651884489"
    )
    assert hits[0].duration_seconds == 53729
    search_request = next(
        item for item in recorded.requests if item.url.path.endswith("/wbi/search/type")
    )
    assert search_request.url.params["keyword"] == "FastAPI Routing 中文教程"
    assert search_request.url.params["wts"] == "1700000000"
    assert len(search_request.url.params["w_rid"]) == 32


def test_bilibili_fetch_metadata_builds_video_resource_from_response() -> None:
    provider, client, _ = _provider()
    try:
        hit = provider.search(_query(maximum_results=1))[0]
        resource = provider.fetch_metadata(hit.external_id)
    finally:
        client.close()

    assert resource.provider == "bilibili"
    assert resource.external_id == "BV1zV2QBtE39"
    assert resource.canonical_url == hit.canonical_url
    assert resource.duration_seconds == 53729
    assert resource.author == "黑马程序员"
    assert resource.published_at == "2025-12-10T01:00:00Z"


def test_bilibili_search_rejects_non_bilibili_video_url() -> None:
    search = _recorded("bilibili_search_response.json")
    search["data"]["result"][0]["arcurl"] = "https://evil.example/video/BV1zV2QBtE39"
    provider, client, _ = _provider(search=search)
    try:
        with pytest.raises(VideoSourceProviderError, match="invalid video URL"):
            provider.search(_query())
    finally:
        client.close()


def test_bilibili_metadata_rejects_missing_duration() -> None:
    view = _recorded("bilibili_view_response.json")
    view["data"].pop("duration")
    provider, client, _ = _provider(view=view)
    try:
        with pytest.raises(VideoSourceProviderError, match="valid duration"):
            provider.fetch_metadata("BV1zV2QBtE39")
    finally:
        client.close()


def test_bilibili_content_fingerprint_is_stable_and_content_bound() -> None:
    provider, client, _ = _provider()
    try:
        first = provider.fetch_metadata("BV1zV2QBtE39")
        second = provider.fetch_metadata("BV1zV2QBtE39")
    finally:
        client.close()

    changed_view = _recorded("bilibili_view_response.json")
    changed_view["data"]["duration"] += 1
    changed_provider, changed_client, _ = _provider(view=changed_view)
    try:
        changed = changed_provider.fetch_metadata("BV1zV2QBtE39")
    finally:
        changed_client.close()

    assert first.content_fingerprint == second.content_fingerprint
    assert first.content_fingerprint.startswith("sha256:")
    assert first.content_fingerprint != changed.content_fingerprint


def test_bilibili_search_deduplicates_repeated_video_ids() -> None:
    search = _recorded("bilibili_search_response.json")
    search["data"]["result"].append(deepcopy(search["data"]["result"][0]))
    provider, client, _ = _provider(search=search)
    try:
        hits = provider.search(_query(maximum_results=10))
    finally:
        client.close()

    assert len(hits) == 2
    assert len({item.external_id for item in hits}) == 2


def _knowledge() -> KnowledgeNode:
    return KnowledgeNode(
        id="knowledge-fastapi-routing",
        name="FastAPI Routing",
        explanation="Routing maps HTTP methods and paths to handlers.",
        whyRequired="CRUD endpoints require stable route definitions.",
        capabilityRefs=["capability-api-design"],
        outcomeRefs=["outcome-crud"],
        importance="required",
        masteryIndicators=["Define GET and POST routes"],
    )


def test_search_query_generator_creates_queries_without_resource_identity() -> None:
    result = SearchQueryGenerator().generate(_knowledge())

    assert result.search_query.knowledge_terms == [
        "FastAPI Routing 中文教程",
        "FastAPI Routing 入门 实战 教程",
    ]
    serialized = " ".join(result.search_query.knowledge_terms)
    assert "http" not in serialized.casefold()
    assert "BV" not in serialized


class UnsafeQueryModel:
    def complete(self, *, response_type, **_kwargs):
        return LearningModelResponse(
            value=response_type.model_validate(
                {"queries": ["https://www.bilibili.com/video/BV1zV2QBtE39"]}
            ),
            model_usage={"provider": "fixture", "model": "unsafe"},
        )


def test_search_query_generator_rejects_model_generated_url_or_bvid() -> None:
    with pytest.raises(SearchQueryGenerationError, match="must not contain"):
        SearchQueryGenerator(model=UnsafeQueryModel()).generate(_knowledge())


def test_mock_provider_keeps_metadata_and_evidence_boundaries() -> None:
    fixture = build_fastapi_crud_evidence_fixture()
    external_id = fixture.document.metadata.external_id

    resource = fixture.provider.fetch_metadata(external_id)
    document = fixture.provider.fetch_evidence(external_id)

    assert resource.external_id == external_id
    assert resource.duration_seconds == fixture.document.metadata.duration_seconds
    assert document == fixture.document
