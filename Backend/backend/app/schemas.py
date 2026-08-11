from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .api_key import INVALID_API_KEY_MESSAGE, validate_api_key_format


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
ModelRoutingTaskType = Literal["learning_semantic"]


def normalize_local_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Base URL must use http(s) and point to localhost, 127.0.0.1, or ::1")
    return cleaned if cleaned.endswith("/v1") else f"{cleaned}/v1"


class AiSettingsUpdate(BaseModel):
    provider: AiProvider = "deepseek"
    base_url: str = Field(default="https://api.deepseek.com", alias="baseUrl")
    model: str = "deepseek-v4-flash"
    api_key: str | None = Field(default=None, alias="apiKey")
    temperature: float = Field(default=0.3, ge=0, le=2)
    timeout_seconds: int = Field(default=40, alias="timeoutSeconds", ge=5, le=120)
    force_non_thinking: bool = Field(default=False, alias="forceNonThinking")

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
    local_fallback_enabled: bool = Field(default=False, alias="localFallbackEnabled")
    updated_at: str = Field(default="", alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)

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
            if provider != "mock" and provider not in cleaned:
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
    force_non_thinking: bool = Field(default=False, alias="forceNonThinking")
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
