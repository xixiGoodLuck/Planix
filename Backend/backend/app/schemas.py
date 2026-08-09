from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .api_key import INVALID_API_KEY_MESSAGE, validate_api_key_format


PlanPriority = Literal["low", "medium", "high"]
PlanSource = Literal["manual", "ai"]
AiProvider = Literal["mock", "deepseek", "kimi", "zhipu_glm", "openai", "custom", "local"]
AiKeyStatus = Literal["unchecked", "valid", "invalid"]
RoutingPrimaryProvider = Literal["auto", "deepseek", "kimi", "zhipu_glm", "openai", "custom", "local"]
AutoModelStrategy = Literal[
    "fast_low_cost",
    "structured_stable",
    "strict_json",
    "context_summary",
    "classification",
    "knowledge_reasoning",
    "balanced",
]
PlanningAgentName = Literal[
    "Memory Evaluation Agent",
    "Understanding Agent",
    "Constraint Compiler",
    "Context Builder",
    "Plan Generator",
    "Plan Quality Reviewer",
    "Schedule Agent",
    "Schedule Quality Reviewer",
    "Calendar Materializer",
    "Final Review Controller",
    "Execution Feedback Evaluator",
    "Learning Observer",
]
PlanningArtifactType = Literal[
    "memory_evaluation",
    "understanding_snapshot",
    "understanding_patch",
    "constraint_set",
    "context_pack",
    "plan_blueprint",
    "plan_quality_report",
    "schedule_blueprint",
    "schedule_quality_report",
    "calendar_proposal",
    "final_approval_bundle",
    "final_revision_patch",
    "execution_outcome",
    "replan_proposal",
    "learning_observation",
    "promotion_audit",
]
PlanningArtifactStatus = Literal["draft", "approved", "blocked", "needs_revision"]
PlanningAgentDecisionType = Literal[
    "approve",
    "block",
    "request_user_input",
    "request_agent_revision",
    "produce_artifact",
    "revise_artifact",
    "handoff",
]
PlanningAgentMessageType = Literal["handoff", "revision_request", "block", "approval", "context_request"]


ModelUsageMode = Literal["llm", "local_fallback", "model_unavailable"]
ModelUsageTaskType = Literal[
    "settings_test",
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
]
ModelRoutingTaskType = Literal[
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
]
ModelRouteAttemptStatus = Literal["success", "error", "skipped"]


class ModelRouteAttempt(BaseModel):
    provider: str
    model: str | None = None
    status: ModelRouteAttemptStatus
    error_type: str | None = Field(default=None, alias="errorType")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    automatic_retry: bool | None = Field(default=None, alias="automaticRetry")
    retry_reason: str | None = Field(default=None, alias="retryReason")

    model_config = ConfigDict(populate_by_name=True)


class ModelUsage(BaseModel):
    provider: str
    model: str
    prompt_tokens: int | None = Field(default=None, alias="promptTokens")
    completion_tokens: int | None = Field(default=None, alias="completionTokens")
    total_tokens: int | None = Field(default=None, alias="totalTokens")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    mode: ModelUsageMode
    task_type: ModelUsageTaskType = Field(alias="taskType")
    fallback_used: bool | None = Field(default=None, alias="fallbackUsed")
    local_fallback_allowed: bool | None = Field(default=None, alias="localFallbackAllowed")
    attempts: list[ModelRouteAttempt] = Field(default_factory=list)
    generation_mode: Literal["single_pass", "two_pass_fallback", "two_pass_repair"] | None = Field(
        default=None,
        alias="generationMode",
    )

    model_config = ConfigDict(populate_by_name=True)


PlanningSessionStatus = Literal[
    "needs_goal_clarification",
    "waiting_understanding_confirmation",
    "planning",
    "final_revision",
    "waiting_final_review",
    "waiting_calendar_write_approval",
    "written_to_calendar",
    "learning_from_feedback",
    "MODEL_UNAVAILABLE",
    "ARCHIVED",
    "cancelled",
]
PlanningBusinessStatus = Literal[
    "goal_clarification",
    "goal_understood",
    "planning",
    "calendar_pending",
    "completed",
    "blocked",
    "cancelled",
]
PlanningRuntimeStatus = Literal[
    "idle",
    "running",
    "blocked_model",
    "retry_required",
]
PlanningControlIntent = Literal[
    "continue_current_stage",
    "skip_current_stage",
    "approve_current_stage",
    "modify_current_stage",
    "restart_planning",
    "cancel_planning",
    "provide_goal_information",
]


