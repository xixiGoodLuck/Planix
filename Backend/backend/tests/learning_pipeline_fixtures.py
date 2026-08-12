from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.learning.contracts import (
    ContentBudget,
    CurrentLevel,
    LanguagePreference,
    LearningScope,
    ResourcePreference,
)
from app.learning.evidence.providers import (
    MockVideoProvider,
    ProviderEvidenceSource,
    ProviderSegmentSource,
    ProviderVideoDocument,
    ProviderVideoMetadata,
)
from app.learning.generators import LearningModelResponse
from app.learning.scope_anchor_semantics import text_matches_concept_anchor


class ScriptedPipelineModel:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = deepcopy(responses)
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
                "payload": payload,
                "maxTokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError(f"unexpected model call at {stage}")
        raw = self.responses.pop(0)
        self._complete_phase_30_contract(stage, payload, raw)
        return LearningModelResponse(
            value=response_type.model_validate(raw),
            model_usage={"provider": "fixture", "model": "scripted"},
        )

    @staticmethod
    def _complete_phase_30_contract(
        stage: str,
        payload: dict[str, Any],
        raw: dict[str, Any],
    ) -> None:
        anchors = payload.get("scopeAnchors", [])
        concept_anchors = [item for item in anchors if item.get("kind") == "concept"]

        def matching_indexes(text: str) -> list[int]:
            matches = [
                item["index"]
                for item in concept_anchors
                if text_matches_concept_anchor(text, item["text"])
            ]
            if matches:
                return matches
            return [anchors[0]["index"]] if anchors else []

        if stage == "learning_outcomes" and anchors:
            for outcome in raw.get("outcomes", []):
                if outcome.get("importance") == "required":
                    outcome.setdefault(
                        "scopeAnchorIndexes",
                        matching_indexes(outcome.get("statement", "")),
                    )
        elif stage == "learning_capabilities" and anchors:
            for capability in raw.get("capabilities", []):
                if capability.get("importance") == "required":
                    capability.setdefault(
                        "scopeAnchorIndexes",
                        matching_indexes(capability.get("name", "")),
                    )
        elif stage == "learning_knowledge" and anchors:
            for knowledge in raw.get("knowledge", []):
                if knowledge.get("importance") == "required":
                    knowledge.setdefault(
                        "scopeAnchorIndexes",
                        matching_indexes(knowledge.get("name", "")),
                    )
                indicators = knowledge.get("masteryIndicators", [])
                if indicators:
                    knowledge.setdefault("coverageRequirements", [indicators[0]])
        elif stage == "learning_evidence_semantics":
            requirements = [
                item.get("coverageRequirements", [])
                for item in payload.get("knowledge", [])
            ]
            for coverage in raw.get("coverage", []):
                index = coverage.get("knowledgeIndex")
                if isinstance(index, int) and index < len(requirements) and requirements[index]:
                    coverage.setdefault("supportedRequirementIndexes", [0])


@dataclass(frozen=True)
class FastApiPipelineFixture:
    scope: LearningScope
    provider: MockVideoProvider
    model: ScriptedPipelineModel


def _transcript(claim: str, start: int, end: int) -> ProviderEvidenceSource:
    return ProviderEvidenceSource(
        kind="transcript_span",
        supportedClaim=claim,
        sourceRange={
            "locatorType": "transcript_chars",
            "startOffset": start,
            "endOffset": end,
        },
        sourceExcerpt=claim,
        verificationStatus="verified",
    )


