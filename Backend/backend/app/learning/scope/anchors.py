from __future__ import annotations

from hashlib import sha256
import re

from ..contracts import ExplicitScopeAnchor, LearningScope


def _anchor_id(kind: str, text: str, source_ref: str) -> str:
    payload = f"{kind}\0{text.strip()}\0{source_ref.strip()}"
    return f"scope-anchor-{sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def build_explicit_scope_anchors(
    *,
    user_goal: str,
    user_goal_source_ref: str,
    target_result: str,
    target_result_status: str,
    target_result_source_ref: str | None = None,
    constraints: list[tuple[str, str]] = (),
) -> list[ExplicitScopeAnchor]:
    anchors: list[ExplicitScopeAnchor] = []

    def add(kind: str, text: str, source_ref: str) -> None:
        normalized = " ".join(text.split())
        if not normalized or any(
            item.kind == kind and item.text == normalized for item in anchors
        ):
            return
        anchors.append(
            ExplicitScopeAnchor(
                id=_anchor_id(kind, normalized, source_ref),
                kind=kind,
                text=normalized,
                sourceRef=source_ref,
            )
        )

    add("user_goal", user_goal, user_goal_source_ref)
    for concept in _explicit_concepts(user_goal):
        add("concept", concept, user_goal_source_ref)
    if target_result_status == "explicit" and target_result_source_ref:
        add("target_result", target_result, target_result_source_ref)
    for text, source_ref in constraints:
        add("constraint", text, source_ref)
    return anchors


_LIST_SEPARATOR = re.compile(r"\s*(?:、|，|,|;|；|(?:和|与|及)|\band\b)\s*", re.IGNORECASE)
_EXPLICIT_LIST_MARKER = re.compile(r"[,;，、；]")
_INTENT_PREFIX = re.compile(
    r"^(?:(?:我|本人)?(?:想|要|希望|需要)?\s*)?"
    r"(?:理解|学习|掌握|了解|解释|区分|认识|learn|understand|master|explain|distinguish)\s+",
    re.IGNORECASE,
)
_RESOURCE_SUFFIX = re.compile(
    r"\s+(?:from|using)\s+(?:this|the)\s+(?:video|resource)$",
    re.IGNORECASE,
)


def _explicit_concepts(user_goal: str) -> list[str]:
    """Extract only a visibly enumerated concept list; never infer adjacent topics."""
    normalized = " ".join(user_goal.strip().split())
    if not _EXPLICIT_LIST_MARKER.search(normalized):
        return []
    parts = [item.strip(" .。!?！？:：\"'()（）") for item in _LIST_SEPARATOR.split(normalized)]
    parts = [_RESOURCE_SUFFIX.sub("", item).strip() for item in parts if item]
    if len(parts) < 2:
        return []
    parts[0] = _INTENT_PREFIX.sub("", parts[0]).strip(" .。!?！？:：\"'()（）")
    concepts: list[str] = []
    for part in parts:
        if part and part.casefold() not in {item.casefold() for item in concepts}:
            concepts.append(part)
    return concepts if len(concepts) >= 2 else []


def refresh_explicit_scope_anchors(
    scope: LearningScope,
    *,
    latest_source_ref: str,
) -> list[ExplicitScopeAnchor]:
    goal_source_ref = next(
        (
            item.source_ref
            for item in scope.explicit_scope_anchors
            if item.kind == "user_goal"
        ),
        scope.source_refs[0] if scope.source_refs else latest_source_ref,
    )
    target_source_ref = next(
        (
            item.source_ref
            for item in scope.explicit_scope_anchors
            if item.kind == "target_result"
        ),
        latest_source_ref if scope.target_result_status == "explicit" else None,
    )
    constraints = [
        (item.text, item.source_ref)
        for item in scope.explicit_scope_anchors
        if item.kind == "constraint"
    ]
    return build_explicit_scope_anchors(
        user_goal=scope.user_goal,
        user_goal_source_ref=goal_source_ref,
        target_result=scope.target_result,
        target_result_status=scope.target_result_status,
        target_result_source_ref=target_source_ref,
        constraints=constraints,
    )


__all__ = [
    "build_explicit_scope_anchors",
    "refresh_explicit_scope_anchors",
]
