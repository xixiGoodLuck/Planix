from __future__ import annotations

from ...services.cognitive_planning.contracts import EvidencePack, StrategyInput, StrategyPortfolio
from ..contracts import GoalUnderstandingArtifact, RealityAssessment
from .base import AgentResult, CognitiveModelClient


STRATEGY_SYSTEM = """
You are Planix Strategy Agent. Design genuinely distinct routes from the understood goal, Reality Assessment,
and Evidence Pack. Do not use universal labels such as foundation stage, improvement stage, project-driven
plan, or steady plan unless the title names the specific decision and outcome. Do not emit execution tasks.

For every route explain why it fits this user, why it is preferable to alternatives, what it sacrifices, which
evidence and assumptions support it, what could fail, and who should choose it. If only one route is credible,
state the rejected alternatives and why. End with a meaningful user choice. Return only the requested JSON
and never hidden chain-of-thought.
This Agent can only propose a Strategy: always return status="waiting_user_approval" and approvedStrategyId=null.
Only the runtime may derive an approved projection after a real, version-bound user approval.
""".strip()


class StrategyAgent:
    name = "Strategy Agent"
    artifact_type = "strategy_portfolio"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        goal: GoalUnderstandingArtifact,
        evidence: EvidencePack,
        *,
        reality: RealityAssessment | None = None,
        previous: StrategyPortfolio | None = None,
        feedback: str | None = None,
    ) -> AgentResult[StrategyPortfolio]:
        payload = StrategyInput(
            goalModel=goal,
            evidencePack=evidence.decision_view(),
            previousStrategy=previous,
            userFeedback=feedback,
        ).model_dump(by_alias=True)
        payload["realityAssessment"] = reality.model_dump(by_alias=True) if reality else None
        return self.model.complete_contract(
            stage="strategy_design",
            task_type="planning_strategy",
            feature="cognitive_os_strategy_design",
            system=STRATEGY_SYSTEM,
            payload=payload,
            contract_type=StrategyPortfolio,
            temperature=0.25,
        )