class PlanBase(BaseModel):
    date: str
    time: str = "09:00"
    content: str | None = None
    title: str | None = None
    done: bool = False
    result: str | None = None
    completion: str | None = None
    priority: PlanPriority = "medium"
    estimated_minutes: int = Field(default=30, alias="estimatedMinutes", ge=1, le=1440)
    source: PlanSource = "manual"
    source_key: str = Field(default="", alias="sourceKey")

    model_config = ConfigDict(populate_by_name=True)


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    date: str | None = None
    time: str | None = None
    content: str | None = None
    title: str | None = None
    done: bool | None = None
    result: str | None = None
    completion: str | None = None
    priority: PlanPriority | None = None
    estimated_minutes: int | None = Field(default=None, alias="estimatedMinutes", ge=1, le=1440)
    source: PlanSource | None = None
    source_key: str | None = Field(default=None, alias="sourceKey")

    model_config = ConfigDict(populate_by_name=True)


class PlanOut(BaseModel):
    id: str
    date: str
    time: str
    content: str
    done: bool
    result: str
    priority: PlanPriority
    estimated_minutes: int = Field(alias="estimatedMinutes")
    source: PlanSource
    source_key: str = Field(default="", alias="sourceKey")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class MonthNotePut(BaseModel):
    year: int = Field(ge=1970, le=2100)
    month: int = Field(ge=1, le=12)
    content: str = ""


class MonthNoteOut(MonthNotePut):
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class AiSettingsUpdate(BaseModel):
    provider: AiProvider = "deepseek"
    base_url: str = Field(default="https://api.deepseek.com", alias="baseUrl")
    model: str = "deepseek-v4-flash"
    api_key: str | None = Field(default=None, alias="apiKey")
    temperature: float = Field(default=0.3, ge=0, le=2)
    timeout_seconds: int = Field(default=40, alias="timeoutSeconds", ge=5, le=120)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://")
        return cleaned

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("model cannot be empty")
        return cleaned

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return ""
        if validate_api_key_format(cleaned):
            raise ValueError(INVALID_API_KEY_MESSAGE)
        return cleaned

    @model_validator(mode="after")
    def normalize_provider_settings(self) -> "AiSettingsUpdate":
        if self.provider == "local":
            self.base_url = normalize_local_base_url(self.base_url)
        return self


