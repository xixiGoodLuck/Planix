from __future__ import annotations

from dataclasses import dataclass

from .deterministic_guards import template_phrase_hits


@dataclass(frozen=True)
class ScenarioEvaluation:
    ok: bool
    issues: tuple[str, ...]


def evaluate_visible_text(values: list[str], *, forbidden_terms: tuple[str, ...] = ()) -> ScenarioEvaluation:
    issues = [f"forbidden template phrase: {item}" for item in template_phrase_hits(values)]
    for term in forbidden_terms:
        if any(term.lower() in value.lower() for value in values):
            issues.append(f"forbidden cross-domain term: {term}")
    return ScenarioEvaluation(ok=not issues, issues=tuple(issues))