def fastapi_pipeline_responses() -> list[dict[str, Any]]:
    return [
        {
            "outcomes": [
                {
                    "statement": "Build a working FastAPI CRUD API",
                    "acceptanceCriteria": [
                        "Define API routes",
                        "Validate request data",
                        "Persist and mutate records",
                    ],
                    "importance": "required",
                }
            ]
        },
        {
            "capabilities": [
                {
                    "name": "API Design",
                    "description": "Map resource operations to clear API endpoints.",
                    "whyRequired": "CRUD requires stable request boundaries.",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "Data Validation",
                    "description": "Define and validate request and response data.",
                    "whyRequired": "Invalid input must be rejected before persistence.",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "Persistence",
                    "description": "Store and retrieve resource state.",
                    "whyRequired": "CRUD state must survive individual requests.",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
            ],
            "edges": [
                {"sourceIndex": 0, "targetIndex": 2, "relation": "supports"},
                {"sourceIndex": 1, "targetIndex": 2, "relation": "supports"},
            ],
        },
        {
            "knowledge": [
                {
                    "name": "Routing",
                    "explanation": "Routing binds HTTP requests to endpoint handlers.",
                    "whyRequired": "API Design needs addressable CRUD endpoints.",
                    "capabilityIndexes": [0],
                    "importance": "required",
                    "masteryIndicators": ["Define CRUD routes with path parameters"],
                },
                {
                    "name": "Pydantic",
                    "explanation": "Pydantic models define and validate API data.",
                    "whyRequired": "Data Validation needs explicit schemas.",
                    "capabilityIndexes": [1],
                    "importance": "required",
                    "masteryIndicators": ["Define create and update schemas"],
                },
                {
                    "name": "Database",
                    "explanation": "A database stores resource state across requests.",
                    "whyRequired": "Persistence needs durable storage.",
                    "capabilityIndexes": [2],
                    "importance": "required",
                    "masteryIndicators": ["Store and retrieve records"],
                },
                {
                    "name": "CRUD",
                    "explanation": "CRUD combines create, read, update, and delete flows.",
                    "whyRequired": "The target result is a complete CRUD API.",
                    "capabilityIndexes": [0, 1, 2],
                    "importance": "required",
                    "masteryIndicators": ["Complete all four operations end to end"],
                },
            ],
            "edges": [
                {
                    "sourceIndex": 0,
                    "targetIndex": 3,
                    "relation": "prerequisite",
                    "reason": "CRUD operations need routes.",
                },
                {
                    "sourceIndex": 1,
                    "targetIndex": 3,
                    "relation": "prerequisite",
                    "reason": "CRUD inputs need schema validation.",
                },
                {
                    "sourceIndex": 2,
                    "targetIndex": 3,
                    "relation": "prerequisite",
                    "reason": "CRUD results need persistence.",
                },
            ],
        },
        {
            "segmentAnnotations": [
                {
                    "segmentIndex": 0,
                    "contentSummary": "Routing and Pydantic validation in FastAPI.",
                    "topics": ["Routing", "Pydantic"],
                },
                {
                    "segmentIndex": 1,
                    "contentSummary": "A longer repetition of routing and validation.",
                    "topics": ["Routing", "Pydantic"],
                },
                {
                    "segmentIndex": 2,
                    "contentSummary": "Database persistence and complete CRUD operations.",
                    "topics": ["Database", "CRUD"],
                },
            ],
            "coverage": [
                {
                    "knowledgeIndex": 0,
                    "segmentIndex": 0,
                    "evidenceIndexes": [0],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.97,
                    "reason": "Verified transcript demonstrates routing.",
                },
                {
                    "knowledgeIndex": 1,
                    "segmentIndex": 0,
                    "evidenceIndexes": [0],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.97,
                    "reason": "Verified transcript demonstrates validation.",
                },
                {
                    "knowledgeIndex": 0,
                    "segmentIndex": 1,
                    "evidenceIndexes": [1],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.88,
                    "reason": "Verified transcript repeats routing.",
                },
                {
                    "knowledgeIndex": 1,
                    "segmentIndex": 1,
                    "evidenceIndexes": [1],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.88,
                    "reason": "Verified transcript repeats validation.",
                },
                {
                    "knowledgeIndex": 2,
                    "segmentIndex": 2,
                    "evidenceIndexes": [2],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.96,
                    "reason": "Verified transcript demonstrates persistence.",
                },
                {
                    "knowledgeIndex": 3,
                    "segmentIndex": 2,
                    "evidenceIndexes": [2],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.95,
                    "reason": "Verified transcript demonstrates CRUD.",
                },
            ],
        },
    ]


def build_fastapi_learning_pipeline_fixture() -> FastApiPipelineFixture:
    scope = LearningScope(
        artifactId="learning-scope-fastapi-pipeline",
        userGoal="我要学习FastAPI并完成CRUD API",
        targetResult="独立完成一个可运行的FastAPI CRUD API",
        currentLevel=CurrentLevel(
            summary="具备Python基础",
            knownSkills=["Python基础"],
            knownTechnologies=["Python"],
            sourceRefs=["user:1"],
        ),
        contentBudget=ContentBudget(
            targetTotalMinutes=20,
            maximumTotalMinutes=30,
            maximumVideoCount=3,
            maximumSegmentMinutes=15,
        ),
        languagePreference=LanguagePreference(preferredLanguages=["zh-CN"]),
        resourcePreference=ResourcePreference(preferredStyles=["hands_on"]),
        sourceRefs=["user:1"],
        confirmed=True,
    )
    video_a = ProviderVideoDocument(
        metadata=ProviderVideoMetadata(
            provider="fixture",
            externalId="fastapi-pipeline-a",
            canonicalUrl="https://example.test/videos/fastapi-pipeline-a",
            title="FastAPI Routing and Validation",
            author="Planix Fixture",
            language="zh-CN",
            durationSeconds=3600,
            contentFingerprint="sha256:fastapi-pipeline-a-v1",
            technologyVersions={"FastAPI": "0.115", "Pydantic": "2"},
        ),
        segments=[
            ProviderSegmentSource(
                sourceKey="routing-validation-core",
                timeRangeSeconds=(300, 900),
                evidence=[
                    _transcript(
                        "Demonstrates FastAPI routes and Pydantic schemas.",
                        100,
                        900,
                    )
                ],
            ),
            ProviderSegmentSource(
                sourceKey="routing-validation-repeat",
                timeRangeSeconds=(1200, 2100),
                evidence=[
                    _transcript(
                        "Repeats the same routing and Pydantic workflow.",
                        901,
                        1900,
                    )
                ],
            ),
        ],
    )
    video_b = ProviderVideoDocument(
        metadata=ProviderVideoMetadata(
            provider="fixture",
            externalId="fastapi-pipeline-b",
            canonicalUrl="https://example.test/videos/fastapi-pipeline-b",
            title="FastAPI Persistence and CRUD",
            author="Planix Fixture",
            language="zh-CN",
            durationSeconds=3000,
            contentFingerprint="sha256:fastapi-pipeline-b-v1",
            technologyVersions={"FastAPI": "0.115", "SQLAlchemy": "2"},
        ),
        segments=[
            ProviderSegmentSource(
                sourceKey="persistence-crud-core",
                timeRangeSeconds=(240, 840),
                evidence=[
                    _transcript(
                        "Demonstrates database persistence and all CRUD operations.",
                        100,
                        1000,
                    )
                ],
            )
        ],
    )
    return FastApiPipelineFixture(
        scope=scope,
        provider=MockVideoProvider([video_a, video_b]),
        model=ScriptedPipelineModel(fastapi_pipeline_responses()),
    )


__all__ = [
    "FastApiPipelineFixture",
    "ScriptedPipelineModel",
    "build_fastapi_learning_pipeline_fixture",
    "fastapi_pipeline_responses",
]
