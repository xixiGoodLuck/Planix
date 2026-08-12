from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Annotated, Callable, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ..learning.contracts import (
    ContentBudget,
    CurrentLevel,
    EvidenceGraph,
    EvidenceInterventionGap,
    EvidenceInterventionReport,
    LanguagePreference,
    LearningAssumption,
    LearningContentPlan,
    KnowledgeGraph,
    LearningQualityReport,
    LearningScope,
    ResourcePreference,
)
from ..learning.generators import LearningModelOutputError
from ..learning.scope import (
    LearningScopeAnalyzer,
    LearningScopePatcher,
    LearningScopeReview,
    canonicalize_bilibili_resource_urls,
    evaluate_readiness,
    is_continue_intent,
    build_explicit_scope_anchors,
    project_scope_review,
)
from ..learning.runtime import (
    ArtifactStoreError,
    LearningRunResult,
    LearningWaitingEvidenceResult,
    LearningRuntime,
    LearningRuntimeConfig,
    LearningRuntimeError,
    LearningRuntimeFactory,
)
from ..learning.evidence.transcript import (
    LearningTranscriptRegistrationService,
    HARD_TRANSCRIPT_MAX_BYTES,
    TranscriptConflict,
    TranscriptRegistrationError,
    TranscriptRepositoryError,
    TranscriptSourceSummary,
)


router = APIRouter(prefix="/api/learning", tags=["learning"])
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
TRANSCRIPT_REQUEST_HARD_BYTES = HARD_TRANSCRIPT_MAX_BYTES + 64 * 1024


class LearningApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LearningTranscriptCreateRequest(LearningApiModel):
    video_url: str = Field(alias="videoUrl", min_length=1)
    format: Literal["srt", "vtt"]
    language: str = Field(default="", max_length=32)
    content: str = Field(min_length=1)
    source_name: str | None = Field(default=None, alias="sourceName", max_length=128)


class LearningTranscriptRevokeResponse(LearningApiModel):
    source_id: str
    status: Literal["revoked"] = "revoked"


class LearningRunPreferences(LearningApiModel):
    target_result: str = ""
    current_level: CurrentLevel = Field(default_factory=CurrentLevel)
    content_budget: ContentBudget = Field(default_factory=ContentBudget)
    language_preference: LanguagePreference = Field(default_factory=LanguagePreference)
    resource_preference: ResourcePreference = Field(
        default_factory=ResourcePreference,
        alias="resourcePreference",
    )
    confirmed: bool = True


class LearningRunCreateRequest(LearningApiModel):
    goal: str = Field(min_length=1)
    preferences: LearningRunPreferences = Field(default_factory=LearningRunPreferences)
    constraints: list[str] = Field(default_factory=list)

    @field_validator("goal")
    @classmethod
    def valid_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal must not be blank")
        return normalized


class LearningRunCreateResponse(LearningApiModel):
    run_id: str


class LearningRunErrorResponse(LearningApiModel):
    stage: str
    error_type: str
    message: str
    validator_rule: str = ""
    field_path: str = ""


class LearningRunStatusResponse(LearningApiModel):
    status: str
    current_stage: str
    completed_stages: list[str]
    error: LearningRunErrorResponse | None = None
    intervention: "LearningEvidenceInterventionResponse | None" = None


class LearningInterventionResourceResponse(LearningApiModel):
    id: str
    title: str
    canonical_url: str = Field(alias="canonicalUrl")
    availability: str


class LearningInterventionSegmentResponse(LearningApiModel):
    id: str
    resource_id: str = Field(alias="resourceId")
    start_seconds: int = Field(alias="startSeconds")
    end_seconds: int = Field(alias="endSeconds")
    content_summary: str = Field(alias="contentSummary")


class LearningInterventionKnowledgeResponse(LearningApiModel):
    id: str
    name: str
    importance: str
    coverage_strength: str = Field(alias="coverageStrength")


