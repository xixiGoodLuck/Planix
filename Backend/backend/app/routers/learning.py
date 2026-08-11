from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import RLock
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..learning.contracts import (
    ContentBudget,
    CurrentLevel,
    EvidenceGraph,
    LanguagePreference,
    LearningAssumption,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
    ResourcePreference,
)
from ..learning.runtime import (
    LearningRunResult,
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


class LearningRunResultResponse(LearningApiModel):
    learning_content_plan: LearningContentPlan
    learning_quality_report: LearningQualityReport
    evidence_graph: EvidenceGraph


LearningRuntimeBuilder = Callable[[], LearningRuntime]
LearningHealthProvider = Callable[[], dict]


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
        max_workers: int = 2,
    ):
        self._runtime_factory = runtime_factory
        self._health_provider = health_provider
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="planix-learning-api",
        )
        self._runs: dict[str, _LearningRunHandle] = {}
        self._lock = RLock()

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
        with self._lock:
            self._runs[run_id].result = result.model_copy(deep=True)

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
            currentLevel=preferences.current_level,
            contentBudget=preferences.content_budget,
            languagePreference=preferences.language_preference,
            resourcePreference=preferences.resource_preference,
            assumptions=assumptions,
            sourceRefs=["api:goal", *constraint_refs],
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
    )


@router.get("/runs/{run_id}/events")
def get_learning_run_events(
    run_id: str,
    manager: LearningManagerDependency,
) -> StreamingResponse:
    runtime = _runtime_or_404(manager, run_id)

    def stream():
        cursor = 0
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
            if state is None or state.status in {"completed", "failed"}:
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