class AiSavedProvider(BaseModel):
    provider: AiProvider
    base_url: str = Field(alias="baseUrl")
    model: str
    has_api_key: bool = Field(alias="hasApiKey")
    key_status: AiKeyStatus = Field(default="unchecked", alias="keyStatus")
    key_error_type: str = Field(default="", alias="keyErrorType")
    last_validated_at: str = Field(default="", alias="lastValidatedAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class AiModelRoutingRule(BaseModel):
    task_type: ModelRoutingTaskType = Field(alias="taskType")
    primary_provider: RoutingPrimaryProvider = Field(alias="primaryProvider")
    fallback_providers: list[AiProvider] = Field(default_factory=list, alias="fallbackProviders")
    local_fallback_enabled: bool = Field(default=True, alias="localFallbackEnabled")
    updated_at: str = Field(default="", alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("primary_provider")
    @classmethod
    def validate_primary_provider(cls, value: RoutingPrimaryProvider) -> RoutingPrimaryProvider:
        return value

    @field_validator("fallback_providers")
    @classmethod
    def validate_fallback_providers(cls, value: list[AiProvider]) -> list[AiProvider]:
        cleaned: list[AiProvider] = []
        for provider in value:
            if provider == "mock":
                raise ValueError("mock cannot be used as a routed model provider")
            if provider not in cleaned:
                cleaned.append(provider)
        if len(cleaned) > 2:
            raise ValueError("fallbackProviders can include at most 2 providers")
        return cleaned

    def model_post_init(self, __context: Any) -> None:
        if self.primary_provider != "auto" and self.primary_provider in self.fallback_providers:
            raise ValueError("primaryProvider cannot also be a fallback provider")


class AiAutoModelPolicy(BaseModel):
    auto_provider_order: list[AiProvider] = Field(default_factory=list, alias="autoProviderOrder")
    task_strategy: dict[ModelRoutingTaskType, AutoModelStrategy] = Field(default_factory=dict, alias="taskStrategy")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("auto_provider_order")
    @classmethod
    def validate_auto_provider_order(cls, value: list[AiProvider]) -> list[AiProvider]:
        cleaned: list[AiProvider] = []
        for provider in value:
            if provider == "mock":
                continue
            if provider not in cleaned:
                cleaned.append(provider)
        return cleaned


class AiModelRoutingUpdate(BaseModel):
    routing_rules: list[AiModelRoutingRule] = Field(alias="routingRules")
    auto_model_policy: AiAutoModelPolicy | None = Field(default=None, alias="autoModelPolicy")

    model_config = ConfigDict(populate_by_name=True)


class AiSettingsOut(BaseModel):
    provider: AiProvider
    base_url: str = Field(alias="baseUrl")
    model: str
    has_api_key: bool = Field(alias="hasApiKey")
    key_status: AiKeyStatus = Field(default="unchecked", alias="keyStatus")
    key_error_type: str = Field(default="", alias="keyErrorType")
    temperature: float
    timeout_seconds: int = Field(alias="timeoutSeconds")
    updated_at: str = Field(alias="updatedAt")
    saved_providers: list[AiSavedProvider] = Field(default_factory=list, alias="savedProviders")
    routing_rules: list[AiModelRoutingRule] = Field(default_factory=list, alias="routingRules")
    auto_model_policy: AiAutoModelPolicy = Field(default_factory=AiAutoModelPolicy, alias="autoModelPolicy")

    model_config = ConfigDict(populate_by_name=True)


class AiSettingsTestPayload(BaseModel):
    prompt: str = "Say OK in one short sentence."


class AiSettingsTestOut(BaseModel):
    ok: bool
    mode: Literal["mock", "llm", "error"]
    message: str
    provider: str | None = None
    model: str | None = None
    error_type: str | None = Field(default=None, alias="errorType")
    status_code: int | None = Field(default=None, alias="statusCode")
    detail: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class PendingPlanningQuestion(BaseModel):
    asked_fields: list[str] = Field(default_factory=list, alias="askedFields")
    expected_answer_type: str = Field(default="", alias="expectedAnswerType")
    question_text: str = Field(default="", alias="questionText")
    questions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class CreatePlanningSessionRequest(BaseModel):
    entry_point: Literal["p_mode"] = Field(default="p_mode", alias="entryPoint")
    thread_id: str | None = Field(default=None, alias="threadId")
    user_input: str = Field(alias="userInput", min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class PlanningSessionTextRequest(BaseModel):
    text: str = Field(default="", max_length=4000)

    model_config = ConfigDict(populate_by_name=True)


class PlanningExecutionFeedbackRequest(BaseModel):
    task_id: str = Field(alias="taskId", min_length=1)
    status: Literal["not_started", "in_progress", "blocked", "completed", "skipped", "rescheduled", "failed"]
    actual_minutes: int | None = Field(default=None, alias="actualMinutes", ge=0)
    completion_evidence: list[str] = Field(default_factory=list, alias="completionEvidence")
    blocker_reason: str | None = Field(default=None, alias="blockerReason")
    failure_reason: str | None = Field(default=None, alias="failureReason")

    model_config = ConfigDict(populate_by_name=True)


class PlanningExecutionFeedbackResponse(BaseModel):
    outcome: dict[str, Any]
    replan_proposal: dict[str, Any] | None = Field(default=None, alias="replanProposal")
    learning_observation: dict[str, Any] = Field(alias="learningObservation")

    model_config = ConfigDict(populate_by_name=True)


class PlanningArtifact(BaseModel):
    id: str
    session_id: str = Field(alias="sessionId")
    owner_agent: PlanningAgentName = Field(alias="ownerAgent")
    artifact_type: PlanningArtifactType = Field(alias="artifactType")
    version: int
    status: PlanningArtifactStatus
    content_json: dict[str, Any] = Field(default_factory=dict, alias="contentJson")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class AgentDecision(BaseModel):
    id: str
    session_id: str = Field(alias="sessionId")
    agent: PlanningAgentName
    decision: PlanningAgentDecisionType
    reason: str = ""
    confidence: float = Field(default=1, ge=0, le=1)
    input_artifact_ids: list[str] = Field(default_factory=list, alias="inputArtifactIds")
    output_artifact_ids: list[str] = Field(default_factory=list, alias="outputArtifactIds")
    user_visible_summary: str = Field(default="", alias="userVisibleSummary")
    model_usage: ModelUsage | None = Field(default=None, alias="modelUsage")
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class AgentMessage(BaseModel):
    id: str
    session_id: str = Field(alias="sessionId")
    from_agent: PlanningAgentName = Field(alias="fromAgent")
    to_agent: PlanningAgentName = Field(alias="toAgent")
    message_type: PlanningAgentMessageType = Field(alias="messageType")
    reason: str = ""
    payload_json: dict[str, Any] = Field(default_factory=dict, alias="payloadJson")
    resolved: bool = False
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class PlanningBlackboard(BaseModel):
    session_id: str = Field(alias="sessionId")
    status: PlanningSessionStatus
    user_input_history: list[str] = Field(default_factory=list, alias="userInputHistory")
    artifacts: list[PlanningArtifact] = Field(default_factory=list)
    decisions: list[AgentDecision] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


PlanningMode = Literal["model_backed", "degraded_read_only", "blocked_model_unavailable"]


class CognitivePlanningMetadata(BaseModel):
    engine_version: Literal["planning-engine-2"] = Field(default="planning-engine-2", alias="engineVersion")
    planning_mode: PlanningMode = Field(alias="planningMode")
    current_stage: str = Field(alias="currentStage")
    agent_confidence: float | None = Field(default=None, alias="agentConfidence", ge=0, le=1)
    applied_user_rules: list[str] = Field(default_factory=list, alias="appliedUserRules")
    repair_count: int = Field(default=0, alias="repairCount", ge=0, le=2)

    model_config = ConfigDict(populate_by_name=True)


class PlanningModelFailureAttempt(BaseModel):
    provider: str
    status: Literal["success", "error", "skipped"]
    error_type: str | None = Field(default=None, alias="errorType")

    model_config = ConfigDict(populate_by_name=True)


class PlanningLocalizedText(BaseModel):
    zh: str
    en: str


class PlanningModelFailure(BaseModel):
    stage: str
    resume_node: str = Field(alias="resumeNode")
    retryable: bool = True
    automatic_retry_attempted: bool = Field(default=False, alias="automaticRetryAttempted")
    attempts: list[PlanningModelFailureAttempt] = Field(default_factory=list)
    summary: PlanningLocalizedText
    action: PlanningLocalizedText

    model_config = ConfigDict(populate_by_name=True)


class PendingPlanningInput(BaseModel):
    text: str
    applied: Literal[False] = False


class PlanningSessionResponse(BaseModel):
    session_id: str = Field(alias="sessionId")
    thread_id: str = Field(default="", alias="threadId")
    entry_point: Literal["p_mode"] = Field(alias="entryPoint")
    status: PlanningSessionStatus
    business_status: PlanningBusinessStatus = Field(default="goal_clarification", alias="businessStatus")
    runtime_status: PlanningRuntimeStatus = Field(default="idle", alias="runtimeStatus")
    user_input: str = Field(alias="userInput")
    pending_question: PendingPlanningQuestion | None = Field(default=None, alias="pendingQuestion")
    cognitive_metadata: CognitivePlanningMetadata | None = Field(default=None, alias="cognitiveMetadata")
    planning_phase: str | None = Field(default=None, alias="planningPhase")
    planning_step: str | None = Field(default=None, alias="planningStep")
    understanding_snapshot: dict[str, Any] | None = Field(default=None, alias="understandingSnapshot")
    constraint_set: dict[str, Any] | None = Field(default=None, alias="constraintSet")
    context_pack: dict[str, Any] | None = Field(default=None, alias="contextPack")
    plan_blueprint: dict[str, Any] | None = Field(default=None, alias="planBlueprint")
    plan_quality_report: dict[str, Any] | None = Field(default=None, alias="planQualityReport")
    schedule_blueprint: dict[str, Any] | None = Field(default=None, alias="scheduleBlueprint")
    schedule_quality_report: dict[str, Any] | None = Field(default=None, alias="scheduleQualityReport")
    calendar_proposal: dict[str, Any] | None = Field(default=None, alias="calendarProposal")
    final_approval_bundle: dict[str, Any] | None = Field(default=None, alias="finalApprovalBundle")
    model_failure: PlanningModelFailure | None = Field(default=None, alias="modelFailure")
    pending_input: PendingPlanningInput | None = Field(default=None, alias="pendingInput")
    artifacts: list[PlanningArtifact] = Field(default_factory=list)
    decisions: list[AgentDecision] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    version: int
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


CommandPermission = Literal["low", "medium", "high"]


def normalize_local_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Base URL must use http(s) and point to localhost, 127.0.0.1, or ::1")
    return cleaned if cleaned.endswith("/v1") else f"{cleaned}/v1"


class CommandChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, alias="threadId")
    message: str = Field(min_length=1, max_length=4000)
    permission: CommandPermission = "low"
    context: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("message")
    @classmethod
    def _message_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")
        return cleaned

class CommandMessageOut(BaseModel):
    id: str
    thread_id: str = Field(alias="threadId")
    role: Literal["user", "assistant", "system", "card"]
    content: str
    kind: str = "text"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandThreadOut(BaseModel):
    id: str
    title: str
    messages: list[CommandMessageOut]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandThreadSummaryOut(BaseModel):
    id: str
    title: str
    message_count: int = Field(alias="messageCount")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandApproveRequest(BaseModel):
    thread_id: str | None = Field(default=None, alias="threadId")
    action_id: str = Field(alias="actionId")
    decision: Literal["approve", "reject"] = "approve"
    approved: bool | None = None
    permission: CommandPermission = "low"

    model_config = ConfigDict(populate_by_name=True)