class LearningEvidenceInterventionResponse(LearningApiModel):
    kind: Literal["additional_evidence_required"] = "additional_evidence_required"
    required_gaps: list[EvidenceInterventionGap] = Field(alias="requiredGaps")
    searched_resources: list[str] = Field(alias="searchedResources")
    transcript_unavailable_resources: list[str] = Field(
        alias="transcriptUnavailableResources"
    )
    verified_resources: list[LearningInterventionResourceResponse] = Field(
        default_factory=list,
        alias="verifiedResources",
    )
    verified_segments: list[LearningInterventionSegmentResponse] = Field(
        default_factory=list,
        alias="verifiedSegments",
    )
    knowledge_coverage: list[LearningInterventionKnowledgeResponse] = Field(
        default_factory=list,
        alias="knowledgeCoverage",
    )
    can_resume: bool = Field(default=True, alias="canResume")


class LearningRunResultResponse(LearningApiModel):
    learning_content_plan: LearningContentPlan
    learning_quality_report: LearningQualityReport
    evidence_graph: EvidenceGraph


class LearningIntakeCreateRequest(LearningApiModel):
    message: str = Field(min_length=1)
    preferred_language: str = Field(default="zh-CN", alias="preferredLanguage", max_length=32)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class LearningIntakeSupplementRequest(LearningApiModel):
    message: str = ""
    preferred_language: str = Field(default="zh-CN", alias="preferredLanguage", max_length=32)
    resource_urls: list[str] = Field(
        default_factory=list,
        alias="resourceUrls",
        max_length=8,
    )
    defer_auto_start: bool = Field(default=False, alias="deferAutoStart")

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        return value.strip()

    @field_validator("resource_urls")
    @classmethod
    def valid_resource_urls(cls, value: list[str]) -> list[str]:
        return canonicalize_bilibili_resource_urls(value)

    @model_validator(mode="after")
    def has_patch(self) -> "LearningIntakeSupplementRequest":
        if not self.message and not self.resource_urls:
            raise ValueError("message or resourceUrls is required")
        return self


LearningIntakeStatus = Literal[
    "analyzing_scope",
    "waiting_scope_review",
    "running",
    "completed",
    "failed",
    "waiting_evidence",
]


class LearningIntakeResponse(LearningApiModel):
    intake_id: str = Field(alias="intakeId")
    status: LearningIntakeStatus
    scope: LearningScope
    review: LearningScopeReview
    run_id: str | None = Field(default=None, alias="runId")


LearningRuntimeBuilder = Callable[[], LearningRuntime]
LearningHealthProvider = Callable[[], dict]
LearningScopeAnalyzerFactory = Callable[[LearningRuntime], LearningScopeAnalyzer]


@dataclass
class _LearningRunHandle:
    runtime: LearningRuntime
    result: LearningRunResult | None = None
    future: Future[None] | None = None


