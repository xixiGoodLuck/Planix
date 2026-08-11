from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from ...contracts import LearningArtifact, LearningArtifactType
from .execution import StageExecutor


LearningStageName = Literal[
    "scope",
    "knowledge_generation",
    "evidence_generation",
    "coverage_analysis",
    "gap_completion",
    "selection",
    "quality",
]
StageArtifacts = dict[LearningArtifactType, LearningArtifact]
StageValidator = Callable[[StageArtifacts], None]


def _no_validation(_: StageArtifacts) -> None:
    return None


@dataclass(frozen=True)
class LearningStage:
    stage_name: LearningStageName
    input_artifacts: tuple[LearningArtifactType, ...]
    output_artifacts: tuple[LearningArtifactType, ...]
    validator: StageValidator
    executor: StageExecutor | None


class LearningStageRegistry:
    def __init__(self, stages: list[LearningStage]):
        names = [stage.stage_name for stage in stages]
        if len(names) != len(set(names)):
            raise ValueError("Learning stage names must be unique")
        self._stages = tuple(stages)
        self._by_name = {stage.stage_name: stage for stage in stages}

    @classmethod
    def default(
        cls,
        *,
        executors: Mapping[LearningStageName, StageExecutor] | None = None,
        validators: Mapping[LearningStageName, StageValidator] | None = None,
    ) -> "LearningStageRegistry":
        stage_executors = executors or {}
        stage_validators = validators or {}

        def stage(
            name: LearningStageName,
            inputs: tuple[LearningArtifactType, ...],
            outputs: tuple[LearningArtifactType, ...],
        ) -> LearningStage:
            return LearningStage(
                stage_name=name,
                input_artifacts=inputs,
                output_artifacts=outputs,
                validator=stage_validators.get(name, _no_validation),
                executor=stage_executors.get(name),
            )

        return cls(
            [
                stage("scope", (), ("learning_scope",)),
                stage(
                    "knowledge_generation",
                    ("learning_scope",),
                    ("capability_graph", "knowledge_graph"),
                ),
                stage(
                    "evidence_generation",
                    ("knowledge_graph",),
                    ("evidence_graph",),
                ),
                stage(
                    "coverage_analysis",
                    ("knowledge_graph", "evidence_graph"),
                    (),
                ),
                stage(
                    "gap_completion",
                    ("knowledge_graph", "evidence_graph"),
                    ("evidence_graph",),
                ),
                stage(
                    "selection",
                    ("learning_scope", "knowledge_graph", "evidence_graph"),
                    ("content_selection", "learning_content_plan"),
                ),
                stage(
                    "quality",
                    (
                        "learning_scope",
                        "capability_graph",
                        "knowledge_graph",
                        "evidence_graph",
                        "content_selection",
                        "learning_content_plan",
                    ),
                    ("learning_quality_report",),
                ),
            ]
        )

    @property
    def stages(self) -> tuple[LearningStage, ...]:
        return self._stages

    def get(self, stage_name: LearningStageName) -> LearningStage:
        try:
            return self._by_name[stage_name]
        except KeyError as exc:
            raise ValueError(f"unknown Learning stage: {stage_name}") from exc

    def next_after(self, stage_name: LearningStageName | None) -> LearningStage | None:
        if stage_name is None:
            return self._stages[0] if self._stages else None
        index = next(
            (
                item_index
                for item_index, item in enumerate(self._stages)
                if item.stage_name == stage_name
            ),
            None,
        )
        if index is None:
            raise ValueError(f"unknown Learning stage: {stage_name}")
        return self._stages[index + 1] if index + 1 < len(self._stages) else None

    def through(self, stage_name: LearningStageName) -> tuple[LearningStage, ...]:
        for index, stage in enumerate(self._stages):
            if stage.stage_name == stage_name:
                return self._stages[: index + 1]
        raise ValueError(f"unknown Learning stage: {stage_name}")


__all__ = [
    "LearningStage",
    "LearningStageName",
    "LearningStageRegistry",
    "StageArtifacts",
    "StageExecutor",
    "StageValidator",
]
