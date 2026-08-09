import os
import json
from dataclasses import dataclass

from ..db import get_conn, jsonb
from ..schemas import AiAutoModelPolicy, AiModelRoutingRule, AiModelRoutingUpdate, AiProvider, AiSavedProvider, AiSettingsOut, AiSettingsUpdate
from .secret_store import get_secret_store, provider_secret_key


SETTINGS_ID = "local-default"
SUPPORTED_PROVIDERS = {"mock", "deepseek", "kimi", "zhipu_glm", "openai", "custom", "local"}
KEYED_PROVIDERS = {"deepseek", "kimi", "zhipu_glm", "openai", "custom", "local"}
PROVIDER_ORDER = ["deepseek", "kimi", "zhipu_glm", "openai", "custom", "local"]
AUTO_PROVIDER_DEFAULT_ORDER = ["deepseek", "zhipu_glm", "kimi", "openai", "custom", "local"]
ROUTABLE_TASK_TYPES = [
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
]
AUTO_MODEL_POLICY_KEY = "ai.autoModelPolicy"
FORCE_NON_THINKING_KEY = "ai.forceNonThinking"
KEY_STATUS_VALUES = {"unchecked", "valid", "invalid"}
KEY_INVALIDATING_ERRORS = {"auth_error", "invalid_key_format"}
DEFAULT_TASK_STRATEGIES = {
    "planning_understanding": "knowledge_reasoning",
    "planning_plan": "structured_stable",
    "planning_review": "strict_json",
    "planning_learning": "knowledge_reasoning",
}
AUTO_STRATEGY_SCORES = {
    "fast_low_cost": {"zhipu_glm": 95, "deepseek": 88, "kimi": 76, "openai": 72, "custom": 70, "local": 74},
    "structured_stable": {"deepseek": 95, "kimi": 90, "openai": 86, "custom": 82, "zhipu_glm": 78, "local": 84},
    "strict_json": {"deepseek": 94, "zhipu_glm": 90, "openai": 88, "custom": 82, "kimi": 78, "local": 84},
    "context_summary": {"kimi": 94, "deepseek": 88, "openai": 86, "custom": 82, "zhipu_glm": 80, "local": 84},
    "classification": {"zhipu_glm": 92, "deepseek": 88, "kimi": 82, "openai": 80, "custom": 78, "local": 80},
    "knowledge_reasoning": {"kimi": 92, "deepseek": 90, "openai": 88, "custom": 84, "zhipu_glm": 80, "local": 86},
    "balanced": {"deepseek": 90, "kimi": 88, "openai": 86, "zhipu_glm": 84, "custom": 82, "local": 84},
}
DEFAULT_BASE_URLS = {
    "mock": "https://api.deepseek.com",
    "deepseek": "https://api.deepseek.com",
    "kimi": "https://api.moonshot.ai/v1",
    "zhipu_glm": "https://open.bigmodel.cn/api/paas/v4",
    "openai": "https://api.openai.com/v1",
    "custom": "",
    "local": "",
}
DEFAULT_MODELS = {
    "mock": "deepseek-v4-flash",
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-k2.7-code",
    "zhipu_glm": "glm-4-flash",
    "openai": "gpt-4o-mini",
    "custom": "gpt-4o-mini",
    "local": "",
}


@dataclass(frozen=True)
class EffectiveAiSettings:
    provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    timeout_seconds: int
    updated_at: str
    key_status: str = "unchecked"
    key_error_type: str = ""
    force_non_thinking: bool = False
    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def has_usable_api_key(self) -> bool:
        return self.has_api_key and self.key_status != "invalid"

    @property
    def can_call(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip()) and (
            self.provider == "local" or self.has_usable_api_key
        )


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    api_key_source: str
    updated_at: str
    key_status: str = "unchecked"
    key_error_type: str = ""
    last_validated_at: str = ""

    @property
    def has_api_key(self) -> bool:
        return self.api_key_source == "user" and bool(self.api_key.strip())

    @property
    def can_call(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip()) and (
            self.provider == "local" or (self.has_api_key and self.key_status != "invalid")
        )


@dataclass(frozen=True)
class PendingProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    api_key_source: str
    configuration_changed: bool
    key_status: str = "unchecked"
    key_error_type: str = ""
    last_validated_at: str = ""


