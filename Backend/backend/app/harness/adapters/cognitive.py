from __future__ import annotations

from ..contracts import AgentContract, FailureCondition
from ..registry import AgentContractRegistry, MEMORY_EVALUATOR_CONTRACT


MODEL_FAILURES = (
    FailureCondition(
        code="model_unavailable",
        description="No configured model produced a contract-valid artifact.",
        recoverable=True,
    ),
    FailureCondition(
        code="invalid_model_output",
        description="Model output failed deterministic JSON or schema repair.",
        recoverable=True,
    ),
    FailureCondition(
        code="missing_input_artifact",
        description="A required upstream artifact is absent or has no persisted version.",
        recoverable=False,
    ),
)


def build_cognitive_agent_registry() -> AgentContractRegistry:
    return AgentContractRegistry(
        [
            AgentContract(
                agentId="goal_intelligence",
                name="Goal Intelligence Agent",
                responsibility="Turn user input and evidence-backed memory into a typed UserGoalModel.",
                inputArtifacts=(),
                outputArtifact="user_goal_model",
                permissions=("read_artifact", "write_artifact", "request_user_input"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="goal_completion",
                name="Goal Completion Judge",
                responsibility="Decide whether remaining goal unknowns block planning.",
                inputArtifacts=("user_goal_model",),
                outputArtifact="goal_completion",
                permissions=("read_artifact", "write_artifact", "request_user_input"),
                failureConditions=MODEL_FAILURES,
                maxRetries=0,
            ),
            AgentContract(
                agentId="reality",
                name="Reality Agent",
                responsibility="Assess feasibility, constraints, risks, and real-world conditions.",
                inputArtifacts=("user_goal_model",),
                outputArtifact="reality_assessment",
                permissions=("read_artifact", "write_artifact", "request_user_input"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="evidence",
                name="Evidence Agent",
                responsibility="Synthesize grounded evidence into an EvidencePack without choosing strategy.",
                inputArtifacts=("user_goal_model", "reality_assessment"),
                outputArtifact="evidence_pack",
                permissions=("read_artifact", "write_artifact", "request_user_input"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="strategy",
                name="Strategy Agent",
                responsibility="Produce and compare typed strategy proposals from Goal and Evidence artifacts.",
                inputArtifacts=("user_goal_model", "evidence_pack"),
                outputArtifact="strategy_portfolio",
                permissions=("read_artifact", "write_artifact", "propose_strategy"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="execution",
                name="Execution Agent",
                responsibility="Turn an approved strategy into a typed ExecutionBlueprint.",
                inputArtifacts=("user_goal_model", "evidence_pack", "strategy_portfolio"),
                outputArtifact="execution_blueprint",
                permissions=("read_artifact", "write_artifact", "propose_execution"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="critic",
                name="Critic Agent",
                responsibility="Independently evaluate every ExecutionBlueprint and target repairs.",
                inputArtifacts=(
                    "user_goal_model",
                    "evidence_pack",
                    "strategy_portfolio",
                    "execution_blueprint",
                ),
                outputArtifact="critique_report",
                permissions=("read_artifact", "write_artifact", "evaluate_execution"),
                failureConditions=MODEL_FAILURES,
                maxRetries=2,
            ),
            AgentContract(
                agentId="feedback_learning",
                name="Critic Agent",
                responsibility="Diagnose user feedback, target the responsible artifact, and propose a memory candidate.",
                # Design feedback is valid before an Execution artifact exists;
                # execution/critique remain optional typed context for the Agent.
                inputArtifacts=("user_goal_model", "evidence_pack", "strategy_portfolio"),
                optionalInputArtifacts=("execution_blueprint", "critique_report"),
                outputArtifact="planning_learning_update",
                permissions=("read_artifact", "write_artifact", "propose_memory"),
                failureConditions=MODEL_FAILURES,
            ),
            MEMORY_EVALUATOR_CONTRACT,
        ]
    )


__all__ = ["build_cognitive_agent_registry"]
