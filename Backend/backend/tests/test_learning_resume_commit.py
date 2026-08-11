from __future__ import annotations

from app.learning.runtime import (
    ArtifactBundle,
    InMemoryArtifactStore,
    LearningResumeCoordinator,
    LearningRunCheckpoint,
    LearningStageRegistry,
    ResumeCommitService,
)
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture


class FailingCommitStore(InMemoryArtifactStore):
    def __init__(self):
        super().__init__()
        self.failure = ""

    def save_artifact(self, session_id, artifact):
        if self.failure == "artifact":
            raise RuntimeError("injected artifact save failure")
        return super().save_artifact(session_id, artifact)

    def save_checkpoint(self, checkpoint):
        if self.failure == "checkpoint":
            raise RuntimeError("injected checkpoint save failure")
        return super().save_checkpoint(checkpoint)

    def save_resume_event(self, event):
        if self.failure == "audit":
            raise RuntimeError("injected resume audit failure")
        return super().save_resume_event(event)


def _complete_artifacts():
    fixture = build_fastapi_learning_pipeline_fixture()
    result = LearningPipeline(
        provider=fixture.provider,
        model=fixture.model,
    ).run(fixture.scope)
    return {
        "learning_scope": result.scope,
        "capability_graph": result.capability_graph,
        "knowledge_graph": result.knowledge_graph,
        "evidence_graph": result.evidence_graph,
        "content_selection": result.content_selection,
        "learning_content_plan": result.learning_content_plan,
        "learning_quality_report": result.quality_report,
    }


def _evidence_commit_setup(store=None, *, evidence=None):
    resolved_store = store or InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-commit-evidence"
    refs = [
        resolved_store.save_artifact(run_id, artifacts[artifact_type])
        for artifact_type in (
            "learning_scope",
            "capability_graph",
            "knowledge_graph",
        )
    ]
    checkpoint = LearningRunCheckpoint(
        runId=run_id,
        currentStage="failed",
        status="failed",
        artifactRefs=refs,
        lastSuccessfulStage="knowledge_generation",
    )
    resolved_store.save_checkpoint(checkpoint)
    output_evidence = evidence or artifacts["evidence_graph"]
    registry = LearningStageRegistry.default(
        executors={
            "evidence_generation": lambda context: ArtifactBundle(
                (output_evidence,)
            )
        }
    )
    coordinator = LearningResumeCoordinator(
        resolved_store,
        resolved_store,
        registry=registry,
    )
    service = ResumeCommitService(
        coordinator,
        resolved_store,
        registry=registry,
    )
    return artifacts, resolved_store, run_id, checkpoint, coordinator, service


def test_stage_execution_success_commits_complete_result() -> None:
    artifacts, store, run_id, before, _, service = _evidence_commit_setup()

    result = service.execute(run_id)

    assert result.status == "completed"
    assert result.stage == "evidence_generation"
    assert result.checkpoint_before == before
    assert result.checkpoint_after is not None
    assert result.checkpoint_after.last_successful_stage == "evidence_generation"
    assert store.get_artifact(run_id, result.artifact_refs[0]) == artifacts[
        "evidence_graph"
    ]


def test_artifact_and_checkpoint_are_updated_together() -> None:
    _, store, run_id, _, _, service = _evidence_commit_setup()

    result = service.execute(run_id)
    persisted_checkpoint = store.get_checkpoint(run_id)

    assert result.status == "completed"
    assert persisted_checkpoint == result.checkpoint_after
    assert result.artifact_refs[0] in persisted_checkpoint.artifact_refs
    assert store.exists(run_id, result.artifact_refs[0]) is True


def test_resume_audit_matches_committed_checkpoint() -> None:
    _, store, run_id, _, _, service = _evidence_commit_setup()

    result = service.execute(run_id)
    events = store.get_resume_events(run_id)

    assert result.status == "completed"
    assert len(events) == 1
    assert result.audit_ref == events[0].event_id
    assert events[0].resume_stage == "evidence_generation"
    assert events[0].artifact_refs == result.checkpoint_after.artifact_refs


def test_committed_stage_can_resume_the_next_adjacent_stage() -> None:
    _, store, run_id, _, coordinator, service = _evidence_commit_setup()

    committed = service.execute(run_id)
    next_decision = coordinator.decide(run_id)

    assert committed.status == "completed"
    assert next_decision.allowed is True
    assert next_decision.resume_stage == "coverage_analysis"


def test_artifact_save_failure_rolls_back_everything() -> None:
    store = FailingCommitStore()
    _, store, run_id, before, _, service = _evidence_commit_setup(store)
    store.failure = "artifact"

    result = service.execute(run_id)

    assert result.status == "failed"
    assert "artifact save failure" in result.error
    assert store.get_checkpoint(run_id) == before
    assert store.get_latest_version(run_id, "evidence_graph") is None
    assert store.get_resume_events(run_id) == []


def test_checkpoint_failure_rolls_back_saved_artifact() -> None:
    store = FailingCommitStore()
    _, store, run_id, before, _, service = _evidence_commit_setup(store)
    store.failure = "checkpoint"

    result = service.execute(run_id)

    assert result.status == "failed"
    assert "checkpoint save failure" in result.error
    assert store.get_checkpoint(run_id) == before
    assert store.get_latest_version(run_id, "evidence_graph") is None
    assert store.get_resume_events(run_id) == []


def test_invalid_lineage_is_rejected_before_commit() -> None:
    artifacts = _complete_artifacts()
    bad_ref = artifacts["evidence_graph"].knowledge_graph_ref.model_copy(
        update={"artifact_id": "forged-knowledge"}
    )
    invalid_evidence = artifacts["evidence_graph"].model_copy(
        update={"knowledge_graph_ref": bad_ref}
    )
    _, store, run_id, before, _, service = _evidence_commit_setup(
        evidence=invalid_evidence
    )

    result = service.execute(run_id)

    assert result.status == "failed"
    assert "version_compatibility" in result.error
    assert store.get_checkpoint(run_id) == before
    assert store.get_latest_version(run_id, "evidence_graph") is None


def test_cross_stage_jump_is_rejected_without_execution() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-commit-jump"
    refs = [store.save_artifact(run_id, artifacts["learning_scope"])]
    checkpoint = LearningRunCheckpoint(
        runId=run_id,
        currentStage="selection",
        status="running",
        artifactRefs=refs,
        lastSuccessfulStage="scope",
    )
    store.save_checkpoint(checkpoint)
    calls = []
    registry = LearningStageRegistry.default(
        executors={
            "selection": lambda context: calls.append(context) or ArtifactBundle()
        }
    )
    coordinator = LearningResumeCoordinator(store, store, registry=registry)
    service = ResumeCommitService(coordinator, store, registry=registry)

    result = service.execute(run_id)

    assert result.status == "failed"
    assert "not adjacent" in result.error
    assert calls == []
    assert store.get_checkpoint(run_id) == checkpoint


def test_audit_failure_rolls_back_artifact_and_checkpoint() -> None:
    store = FailingCommitStore()
    _, store, run_id, before, _, service = _evidence_commit_setup(store)
    store.failure = "audit"

    result = service.execute(run_id)

    assert result.status == "failed"
    assert "resume audit failure" in result.error
    assert store.get_checkpoint(run_id) == before
    assert store.get_latest_version(run_id, "evidence_graph") is None
    assert store.get_resume_events(run_id) == []
