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
MemoryKind = Literal["note", "material", "planning_history", "preference", "review"]
MemorySource = Literal["user", "ai", "system"]
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
    "command_decision",
    "plan_generation",
    "task_refinement",
    "calendar_patch",
    "memory_query",
    "memory_write",
    "note_query",
    "note_write",
    "chat",
    "model_knowledge",
    "settings_test",
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
]
ModelRoutingTaskType = Literal[
    "command_decision",
    "plan_generation",
    "task_refinement",
    "calendar_patch",
    "memory_query",
    "memory_write",
    "note_query",
    "note_write",
    "chat",
    "model_knowledge",
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


class AiPayload(BaseModel):
    goal: str = ""
    deadline: str = ""
    daily_hours: float = Field(default=2, alias="dailyHours")
    materials: str = ""
    preferences: str = ""
    date: str = ""
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class AgentRunOptions(BaseModel):
    force_model_knowledge: bool = Field(default=False, alias="forceModelKnowledge")

    model_config = ConfigDict(populate_by_name=True)


class AgentRunRequest(BaseModel):
    input: str
    date: str
    preferences: str | dict[str, Any] = ""
    materials: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    options: AgentRunOptions = Field(default_factory=AgentRunOptions)

    model_config = ConfigDict(populate_by_name=True)


class MemoryPayload(BaseModel):
    user_id: str = Field(default="local-user", alias="userId")
    preferences: str = ""

    model_config = ConfigDict(populate_by_name=True)


class MemoryCreate(BaseModel):
    kind: MemoryKind = "note"
    title: str = Field(default="", max_length=240)
    content: str = Field(min_length=1)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = "user"
    source_id: str = Field(default="", alias="sourceId")
    source_key: str = Field(default="", alias="sourceKey")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("title", "content", "summary", "source_id", "source_key")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        result = []
        seen = set()
        for tag in value:
            cleaned = str(tag).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned[:48])
        return result[:12]


class MemoryUpdate(BaseModel):
    kind: MemoryKind | None = None
    title: str | None = Field(default=None, max_length=240)
    content: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    source: MemorySource | None = None
    source_id: str | None = Field(default=None, alias="sourceId")
    source_key: str | None = Field(default=None, alias="sourceKey")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("title", "content", "summary", "source_id", "source_key")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class MemoryItemOut(BaseModel):
    id: str
    kind: MemoryKind
    title: str
    content: str
    summary: str
    tags: list[str]
    source: MemorySource
    source_id: str = Field(default="", alias="sourceId")
    source_key: str = Field(default="", alias="sourceKey")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class MemoryResultGroup(BaseModel):
    kind: MemoryKind
    title: str
    items: list[MemoryItemOut]

    model_config = ConfigDict(populate_by_name=True)


class MemorySearchResult(BaseModel):
    query: str
    summary: str
    groups: list[MemoryResultGroup]
    results: list[MemoryItemOut]

    model_config = ConfigDict(populate_by_name=True)


class RagIngestPayload(BaseModel):
    title: str = "Untitled material"
    content: str


class RagDocumentCreate(BaseModel):
    title: str = "Untitled material"
    content: str
    source_type: str = Field(default="paste", alias="sourceType")

    model_config = ConfigDict(populate_by_name=True)


class RagDocumentOut(BaseModel):
    id: str
    title: str
    source_type: str = Field(alias="sourceType")
    summary: str
    chunks: int
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class RagSource(BaseModel):
    document_id: str = Field(alias="documentId")
    title: str
    chunk: str
    score: float
    chunk_index: int = Field(alias="chunkIndex")

    model_config = ConfigDict(populate_by_name=True)


class RagQueryOut(BaseModel):
    mode: Literal["mock", "llm"]
    answer: str
    sources: list[RagSource]
    keywords: list[str]
    provider: str | None = None
    model: str | None = None


class AiMaterialDraftRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    output_language: Literal["zh", "en"] = Field(default="zh", alias="outputLanguage")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query cannot be empty")
        return cleaned


class AiMaterialDraftOut(BaseModel):
    title: str
    content: str
    summary: str
    source_type: Literal["model_knowledge", "local_knowledge_template"] = Field(alias="sourceType")
    caveat: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, str]


