import logging
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException

from ..schemas import AiModelRoutingUpdate, AiSettingsOut, AiSettingsTestOut, AiSettingsTestPayload, AiSettingsUpdate
from ..services.ai_settings import (
    SUPPORTED_PROVIDERS,
    AiSettingsSaveValidationError,
    delete_provider_api_key,
    get_public_ai_settings,
    save_ai_settings,
    save_model_routing_rules,
)
from ..services.llm import LlmClient

logger = logging.getLogger("planix.api.settings")
router = APIRouter(prefix="/api/ai", tags=["ai-settings"])

_SAFE_SETTINGS_TEST_ERRORS = {
    "auth_error": "API key is invalid or expired.",
    "bad_model": "The model name does not exist or is not supported.",
    "bad_base_url": "The Base URL endpoint is unavailable or invalid.",
    "bad_request": "The model service rejected the test request.",
    "network_error": "The model service cannot be reached. Check the network or service availability.",
    "timeout": "The model request timed out. Check the network or increase the timeout.",
    "rate_limit": "The model service rate limit was reached. Try again later.",
    "insufficient_balance": "The model account has insufficient balance.",
    "invalid_key_format": "The API key format is invalid.",
    "invalid_model_output": "The model returned an invalid response.",
    "model_output_truncated": "The model output was truncated before the test completed.",
    "unknown": "The model test failed. Check the provider settings and try again.",
}


def _safe_settings_test_error(error_type: str | None) -> tuple[str, str]:
    safe_type = error_type if error_type in _SAFE_SETTINGS_TEST_ERRORS else "unknown"
    return safe_type, _SAFE_SETTINGS_TEST_ERRORS[safe_type]


def _sanitize_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))
    except Exception:
        return "<invalid-url>"


@router.get("/settings", response_model=AiSettingsOut)
def read_ai_settings() -> AiSettingsOut:
    settings = get_public_ai_settings()
    logger.info(
        "GET /api/ai/settings -> provider=%s model=%s base_url=%s api_key_present=%s",
        settings.provider,
        settings.model,
        _sanitize_base_url(settings.base_url),
        settings.has_api_key,
    )
    return settings


@router.put("/settings", response_model=AiSettingsOut)
def update_ai_settings(payload: AiSettingsUpdate) -> AiSettingsOut:
    has_key = bool(payload.api_key and payload.api_key.strip())
    logger.info(
        "PUT /api/ai/settings provider=%s base_url=%s model=%s api_key_present=%s",
        payload.provider,
        _sanitize_base_url(payload.base_url),
        payload.model,
        has_key,
    )
    try:
        result = save_ai_settings(payload)
        logger.info("AI settings saved successfully")
        return result
    except AiSettingsSaveValidationError as exc:
        logger.info(
            "AI settings validation failed provider=%s model=%s error_type=%s status_code=%s",
            exc.provider,
            exc.model,
            exc.error_type,
            exc.status_code,
        )
        raise HTTPException(status_code=400, detail=exc.to_detail()) from exc
    except Exception as exc:
        logger.error("AI settings save failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Backend failed to save AI settings") from exc


@router.delete("/settings/key/{provider}", response_model=AiSettingsOut)
def clear_ai_provider_key(provider: str) -> AiSettingsOut:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider not found")
    logger.info("DELETE /api/ai/settings/key/%s", provider)
    try:
        return delete_provider_api_key(provider)
    except Exception as exc:
        logger.error("AI provider key clear failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Backend failed to clear provider API key") from exc


@router.put("/settings/routing", response_model=AiSettingsOut)
def update_ai_model_routing(payload: AiModelRoutingUpdate) -> AiSettingsOut:
    logger.info("PUT /api/ai/settings/routing rules=%s", len(payload.routing_rules))
    try:
        return save_model_routing_rules(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("AI model routing save failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Backend failed to save AI model routing") from exc


@router.post("/test", response_model=AiSettingsTestOut)
def test_ai_settings(payload: AiSettingsTestPayload) -> AiSettingsTestOut:
    client = LlmClient()
    if not client.is_enabled():
        if client.settings.provider == "mock":
            return AiSettingsTestOut(
                ok=True,
                mode="mock",
                message="Mock mode is active and does not require an API key. Save a real key to test a live model.",
                provider=client.settings.provider,
                model=client.settings.model,
            )
        return AiSettingsTestOut(
            ok=False,
            mode="error",
            message="API key is not saved. Enter an API key in settings first.",
            provider=client.settings.provider,
            model=client.settings.model,
            error_type="no_key",
        )

    result, err = client.complete(
        "settings_test",
        "You are a concise health-check assistant. Reply in one short sentence.",
        payload.prompt,
        max_tokens=512,
        temperature=0.1,
        task_type="settings_test",
    )
    if result:
        return AiSettingsTestOut(
            ok=True,
            mode="llm",
            message=result.content,
            provider=result.provider,
            model=result.model,
        )
    safe_error_type, safe_message = _safe_settings_test_error(err.error_type if err else None)
    return AiSettingsTestOut(
        ok=False,
        mode="error",
        message=safe_message,
        provider=client.settings.provider,
        model=client.settings.model,
        error_type=safe_error_type,
        status_code=err.status_code if err else 0,
        detail=None,
    )
