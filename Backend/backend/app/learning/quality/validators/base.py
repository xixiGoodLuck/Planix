from __future__ import annotations

from dataclasses import dataclass, field

from ...contracts import (
    LearningQualityCheck,
    LearningQualityIssue,
    LearningQualityRule,
)
from ...generators.base import generated_id


@dataclass
class QualityEvaluation:
    checks: list[LearningQualityCheck] = field(default_factory=list)
    issues: list[LearningQualityIssue] = field(default_factory=list)

    def add(
        self,
        *,
        rule: LearningQualityRule,
        passed: bool,
        evidence: list[str],
        owner_id: str,
        severity: str,
        target_type: str,
        target_id: str,
        description: str,
    ) -> None:
        self.checks.append(
            LearningQualityCheck(rule=rule, passed=passed, evidence=evidence)
        )
        if passed:
            return
        self.issues.append(
            LearningQualityIssue(
                issueId=generated_id(
                    "learning-quality-issue",
                    owner_id,
                    len(self.issues),
                    f"{rule}:{target_type}:{target_id}:{description}",
                ),
                rule=rule,
                severity=severity,
                targetType=target_type,
                targetId=target_id,
                description=description,
                evidenceRefs=evidence,
                allowedOperations=["regenerate", "repair"],
            )
        )

    def extend(self, other: "QualityEvaluation") -> None:
        self.checks.extend(other.checks)
        self.issues.extend(other.issues)


__all__ = ["QualityEvaluation"]
