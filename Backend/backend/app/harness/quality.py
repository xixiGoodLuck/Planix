from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MIN_CRITIC_PASS_SCORE = 90


def critic_score(report: Any) -> int:
    raw = report.get("score", 0) if isinstance(report, Mapping) else getattr(report, "score", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def meets_critic_score_gate(report: Any) -> bool:
    return critic_score(report) >= MIN_CRITIC_PASS_SCORE


__all__ = ["MIN_CRITIC_PASS_SCORE", "critic_score", "meets_critic_score_gate"]