class LearningRunManager:
    """API lifecycle boundary; generation remains owned by LearningRuntime."""

    def __init__(
        self,
        runtime_factory: LearningRuntimeBuilder,
        *,
        health_provider: LearningHealthProvider | None = None,
        scope_analyzer_factory: LearningScopeAnalyzerFactory | None = None,
        max_workers: int = 2,
    ):
        self._runtime_factory = runtime_factory
        self._health_provider = health_provider
        self._scope_analyzer_factory = scope_analyzer_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="planix-learning-api",
        )
        self._runs: dict[str, _LearningRunHandle] = {}
        self._lock = RLock()

    def create_intake(self, payload: LearningIntakeCreateRequest) -> LearningIntakeResponse:
        intake_id = f"learning-intake-{uuid4()}"
        runtime = self._runtime_factory()
        analysis = self._analyzer(runtime).analyze(
            intake_id=intake_id,
            message=payload.message,
            source_ref=f"user:{intake_id}:message:1",
            preferred_language=payload.preferred_language,
        )
        runtime.create_session(intake_id)
        runtime.artifact_store.save_artifact(intake_id, analysis.scope)
        with self._lock:
            self._runs[intake_id] = _LearningRunHandle(runtime=runtime)
        run_id = None
        if analysis.readiness.ready_for_planning:
            self._start_existing(intake_id, analysis.scope)
            run_id = intake_id
        return self._intake_response(
            intake_id,
            runtime,
            analysis.scope,
            run_id=run_id,
        )

    def get_intake(self, intake_id: str) -> LearningIntakeResponse:
        runtime = self.get_runtime(intake_id)
        scope = self._latest_scope(runtime, intake_id)
        state = runtime.get_session(intake_id)
        with self._lock:
            handle = self._runs.get(intake_id)
        started = bool(handle and handle.future is not None)
        run_id = (
            intake_id
            if state is not None and (state.status != "created" or started)
            else None
        )
        return self._intake_response(intake_id, runtime, scope, run_id=run_id)

    def supplement_intake(
        self,
        intake_id: str,
        payload: LearningIntakeSupplementRequest,
    ) -> LearningIntakeResponse:
        runtime = self.get_runtime(intake_id)
        state = runtime.get_session(intake_id)
        if state is None:
            raise KeyError(intake_id)
        if state.status != "created":
            raise ValueError("Learning intake has already started")
        if payload.message and is_continue_intent(payload.message) and not payload.resource_urls:
            return self.continue_intake(intake_id)
        current = self._latest_scope(runtime, intake_id)
        source_ref = f"user:{intake_id}:message:{current.version + 1}"
        scope = current
        if payload.message:
            analysis = self._analyzer(runtime).analyze(
                intake_id=intake_id,
                current_scope=current,
                message=payload.message,
                source_ref=source_ref,
                preferred_language=payload.preferred_language,
            )
            scope = analysis.scope
        if payload.resource_urls:
            resource_version = scope.version if payload.message else scope.version + 1
            resource_source_ref = f"user:{intake_id}:resource:{resource_version}"
            scope = LearningScopePatcher.patch_resource_urls(
                scope,
                payload.resource_urls,
                resource_source_ref,
                increment_version=not bool(payload.message),
            )
        runtime.artifact_store.save_artifact(intake_id, scope)
        run_id = None
        readiness = evaluate_readiness(scope)
        if readiness.ready_for_planning and not payload.defer_auto_start:
            self._start_existing(intake_id, scope)
            run_id = intake_id
        return self._intake_response(
            intake_id,
            runtime,
            scope,
            run_id=run_id,
        )

    def continue_intake(self, intake_id: str) -> LearningIntakeResponse:
        runtime = self.get_runtime(intake_id)
        state = runtime.get_session(intake_id)
        if state is None:
            raise KeyError(intake_id)
        scope = self._latest_scope(runtime, intake_id)
        if state.status == "created":
            if not scope.confirmed:
                scope = scope.model_copy(
                    deep=True,
                    update={
                        "version": scope.version + 1,
                        "created_at": datetime.now(UTC)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "confirmed": True,
                        "source_refs": [
                            *scope.source_refs,
                            f"user:{intake_id}:action:continue",
                        ],
                    },
                )
                runtime.artifact_store.save_artifact(intake_id, scope)
            self._start_existing(intake_id, scope)
            return self._intake_response(intake_id, runtime, scope, run_id=intake_id)
        if state.status in {"running", "completed"}:
            return self._intake_response(intake_id, runtime, scope, run_id=intake_id)
        raise ValueError("Learning intake cannot continue after failure")

    def create_run(self, payload: LearningRunCreateRequest) -> str:
        runtime = self._runtime_factory()
        session = runtime.create_session()
        handle = _LearningRunHandle(runtime=runtime)
        with self._lock:
            self._runs[session.session_id] = handle
            handle.future = self._executor.submit(
                self._execute,
                session.session_id,
                self._scope(payload),
            )
        return session.session_id

    def _start_existing(self, run_id: str, scope: LearningScope) -> None:
        with self._lock:
            handle = self._runs.get(run_id)
            if handle is None:
                handle = _LearningRunHandle(runtime=self.get_runtime(run_id))
                self._runs[run_id] = handle
            if handle.future is not None and not handle.future.done():
                return
            handle.future = self._executor.submit(self._execute, run_id, scope)

    def resume_evidence(self, run_id: str) -> None:
        runtime = self.get_runtime(run_id)
        state = runtime.get_session(run_id)
        if state is None:
            raise KeyError(run_id)
        if state.status != "waiting_evidence":
            raise ValueError("Learning run is not waiting for evidence")
        with self._lock:
            handle = self._runs.get(run_id)
            if handle is None:
                handle = _LearningRunHandle(runtime=runtime)
                self._runs[run_id] = handle
            if handle.future is not None and not handle.future.done():
                raise RuntimeError("Learning evidence resume is already running")
            handle.future = self._executor.submit(self._execute_resume, run_id)

    def get_intervention(
        self,
        run_id: str,
    ) -> LearningEvidenceInterventionResponse | None:
        runtime = self.get_runtime(run_id)
        report = runtime.artifact_store.get_latest_artifact(
            run_id,
            "evidence_intervention_report",
        )
        if not isinstance(report, EvidenceInterventionReport):
            return None
        evidence = runtime.artifact_store.get_latest_artifact(
            run_id,
            "evidence_graph",
        )
        graph = evidence if isinstance(evidence, EvidenceGraph) else None
        knowledge_artifact = runtime.artifact_store.get_latest_artifact(
            run_id,
            "knowledge_graph",
        )
        knowledge = (
            knowledge_artifact
            if isinstance(knowledge_artifact, KnowledgeGraph)
            else None
        )
        coverage_by_id = {
            item.knowledge_id: item.coverage_strength
            for item in report.knowledge_coverage
        }
        return LearningEvidenceInterventionResponse(
            requiredGaps=report.required_gaps,
            searchedResources=report.searched_resource_refs,
            transcriptUnavailableResources=(
                report.transcript_unavailable_resource_refs
            ),
            verifiedResources=[
                LearningInterventionResourceResponse(
                    id=item.id,
                    title=item.title,
                    canonicalUrl=item.canonical_url,
                    availability=item.availability,
                )
                for item in (graph.resources if graph is not None else [])
            ],
            verifiedSegments=[
                LearningInterventionSegmentResponse(
                    id=item.id,
                    resourceId=item.resource_id,
                    startSeconds=item.start_seconds,
                    endSeconds=item.end_seconds,
                    contentSummary=item.content_summary,
                )
                for item in (graph.segments if graph is not None else [])
            ],
            knowledgeCoverage=[
                LearningInterventionKnowledgeResponse(
                    id=item.id,
                    name=item.name,
                    importance=item.importance,
                    coverageStrength=coverage_by_id.get(item.id, "MISSING"),
                )
                for item in (knowledge.nodes if knowledge is not None else [])
            ],
            canResume=True,
        )

    def get_runtime(self, run_id: str) -> LearningRuntime:
        with self._lock:
            handle = self._runs.get(run_id)
        if handle is None:
            runtime = self._runtime_factory()
            if runtime.get_session(run_id) is None:
                raise KeyError(run_id)
            recovered = _LearningRunHandle(runtime=runtime)
            with self._lock:
                handle = self._runs.setdefault(run_id, recovered)
        return handle.runtime

    def get_result(self, run_id: str) -> LearningRunResult | None:
        with self._lock:
            handle = self._runs.get(run_id)
        if handle is None:
            self.get_runtime(run_id)
            with self._lock:
                handle = self._runs[run_id]
        result = handle.result
        if result is None:
            result = handle.runtime.get_result(run_id)
            if result is not None:
                with self._lock:
                    handle.result = result.model_copy(deep=True)
        return result.model_copy(deep=True) if result is not None else None

    def get_evidence_graph(self, run_id: str) -> EvidenceGraph | None:
        result = self.get_result(run_id)
        if result is None:
            return None
        ref = result.artifacts.get("evidence_graph")
        if ref is None:
            return None
        artifact = self.get_runtime(run_id).artifact_store.get_artifact(run_id, ref)
        return artifact.model_copy(deep=True) if isinstance(artifact, EvidenceGraph) else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def health(self) -> dict:
        if self._health_provider is not None:
            return self._health_provider()
        health_check = getattr(self._runtime_factory, "health", None)
        if callable(health_check):
            return health_check()
        try:
            self._runtime_factory()
            return {
                "status": "ready",
                "environment": "injected",
                "providers": {},
                "artifact_store": {"status": "ready", "name": "injected"},
                "error": None,
            }
        except RuntimeError as exc:
            return {
                "status": "unavailable",
                "environment": "unconfigured",
                "providers": {},
                "artifact_store": {"status": "unknown", "name": ""},
                "error": {"component": "runtime", "message": str(exc)},
            }

    def _execute(self, run_id: str, scope: LearningScope) -> None:
        runtime = self.get_runtime(run_id)
        try:
            result = runtime.run(scope, session_id=run_id)
        except LearningRuntimeError:
            return
        if isinstance(result, LearningWaitingEvidenceResult):
            return
        with self._lock:
            self._runs[run_id].result = result.model_copy(deep=True)

    def _execute_resume(self, run_id: str) -> None:
        runtime = self.get_runtime(run_id)
        try:
            result = runtime.resume_evidence(run_id)
        except LearningRuntimeError:
            return
        if isinstance(result, LearningWaitingEvidenceResult):
            return
        with self._lock:
            self._runs[run_id].result = result.model_copy(deep=True)

    def _analyzer(self, runtime: LearningRuntime) -> LearningScopeAnalyzer:
        if self._scope_analyzer_factory is not None:
            return self._scope_analyzer_factory(runtime)
        model = getattr(runtime.pipeline, "semantic_model", None)
        if model is None:
            raise RuntimeError("Learning semantic model is unavailable")
        return LearningScopeAnalyzer(model)

    @staticmethod
    def _latest_scope(runtime: LearningRuntime, intake_id: str) -> LearningScope:
        scope = runtime.artifact_store.get_latest_artifact(intake_id, "learning_scope")
        if not isinstance(scope, LearningScope):
            raise KeyError(intake_id)
        return scope

    @staticmethod
    def _intake_response(
        intake_id: str,
        runtime: LearningRuntime,
        scope: LearningScope,
        *,
        run_id: str | None,
    ) -> LearningIntakeResponse:
        state = runtime.get_session(intake_id)
        if state is None:
            raise KeyError(intake_id)
        status: LearningIntakeStatus
        if state.status == "created":
            status = "running" if run_id is not None else "waiting_scope_review"
        else:
            status = state.status
        readiness = evaluate_readiness(scope)
        return LearningIntakeResponse(
            intakeId=intake_id,
            status=status,
            scope=scope,
            review=project_scope_review(scope, readiness),
            runId=run_id,
        )

    @staticmethod
    def _scope(payload: LearningRunCreateRequest) -> LearningScope:
        goal = payload.goal.strip()
        if not goal:
            raise ValueError("goal must not be blank")
        constraints = [item.strip() for item in payload.constraints if item.strip()]
        constraint_refs = [f"api:constraint:{index}" for index in range(len(constraints))]
        assumptions = [
            LearningAssumption(
                id=f"constraint-{index}",
                statement=constraint,
                basis="Explicit Learning API constraint.",
                sourceRef=constraint_refs[index],
                impact="high",
            )
            for index, constraint in enumerate(constraints)
        ]
        preferences = payload.preferences
        return LearningScope(
            userGoal=goal,
            targetResult=preferences.target_result.strip() or goal,
            targetResultStatus=(
                "explicit" if preferences.target_result.strip() else "assumed"
            ),
            currentLevel=preferences.current_level,
            contentBudget=preferences.content_budget,
            languagePreference=preferences.language_preference,
            resourcePreference=preferences.resource_preference,
            assumptions=assumptions,
            sourceRefs=[
                "api:goal",
                *(["api:target-result"] if preferences.target_result.strip() else []),
                *constraint_refs,
            ],
            explicitScopeAnchors=build_explicit_scope_anchors(
                user_goal=goal,
                user_goal_source_ref="api:goal",
                target_result=preferences.target_result.strip() or goal,
                target_result_status=(
                    "explicit" if preferences.target_result.strip() else "assumed"
                ),
                target_result_source_ref=(
                    "api:target-result" if preferences.target_result.strip() else None
                ),
                constraints=list(zip(constraints, constraint_refs, strict=True)),
            ),
            confirmed=preferences.confirmed,
        )


