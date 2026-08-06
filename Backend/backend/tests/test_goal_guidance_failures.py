from __future__ import annotations

import pytest

from app.schemas import ModelRouteAttempt, ModelUsage
from app.services.command_agent import _goal_understanding_unavailable_reply
from app.services.goal_understanding import GoalUnderstandingOutcome


def _outcome(
    *attempts: tuple[str, str],
    source: str = "model_unavailable",
) -> GoalUnderstandingOutcome:
    return GoalUnderstandingOutcome(
        result=None,
        usage=ModelUsage(
            provider="deepseek",
            model="deepseek-v4-flash",
            mode="llm" if source == "invalid_model_output" else "model_unavailable",
            taskType="goal_understanding",
            fallbackUsed=len(attempts) > 1,
            localFallbackAllowed=False,
            attempts=[
                ModelRouteAttempt(
                    provider=provider,
                    model=f"{provider}-model",
                    status="skipped" if error_type == "missing_api_key" else "error",
                    errorType=error_type,
                    latencyMs=1,
                )
                for provider, error_type in attempts
            ],
        ),
        source=source,
        error="raw provider detail must not be shown",
    )


def test_goal_failure_explains_the_observed_auth_and_missing_key_scenario() -> None:
    outcome = _outcome(
        ("deepseek", "auth_error"),
        ("kimi", "missing_api_key"),
        ("openai", "missing_api_key"),
        ("zhipu_glm", "auth_error"),
        ("custom", "missing_api_key"),
    )

    reply = _goal_understanding_unavailable_reply("我要学 Python", outcome)

    assert "DeepSeek、GLM 的 API Key 无效或已过期" in reply
    assert "Kimi 尚未配置 Key" in reply
    assert "这不是你的目标表达有问题" in reply
    assert "原始输入已经保留" in reply
    assert "无法可靠理解" not in reply
    assert outcome.error not in reply


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (
            _outcome(
                ("deepseek", "missing_api_key"),
                ("zhipu_glm", "missing_api_key"),
                ("kimi", "missing_api_key"),
            ),
            "没有可用的 API Key",
        ),
        (_outcome(("deepseek", "timeout")), "返回结果前超时"),
        (_outcome(("deepseek", "rate_limit")), "触发频率限制"),
        (_outcome(source="invalid_model_output"), "不符合 Goal Understanding 结构化协议"),
        (_outcome(("deepseek", "model_output_truncated")), "模型返回内容被截断"),
    ],
)
def test_goal_failure_uses_actionable_sanitized_reason_categories(
    outcome: GoalUnderstandingOutcome,
    expected: str,
) -> None:
    reply = _goal_understanding_unavailable_reply("我要学 Python", outcome)

    assert expected in reply
    assert "没有启动规划" in reply
    assert "无法可靠理解" not in reply
    assert outcome.error not in reply


def test_goal_failure_reply_is_localized_for_english_input() -> None:
    outcome = _outcome(("deepseek", "auth_error"), ("kimi", "missing_api_key"))

    reply = _goal_understanding_unavailable_reply("I want to learn Python", outcome)

    assert "DeepSeek rejected the saved API Key as invalid or expired" in reply
    assert "Kimi does not have a saved Key" in reply
    assert "not caused by how you phrased the goal" in reply
    assert outcome.error not in reply