LearningResourceType = Literal["official_doc", "library_doc", "search_keyword", "local_source"]
PlanningResourceSourceType = Literal[
    "user_material",
    "memory_note",
    "official_doc",
    "tutorial",
    "coach_or_human",
    "built_in_catalog",
    "project_template",
    "practice_bank",
    "practice_drill",
    "safety_checklist",
    "route_info",
    "tool",
    "example",
    "web_search",
    "github",
    "video",
    "book",
    "ai_generated",
    "search_keyword",
]
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


class TimeBlock(BaseModel):
    title: str = Field(min_length=1)
    duration_minutes: int = Field(alias="durationMinutes", ge=1, le=30)
    action: str = Field(min_length=1)
    expected_output: str | None = Field(default=None, alias="expectedOutput")

    model_config = ConfigDict(populate_by_name=True)


class LearningResource(BaseModel):
    title: str = Field(min_length=1)
    type: LearningResourceType = "search_keyword"
    url: str | None = None
    search_keyword: str | None = Field(default=None, alias="searchKeyword")
    reason: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class TaskLearningResource(BaseModel):
    title: str
    source_type: PlanningResourceSourceType = Field(alias="sourceType")
    url: str | None = None
    section: str | None = None
    search_keyword: str | None = Field(default=None, alias="searchKeyword")
    use_step: str = Field(default="", alias="useStep")
    estimated_minutes: int = Field(default=15, alias="estimatedMinutes", ge=1, le=240)
    why_this_resource: str = Field(default="", alias="whyThisResource")
    expected_output: str = Field(default="", alias="expectedOutput")
    fallback_if_too_hard: str = Field(default="", alias="fallbackIfTooHard")

    model_config = ConfigDict(populate_by_name=True)


class TaskResourceBundle(BaseModel):
    primary: TaskLearningResource | None = None
    support: TaskLearningResource | None = None
    practice: TaskLearningResource | None = None
    fallback: TaskLearningResource | None = None

    model_config = ConfigDict(populate_by_name=True)