_learning_runtime_factory = LearningRuntimeFactory(
    LearningRuntimeConfig(
        video_provider=None,
        transcript_provider=None,
        artifact_store="postgres",
        model_provider=None,
        environment="production",
    )
)
_learning_run_manager = LearningRunManager(_learning_runtime_factory)
_learning_transcript_service: LearningTranscriptRegistrationService | None = None


def get_learning_run_manager() -> LearningRunManager:
    return _learning_run_manager


def get_learning_transcript_service() -> LearningTranscriptRegistrationService:
    if _learning_transcript_service is None:
        raise HTTPException(
            status_code=503,
            detail="Learning Transcript Registry is unavailable",
        )
    return _learning_transcript_service


LearningTranscriptServiceDependency = Annotated[
    LearningTranscriptRegistrationService,
    Depends(get_learning_transcript_service),
]


def configure_learning_runtime_factory(
    factory: LearningRuntimeBuilder,
    *,
    health_provider: LearningHealthProvider | None = None,
) -> None:
    """Install the production composition root during application startup."""
    global _learning_run_manager
    previous = _learning_run_manager
    _learning_run_manager = LearningRunManager(
        factory,
        health_provider=health_provider,
    )
    previous.shutdown()


def configure_learning_transcript_service(
    service: LearningTranscriptRegistrationService | None,
) -> None:
    global _learning_transcript_service
    _learning_transcript_service = service


