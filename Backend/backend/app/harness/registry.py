from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import AgentContract, ArtifactKind


ARTIFACT_STATE_KEYS: dict[ArtifactKind, str] = {
    "user_goal_model": "goal_model",
    "goal_completion": "goal_completion",
    "reality_assessment": "reality_assessment",
    "evidence_pack": "evidence_pack",
    "strategy_portfolio": "strategy_portfolio",
    "execution_blueprint": "execution_blueprint",
    "critique_report": "critique_report",
    "planning_learning_update": "learning_update",
    "memory_evaluation": "memory_evaluation",
}


MEMORY_EVALUATOR_CONTRACT = AgentContract(
    agentId="memory_evaluator",
    name="Memory Evaluation Agent",
    responsibility=(
        "Independently decide whether a candidate observation is a durable, evidence-backed "
        "planning rule. It evaluates only and cannot persist memory."
    ),
    inputArtifacts=("planning_learning_update",),
    outputArtifact="memory_evaluation",
    permissions=("read_artifact", "write_artifact", "evaluate_memory"),
    failureConditions=(
        {
            "code": "missing_candidate",
            "description": "The learning artifact contains no evidence-backed memory candidate.",
            "recoverable": False,
        },
        {
            "code": "invalid_evaluation",
            "description": "The evaluator did not bind its result to the candidate and source artifact.",
            "recoverable": True,
        },
    ),
    maxRetries=1,
)


class AgentContractRegistry:
    def __init__(self, contracts: list[AgentContract] | None = None):
        self._contracts: dict[str, AgentContract] = {}
        for contract in contracts or []:
            self.register(contract)

    def register(self, contract: AgentContract) -> None:
        if contract.agent_id in self._contracts:
            raise ValueError(f"duplicate harness agent contract: {contract.agent_id}")
        self._contracts[contract.agent_id] = contract

    def get(self, agent_id: str) -> AgentContract:
        try:
            return self._contracts[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown harness agent: {agent_id}") from exc

    def list(self) -> list[AgentContract]:
        return list(self._contracts.values())

    def missing_inputs(self, agent_id: str, state: Mapping[str, Any]) -> list[ArtifactKind]:
        contract = self.get(agent_id)
        return [
            kind
            for kind in contract.input_artifacts
            if not state.get(ARTIFACT_STATE_KEYS[kind])
        ]


__all__ = ["ARTIFACT_STATE_KEYS", "AgentContractRegistry", "MEMORY_EVALUATOR_CONTRACT"]
