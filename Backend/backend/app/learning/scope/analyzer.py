from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError, model_validator

from ..contracts import LearningContract, LearningScope
from ..generators import LearningSemanticModel, RouterLearningModel
from .patcher import LearningScopePatcher
from .readiness import LearningScopeReadiness, evaluate_readiness
from .validators import contains_url, extract_bilibili_urls, redact_urls


SCOPE_ANALYZER_SYSTEM = """
Analyze one user's progressive technical-learning intake. Extract only semantic facts explicitly supported by
the latest user message. Existing scope facts may be retained but must not be returned as new user facts. A
question, option, example, common learning path, or plausible preference is never a user fact. Do not infer a
target outcome, current level, time budget, content language, platform, teaching style, or video preference when
the user did not state it. Return goalIdentified=false when no understandable learning topic is present.

Suggest at most six clarification gaps in one batch. Questions must be optional unless the learning topic itself
cannot be identified. Return semantic prose and affected field names only. Never return URLs, video identifiers,
artifact ids, versions, timestamps, source references, confirmation state, readiness, or hidden reasoning. URL
extraction, user-message lineage, priorities, assumptions, readiness, versions, and confirmation are owned by
code. Return JSON only.
""".strip()


class ScopeCurrentLevelDraft(LearningContract):
    summary: str = ""
    known_skills: list[str] = Field(default_factory=list, max_length=12)
    known_technologies: list[str] = Field(default_factory=list, max_length=12)
    uncertain_areas: list[str] = Field(default_factory=list, max_length=12)


class ScopeGapDraft(LearningContract):
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    affected_fields: list[str] = Field(min_length=1, max_length=3)


class ScopeSemanticDraft(LearningContract):
    goal_identified: bool
    user_goal: str | None = None
    target_result: str | None = None
    current_level: ScopeCurrentLevelDraft | None = None
    recommended_gaps: list[ScopeGapDraft] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def model_must_not_return_urls(self) -> "ScopeSemanticDraft":
        def walk(value: Any) -> bool:
            if isinstance(value, str):
                return contains_url(value)
            if isinstance(value, dict):
                return any(walk(item) for item in value.values())
            if isinstance(value, list):
                return any(walk(item) for item in value)
            return False

        if walk(self.model_dump(mode="python")):
            raise ValueError("scope semantic model must not return URLs")
        return self


@dataclass(frozen=True)
class LearningScopeAnalysis:
    scope: LearningScope
    readiness: LearningScopeReadiness
    model_usage: dict[str, Any]


class LearningScopeAnalyzer:
    def __init__(
        self,
        model: LearningSemanticModel | None = None,
        *,
        patcher: LearningScopePatcher | None = None,
    ):
        self.model = model or RouterLearningModel()
        self.patcher = patcher or LearningScopePatcher()

    def analyze(
        self,
        *,
        intake_id: str,
        message: str,
        source_ref: str,
        preferred_language: str,
        current_scope: LearningScope | None = None,
    ) -> LearningScopeAnalysis:
        current = self._semantic_scope(current_scope)
        user_urls = current_scope.resource_preference.user_supplied_urls if current_scope else []
        try:
            response = self.model.complete(
                stage="learning_scope_analysis",
                feature="learning_scope_analysis",
                system=SCOPE_ANALYZER_SYSTEM,
                payload={
                    "currentScope": current,
                    "latestUserMessage": redact_urls(message),
                    "preferredResponseLanguage": preferred_language,
                    "userSuppliedUrlCount": len(user_urls) + len(extract_bilibili_urls(message)),
                },
                response_type=ScopeSemanticDraft,
                max_tokens=1600,
            )
        except ValidationError as exc:
            from ..generators import LearningModelOutputError

            raise LearningModelOutputError(
                "learning_scope_analysis",
                "model output failed the Learning Scope contract",
            ) from exc
        scope = self.patcher.patch(
            intake_id=intake_id,
            current=current_scope,
            message=message,
            source_ref=source_ref,
            preferred_language=preferred_language,
            draft=response.value,
        )
        return LearningScopeAnalysis(
            scope=scope,
            readiness=evaluate_readiness(scope),
            model_usage=response.model_usage,
        )

    @staticmethod
    def _semantic_scope(scope: LearningScope | None) -> dict[str, Any] | None:
        if scope is None:
            return None
        return {
            "userGoal": scope.user_goal,
            "targetResult": scope.target_result,
            "currentLevel": {
                "summary": scope.current_level.summary,
                "knownSkills": scope.current_level.known_skills,
                "knownTechnologies": scope.current_level.known_technologies,
                "uncertainAreas": scope.current_level.uncertain_areas,
            },
            "contentBudget": scope.content_budget.model_dump(mode="json", by_alias=True),
            "languagePreference": scope.language_preference.model_dump(mode="json", by_alias=True),
            "resourcePreference": {
                "preferredPlatforms": scope.resource_preference.preferred_platforms,
                "preferredStyles": scope.resource_preference.preferred_styles,
                "hasUserSuppliedUrls": bool(scope.resource_preference.user_supplied_urls),
            },
            "assumptions": [item.statement for item in scope.assumptions],
            "unknownFields": [field for item in scope.unknowns for field in item.affected_fields],
        }


__all__ = [
    "LearningScopeAnalysis",
    "LearningScopeAnalyzer",
    "ScopeCurrentLevelDraft",
    "ScopeGapDraft",
    "ScopeSemanticDraft",
]