def shutdown_learning_runtime_manager() -> None:
    _learning_run_manager.shutdown()


LearningManagerDependency = Annotated[
    LearningRunManager,
    Depends(get_learning_run_manager),
]


@router.get("/health")
def get_learning_health(
    response: Response,
    manager: LearningManagerDependency,
) -> dict:
    health = manager.health()
    if health.get("status") != "ready":
        response.status_code = 503
    return health


@router.post(
    "/intakes",
    response_model=LearningIntakeResponse,
    status_code=201,
)
def create_learning_intake(
    payload: LearningIntakeCreateRequest,
    manager: LearningManagerDependency,
) -> LearningIntakeResponse:
    try:
        return manager.create_intake(payload)
    except (LearningModelOutputError, ArtifactStoreError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Learning scope analysis is temporarily unavailable",
        ) from exc


@router.get("/intakes/{intake_id}", response_model=LearningIntakeResponse)
def get_learning_intake(
    intake_id: str,
    manager: LearningManagerDependency,
) -> LearningIntakeResponse:
    try:
        return manager.get_intake(intake_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning intake not found") from exc


@router.post(
    "/intakes/{intake_id}/supplements",
    response_model=LearningIntakeResponse,
)
def supplement_learning_intake(
    intake_id: str,
    payload: LearningIntakeSupplementRequest,
    manager: LearningManagerDependency,
) -> LearningIntakeResponse:
    try:
        return manager.supplement_intake(intake_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning intake not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Learning intake has already started") from exc
    except (LearningModelOutputError, ArtifactStoreError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Learning scope analysis is temporarily unavailable",
        ) from exc


@router.post(
    "/intakes/{intake_id}/continue",
    response_model=LearningIntakeResponse,
    status_code=202,
)
def continue_learning_intake(
    intake_id: str,
    manager: LearningManagerDependency,
) -> LearningIntakeResponse:
    try:
        return manager.continue_intake(intake_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning intake not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Learning intake cannot continue") from exc
    except (ArtifactStoreError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Learning run is temporarily unavailable",
        ) from exc


@router.post("/runs", response_model=LearningRunCreateResponse, status_code=202)
def create_learning_run(
    payload: LearningRunCreateRequest,
    manager: LearningManagerDependency,
) -> LearningRunCreateResponse:
    try:
        return LearningRunCreateResponse(run_id=manager.create_run(payload))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/transcripts",
    response_model=TranscriptSourceSummary,
    response_model_by_alias=False,
    status_code=201,
)
async def register_learning_transcript(
    request: Request,
    service: LearningTranscriptServiceDependency,
) -> TranscriptSourceSummary:
    payload = await _bounded_transcript_request(request)
    try:
        source = service.register(
            video_url=payload.video_url,
            source_format=payload.format,
            language=payload.language,
            content=payload.content,
            source_name=payload.source_name,
        )
        metadata = service.get_metadata(source.source_id)
        if metadata is None:
            raise TranscriptRepositoryError("registered transcript metadata is missing")
        return metadata
    except TranscriptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TranscriptRegistrationError as exc:
        status_code = 413 if exc.error_type == "payload_too_large" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"message": exc.message, "errorType": exc.error_type},
        ) from exc
    except TranscriptRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Learning Transcript Registry write failed",
        ) from exc