@dataclass(frozen=True)
class ModelRoutingRuleConfig:
    task_type: str
    primary_provider: str
    fallback_providers: tuple[str, ...]
    local_fallback_enabled: bool
    updated_at: str = ""


def _env_provider() -> str:
    provider = os.getenv("AI_PROVIDER", "deepseek").strip() or "deepseek"
    return provider if provider in SUPPORTED_PROVIDERS else "custom"


def _default_base_url(provider: str) -> str:
    return DEFAULT_BASE_URLS.get(provider) or "https://api.deepseek.com"


def _default_model(provider: str) -> str:
    return DEFAULT_MODELS.get(provider, "deepseek-v4-flash")


def _env_base_url() -> str:
    provider = _env_provider()
    default = _default_base_url(provider)
    return os.getenv("AI_API_BASE", default).strip() or default


def _env_model() -> str:
    provider = _env_provider()
    default = _default_model(provider)
    return os.getenv("AI_MODEL", default).strip() or default


def _env_api_key() -> str:
    provider = _env_provider()
    provider_key = {
        "deepseek": "DEEPSEEK_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "zhipu_glm": "ZHIPU_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider)
    if provider_key:
        return (os.getenv(provider_key) or os.getenv("AI_API_KEY") or "").strip()
    return (os.getenv("AI_API_KEY") or "").strip()


def _row_to_provider_config(row) -> ProviderConfig | None:
    if not row:
        return None
    provider = row["provider"]
    api_key = get_secret_store().get(provider_secret_key(provider)) if row["api_key_source"] in {"user", "secret_store"} else ""
    return ProviderConfig(
        provider=provider,
        base_url=row["base_url"] or _default_base_url(provider),
        model=row["model"] or _default_model(provider),
        api_key=api_key,
        api_key_source="user" if api_key else "",
        updated_at=row["updated_at"],
        key_status=row["key_status"] if row["key_status"] in KEY_STATUS_VALUES else "unchecked",
        key_error_type=row["key_error_type"] or "",
        last_validated_at=row["last_validated_at"] or "",
    )


def _config_for_provider(conn, provider: str) -> ProviderConfig | None:
    row = conn.execute("SELECT * FROM ai_provider_configs WHERE provider = %s", (provider,)).fetchone()
    return _row_to_provider_config(row)


def _saved_provider_rows(conn) -> list[AiSavedProvider]:
    rows = conn.execute("SELECT * FROM ai_provider_configs").fetchall()
    configs = [_row_to_provider_config(row) for row in rows]
    configs = [
        config
        for config in configs
        if config
        and config.provider in KEYED_PROVIDERS
        and (
            config.has_api_key
            or (config.provider == "local" and bool(config.base_url.strip() and config.model.strip()))
        )
    ]
    order = {provider: index for index, provider in enumerate(PROVIDER_ORDER)}
    configs.sort(key=lambda item: order.get(item.provider, 999))
    return [
        AiSavedProvider(
            provider=config.provider,
            baseUrl=config.base_url,
            model=config.model,
            hasApiKey=config.has_api_key,
            keyStatus=config.key_status,
            keyErrorType=config.key_error_type,
            lastValidatedAt=config.last_validated_at,
            updatedAt=config.updated_at,
        )
        for config in configs
    ]


def _normalize_auto_provider_order(value: object) -> list[str]:
    raw = value if isinstance(value, list) else []
    cleaned: list[str] = []
    for provider in raw:
        if provider in KEYED_PROVIDERS and provider not in cleaned:
            cleaned.append(provider)
    for provider in AUTO_PROVIDER_DEFAULT_ORDER:
        if provider not in cleaned:
            cleaned.append(provider)
    return cleaned


def _normalize_task_strategies(value: object) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    valid_strategies = set(AUTO_STRATEGY_SCORES)
    strategies: dict[str, str] = {}
    for task_type in ROUTABLE_TASK_TYPES:
        strategy = raw.get(task_type) if isinstance(raw, dict) else None
        strategies[task_type] = strategy if strategy in valid_strategies else DEFAULT_TASK_STRATEGIES[task_type]
    return strategies


