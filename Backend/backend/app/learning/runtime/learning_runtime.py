from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
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
        return state.model_copy(deep=True) if state is not None else None

    def get_events(self, session_id: str) -> list[LearningProgressEvent]:
        return [item.model_copy(deep=True) for item in self._events.get(session_id, [])]

    def run(
        self,
        scope: LearningScope,
        *,
        session_id: str | None = None,
    ) -> LearningRunResult:
        if session_id is None:
            state = self._sessions[self.create_session().session_id]
        elif session_id not in self._sessions:
            state = self._sessions[self.create_session(session_id).session_id]
        else:
            state = self._sessions[session_id]
        if state.status != "created":
            raise ValueError(
                f"Learning session {state.session_id} is already {state.status}"
            )

        artifacts = self._artifact_refs[state.session_id]
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
        self._events[state.session_id].append(event)
        if self.event_sink is not None:
            self.event_sink(state.session_id, event.model_copy(deep=True))
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


__all__ = [
    "LearningRuntime",
    "LearningRuntimeError",
    "ProgressEventSink",
]
