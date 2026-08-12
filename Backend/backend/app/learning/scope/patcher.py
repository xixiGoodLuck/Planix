from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ..contracts import (
    ContentBudget,
    CurrentLevel,
    LanguagePreference,
    LearningAssumption,
    LearningScope,
    LearningUnknown,
    ResourcePreference,
)
from .anchors import refresh_explicit_scope_anchors
from .validators import (
    extract_bilibili_urls,
    extract_target_result,
    has_current_level_signal,
    has_target_result_signal,
    language_preferences,
    parse_content_minutes,
    platform_preferences,
    redact_urls,
    style_preferences,
    text_supported_by_message,
)


class CurrentLevelPatch(Protocol):
    summary: str
    known_skills: list[str]
    known_technologies: list[str]
    uncertain_areas: list[str]


class ScopeSemanticPatch(Protocol):
    goal_identified: bool
    user_goal: str | None
    target_result: str | None
    current_level: CurrentLevelPatch | None
    recommended_gaps: list[object]


@dataclass(frozen=True)
class GapCopy:
    question: str
    why_it_matters: str


_GAP_POLICY: tuple[tuple[str, str, bool], ...] = (
    ("user_goal", "high", True),
    ("target_result", "high", False),
    ("current_level", "high", False),
    ("content_budget", "medium", False),
    ("language_preference", "medium", False),
    ("resource_preference", "medium", False),
    ("resource_preference.user_supplied_urls", "low", False),
)


_ZH_GAPS = {
    "user_goal": ("你希望学习哪一项技术或能力？", "明确学习主题后才能生成对应知识路线。"),
    "target_result": ("最终希望做到什么？", "目标成果会明显改变知识范围和练习深度。"),
    "current_level": ("目前掌握哪些相关知识？", "已有基础会改变起点和需要覆盖的前置知识。"),
    "content_budget": ("大约愿意投入多少内容时间？", "内容预算用于控制推荐片段的总时长。"),
    "language_preference": ("是否只看中文内容？", "语言偏好会影响可选择的内容来源。"),
    "resource_preference": ("是否偏好 B 站或某类讲解方式？", "平台与形式偏好会影响候选资源排序。"),
    "resource_preference.user_supplied_urls": ("是否已有指定视频？", "指定视频可以优先进入验证，但完全可选。"),
}


_EN_GAPS = {
    "user_goal": ("What technology or capability do you want to learn?", "A clear topic is required before a knowledge route can be generated."),
    "target_result": ("What do you ultimately want to be able to do?", "The desired outcome can materially change the knowledge scope and depth."),
    "current_level": ("What related knowledge do you already have?", "Your current foundation changes the starting point and prerequisites."),
    "content_budget": ("Roughly how much content time do you want to spend?", "The content budget controls the total duration of recommended segments."),
    "language_preference": ("Do you want content in a particular language?", "Language preference affects which sources can be selected."),
    "resource_preference": ("Do you prefer Bilibili or a particular teaching style?", "Platform and style preferences affect resource ranking."),
    "resource_preference.user_supplied_urls": ("Do you already have a specific video?", "A supplied video can be verified first, but it is optional."),
}


def _model_gap_copy(draft: ScopeSemanticPatch) -> dict[str, GapCopy]:
    copies: dict[str, GapCopy] = {}
    for candidate in draft.recommended_gaps:
        affected_fields = list(getattr(candidate, "affected_fields", []))
        question = str(getattr(candidate, "question", "")).strip()
        why = str(getattr(candidate, "why_it_matters", "")).strip()
        if not question or not why:
            continue
        for field in affected_fields:
            if field in dict((name, None) for name, _, _ in _GAP_POLICY):
                copies.setdefault(field, GapCopy(question, why))
    return copies


def _accepted_items(items: list[str], message: str) -> list[str]:
    accepted: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and text_supported_by_message(normalized, message) and normalized not in accepted:
            accepted.append(normalized)
    return accepted


def _current_level(
    current: LearningScope | None,
    draft: ScopeSemanticPatch,
    message: str,
    source_ref: str,
) -> CurrentLevel:
    existing = current.current_level.model_copy(deep=True) if current else CurrentLevel()
    patch = draft.current_level
    if patch is None or not has_current_level_signal(message):
        return existing
    skills = _accepted_items(patch.known_skills, message)
    technologies = _accepted_items(patch.known_technologies, message)
    uncertain = _accepted_items(patch.uncertain_areas, message)
    summary = patch.summary.strip()
    if summary and not text_supported_by_message(summary, message):
        summary = ""
    if not summary:
        summary = ", ".join(dict.fromkeys([*skills, *technologies, *uncertain]))
    if not (summary or skills or technologies or uncertain):
        return existing
    return CurrentLevel(
        summary=summary or existing.summary,
        knownSkills=list(dict.fromkeys([*existing.known_skills, *skills])),
        knownTechnologies=list(dict.fromkeys([*existing.known_technologies, *technologies])),
        uncertainAreas=list(dict.fromkeys([*existing.uncertain_areas, *uncertain])),
        sourceRefs=list(dict.fromkeys([*existing.source_refs, source_ref])),
    )