async def _bounded_transcript_request(
    request: Request,
) -> LearningTranscriptCreateRequest:
    content_type = (
        request.headers.get("content-type", "")
        .split(";", 1)[0]
        .strip()
        .casefold()
    )
    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Transcript registration requires application/json",
        )
    declared_length = request.headers.get("content-length", "").strip()
    if declared_length:
        try:
            if int(declared_length) > TRANSCRIPT_REQUEST_HARD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Transcript request exceeds the hard size limit",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > TRANSCRIPT_REQUEST_HARD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Transcript request exceeds the hard size limit",
            )
    try:
        decoded = bytes(body).decode("utf-8", errors="strict")
        raw = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Transcript request must be valid UTF-8 JSON",
        ) from exc
    try:
        return LearningTranscriptCreateRequest.model_validate(raw)
    except ValidationError as exc:
        safe_errors = [
            {
                "loc": list(item.get("loc", ())),
                "msg": item.get("msg", "invalid value"),
                "type": item.get("type", "value_error"),
            }
            for item in exc.errors()
        ]
        raise HTTPException(status_code=422, detail=safe_errors) from exc


@router.get(
    "/transcripts/{source_id}",
    response_model=TranscriptSourceSummary,
    response_model_by_alias=False,
)
def get_learning_transcript(
    source_id: str,
    service: LearningTranscriptServiceDependency,
) -> TranscriptSourceSummary:
    metadata = service.get_metadata(source_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Learning transcript not found")
    return metadata


@router.delete(
    "/transcripts/{source_id}",
    response_model=LearningTranscriptRevokeResponse,
    response_model_by_alias=False,
)
def revoke_learning_transcript(
    source_id: str,
    service: LearningTranscriptServiceDependency,
) -> LearningTranscriptRevokeResponse:
    if not service.revoke(source_id):
        raise HTTPException(status_code=404, detail="Learning transcript not found")
    return LearningTranscriptRevokeResponse(source_id=source_id)


@router.get("/runs/{run_id}", response_model=LearningRunStatusResponse)
def get_learning_run(
    run_id: str,
    manager: LearningManagerDependency,
) -> LearningRunStatusResponse:
    runtime = _runtime_or_404(manager, run_id)
    state = runtime.get_session(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Learning run not found")
    error = (
        LearningRunErrorResponse(
            stage=state.error.stage,
            error_type=state.error.error_type,
            message=state.error.message,
            validator_rule=state.error.validator_rule,
            field_path=state.error.field_path,
        )
        if state.error is not None
        else None
    )
    return LearningRunStatusResponse(
        status=state.status,
        current_stage=state.current_stage,
        completed_stages=list(state.completed_stages),
        error=error,
        intervention=(
            manager.get_intervention(run_id)
            if state.status == "waiting_evidence"
            else None
        ),
    )


@router.post(
    "/runs/{run_id}/resume-evidence",
    response_model=LearningRunStatusResponse,
    status_code=202,
)
def resume_learning_run_evidence(
    run_id: str,
    manager: LearningManagerDependency,
) -> LearningRunStatusResponse:
    try:
        manager.resume_evidence(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning run not found") from exc
    except ArtifactStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail="Learning evidence resume is temporarily unavailable",
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    return get_learning_run(run_id, manager)


@router.get("/runs/{run_id}/events")
def get_learning_run_events(
    run_id: str,
    manager: LearningManagerDependency,
    after: int = 0,
) -> StreamingResponse:
    runtime = _runtime_or_404(manager, run_id)

    def stream():
        cursor = max(0, after)
        while True:
            events = runtime.get_events(run_id)
            while cursor < len(events):
                event = events[cursor]
                payload = json.dumps(
                    event.model_dump(mode="json", by_alias=False),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {cursor + 1}\nevent: progress\ndata: {payload}\n\n"
                cursor += 1
            state = runtime.get_session(run_id)
            if state is None or state.status in {
                "completed",
                "failed",
                "waiting_evidence",
            }:
                time.sleep(0.01)
                final_events = runtime.get_events(run_id)
                while cursor < len(final_events):
                    event = final_events[cursor]
                    payload = json.dumps(
                        event.model_dump(mode="json", by_alias=False),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield (
                        f"id: {cursor + 1}\nevent: progress\n"
                        f"data: {payload}\n\n"
                    )
                    cursor += 1
                return
            time.sleep(0.02)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/runs/{run_id}/result", response_model=LearningRunResultResponse)
def get_learning_run_result(
    run_id: str,
    manager: LearningManagerDependency,
) -> LearningRunResultResponse:
    runtime = _runtime_or_404(manager, run_id)
    state = runtime.get_session(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Learning run not found")
    if state.status == "failed":
        raise HTTPException(status_code=409, detail="Learning run failed")
    if state.status == "waiting_evidence":
        raise HTTPException(
            status_code=409,
            detail="Learning run is waiting for additional evidence",
        )
    result = manager.get_result(run_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Learning run is not complete")
    evidence_graph = manager.get_evidence_graph(run_id)
    if evidence_graph is None:
        raise HTTPException(status_code=409, detail="Learning evidence is unavailable")
    return LearningRunResultResponse(
        learning_content_plan=result.final_plan,
        learning_quality_report=result.quality_report,
        evidence_graph=evidence_graph,
    )


def _runtime_or_404(manager: LearningRunManager, run_id: str) -> LearningRuntime:
    try:
        return manager.get_runtime(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning run not found") from exc


__all__ = [
    "LearningIntakeCreateRequest",
    "LearningIntakeResponse",
    "LearningIntakeSupplementRequest",
    "LearningRunCreateRequest",
    "LearningRunManager",
    "LearningRunPreferences",
    "LearningTranscriptCreateRequest",
    "LearningTranscriptRevokeResponse",
    "configure_learning_transcript_service",
    "configure_learning_runtime_factory",
    "get_learning_health",
    "get_learning_run_manager",
    "get_learning_transcript_service",
    "router",
    "shutdown_learning_runtime_manager",
]
