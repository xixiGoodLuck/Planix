import json
from types import SimpleNamespace

import pytest

from app.schemas import GoalUnderstandingResult
from app.services.goal_understanding import GoalUnderstandingService, extract_obvious_goal_facts


class UnavailableGoalModel:
    settings = SimpleNamespace(provider="test", model="unavailable")

    def complete(self, *args, **kwargs):
        return None, SimpleNamespace(
            message="goal understanding model unavailable",
            attempts=[
                {
                    "provider": "test",
                    "model": "unavailable",
                    "status": "error",
                    "errorType": "timeout",
                }
            ],
            fallback_used=False,
            local_fallback_allowed=True,
        )


class StaticGoalModel:
    settings = SimpleNamespace(provider="test", model="goal-understanding")

    def __init__(self, payload):
        self.payload = payload
        self.last_user = ""

    def complete(self, _feature, _system, user, **_kwargs):
        self.last_user = user
        return SimpleNamespace(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="goal-understanding",
            usage={},
            latency_ms=1,
            attempts=[],
            fallback_used=False,
            local_fallback_allowed=False,
        ), None


def test_goal_understanding_result_parses_public_aliases():
    result = GoalUnderstandingResult.model_validate(
        {
            "intentState": "ambiguous_goal",
            "understoodIntent": "用户想前往北京，但目的尚未说明。",
            "possibleDomains": ["travel", "career", "relocation", "other"],
            "knownFacts": {"location": "北京"},
            "uncertainties": [
                {
                    "field": "purpose",
                    "impact": "目的会改变规划策略。",
                }
            ],
            "consistencyWarnings": [],
            "nextQuestion": "你去北京主要想实现什么目标？",
            "clarificationOptions": ["工作", "学习", "旅行", "探亲或其他安排"],
            "confidence": 0.55,
        }
    )

    assert result.intent_state == "ambiguous_goal"
    assert result.known_facts == {"location": "北京"}
    assert result.uncertainties[0].field == "purpose"
    assert result.model_dump(by_alias=True)["nextQuestion"] == "你去北京主要想实现什么目标？"
    assert result.clarification_options == ["工作", "学习", "旅行", "探亲或其他安排"]


def test_direct_learning_goal_is_preserved_as_clear_goal_for_goal_intelligence():
    client = StaticGoalModel(
        {
            "intentState": "clear_goal",
            "understoodIntent": "用户想学习 Python。",
            "possibleDomains": ["programming"],
            "knownFacts": {"skill": "Python"},
            "uncertainties": [],
            "consistencyWarnings": [],
            "nextQuestion": None,
            "clarificationOptions": ["找工作", "做项目"],
            "confidence": 0.92,
        }
    )

    outcome = GoalUnderstandingService(client=client).understand("我要学 Python")

    assert outcome.result is not None
    assert outcome.result.intent_state == "clear_goal"
    assert outcome.result.understood_intent == "用户想学习 Python。"
    assert outcome.result.known_facts == {"skill": "Python"}
    assert outcome.result.clarification_options == []


@pytest.mark.parametrize(
    ("message", "location"),
    [
        ("我要去北京", "北京"),
        ("我要去乌鲁木齐", "乌鲁木齐"),
    ],
)
def test_location_extraction_stays_literal_when_goal_model_is_unavailable(message, location):
    facts = extract_obvious_goal_facts(message)
    outcome = GoalUnderstandingService(client=UnavailableGoalModel()).understand(message)

    assert facts == {"location": location}
    assert "domain" not in facts
    assert outcome.source == "model_unavailable"
    assert outcome.result is None
    assert outcome.usage is not None
    assert outcome.usage.fallback_used is False
    assert len(outcome.usage.attempts) == 1


def test_consistency_warning_quarantines_the_rejected_known_fact():
    client = StaticGoalModel(
        {
            "intentState": "clear_goal",
            "understoodIntent": "用户想从零开始学习滑雪，但最终用途需要确认。",
            "possibleDomains": ["sports_skill", "content_creation"],
            "knownFacts": {"skill": "滑雪", "purpose": "做项目"},
            "uncertainties": [{"field": "purpose", "impact": "不同用途会改变训练路线。"}],
            "consistencyWarnings": ["做项目与滑雪技能目标并不直接一致。"],
            "nextQuestion": "你想提升滑雪技能、记录学习过程，还是参加比赛挑战？",
            "clarificationOptions": ["提升滑雪技能", "记录学习过程", "参加比赛挑战", "提升滑雪技能"],
            "confidence": 0.9,
        }
    )

    outcome = GoalUnderstandingService(client=client).understand("我要学滑雪，零基础 2小时 做项目")

    assert outcome.result is not None
    assert outcome.result.intent_state == "ambiguous_goal"
    assert outcome.result.known_facts == {"skill": "滑雪"}
    assert outcome.result.consistency_warnings
    assert outcome.result.clarification_options == ["提升滑雪技能", "记录学习过程", "参加比赛挑战"]


def test_consistency_warning_rejects_an_understood_intent_that_accepts_the_bad_purpose():
    client = StaticGoalModel(
        {
            "intentState": "clear_goal",
            "understoodIntent": "用户想学习滑雪并通过做项目完成目标。",
            "possibleDomains": ["sports_skill"],
            "knownFacts": {"skill": "滑雪", "purpose": "做项目"},
            "uncertainties": [{"field": "purpose", "impact": "目的会改变训练路线。"}],
            "consistencyWarnings": ["做项目与滑雪技能目标并不直接一致。"],
            "nextQuestion": "你想提升滑雪技能、记录学习过程，还是参加比赛挑战？",
            "confidence": 0.9,
        }
    )

    outcome = GoalUnderstandingService(client=client).understand("我要学滑雪，零基础 2小时 做项目")

    assert outcome.result is None
    assert outcome.source == "invalid_model_output"
    assert "consistency-rejected fact" in outcome.error


def test_prior_goal_understanding_is_passed_as_structured_followup_context():
    client = StaticGoalModel(
        {
            "intentState": "normal_chat",
            "understoodIntent": "用户正在回答上一轮问题。",
            "possibleDomains": [],
            "knownFacts": {},
            "uncertainties": [],
            "consistencyWarnings": [],
            "nextQuestion": None,
            "confidence": 0.9,
        }
    )
    prior = {
        "intentState": "ambiguous_goal",
        "possibleDomains": ["travel", "career", "relocation", "other"],
        "knownFacts": {"location": "北京"},
        "nextQuestion": "你去北京主要是旅游、工作、学习、长期居住，还是其他目的？",
        "clarificationOptions": ["旅游", "工作", "学习", "长期居住"],
    }

    GoalUnderstandingService(client=client).understand(
        "第二个",
        thread_context="用户: 我要去北京",
        prior_understanding=prior,
    )

    request = json.loads(client.last_user)
    assert request["message"] == "第二个"
    assert request["priorGoalUnderstanding"] == prior
