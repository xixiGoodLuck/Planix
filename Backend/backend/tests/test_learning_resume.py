from __future__ import annotations

from app.learning.runtime import (
    InMemoryArtifactStore,
    LearningResumeCoordinator,
    LearningRunCheckpoint,
    LearningStageRegistry,
)
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture


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


def _save(store, run_id, artifacts, artifact_types):
    return [
        store.save_artifact(run_id, artifacts[artifact_type])
        for artifact_type in artifact_types
    ]


def _checkpoint(
    store,
    run_id,
    refs,
    *,
    current_stage,
    last_successful_stage,
    status="running",
):
    checkpoint = LearningRunCheckpoint(
        runId=run_id,
        currentStage=current_stage,
        status=status,
        artifactRefs=refs,
        lastSuccessfulStage=last_successful_stage,
    )
    store.save_checkpoint(checkpoint)
    return checkpoint


def test_evidence_stage_failure_resumes_evidence_executor() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-evidence"
    refs = _save(
        store,
        run_id,
        artifacts,
        ("learning_scope", "capability_graph", "knowledge_graph"),
    )
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="failed",
        last_successful_stage="knowledge_generation",
        status="failed",
    )
    calls = []
    registry = LearningStageRegistry.default(
        executors={"evidence_generation": lambda inputs: calls.append(set(inputs))}
    )

    decision = LearningResumeCoordinator(
        store,
        store,
        registry=registry,
    ).resume(run_id)

    assert decision.allowed is True
    assert decision.resume_stage == "evidence_generation"
    assert calls == [
        {"learning_scope", "capability_graph", "knowledge_graph"}
    ]


def test_quality_failure_resumes_quality_executor() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    artifacts["learning_quality_report"] = artifacts[
        "learning_quality_report"
    ].model_copy(update={"hard_rules_passed": False})
    run_id = "resume-quality"
    refs = _save(store, run_id, artifacts, tuple(artifacts))
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="failed",
        last_successful_stage="selection",
        status="failed",
    )
    calls = []
    registry = LearningStageRegistry.default(
        executors={"quality": lambda inputs: calls.append(inputs)}
    )

    decision = LearningResumeCoordinator(
        store,
        store,
        registry=registry,
    ).resume(run_id)

    assert decision.allowed is True
    assert decision.resume_stage == "quality"
    assert len(calls) == 1
    assert "learning_content_plan" in calls[0]


def test_coordinator_reads_checkpoint_and_records_resume_audit() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-checkpoint"
    refs = _save(
        store,
        run_id,
        artifacts,
        (
            "learning_scope",
            "capability_graph",
            "knowledge_graph",
            "evidence_graph",
        ),
    )
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="coverage_analysis",
        last_successful_stage="evidence_generation",
    )
    coordinator = LearningResumeCoordinator(store, store)

    decision = coordinator.decide(run_id)
    event = coordinator.get_events(run_id)[0]

    assert decision.resume_stage == "coverage_analysis"
    assert event.run_id == run_id
    assert event.previous_stage == "evidence_generation"
    assert event.resume_stage == "coverage_analysis"
    assert event.artifact_refs == refs


def test_validated_artifacts_continue_to_adjacent_gap_stage() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-gap"
    refs = _save(
        store,
        run_id,
        artifacts,
        (
            "learning_scope",
            "capability_graph",
            "knowledge_graph",
            "evidence_graph",
        ),
    )
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="gap_completion",
        last_successful_stage="coverage_analysis",
    )

    decision = LearningResumeCoordinator(store, store).decide(run_id)

    assert decision.allowed is True
    assert decision.resume_stage == "gap_completion"
    assert decision.validated_artifacts == refs


def test_corrupt_artifact_refuses_resume_and_is_removed() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-corrupt"
    refs = _save(
        store,
        run_id,
        artifacts,
        ("learning_scope", "capability_graph", "knowledge_graph"),
    )
    knowledge_ref = refs[-1]
    envelope = store._artifacts[run_id]["knowledge_graph"][
        knowledge_ref.artifact_id
    ][knowledge_ref.version]
    content = envelope.content.copy()
    content["nodes"] = []
    store._artifacts[run_id]["knowledge_graph"][knowledge_ref.artifact_id][
        knowledge_ref.version
    ] = envelope.model_copy(update={"content": content})
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="evidence_generation",
        last_successful_stage="knowledge_generation",
    )

    decision = LearningResumeCoordinator(store, store).decide(run_id)

    assert decision.allowed is False
    assert decision.resume_stage is None
    assert "Recovery validation failed" in decision.reason
    assert store.exists(run_id, knowledge_ref) is False


def test_missing_dependency_refuses_cross_stage_jump() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-missing-dependency"
    refs = _save(
        store,
        run_id,
        artifacts,
        ("learning_scope", "capability_graph", "knowledge_graph"),
    )
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="selection",
        last_successful_stage="evidence_generation",
    )

    decision = LearningResumeCoordinator(store, store).decide(run_id)

    assert decision.allowed is False
    assert decision.resume_stage is None
    assert "without outputs" in decision.reason
    assert "evidence_graph" in decision.reason


def test_incompatible_schema_refuses_resume() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-schema"
    refs = _save(store, run_id, artifacts, ("learning_scope",))
    scope_ref = refs[0]
    envelope = store._artifacts[run_id]["learning_scope"][scope_ref.artifact_id][
        scope_ref.version
    ]
    store._artifacts[run_id]["learning_scope"][scope_ref.artifact_id][
        scope_ref.version
    ] = envelope.model_copy(update={"schema_version": 2})
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="knowledge_generation",
        last_successful_stage="scope",
    )

    decision = LearningResumeCoordinator(store, store).decide(run_id)

    assert decision.allowed is False
    assert "unsupported Learning artifact schema" in decision.reason


def test_forged_checkpoint_stage_jump_is_rejected() -> None:
    store = InMemoryArtifactStore()
    artifacts = _complete_artifacts()
    run_id = "resume-forged"
    refs = _save(store, run_id, artifacts, ("learning_scope",))
    _checkpoint(
        store,
        run_id,
        refs,
        current_stage="quality",
        last_successful_stage="scope",
    )

    decision = LearningResumeCoordinator(store, store).decide(run_id)

    assert decision.allowed is False
    assert decision.resume_stage is None
    assert "not adjacent" in decision.reason
