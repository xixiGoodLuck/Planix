from __future__ import annotations

from ..contracts import GoalUnderstandingArtifact, RealityAssessment, RealityAssessmentInput
from .base import AgentResult, CognitiveModelClient


REALITY_SYSTEM = """
You are Planix Reality Agent. Independently challenge the understood goal before any strategy is designed.
Assess whether the outcome, time, available resources, environment, and success standard are realistic.
Treat the current goalModel as authoritative over contradictory older conversation or memory context; older
conflicts are superseded context and must not reopen facts or constraints that the current Goal already resolved.
Discover hidden domain risks using model knowledge; do not rely on domain-specific code branches or fixed
checklists. Distinguish a risk that truly blocks planning from one that can be carried as an explicit assumption.
If the goal is overbroad, propose a more honest scope or staged outcome instead of silently accepting it.

Ask at most three questions only when their answers materially change feasibility, safety, scope, or resource
requirements. A blocked assessment must provide a user-resolvable question. Return only the requested JSON
and a concise user-visible judgment, never hidden chain-of-thought.
""".strip()


class RealityAgent:
    name = "Reality Agent"
    artifact_type = "reality_assessment"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(self, payload: RealityAssessmentInput) -> AgentResult[RealityAssessment]:
        return self.model.complete_contract(
            stage="reality_assessment",
            task_type="planning_reality",
            feature="cognitive_os_reality_assessment",
            system=REALITY_SYSTEM,
            payload=payload.model_dump(by_alias=True),
            contract_type=RealityAssessment,
            temperature=0.1,
        )