class PlanFitCheck(BaseModel):
    fits_current_milestone: bool = Field(alias="fitsCurrentMilestone")
    advances_overall_goal: bool = Field(alias="advancesOverallGoal")
    has_checkable_output: bool = Field(alias="hasCheckableOutput")
    note: str = Field(min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class RefinePlanContext(BaseModel):
    plan_title: str = Field(default="", alias="planTitle")
    plan_summary: str = Field(default="", alias="planSummary")
    duration_days: int | None = Field(default=None, alias="durationDays", ge=1)
    quality_status: str | None = Field(default=None, alias="qualityStatus")
    daily_learning_minutes: int | None = Field(default=None, alias="dailyLearningMinutes", ge=1)
    current_milestone: dict[str, Any] = Field(default_factory=dict, alias="currentMilestone")
    current_task: dict[str, Any] = Field(default_factory=dict, alias="currentTask")
    previous_task: dict[str, Any] | None = Field(default=None, alias="previousTask")
    next_task: dict[str, Any] | None = Field(default=None, alias="nextTask")
    same_milestone_tasks: list[str] = Field(default_factory=list, alias="sameMilestoneTasks")
    sources: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class RefinedTask(BaseModel):
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    estimated_minutes: int = Field(alias="estimatedMinutes", gt=0)
    steps: list[str] = Field(min_length=3)
    checklist: list[str] = Field(min_length=2)
    acceptance_criteria: list[str] = Field(alias="acceptanceCriteria", min_length=1)
    deliverable: str = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    fallback_tips: list[str] = Field(default_factory=list, alias="fallbackTips")
    mode: Literal["llm", "local_fallback"] = "local_fallback"
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    error_type: str | None = Field(default=None, alias="errorType")
    time_blocks: list[TimeBlock] = Field(default_factory=list, alias="timeBlocks")
    learning_resources: list[LearningResource] = Field(default_factory=list, alias="learningResources")
    budget_explanation: str | None = Field(default=None, alias="budgetExplanation")
    plan_fit_check: PlanFitCheck | None = Field(default=None, alias="planFitCheck")
    model_usage: ModelUsage | None = Field(default=None, alias="modelUsage")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("title", "objective", "deliverable")
    @classmethod
    def _required_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field cannot be empty")
        return cleaned


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
    refined_task: RefinedTask | None = Field(default=None, alias="refinedTask")

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
    refined_task: RefinedTask | None = Field(default=None, alias="refinedTask")
    refined_task_updated_at: str | None = Field(default=None, alias="refinedTaskUpdatedAt")
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


class PhaseItem(BaseModel):
    title: str
    detail: str


class PlannerTask(BaseModel):
    time: str = "09:00"
    title: str
    reason: str = ""


GoalPriority = Literal["low", "medium", "high"]
ReviewFrequency = Literal["daily", "weekly"]
PlanHorizonType = Literal["daily", "weekly", "monthly", "quarterly", "long_term"]
PlanQualityStatus = Literal["passed", "repaired", "local_fallback"]
PlanSourceType = Literal["local_context", "model_knowledge", "local_fallback", "insufficient_context"]
LocalRelevance = Literal["high", "medium", "low"]


class GoalPlanTask(BaseModel):
    title: str
    description: str = ""
    estimated_minutes: int = Field(default=45, alias="estimatedMinutes", ge=1, le=1440)
    due_date: str | None = Field(default=None, alias="dueDate")
    priority: GoalPriority = "medium"
    learning_resources: list[TaskLearningResource] = Field(default_factory=list, alias="learningResources")
    resource_bundle: TaskResourceBundle | None = Field(default=None, alias="resourceBundle")

    model_config = ConfigDict(populate_by_name=True)


class GoalMilestone(BaseModel):
    title: str
    description: str = ""
    tasks: list[GoalPlanTask] = Field(default_factory=list)


class ReviewPlan(BaseModel):
    frequency: ReviewFrequency = "daily"
    questions: list[str] = Field(default_factory=list)


class StructuredGoalPlan(BaseModel):
    goal_title: str = Field(alias="goalTitle")
    goal_description: str = Field(alias="goalDescription")
    duration_days: int = Field(alias="durationDays", ge=1, le=3650)
    milestones: list[GoalMilestone]
    review_plan: ReviewPlan = Field(alias="reviewPlan")

    model_config = ConfigDict(populate_by_name=True)


class PlanHorizon(BaseModel):
    raw_text: str = Field(alias="rawText")
    duration_days: int = Field(alias="durationDays", ge=1, le=3650)
    horizon_type: PlanHorizonType = Field(alias="horizonType")
    start_date: str = Field(alias="startDate")
    end_date: str = Field(alias="endDate")
    expected_milestone_count: int = Field(alias="expectedMilestoneCount", ge=1)
    expected_min_task_count: int = Field(alias="expectedMinTaskCount", ge=1)
    expected_week_count: int = Field(alias="expectedWeekCount", ge=1)

    model_config = ConfigDict(populate_by_name=True)


class PlanDensityPolicy(BaseModel):
    duration_days: int = Field(alias="durationDays", ge=1)
    min_milestones: int = Field(alias="minMilestones", ge=1)
    max_milestones: int = Field(alias="maxMilestones", ge=1)
    min_total_tasks: int = Field(alias="minTotalTasks", ge=1)
    max_total_tasks: int = Field(alias="maxTotalTasks", ge=1)
    min_tasks_per_milestone: int = Field(alias="minTasksPerMilestone", ge=1)
    require_weekly_coverage: bool = Field(alias="requireWeeklyCoverage")
    min_covered_weeks: int = Field(alias="minCoveredWeeks", ge=1)
    first_days_detail: int = Field(alias="firstDaysDetail", ge=1)

    model_config = ConfigDict(populate_by_name=True)


class PlanQualityMetrics(BaseModel):
    duration_days: int | None = Field(default=None, alias="durationDays", ge=1)
    total_tasks: int | None = Field(default=None, alias="totalTasks", ge=0)
    milestone_count: int | None = Field(default=None, alias="milestoneCount", ge=0)
    covered_week_count: int | None = Field(default=None, alias="coveredWeekCount", ge=0)
    date_span_days: int | None = Field(default=None, alias="dateSpanDays", ge=0)
    weak_task_count: int | None = Field(default=None, alias="weakTaskCount", ge=0)
    missing_due_date_count: int | None = Field(default=None, alias="missingDueDateCount", ge=0)
    out_of_range_due_date_count: int | None = Field(default=None, alias="outOfRangeDueDateCount", ge=0)
    repair_attempted: bool | None = Field(default=None, alias="repairAttempted")
    fallback_used: bool | None = Field(default=None, alias="fallbackUsed")
    quality_status: PlanQualityStatus | None = Field(default=None, alias="qualityStatus")
    source_type: PlanSourceType | None = Field(default=None, alias="sourceType")
    local_relevance: LocalRelevance | None = Field(default=None, alias="localRelevance")

    model_config = ConfigDict(populate_by_name=True)


class PlanQualityIssue(BaseModel):
    code: str
    message: str
    severity: Literal["warning", "error"]


class PlanQualityReport(BaseModel):
    ok: bool
    score: int = Field(ge=0, le=100)
    total_tasks: int = Field(alias="totalTasks", ge=0)
    milestone_count: int = Field(alias="milestoneCount", ge=0)
    covered_week_count: int = Field(alias="coveredWeekCount", ge=0)
    date_span_days: int = Field(alias="dateSpanDays", ge=0)
    issues: list[PlanQualityIssue] = Field(default_factory=list)
    metrics: PlanQualityMetrics | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReplanTask(PlannerTask):
    target_date: str = Field(alias="targetDate")
    source_plan_id: str | None = Field(default=None, alias="sourcePlanId")

    model_config = ConfigDict(populate_by_name=True)


class GoalPlanRequest(BaseModel):
    goal: str
    deadline: str = ""
    daily_hours: float = Field(default=2, alias="dailyHours")
    materials: str = ""
    preferences: str = ""
    date: str
    output_language: Literal["zh-CN", "en-US"] | None = Field(default=None, alias="outputLanguage")

    model_config = ConfigDict(populate_by_name=True)


class GoalPlanOut(BaseModel):
    id: str
    mode: Literal["mock", "llm"]
    summary: str
    phases: list[PhaseItem]
    tasks: list[PlannerTask]
    sources: list[RagSource] = Field(default_factory=list)
    structured_plan: StructuredGoalPlan | None = Field(default=None, alias="structuredPlan")
    provider: str | None = None
    model: str | None = None
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    error_type: str | None = Field(default=None, alias="errorType")
    error_message: str | None = Field(default=None, alias="errorMessage")
    base_url_host: str | None = Field(default=None, alias="baseUrlHost")
    plan_horizon: PlanHorizon | None = Field(default=None, alias="planHorizon")
    quality_report: PlanQualityReport | None = Field(default=None, alias="qualityReport")
    quality_status: PlanQualityStatus | None = Field(default=None, alias="qualityStatus")
    source_type: PlanSourceType | None = Field(default=None, alias="sourceType")
    local_relevance: LocalRelevance | None = Field(default=None, alias="localRelevance")
    model_usage: ModelUsage | None = Field(default=None, alias="modelUsage")

    model_config = ConfigDict(populate_by_name=True)


class MemoryHit(BaseModel):
    id: str = ""
    kind: MemoryKind
    title: str
    summary: str
    relevance: str

    model_config = ConfigDict(populate_by_name=True)


class MemoryInsightHits(BaseModel):
    preferences: list[MemoryHit] = Field(default_factory=list)
    reviews: list[MemoryHit] = Field(default_factory=list)
    planning_history: list[MemoryHit] = Field(default_factory=list, alias="planningHistory")
    materials: list[MemoryHit] = Field(default_factory=list)
    notes: list[MemoryHit] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class PlanningInsights(BaseModel):
    user_style_rules: list[str] = Field(default_factory=list, alias="userStyleRules")
    past_failure_warnings: list[str] = Field(default_factory=list, alias="pastFailureWarnings")
    positive_patterns: list[str] = Field(default_factory=list, alias="positivePatterns")
    constraints_to_respect: list[str] = Field(default_factory=list, alias="constraintsToRespect")

    model_config = ConfigDict(populate_by_name=True)


class MemoryInsightBrief(BaseModel):
    memory_hits: MemoryInsightHits = Field(default_factory=MemoryInsightHits, alias="memoryHits")
    planning_insights: PlanningInsights = Field(default_factory=PlanningInsights, alias="planningInsights")
    calendar_constraints: list[str] = Field(default_factory=list, alias="calendarConstraints")
    confidence: float = Field(default=0, ge=0, le=1)
    missing_memory_warning: str | None = Field(default=None, alias="missingMemoryWarning")
    memory_reflection: "MemoryReflection | None" = Field(default=None, alias="memoryReflection")

    model_config = ConfigDict(populate_by_name=True)


class MemoryReflectionHit(BaseModel):
    id: str | None = None
    kind: str
    summary: str
    why_relevant: str = Field(alias="whyRelevant")

    model_config = ConfigDict(populate_by_name=True)


class MemoryPlanningRule(BaseModel):
    rule: str
    source: str = ""
    strength: Literal["hard", "soft"] = "soft"
    confidence: float = Field(default=0.5, ge=0, le=1)


class MemoryRiskWarning(BaseModel):
    warning: str
    evidence: str = ""


class MemoryReflection(BaseModel):
    used_memories: list[MemoryReflectionHit] = Field(default_factory=list, alias="usedMemories")
    planning_rules: list[MemoryPlanningRule] = Field(default_factory=list, alias="planningRules")
    risk_warnings: list[MemoryRiskWarning] = Field(default_factory=list, alias="riskWarnings")
    user_profile_gaps: list[str] = Field(default_factory=list, alias="userProfileGaps")
    influence_on_plan: list[str] = Field(default_factory=list, alias="influenceOnPlan")

    model_config = ConfigDict(populate_by_name=True)


class ResourceFitScore(BaseModel):
    total: int = Field(ge=0, le=100)
    level_fit: int = Field(default=60, alias="levelFit", ge=0, le=100)
    task_fit: int = Field(default=60, alias="taskFit", ge=0, le=100)
    user_preference_fit: int = Field(default=60, alias="userPreferenceFit", ge=0, le=100)
    time_fit: int = Field(default=60, alias="timeFit", ge=0, le=100)
    credibility: int = Field(default=60, ge=0, le=100)
    actionability: int = Field(default=60, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ResourceCandidate(BaseModel):
    id: str
    title: str
    source_type: PlanningResourceSourceType = Field(alias="sourceType")
    url: str | None = None
    section: str | None = None
    search_keyword: str | None = Field(default=None, alias="searchKeyword")
    domain: str = ""
    topics: list[str] = Field(default_factory=list)
    difficulty: Literal["beginner", "intermediate", "advanced"] = "beginner"
    language: Literal["zh", "en", "mixed"] = "mixed"
    estimated_minutes: int = Field(default=20, alias="estimatedMinutes", ge=1, le=240)
    how_to_use: str = Field(default="", alias="howToUse")
    expected_output: str = Field(default="", alias="expectedOutput")
    fallback_if_too_hard: str = Field(default="", alias="fallbackIfTooHard")
    fit_score: ResourceFitScore = Field(alias="fitScore")

    model_config = ConfigDict(populate_by_name=True)


class ResourceCoverage(BaseModel):
    status: Literal["strong", "partial", "weak", "missing"]
    missing_topics: list[str] = Field(default_factory=list, alias="missingTopics")
    explanation: str
    fallback_strategy: Literal[
        "use_user_material",
        "use_builtin_catalog",
        "use_project_template",
        "use_practice_bank",
        "use_ai_micro_material",
        "ask_user",
        "optional_web_search",
    ] = Field(alias="fallbackStrategy")

    model_config = ConfigDict(populate_by_name=True)


class ResourceBrief(BaseModel):
    resource_candidates: list[ResourceCandidate] = Field(default_factory=list, alias="resourceCandidates")
    coverage: ResourceCoverage
    resource_rules_for_this_plan: list[str] = Field(default_factory=list, alias="resourceRulesForThisPlan")
    resource_reasoning: "ResourceReasoningResult | None" = Field(default=None, alias="resourceReasoning")

    model_config = ConfigDict(populate_by_name=True)


class ResourceNeed(BaseModel):
    topic: str
    resource_type: Literal[
        "official_doc",
        "tutorial",
        "coach_or_human",
        "practice_drill",
        "project_template",
        "safety_checklist",
        "route_info",
        "tool",
        "example",
        "ai_micro_material",
    ] = Field(alias="resourceType")
    why_needed: str = Field(default="", alias="whyNeeded")

    model_config = ConfigDict(populate_by_name=True)


class ResourceReasoningResult(BaseModel):
    resource_strategy: str = Field(alias="resourceStrategy")
    resource_needs: list[ResourceNeed] = Field(default_factory=list, alias="resourceNeeds")
    resource_candidates: list[ResourceCandidate] = Field(default_factory=list, alias="resourceCandidates")
    resource_risks: list[str] = Field(default_factory=list, alias="resourceRisks")
    coverage: Literal["strong", "partial", "weak", "missing"] = "partial"

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
    accept_missing_resources: bool = Field(default=False, alias="acceptMissingResources")

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


class RefineTaskRequest(BaseModel):
    goal: str = ""
    task_title: str = Field(alias="taskTitle", min_length=1)
    task_description: str = Field(default="", alias="taskDescription")
    date: str = ""
    available_minutes: int | None = Field(default=None, alias="availableMinutes", ge=1)
    plan_context: RefinePlanContext | None = Field(default=None, alias="planContext")
    source_key: str = Field(default="", alias="sourceKey")
    plan_id: str = Field(default="", alias="planId")
    user_constraints: list[str] = Field(default_factory=list, alias="userConstraints")
    retrieved_sources: list[RagSource] = Field(default_factory=list, alias="retrievedSources")
    output_language: Literal["zh", "en"] = Field(default="zh", alias="outputLanguage")
    refinement_instruction: str = Field(default="", alias="refinementInstruction")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("task_title")
    @classmethod
    def _task_title_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("taskTitle cannot be empty")
        return cleaned


class PlanRefinedTaskUpdate(BaseModel):
    refined_task: RefinedTask = Field(alias="refinedTask")

    model_config = ConfigDict(populate_by_name=True)


class DailyReviewRequest(BaseModel):
    date: str
    goal: str = ""
    preferences: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class DailyReviewOut(BaseModel):
    id: str
    mode: Literal["mock", "llm", "saved"]
    date: str
    summary: str
    suggestions: list[str]
    done_count: int = Field(alias="doneCount")
    total_count: int = Field(alias="totalCount")
    target_date: str = Field(alias="targetDate")
    replan_tasks: list[ReplanTask] = Field(alias="replanTasks")
    provider: str | None = None
    model: str | None = None
    updated_at: str = Field(default="", alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ReplanApplyRequest(BaseModel):
    tasks: list[ReplanTask]


CommandPermission = Literal["low", "medium", "high"]
CommandMode = Literal["auto", "chat", "workbench"]
CommandDraftKind = Literal["calendar_plan"]
CommandDraftStatus = Literal["current", "superseded", "written", "dismissed"]
CommandActionTarget = Literal["calendar", "memory", "notes", "materials", "goals", "settings", "dashboard", "ui"]
CommandActionOperation = Literal["read", "create", "update", "delete", "navigate", "run", "create_or_update_plans"]
CommandActionRisk = Literal["read", "write", "delete", "dangerous"]
CommandActionStatus = Literal["proposed", "waiting_approval", "running", "success", "failed", "rejected"]
CommandDecisionIntent = Literal[
    "create_plan",
    "save_plan_to_calendar",
    "query_plan",
    "query_memory",
    "patch_calendar_plan",
    "refine_plan",
    "refine_task",
    "query_notes",
    "save_memory",
    "save_note",
    "modify_current_draft",
    "chat",
    "clarify",
]
CommandDecisionTargetType = Literal[
    "current_draft",
    "calendar_plan",
    "calendar_date",
    "memory",
    "note",
    "material",
    "preference",
    "review",
    "unknown",
]
CommandDecisionAction = Literal[
    "create",
    "save",
    "query",
    "update",
    "delete",
    "refine",
    "reschedule",
    "summarize",
    "answer",
]
CommandOutputKind = Literal[
    "assistant_text",
    "runtime_trace",
    "task_proposal_summary",
    "task_proposal_detail",
    "calendar_plan_preview",
    "approval_request",
    "calendar_write_result",
    "command_decision",
    "plan_search_results",
    "memory_search_results",
    "note_search_results",
    "plan_patch_preview",
    "plan_patch_result",
    "memory_write_preview",
    "memory_write_result",
    "note_write_preview",
    "note_write_result",
    "planning_session_started",
    "user_need_contract",
    "memory_insight_brief",
    "resource_brief",
    "plan_design_proposal",
    "execution_plan_draft",
    "learning_update",
    "agent_decision",
    "agent_message",
    "planning_session_status",
    "model_usage",
    "clarify_question",
    "execution_result",
    "error",
]


class CommandDecisionDateRange(BaseModel):
    start: str = ""
    end: str = ""


class CommandDecisionPatchFields(BaseModel):
    title: str | None = None
    date: str | None = None
    time: str | None = None
    estimated_minutes: int | None = Field(default=None, alias="estimatedMinutes")

    model_config = ConfigDict(populate_by_name=True)


class CommandDecisionParams(BaseModel):
    title: str | None = None
    date: str | None = None
    date_range: CommandDecisionDateRange | None = Field(default=None, alias="dateRange")
    time: str | None = None
    estimated_minutes: int | None = Field(default=None, alias="estimatedMinutes")
    target_index: int | None = Field(default=None, alias="targetIndex")
    query: str | None = None
    refinement_instruction: str | None = Field(default=None, alias="refinementInstruction")
    patch_fields: CommandDecisionPatchFields | None = Field(default=None, alias="patchFields")
    note_text: str | None = Field(default=None, alias="noteText")

    model_config = ConfigDict(populate_by_name=True)


class CommandDecision(BaseModel):
    intent: CommandDecisionIntent
    confidence: float = Field(default=0, ge=0, le=1)
    target_type: CommandDecisionTargetType = Field(default="unknown", alias="targetType")
    action: CommandDecisionAction = "answer"
    extracted_params: CommandDecisionParams = Field(default_factory=CommandDecisionParams, alias="extractedParams")
    needs_confirmation: bool = Field(default=False, alias="needsConfirmation")
    needs_clarification: bool = Field(default=False, alias="needsClarification")
    clarification_question: str | None = Field(default=None, alias="clarificationQuestion")
    decision_summary: str = Field(default="", alias="decisionSummary")

    model_config = ConfigDict(populate_by_name=True)


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
    mode: CommandMode = "auto"
    context: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("message")
    @classmethod
    def _message_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")
        return cleaned

    @field_validator("history")
    @classmethod
    def _valid_chat_history(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        cleaned: list[dict[str, str]] = []
        for item in value:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                cleaned.append({"role": role, "content": content[:4000]})
        return cleaned[-20:]


class CommandActionPlan(BaseModel):
    target: CommandActionTarget
    operation: CommandActionOperation
    risk: CommandActionRisk
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class CommandDraftCreate(BaseModel):
    thread_id: str | None = Field(default=None, alias="threadId")
    kind: CommandDraftKind = "calendar_plan"
    title: str = ""
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class CommandDraftOut(BaseModel):
    id: str
    thread_id: str = Field(alias="threadId")
    kind: CommandDraftKind
    version: int
    status: CommandDraftStatus
    title: str
    summary: str
    payload: dict[str, Any]
    source_run_id: str = Field(default="", alias="sourceRunId")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandMessageOut(BaseModel):
    id: str
    thread_id: str = Field(alias="threadId")
    role: Literal["user", "assistant", "system", "card"]
    content: str
    kind: str = "text"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandActionOut(BaseModel):
    id: str
    thread_id: str = Field(alias="threadId")
    draft_id: str = Field(default="", alias="draftId")
    target: CommandActionTarget
    operation: CommandActionOperation
    risk: CommandActionRisk
    status: CommandActionStatus
    reason: str
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandOutputEnvelope(BaseModel):
    id: str
    thread_id: str = Field(alias="threadId")
    kind: CommandOutputKind
    title: str = ""
    summary: str = ""
    payload: dict[str, Any]
    source: Literal["command_agent", "dashboard_runtime", "calendar", "goals", "materials", "notes", "memory", "settings"] = "command_agent"
    related_action_id: str = Field(default="", alias="relatedActionId")
    related_run_id: str = Field(default="", alias="relatedRunId")
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandThreadOut(BaseModel):
    id: str
    title: str
    messages: list[CommandMessageOut]
    current_draft: CommandDraftOut | None = Field(default=None, alias="currentDraft")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CommandThreadSummaryOut(BaseModel):
    id: str
    title: str
    message_count: int = Field(alias="messageCount")
    current_draft_title: str = Field(default="", alias="currentDraftTitle")
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