def _assumptions(
    scope: LearningScope,
    missing_fields: set[str],
    preferred_language: str,
) -> list[LearningAssumption]:
    retained = [
        item
        for item in scope.assumptions
        if not item.id.startswith("scope-default-")
    ]
    definitions_en = {
        "target_result": (
            "scope-default-target-result",
            "The final outcome is unspecified; planning will stay close to the stated learning goal.",
            "high",
        ),
        "current_level": (
            "scope-default-current-level",
            "Current learning depth is unspecified and will be handled conservatively.",
            "high",
        ),
        "content_budget": (
            "scope-default-content-budget",
            "Content budget is unspecified; selection will avoid unnecessary material.",
            "medium",
        ),
        "language_preference": (
            "scope-default-language",
            "Content language is unspecified.",
            "medium",
        ),
        "resource_preference": (
            "scope-default-resource",
            "No platform or teaching style was specified; verified configured sources may be searched.",
            "medium",
        ),
    }
    definitions_zh = {
        "target_result": (
            "scope-default-target-result",
            "最终成果尚未指定；规划将保守地围绕已表达的学习目标展开。",
            "high",
        ),
        "current_level": (
            "scope-default-current-level",
            "当前学习深度尚未指定，后续将采用保守策略。",
            "high",
        ),
        "content_budget": (
            "scope-default-content-budget",
            "当前内容预算尚未指定；内容选择将避免不必要的材料。",
            "medium",
        ),
        "language_preference": (
            "scope-default-language",
            "内容语言尚未指定。",
            "medium",
        ),
        "resource_preference": (
            "scope-default-resource",
            "尚未指定平台或讲解方式；可以检索已配置且可验证的来源。",
            "medium",
        ),
    }
    definitions = (
        definitions_zh
        if preferred_language.casefold().startswith("zh")
        else definitions_en
    )
    for field, (identifier, statement, impact) in definitions.items():
        if field in missing_fields:
            retained.append(
                LearningAssumption(
                    id=identifier,
                    statement=statement,
                    basis="Code-owned conservative default for an unresolved optional field.",
                    sourceRef=f"system:scope-readiness:{field}",
                    impact=impact,
                )
            )
    return retained


def _unknowns(
    scope: LearningScope,
    *,
    goal_identified: bool,
    target_identified: bool,
    draft: ScopeSemanticPatch,
    preferred_language: str,
) -> list[LearningUnknown]:
    missing: set[str] = set()
    if not goal_identified:
        missing.add("user_goal")
    if not target_identified:
        missing.add("target_result")
    if not scope.current_level.source_refs:
        missing.add("current_level")
    if not any(
        value is not None
        for value in (
            scope.content_budget.target_total_minutes,
            scope.content_budget.maximum_total_minutes,
            scope.content_budget.maximum_video_count,
            scope.content_budget.maximum_segment_minutes,
        )
    ):
        missing.add("content_budget")
    if not scope.language_preference.preferred_languages:
        missing.add("language_preference")
    if not (
        scope.resource_preference.preferred_platforms
        or scope.resource_preference.preferred_styles
    ):
        missing.add("resource_preference")
    if not scope.resource_preference.user_supplied_urls:
        missing.add("resource_preference.user_supplied_urls")

    language = _ZH_GAPS if preferred_language.casefold().startswith("zh") else _EN_GAPS
    model_copies = _model_gap_copy(draft)
    result: list[LearningUnknown] = []
    for field, impact, blocking in _GAP_POLICY:
        if field not in missing or len(result) >= 6:
            continue
        copy = model_copies.get(field)
        question, why = (
            (copy.question, copy.why_it_matters)
            if copy is not None
            else language[field]
        )
        result.append(
            LearningUnknown(
                id=f"scope-gap-{field.replace('.', '-')}",
                question=question,
                whyItMatters=why,
                impact=impact,
                blocking=blocking,
                affectedFields=[field],
            )
        )
    return result


