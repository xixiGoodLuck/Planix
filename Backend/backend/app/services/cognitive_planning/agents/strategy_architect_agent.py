from __future__ import annotations

from ..contracts import EvidencePack, StrategyInput, StrategyPortfolio, UserGoalModel
from .base import AgentResult, CognitiveModelClient


STRATEGY_SYSTEM = """
You are Planix Strategy Architect Agent. Design one or more genuinely distinct ways to reach the user's goal
from the formal goal model and evidence pack. Do not output fixed phases, universal learning stages, or tasks.
Compare time, effort, risk, assumptions, evidence, and expected outcomes. Recommend a strategy, explain what
was traded away, and ask the user to choose or adjust it. If only one path is credible, still state risks,
assumptions, and rejected alternatives. Return only the requested JSON and never hidden chain-of-thought.
This Agent can only propose a Strategy: always return status="waiting_user_approval" and approvedStrategyId=null.
Only the runtime may derive an approved projection after a real, version-bound user approval.
""".strip()


class StrategyArchitectAgent:
    name = "Strategy Architect Agent"
    artifact_type = "strategy_portfolio"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        goal: UserGoalModel,
        evidence: EvidencePack,
        *,
        previous: StrategyPortfolio | None = None,
        feedback: str | None = None,
    ) -> AgentResult[StrategyPortfolio]:
        return self.model.complete_contract(
            stage="strategy_architecture",
            task_type="planning_strategy",
            feature="cognitive_strategy_architecture",
            system=STRATEGY_SYSTEM,
            payload=StrategyInput(
                goalModel=goal,
                evidencePack=evidence.decision_view(),
                previousStrategy=previous,
                userFeedback=feedback,
            ).model_dump(by_alias=True),
            contract_type=StrategyPortfolio,
            temperature=0.25,
        )
