import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.schemas import GoalUnderstandingResult, ModelUsage  # noqa: E402
from app.services.command_agent import detect_command_intent  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'planix-test.db'}")
    monkeypatch.setenv("USE_REAL_LLM", "0")
    monkeypatch.delenv("PLANIX_USE_LANGGRAPH_PLANNING", raising=False)
    monkeypatch.delenv("PLANIX_USE_COGNITIVE_PLANNING", raising=False)
    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "false")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)

    # Downstream API tests use a deterministic model boundary; dedicated Goal Understanding
    # tests replace this double with exact ambiguity, consistency, and failure outcomes.
    class DeterministicGoalUnderstandingService:
        def understand(self, message, **_kwargs):
            detected = detect_command_intent(message)
            intent_state = (
                "clear_goal"
                if detected == "planning_request"
                else "normal_chat"
                if detected == "normal_chat"
                else "command"
            )
            result = GoalUnderstandingResult.model_validate(
                {
                    "intentState": intent_state,
                    "understoodIntent": message.strip() or "Test request",
                    "possibleDomains": [],
                    "knownFacts": {},
                    "uncertainties": [],
                    "consistencyWarnings": [],
                    "nextQuestion": None,
                    "confidence": 0.99,
                }
            )
            return SimpleNamespace(
                result=result,
                usage=ModelUsage(
                    provider="test",
                    model="goal-understanding",
                    mode="llm",
                    taskType="goal_understanding",
                ),
                source="llm",
                error="",
            )

    monkeypatch.setattr(
        "app.services.command_agent.GoalUnderstandingService",
        lambda: DeterministicGoalUnderstandingService(),
    )
    with TestClient(app) as test_client:
        yield test_client