class LearningScopePatcher:
    @staticmethod
    def patch_resource_urls(
        current: LearningScope,
        resource_urls: list[str],
        source_ref: str,
        *,
        increment_version: bool = True,
    ) -> LearningScope:
        resource_preference = current.resource_preference.model_copy(deep=True)
        resource_preference.user_supplied_urls = list(
            dict.fromkeys(
                [*resource_preference.user_supplied_urls, *resource_urls]
            )
        )
        resource_preference.preferred_platforms = list(
            dict.fromkeys([*resource_preference.preferred_platforms, "bilibili"])
        )
        unknowns = [
            item.model_copy(deep=True)
            for item in current.unknowns
            if "resource_preference.user_supplied_urls" not in item.affected_fields
            and "resource_preference" not in item.affected_fields
        ]
        assumptions = [
            item.model_copy(deep=True)
            for item in current.assumptions
            if item.id != "scope-default-resource"
        ]
        version = current.version + 1 if increment_version else current.version
        created_at = (
            datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
            if increment_version
            else current.created_at
        )
        return current.model_copy(
            deep=True,
            update={
                "version": version,
                "created_at": created_at,
                "resource_preference": resource_preference,
                "unknowns": unknowns,
                "assumptions": assumptions,
                "source_refs": list(
                    dict.fromkeys([*current.source_refs, source_ref])
                ),
                "confirmed": False,
            },
        )

    def patch(
        self,
        *,
        intake_id: str,
        current: LearningScope | None,
        message: str,
        source_ref: str,
        preferred_language: str,
        draft: ScopeSemanticPatch,
    ) -> LearningScope:
        safe_message = redact_urls(message).strip()
        semantic_source = " ".join(
            item
            for item in [current.user_goal if current else "", safe_message]
            if item
        )
        current_goal_clear = bool(
            current
            and not any("user_goal" in item.affected_fields for item in current.unknowns)
        )
        current_target_clear = bool(
            current
            and not any("target_result" in item.affected_fields for item in current.unknowns)
        )
        proposed_goal = (draft.user_goal or "").strip()
        accepts_goal = bool(
            draft.goal_identified
            and proposed_goal
            and text_supported_by_message(proposed_goal, semantic_source)
        )
        user_goal = (
            proposed_goal
            if accepts_goal
            else current.user_goal if current else safe_message or "Unspecified learning goal"
        )
        goal_identified = accepts_goal or current_goal_clear

        target_result = current.target_result if current else user_goal
        target_result_status = (
            current.target_result_status
            if current
            else "assumed" if goal_identified else "unknown"
        )
        proposed_target = (draft.target_result or "").strip()
        target_signal = has_target_result_signal(safe_message)
        explicit_target = extract_target_result(safe_message)
        proposed_target_supported = bool(
            proposed_target
            and target_signal
            and text_supported_by_message(proposed_target, semantic_source)
        )
        target_identified = current_target_clear or bool(
            explicit_target
            or target_signal
            and (proposed_target_supported or (current is not None and accepts_goal))
        )
        if explicit_target:
            target_result = explicit_target
            target_result_status = "explicit"
        elif current is not None and accepts_goal and target_signal:
            target_result = proposed_goal
            target_result_status = "explicit"
        if (
            proposed_target_supported
            and not explicit_target
            and len(proposed_target) > len(target_result)
        ):
            target_result = proposed_target
            target_result_status = "explicit"

        current_level = _current_level(current, draft, safe_message, source_ref)
        minutes = parse_content_minutes(safe_message)
        content_budget = (
            current.content_budget.model_copy(deep=True) if current else ContentBudget()
        )
        if minutes is not None:
            content_budget.target_total_minutes = minutes

        preferred_languages, acceptable_languages = language_preferences(safe_message)
        language_preference = (
            current.language_preference.model_copy(deep=True)
            if current
            else LanguagePreference()
        )
        if preferred_languages:
            language_preference.preferred_languages = preferred_languages
            language_preference.acceptable_languages = acceptable_languages

        resource_preference = (
            current.resource_preference.model_copy(deep=True)
            if current
            else ResourcePreference()
        )
        platforms = platform_preferences(safe_message)
        styles = style_preferences(safe_message)
        supplied_urls = extract_bilibili_urls(message)
        if platforms:
            resource_preference.preferred_platforms = list(
                dict.fromkeys([*resource_preference.preferred_platforms, *platforms])
            )
        if styles:
            resource_preference.preferred_styles = list(
                dict.fromkeys([*resource_preference.preferred_styles, *styles])
            )
        if supplied_urls:
            resource_preference.user_supplied_urls = list(
                dict.fromkeys([*resource_preference.user_supplied_urls, *supplied_urls])
            )

        scope = LearningScope(
            artifactId=current.artifact_id if current else f"learning-scope-{intake_id}",
            version=(current.version + 1) if current else 1,
            userGoal=user_goal,
            targetResult=target_result,
            targetResultStatus=target_result_status,
            currentLevel=current_level,
            contentBudget=content_budget,
            languagePreference=language_preference,
            resourcePreference=resource_preference,
            assumptions=list(current.assumptions) if current else [],
            unknowns=[],
            sourceRefs=list(dict.fromkeys([*(current.source_refs if current else []), source_ref])),
            confirmed=False,
        )
        scope.explicit_scope_anchors = refresh_explicit_scope_anchors(
            scope,
            latest_source_ref=source_ref,
        )
        scope.unknowns = _unknowns(
            scope,
            goal_identified=goal_identified,
            target_identified=target_identified,
            draft=draft,
            preferred_language=preferred_language,
        )
        missing_fields = {
            field
            for item in scope.unknowns
            for field in item.affected_fields
        }
        scope.assumptions = _assumptions(scope, missing_fields, preferred_language)
        return scope


__all__ = ["LearningScopePatcher"]