def _saved_key_provider_order(conn) -> list[str]:
    rows = conn.execute("SELECT * FROM ai_provider_configs").fetchall()
    saved = {
        config.provider
        for row in rows if (config := _row_to_provider_config(row))
        if config.provider in KEYED_PROVIDERS
        and (
            config.provider == "local"
            or config.has_api_key
        )
        and config.key_status != "invalid"
    }
    ordered = [provider for provider in AUTO_PROVIDER_DEFAULT_ORDER if provider in saved]
    ordered.extend(provider for provider in AUTO_PROVIDER_DEFAULT_ORDER if provider not in ordered)
    return ordered


def _default_auto_model_policy(conn=None) -> AiAutoModelPolicy:
    return AiAutoModelPolicy(
        autoProviderOrder=_saved_key_provider_order(conn) if conn is not None else AUTO_PROVIDER_DEFAULT_ORDER,
        taskStrategy=DEFAULT_TASK_STRATEGIES,
    )


def _auto_model_policy(conn) -> AiAutoModelPolicy:
    row = conn.execute("SELECT value FROM user_preferences WHERE key = %s", (AUTO_MODEL_POLICY_KEY,)).fetchone()
    if not row:
        return _default_auto_model_policy(conn)
    raw = row["value"] if isinstance(row["value"], dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    normalized = AiAutoModelPolicy(
        autoProviderOrder=_normalize_auto_provider_order(raw.get("autoProviderOrder")),
        taskStrategy=_normalize_task_strategies(raw.get("taskStrategy")),
    )
    normalized_json = json.dumps(
        normalized.model_dump(by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True) != json.dumps(
        normalized.model_dump(by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ):
        conn.execute(
            "UPDATE user_preferences SET value = %s, updated_at = CURRENT_TIMESTAMP WHERE key = %s",
            (jsonb(normalized.model_dump(by_alias=True)), AUTO_MODEL_POLICY_KEY),
        )
    return normalized


def _force_non_thinking(conn) -> bool:
    row = conn.execute("SELECT value FROM user_preferences WHERE key = %s", (FORCE_NON_THINKING_KEY,)).fetchone()
    return bool(row and row["value"] is True)


def _save_force_non_thinking(conn, value: bool) -> None:
    conn.execute(
        """
        INSERT INTO user_preferences(key, value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (FORCE_NON_THINKING_KEY, jsonb(bool(value))),
    )


def _save_auto_model_policy(conn, policy: AiAutoModelPolicy | None) -> None:
    if policy is None:
        return
    normalized = AiAutoModelPolicy(
        autoProviderOrder=_normalize_auto_provider_order(policy.auto_provider_order),
        taskStrategy=_normalize_task_strategies(policy.task_strategy),
    )
    conn.execute(
        """
        INSERT INTO user_preferences(key, value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (
            AUTO_MODEL_POLICY_KEY,
            jsonb(normalized.model_dump(by_alias=True)),
        ),
    )


def _routing_primary_for_active(active_provider: str) -> str:
    return active_provider if active_provider in KEYED_PROVIDERS else "deepseek"


def _default_routing_rule_configs(active_provider: str) -> list[ModelRoutingRuleConfig]:
    return [
        ModelRoutingRuleConfig(
            task_type=task_type,
            primary_provider="auto",
            fallback_providers=("deepseek",),
            local_fallback_enabled=not task_type.startswith("planning_"),
        )
        for task_type in ROUTABLE_TASK_TYPES
    ]


def _routing_row_to_config(row) -> ModelRoutingRuleConfig | None:
    if not row:
        return None
    raw_fallbacks = row["fallback_providers_json"] if isinstance(row["fallback_providers_json"], list) else []
    fallbacks: list[str] = []
    if isinstance(raw_fallbacks, list):
        for provider in raw_fallbacks:
            if provider in KEYED_PROVIDERS and provider not in fallbacks:
                fallbacks.append(provider)
            if len(fallbacks) >= 2:
                break
    primary = row["primary_provider"] if row["primary_provider"] == "auto" or row["primary_provider"] in KEYED_PROVIDERS else "auto"
    if primary != "auto":
        fallbacks = [provider for provider in fallbacks if provider != primary]
    task_type = row["task_type"]
    return ModelRoutingRuleConfig(
        task_type=task_type,
        primary_provider=primary,
        fallback_providers=tuple(fallbacks),
        local_fallback_enabled=False
        if task_type.startswith("planning_")
        else bool(row["local_fallback_enabled"]),
        updated_at=row["updated_at"],
    )


def _routing_config_to_public(rule: ModelRoutingRuleConfig) -> AiModelRoutingRule:
    return AiModelRoutingRule(
        taskType=rule.task_type,
        primaryProvider=rule.primary_provider,
        fallbackProviders=list(rule.fallback_providers),
        localFallbackEnabled=rule.local_fallback_enabled,
        updatedAt=rule.updated_at,
    )


def _ensure_default_routing_rules(conn) -> None:
    settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
    active_provider = settings_row["provider"] if settings_row else _env_provider()
    for rule in _default_routing_rule_configs(active_provider):
        conn.execute(
            """
            INSERT INTO ai_model_routing_rules(
              task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
            )
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (task_type) DO NOTHING
            """,
            (
                rule.task_type,
                rule.primary_provider,
                jsonb(list(rule.fallback_providers)),
                rule.local_fallback_enabled,
            ),
        )


def _routing_rule_rows(conn) -> list[AiModelRoutingRule]:
    _ensure_default_routing_rules(conn)
    rows = conn.execute("SELECT * FROM ai_model_routing_rules ORDER BY task_type").fetchall()
    by_task: dict[str, ModelRoutingRuleConfig] = {}
    for row in rows:
        config = _routing_row_to_config(row)
        if not config or config.task_type not in ROUTABLE_TASK_TYPES:
            continue
        by_task[config.task_type] = config
    settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
    active_provider = settings_row["provider"] if settings_row else _env_provider()
    defaults = {rule.task_type: rule for rule in _default_routing_rule_configs(active_provider)}
    rules = [by_task.get(task_type) or defaults[task_type] for task_type in ROUTABLE_TASK_TYPES]
    return [_routing_config_to_public(rule) for rule in rules]


def _env_effective(force_non_thinking: bool = False) -> EffectiveAiSettings:
    return EffectiveAiSettings(
        provider=_env_provider(),
        base_url=_env_base_url(),
        model=_env_model(),
        api_key=_env_api_key(),
        temperature=0.3,
        timeout_seconds=40,
        updated_at="",
        force_non_thinking=force_non_thinking,
    )


def _effective_from_rows(
    settings_row,
    config: ProviderConfig | None,
    force_non_thinking: bool = False,
) -> EffectiveAiSettings:
    if not settings_row:
        return _env_effective(force_non_thinking)
    provider = settings_row["provider"] if settings_row["provider"] in SUPPORTED_PROVIDERS else "custom"
    base_url = settings_row["base_url"] or _default_base_url(provider)
    model = settings_row["model"] or _default_model(provider)
    api_key = ""
    key_status = "unchecked"
    key_error_type = ""
    if config:
        base_url = config.base_url
        model = config.model
        api_key = config.api_key if config.has_api_key else ""
        key_status = config.key_status
        key_error_type = config.key_error_type
    elif settings_row["api_key_source"] in {"user", "secret_store"}:
        api_key = get_secret_store().get(provider_secret_key(provider))
    return EffectiveAiSettings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=float(settings_row["temperature"]),
        timeout_seconds=int(settings_row["timeout_seconds"]),
        updated_at=settings_row["updated_at"],
        key_status=key_status,
        key_error_type=key_error_type,
        force_non_thinking=force_non_thinking,
    )


def _to_public(
    settings: EffectiveAiSettings,
    saved_providers: list[AiSavedProvider],
    routing_rules: list[AiModelRoutingRule],
    auto_model_policy: AiAutoModelPolicy,
) -> AiSettingsOut:
    return AiSettingsOut(
        provider=settings.provider,
        baseUrl=settings.base_url,
        model=settings.model,
        hasApiKey=settings.has_api_key,
        keyStatus=settings.key_status,
        keyErrorType=settings.key_error_type,
        temperature=settings.temperature,
        timeoutSeconds=settings.timeout_seconds,
        forceNonThinking=settings.force_non_thinking,
        updatedAt=settings.updated_at,
        savedProviders=saved_providers,
        routingRules=routing_rules,
        autoModelPolicy=auto_model_policy,
    )


def get_effective_ai_settings() -> EffectiveAiSettings:
    with get_conn() as conn:
        settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
        provider = settings_row["provider"] if settings_row else _env_provider()
        config = _config_for_provider(conn, provider) if provider in KEYED_PROVIDERS else None
        force_non_thinking = _force_non_thinking(conn)
    return _effective_from_rows(settings_row, config, force_non_thinking)


def get_effective_ai_settings_for_provider(provider: str, active_settings: EffectiveAiSettings | None = None) -> EffectiveAiSettings:
    with get_conn() as conn:
        settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
        force_non_thinking = active_settings.force_non_thinking if active_settings is not None else _force_non_thinking(conn)
        active = active_settings or _effective_from_rows(
            settings_row,
            _config_for_provider(conn, settings_row["provider"]) if settings_row and settings_row["provider"] in KEYED_PROVIDERS else None,
            force_non_thinking,
        )
        if provider not in KEYED_PROVIDERS:
            return EffectiveAiSettings(
                provider=provider,
                base_url=_default_base_url(provider),
                model=_default_model(provider),
                api_key="",
                temperature=active.temperature,
                timeout_seconds=active.timeout_seconds,
                updated_at=active.updated_at,
                key_status="unchecked",
                key_error_type="",
                force_non_thinking=force_non_thinking,
            )
        config = _config_for_provider(conn, provider)
        return EffectiveAiSettings(
            provider=provider,
            base_url=config.base_url if config else _default_base_url(provider),
            model=config.model if config else _default_model(provider),
            api_key=config.api_key if config and config.has_api_key else "",
            temperature=active.temperature,
            timeout_seconds=active.timeout_seconds,
            updated_at=config.updated_at if config else active.updated_at,
            key_status=config.key_status if config else "unchecked",
            key_error_type=config.key_error_type if config else "",
            force_non_thinking=force_non_thinking,
        )


def mark_provider_key_valid(provider: str, attempted_api_key: str) -> None:
    if provider not in KEYED_PROVIDERS or not attempted_api_key.strip():
        return
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ai_provider_configs
            SET key_status = 'valid', key_error_type = '',
                last_validated_at = CURRENT_TIMESTAMP
            WHERE provider = %s AND api_key_source = 'secret_store'
              AND key_status != 'valid'
            """,
            (provider,),
        )


def mark_provider_key_invalid(provider: str, attempted_api_key: str, error_type: str) -> None:
    if provider not in KEYED_PROVIDERS or not attempted_api_key.strip() or error_type not in KEY_INVALIDATING_ERRORS:
        return
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ai_provider_configs
            SET key_status = 'invalid', key_error_type = %s,
                last_validated_at = CURRENT_TIMESTAMP
            WHERE provider = %s AND api_key_source = 'secret_store'
            """,
            (error_type, provider),
        )


def get_model_routing_rule(task_type: str, active_provider: str) -> ModelRoutingRuleConfig:
    if task_type not in ROUTABLE_TASK_TYPES:
        return ModelRoutingRuleConfig(
            task_type=task_type,
            primary_provider="auto",
            fallback_providers=("deepseek",),
            local_fallback_enabled=False,
        )
    with get_conn() as conn:
        _ensure_default_routing_rules(conn)
        row = conn.execute("SELECT * FROM ai_model_routing_rules WHERE task_type = %s", (task_type,)).fetchone()
        config = _routing_row_to_config(row)
    if config:
        return config
    return ModelRoutingRuleConfig(
        task_type=task_type,
        primary_provider="auto",
        fallback_providers=("deepseek",),
        local_fallback_enabled=not task_type.startswith("planning_"),
    )


def get_auto_model_provider_chain(task_type: str, fallback_providers: tuple[str, ...] = ()) -> tuple[str, ...]:
    with get_conn() as conn:
        policy = _auto_model_policy(conn)
    strategy = policy.task_strategy.get(task_type, DEFAULT_TASK_STRATEGIES.get(task_type, "balanced"))
    order = _normalize_auto_provider_order(policy.auto_provider_order)
    order_rank = {provider: index for index, provider in enumerate(order)}
    scores = AUTO_STRATEGY_SCORES.get(strategy, AUTO_STRATEGY_SCORES["balanced"])

    def score(provider: str) -> int:
        priority_bonus = max(0, 10 - order_rank.get(provider, 99) * 2)
        return int(scores.get(provider, 70)) + priority_bonus

    selected = sorted(order, key=lambda provider: (-score(provider), order_rank.get(provider, 99)))
    chain: list[str] = []
    for provider in [*selected, *fallback_providers]:
        if provider in KEYED_PROVIDERS and provider not in chain:
            chain.append(provider)
    return tuple(chain)


def get_public_ai_settings() -> AiSettingsOut:
    with get_conn() as conn:
        settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
        provider = settings_row["provider"] if settings_row else _env_provider()
        config = _config_for_provider(conn, provider) if provider in KEYED_PROVIDERS else None
        settings = _effective_from_rows(settings_row, config, _force_non_thinking(conn))
        saved_providers = _saved_provider_rows(conn)
        routing_rules = _routing_rule_rows(conn)
        auto_model_policy = _auto_model_policy(conn)
    return _to_public(settings, saved_providers, routing_rules, auto_model_policy)


def _candidate_provider_config(conn, payload: AiSettingsUpdate) -> PendingProviderConfig:
    provider = payload.provider
    base_url = payload.base_url.strip().rstrip("/") or _default_base_url(provider)
    model = payload.model.strip() or _default_model(provider)
    existing = _config_for_provider(conn, provider)
    if payload.api_key is None:
        api_key = existing.api_key if existing and existing.has_api_key else ""
        api_key_source = "user" if api_key else ""
        configuration_changed = bool(
            not existing or base_url != existing.base_url or model != existing.model
        )
        key_status = existing.key_status if api_key and existing else "unchecked"
        key_error_type = existing.key_error_type if api_key and existing else ""
        last_validated_at = existing.last_validated_at if api_key and existing else ""
    else:
        api_key = payload.api_key.strip()
        api_key_source = "user" if api_key else ""
        configuration_changed = bool(
            not existing
            or base_url != existing.base_url
            or model != existing.model
            or api_key != existing.api_key
        )
        key_status = existing.key_status if existing and not configuration_changed else "unchecked"
        key_error_type = existing.key_error_type if existing and not configuration_changed else ""
        last_validated_at = existing.last_validated_at if existing and not configuration_changed else ""
    if configuration_changed:
        key_status = "unchecked"
        key_error_type = ""
        last_validated_at = ""
    return PendingProviderConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_source=api_key_source,
        configuration_changed=configuration_changed,
        key_status=key_status,
        key_error_type=key_error_type,
        last_validated_at=last_validated_at,
    )


def _upsert_provider_config(conn, candidate: PendingProviderConfig) -> tuple[str, str]:
    store = get_secret_store()
    if candidate.api_key:
        store.set(provider_secret_key(candidate.provider), candidate.api_key)
    else:
        store.delete(provider_secret_key(candidate.provider))
    stored_source = "secret_store" if candidate.api_key else ""
    conn.execute(
        """
        INSERT INTO ai_provider_configs(
          provider, base_url, model, api_key_source,
          key_status, key_error_type, last_validated_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(provider)
        DO UPDATE SET
          base_url = excluded.base_url,
          model = excluded.model,
          api_key_source = excluded.api_key_source,
          key_status = excluded.key_status,
          key_error_type = excluded.key_error_type,
          last_validated_at = excluded.last_validated_at,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            candidate.provider,
            candidate.base_url,
            candidate.model,
            stored_source,
            candidate.key_status,
            candidate.key_error_type,
            candidate.last_validated_at or None,
        ),
    )
    return "", stored_source


def _restore_provider_secret(provider: str, previous_secret: str | None) -> None:
    store = get_secret_store()
    key = provider_secret_key(provider)
    if previous_secret:
        store.set(key, previous_secret)
    else:
        store.delete(key)


def save_ai_settings(payload: AiSettingsUpdate) -> AiSettingsOut:
    secret_store = get_secret_store()
    secret_key = provider_secret_key(payload.provider)
    previous_secret = secret_store.get(secret_key) if payload.provider != "mock" else None
    try:
        with get_conn() as conn:
            if payload.provider == "mock":
                base_url = payload.base_url.strip().rstrip("/") or _default_base_url(payload.provider)
                model = payload.model.strip() or _default_model(payload.provider)
                api_key_source = ""
            else:
                candidate = _candidate_provider_config(conn, payload)
                _db_api_key, api_key_source = _upsert_provider_config(conn, candidate)
                base_url = candidate.base_url
                model = candidate.model

            conn.execute(
                """
                INSERT INTO ai_settings(
                  id, provider, base_url, model, api_key_source,
                  temperature, timeout_seconds, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(id)
                DO UPDATE SET
                  provider = excluded.provider,
                  base_url = excluded.base_url,
                  model = excluded.model,
                  api_key_source = excluded.api_key_source,
                  temperature = excluded.temperature,
                  timeout_seconds = excluded.timeout_seconds,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    SETTINGS_ID,
                    payload.provider,
                    base_url,
                    model,
                    api_key_source,
                    payload.temperature,
                    payload.timeout_seconds,
                ),
            )
            _save_force_non_thinking(conn, payload.force_non_thinking)
    except Exception:
        if payload.provider != "mock":
            _restore_provider_secret(payload.provider, previous_secret)
        raise
    return get_public_ai_settings()


def save_model_routing_rules(payload: AiModelRoutingUpdate) -> AiSettingsOut:
    incoming = {rule.task_type: rule for rule in payload.routing_rules}
    unknown_tasks = set(incoming) - set(ROUTABLE_TASK_TYPES)
    if unknown_tasks:
        raise ValueError(f"Unsupported routing task type: {sorted(unknown_tasks)[0]}")
    with get_conn() as conn:
        settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
        active_provider = settings_row["provider"] if settings_row else _env_provider()
        defaults = {rule.task_type: rule for rule in _default_routing_rule_configs(active_provider)}
        _save_auto_model_policy(conn, payload.auto_model_policy)
        for task_type in ROUTABLE_TASK_TYPES:
            rule = incoming.get(task_type)
            if rule:
                primary = rule.primary_provider
                fallbacks = [
                    provider
                    for provider in rule.fallback_providers
                    if primary == "auto" or provider != primary
                ]
                local_fallback_enabled = (
                    False
                    if task_type.startswith("planning_")
                    else rule.local_fallback_enabled
                )
            else:
                default = defaults[task_type]
                primary = default.primary_provider
                fallbacks = list(default.fallback_providers)
                local_fallback_enabled = default.local_fallback_enabled
            conn.execute(
                """
                INSERT INTO ai_model_routing_rules(
                  task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
                )
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(task_type)
                DO UPDATE SET
                  primary_provider = excluded.primary_provider,
                  fallback_providers_json = excluded.fallback_providers_json,
                  local_fallback_enabled = excluded.local_fallback_enabled,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    task_type,
                    primary,
                    jsonb(fallbacks[:2]),
                    local_fallback_enabled,
                ),
            )
    return get_public_ai_settings()


def delete_provider_api_key(provider: AiProvider | str) -> AiSettingsOut:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    provider_name = str(provider)
    store = get_secret_store()
    key = provider_secret_key(provider_name)
    previous_secret = store.get(key)
    try:
        with get_conn() as conn:
            store.delete(key)
            if provider == "local":
                conn.execute("DELETE FROM ai_provider_configs WHERE provider = %s", (provider,))
            elif provider in KEYED_PROVIDERS:
                existing = _config_for_provider(conn, provider)
                if existing:
                    conn.execute(
                        """
                        UPDATE ai_provider_configs
                        SET api_key_source = '', key_status = 'unchecked',
                            key_error_type = '', last_validated_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE provider = %s
                        """,
                        (provider,),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO ai_provider_configs(
                          provider, base_url, model, api_key_source, updated_at
                        )
                        VALUES (%s, %s, %s, '', CURRENT_TIMESTAMP)
                        """,
                        (provider, _default_base_url(provider), _default_model(provider)),
                    )
            settings_row = conn.execute("SELECT * FROM ai_settings WHERE id = %s", (SETTINGS_ID,)).fetchone()
            if settings_row and settings_row["provider"] == provider:
                if provider == "local":
                    conn.execute(
                        """
                        UPDATE ai_settings
                        SET base_url = '', model = '', api_key_source = '', updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (SETTINGS_ID,),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE ai_settings
                        SET api_key_source = '', updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (SETTINGS_ID,),
                    )
    except Exception:
        _restore_provider_secret(provider_name, previous_secret)
        raise
    return get_public_ai_settings()
