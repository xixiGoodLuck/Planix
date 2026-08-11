from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import nullcontext
from hashlib import sha256
import json
from typing import cast
from uuid import uuid4

from ..contracts import LearningArtifact, LearningArtifactRef, LearningArtifactType, LearningScope
from ..services.learning_pipeline import (
    LearningPipeline,
    LearningPipelineError,
    PipelineProgressStatus,
)
from .artifact_store import ArtifactStore, CheckpointStore, InMemoryArtifactStore
from .contracts import (
    LearningProgressEvent,
    LearningRunCheckpoint,
    LearningRunResult,
    LearningSessionError,
    LearningSessionStage,
    LearningSessionState,
)
from .recovery import LearningRecoveryService


ProgressEventSink = Callable[[str, LearningProgressEvent], None]


class LearningRuntimeError(RuntimeError):
    def __init__(self, session: LearningSessionState):
        self.session = session
        message = session.error.message if session.error else "Learning runtime failed"
        super().__init__(message)


class LearningRuntime:
    """Runtime boundary around LearningPipeline; it owns no generation logic."""

    _PIPELINE_STAGES = {
        "knowledge_generating",
        "evidence_generating",
        "content_selecting",
        "quality_checking",
    }

    def __init__(
        self,
        pipeline: LearningPipeline,
        *,
        artifact_store: ArtifactStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        event_sink: ProgressEventSink | None = None,
    ):
        self.pipeline = pipeline
        self.artifact_store = (
            artifact_store if artifact_store is not None else InMemoryArtifactStore()
        )
        if checkpoint_store is not None:
            self.checkpoint_store = checkpoint_store
        elif hasattr(self.artifact_store, "save_checkpoint") and hasattr(
            self.artifact_store,
            "get_checkpoint",
        ):
            self.checkpoint_store = cast(CheckpointStore, self.artifact_store)
        else:
            self.checkpoint_store = None
        self.event_sink = event_sink
        self._sessions: dict[str, LearningSessionState] = {}
        self._artifact_refs: dict[
            str,
            dict[LearningArtifactType, LearningArtifactRef],
        ] = {}
        self._events: dict[str, list[LearningProgressEvent]] = defaultdict(list)
        self._run_fingerprints: dict[str, str] = {}

    def create_session(self, session_id: str | None = None) -> LearningSessionState:
        resolved_id = session_id or f"learning-session-{uuid4()}"
        if resolved_id in self._sessions:
            raise ValueError(f"Learning session already exists: {resolved_id}")
        state = LearningSessionState(sessionId=resolved_id)
        self._sessions[resolved_id] = state
        self._artifact_refs[resolved_id] = {}
        self._emit(
            state,
            LearningProgressEvent(
                eventType="session_created",
                stage="created",
                status="created",
                message="Learning session created.",
            ),
        )
        return state.model_copy(deep=True)

    def get_session(self, session_id: str) -> LearningSessionState | None:
        state = self._sessions.get(session_id)
        if state is None:
            load = getattr(self.artifact_store, "get_run", None)
            if callable(load):
                state = load(session_id)
                if state is not None:
                    checkpoint = (
                        self.checkpoint_store.get_checkpoint(session_id)
                        if self.checkpoint_store is not None
                        else None
                    )
                    if checkpoint is not None and self.checkpoint_store is not None:
                        recovered = LearningRecoveryService(
                            self.artifact_store,
                            self.checkpoint_store,
                        ).recover(session_id)
                        checkpoint = recovered.checkpoint
                        if recovered.status == "failed":
                            state.current_stage = "failed"
                            state.status = "failed"
                            state.error = LearningSessionError(
                                stage="failed",
                                errorType="LearningRecoveryError",
                                message=recovered.error or "Learning recovery failed",
                            )
                            save_run = getattr(self.artifact_store, "save_run", None)
                            if callable(save_run):
                                save_run(state)
                    self._sessions[session_id] = state.model_copy(deep=True)
                    self._artifact_refs[session_id] = {
                        ref.artifact_type: ref
                        for ref in (checkpoint.artifact_refs if checkpoint else [])
                    }
        return state.model_copy(deep=True) if state is not None else None

    def get_events(self, session_id: str) -> list[LearningProgressEvent]:
        events = self._events.get(session_id, [])
        if not events:
            load = getattr(self.artifact_store, "get_progress_events", None)
            if callable(load):
                events = load(session_id)
                self._events[session_id] = [item.model_copy(deep=True) for item in events]
        return [item.model_copy(deep=True) for item in events]

    def get_result(self, session_id: str) -> LearningRunResult | None:
        state = self.get_session(session_id)
        if state is None or state.status != "completed":
            return None
        checkpoint = (
            self.checkpoint_store.get_checkpoint(session_id)
            if self.checkpoint_store is not None
            else None
        )
        refs = {
            ref.artifact_type: ref
            for ref in (checkpoint.artifact_refs if checkpoint is not None else [])
        }
        plan_ref = refs.get("learning_content_plan")
        quality_ref = refs.get("learning_quality_report")
        if plan_ref is None or quality_ref is None:
            return None
        plan = self.artifact_store.get_artifact(session_id, plan_ref)
        quality = self.artifact_store.get_artifact(session_id, quality_ref)
        from ..contracts import LearningContentPlan, LearningQualityReport

        if not isinstance(plan, LearningContentPlan) or not isinstance(
            quality,
            LearningQualityReport,
        ):
            return None
        return LearningRunResult(
            session=state,
            artifacts=refs,
            finalPlan=plan,
            qualityReport=quality,
        )

    def run(
        self,
        scope: LearningScope,
        *,
        session_id: str | None = None,
    ) -> LearningRunResult:
        if session_id is None:
            state = self._sessions[self.create_session().session_id]
        elif session_id not in self._sessions:
            loaded = self.get_session(session_id)
            state = (
                self._sessions[self.create_session(session_id).session_id]
                if loaded is None
                else self._sessions[session_id]
            )
        else:
            state = self._sessions[session_id]
        if state.status != "created":
            raise ValueError(
                f"Learning session {state.session_id} is already {state.status}"
            )

        artifacts = self._artifact_refs[state.session_id]
        self._run_fingerprints[state.session_id] = self._scope_fingerprint(scope)
        try:
            self._start_stage(state, "understanding")
            self._save_artifact(state, scope, artifacts)
            self._complete_stage(state, "understanding")

            pipeline_result = self.pipeline.run(
                scope,
                progress_callback=lambda stage, status, artifact: self._on_pipeline_progress(
                    state,
                    artifacts,
                    stage,
                    status,
                    artifact,
                ),
            )
            if not pipeline_result.quality_report.passed:
                raise LearningPipelineError(
                    stage="learning_quality",
                    artifact_type="learning_quality_report",
                    validator_rule="quality_gate",
                    field_path="learningQualityReport",
                    message="Learning quality gate failed",
                )
            state.current_stage = "completed"
            state.status = "completed"
            state.error = None
            self._emit(
                state,
                LearningProgressEvent(
                    eventType="session_completed",
                    stage="completed",
                    status="completed",
                    message="Learning pipeline completed.",
                ),
            )
            return LearningRunResult(
                session=state.model_copy(deep=True),
                artifacts=dict(artifacts),
                finalPlan=pipeline_result.learning_content_plan,
                qualityReport=pipeline_result.quality_report,
            )
        except Exception as exc:
            failed_stage = state.current_stage
            error = LearningSessionError(
                stage=failed_stage,
                errorType=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
                validatorRule=str(getattr(exc, "validator_rule", "")),
                fieldPath=str(getattr(exc, "field_path", "")),
            )
            state.current_stage = "failed"
            state.status = "failed"
            state.error = error
            self._emit(
                state,
                LearningProgressEvent(
                    eventType="session_failed",
                    stage="failed",
                    status="failed",
                    message=f"Learning runtime failed during {failed_stage}.",
                ),
            )
            raise LearningRuntimeError(state.model_copy(deep=True)) from exc

    def _on_pipeline_progress(
        self,
        state: LearningSessionState,
        artifacts: dict[LearningArtifactType, LearningArtifactRef],
        stage: str,
        status: PipelineProgressStatus,
        artifact: LearningArtifact | None,
    ) -> None:
        if stage not in self._PIPELINE_STAGES:
            raise ValueError(f"unsupported Learning pipeline stage: {stage}")
        runtime_stage = cast(LearningSessionStage, stage)
        if status == "started":
            self._start_stage(state, runtime_stage)
            return
        if artifact is not None:
            self._save_artifact(state, artifact, artifacts)
        if status == "completed":
            self._complete_stage(state, runtime_stage)

    def _start_stage(
        self,
        state: LearningSessionState,
        stage: LearningSessionStage,
    ) -> None:
        state.current_stage = stage
        state.status = "running"
        self._emit(
            state,
            LearningProgressEvent(
                eventType="stage_started",
                stage=stage,
                status="started",
                message=f"Learning stage started: {stage}.",
            ),
        )

    def _complete_stage(
        self,
        state: LearningSessionState,
        stage: LearningSessionStage,
    ) -> None:
        if stage not in state.completed_stages:
            state.completed_stages.append(stage)
        self._emit(
            state,
            LearningProgressEvent(
                eventType="stage_completed",
                stage=stage,
                status="completed",
                message=f"Learning stage completed: {stage}.",
            ),
        )

    def _save_artifact(
        self,
        state: LearningSessionState,
        artifact: LearningArtifact,
        artifacts: dict[LearningArtifactType, LearningArtifactRef],
    ) -> None:
        atomic = getattr(self.artifact_store, "atomic", None)
        boundary = atomic() if callable(atomic) else nullcontext()
        with boundary:
            ref = self.artifact_store.save_artifact(state.session_id, artifact)
            artifacts[ref.artifact_type] = ref
            state.current_artifact_ref = ref
            self._emit(
                state,
                LearningProgressEvent(
                    eventType="artifact_saved",
                    stage=state.current_stage,
                    status="saved",
                    message=f"Validated artifact saved: {ref.artifact_type}.",
                ),
            )

    def _emit(
        self,
        state: LearningSessionState,
        event: LearningProgressEvent,
    ) -> None:
        state.updated_at = event.timestamp
        atomic = getattr(self.artifact_store, "atomic", None)
        boundary = atomic() if callable(atomic) else nullcontext()
        with boundary:
            save_run = getattr(self.artifact_store, "save_run", None)
            if callable(save_run):
                save_run(
                    state,
                    run_fingerprint=self._run_fingerprints.get(state.session_id, ""),
                )
            if self.checkpoint_store is not None:
                self.checkpoint_store.save_checkpoint(
                    LearningRunCheckpoint(
                        runId=state.session_id,
                        currentStage=state.current_stage,
                        status=state.status,
                        artifactRefs=list(
                            self._artifact_refs.get(state.session_id, {}).values()
                        ),
                        lastSuccessfulStage=(
                            state.completed_stages[-1]
                            if state.completed_stages
                            else None
                        ),
                    )
                )
            save_event = getattr(self.artifact_store, "save_progress_event", None)
            if callable(save_event):
                save_event(state.session_id, event)
        self._events[state.session_id].append(event)
        if self.event_sink is not None:
            self.event_sink(state.session_id, event.model_copy(deep=True))

    @staticmethod
    def _scope_fingerprint(scope: LearningScope) -> str:
        payload = scope.model_dump(mode="json", by_alias=True)
        payload.pop("createdAt", None)
        canonical = json.dumps(
            {"scope": payload, "runtimeVersion": "learning-runtime-v1"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "LearningRuntime",
    "LearningRuntimeError",
    "ProgressEventSink",
]
