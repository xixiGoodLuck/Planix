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
                agentId="understanding_agent",
                name="Understanding Agent",
                responsibility="Produce the native UnderstandingSnapshot from the current thread.",
                outputArtifact="understanding_snapshot",
                permissions=("read_artifact", "write_artifact", "request_user_input"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="plan_generator",
                name="Plan Generator",
                responsibility="Produce a native PlanBlueprint from approved Understanding, Constraint, and Context.",
                inputArtifacts=("understanding_snapshot", "constraint_set", "context_pack"),
                outputArtifact="plan_blueprint",
                permissions=("read_artifact", "write_artifact", "propose_repair"),
                failureConditions=MODEL_FAILURES,
            ),
            AgentContract(
                agentId="plan_reviewer",
                name="Plan Quality Reviewer",
                responsibility="Review current Plan semantics and emit native QualityIssue values.",
                inputArtifacts=("understanding_snapshot", "constraint_set", "context_pack", "plan_blueprint", "plan_quality_report"),
                outputArtifact="plan_quality_report",
                permissions=("read_artifact", "write_artifact", "review_plan"),
                failureConditions=MODEL_FAILURES,
                maxRetries=1,
            ),
            AgentContract(
                agentId="feedback_learning",
                name="Learning Observer",
                responsibility="Turn execution feedback into a versioned LearningObservation.",
                inputArtifacts=("final_approval_bundle",),
                optionalInputArtifacts=("execution_outcome", "replan_proposal"),
                outputArtifact="learning_observation",
                permissions=("read_artifact", "write_artifact", "propose_memory"),
                failureConditions=MODEL_FAILURES,
            ),
            MEMORY_EVALUATOR_CONTRACT,
        ]
    )


__all__ = ["build_cognitive_agent_registry"]
