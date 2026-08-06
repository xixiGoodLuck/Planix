from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.cognitive_planning.agents import GoalIntelligenceAgent
from app.services.cognitive_planning.agents import (
    CognitiveModelClient,
    GoalModelingAgent,
    PlanningModelUnavailable,
)
from app.services.cognitive_planning.contracts import (
    ConversationTurn,
    GoalModelingInput,
    UserGoalModel,
)
from app.services.llm import LlmResult


def _sparse_ready_goal() -> dict[str, Any]:
    return {
        "goalStatement": "Travel to Japan this autumn",
        "desiredChange": "Complete a viable trip to Japan this autumn",
        "domain": "travel",
        "possibleIntents": ["culture", "food"],
        "currentKnowledge": [],
        "uncertainties": ["Destination scope", "Trip duration", "Available budget"],
        "consistencyWarnings": [],
        "userLanguage": ["I want to travel to Japan this autumn"],
        "hardConstraints": [],
        "softPreferences": [],
        "knownFacts": [
            {
                "key": "destinationCountry",
                "statement": "Japan",
                "sourceText": "Japan",
                "confidence": 1,
            },
            {
                "key": "season",
                "statement": "This autumn",
                "sourceText": "this autumn",
                "confidence": 1,
            },
        ],
        "decisionRelevantUnknowns": [],
        "assumptions": [],
        "successModel": {
            "definition": "Complete a viable Japan trip",
            "measurableSignals": [],
            "intermediateMilestones": [],
        },
        "feasibilityJudgment": {
            "summary": "Trip shape is needed before feasibility can be assessed.",
            "risks": [],
            "unrealisticParts": [],
        },
        "questions": [],
        "confidence": 0.72,
        "canProceedToEvidence": True,
    }


def _repaired_blocked_goal() -> dict[str, Any]:
    repaired = copy.deepcopy(_sparse_ready_goal())
    repaired["decisionRelevantUnknowns"] = [
        {
            "key": "tripShape",
            "description": "Which destination scope and duration the trip should use",
            "whyItChangesThePlan": "The answer changes route, lodging, transport, and budget feasibility.",
            "impact": "strategy",
            "priority": "blocking",
        }
    ]
    repaired["questions"] = [
        {
            "question": "What destination scope and trip duration should the route use?",
            "whyThisQuestionMatters": "These choices determine the route and whether the budget is feasible.",
            "expectedDecisionImpact": "Selects the route, lodging pattern, and transport strategy.",
            "answerOptions": [],
        }
    ]
    repaired["canProceedToEvidence"] = False
    return repaired


def _result(payload: dict[str, Any]) -> LlmResult:
    return LlmResult(
        content=json.dumps(payload),
        provider="deepseek",
        model="stub",
        attempts=[{"provider": "deepseek", "model": "stub", "status": "success"}],
    )


class _SequencedLlm:
    def __init__(self, payloads: list[dict[str, Any]]):
        self.results = [_result(payload) for payload in payloads]
        self.calls: list[dict[str, Any]] = []

    def complete(self, feature: str, system: str, user: str, **kwargs: Any):
        self.calls.append(
            {
                "feature": feature,
                "system": system,
                "user": user,
                **kwargs,
            }
        )
        return self.results.pop(0), None


def _input() -> GoalModelingInput:
    return GoalModelingInput(
        conversationHistory=[
            ConversationTurn(role="user", content="I want to travel to Japan this autumn")
        ]
    )


def _exact_sparse_travel_input() -> GoalModelingInput:
    message = "我想今年秋天去日本旅游"
    return GoalModelingInput(
        conversationHistory=[ConversationTurn(role="user", content=message)],
        preExtractedFacts={
            "rawUserStatement": message,
            "goalUnderstanding": {
                "intentState": "clear_goal",
                "understoodIntent": "用户想今年秋天去日本旅游",
                "possibleDomains": ["旅游规划"],
                "knownFacts": {
                    "目的地": "日本",
                    "季节": "秋天",
                    "目的": "旅游",
                },
                "uncertainties": [],
                "consistencyWarnings": [],
                "nextQuestion": None,
                "clarificationOptions": [],
                "confidence": 0.95,
            },
        },
    )


def _hard_constraint_fragment_bypass_goal() -> dict[str, Any]:
    message = "我想今年秋天去日本旅游"
    bypass = copy.deepcopy(_sparse_ready_goal())
    bypass.update(
        {
            "currentKnowledge": ["用户想去日本", "时间是今年秋天", "目的是旅游"],
            "uncertainties": [],
            "hardConstraints": [
                {
                    "statement": "今年秋天",
                    "sourceText": "今年秋天",
                    "category": "schedule",
                }
            ],
            "knownFacts": [
                {"key": "目的地", "statement": "日本", "sourceText": message, "confidence": 1},
                {"key": "季节", "statement": "秋天", "sourceText": message, "confidence": 1},
                {"key": "目的", "statement": "旅游", "sourceText": message, "confidence": 1},
            ],
            "decisionRelevantUnknowns": [],
            "questions": [],
            "canProceedToEvidence": True,
        }
    )
    return bypass


def test_sparse_goal_contract_rejects_unexplained_evidence_readiness() -> None:
    with pytest.raises(ValidationError, match="sparse goal without enough decision-shaping evidence"):
        UserGoalModel.model_validate(_sparse_ready_goal())


def test_exact_sparse_travel_input_rejects_split_facts_and_one_fake_constraint() -> None:
    payload = _exact_sparse_travel_input()

    with pytest.raises(ValidationError, match="initial single-turn input"):
        UserGoalModel.model_validate(
            _hard_constraint_fragment_bypass_goal(),
            context={"goalModelingInput": payload.model_dump(by_alias=True)},
        )


def test_detailed_single_turn_is_not_blocked_by_input_shape_guard() -> None:
    message = (
        "我们两人从上海出发，计划今年秋天去日本关西自由行7天，总预算2万元。"
        "这是第一次自由行，偏好文化与美食。"
    )
    payload = GoalModelingInput(
        conversationHistory=[ConversationTurn(role="user", content=message)],
        preExtractedFacts={
            "rawUserStatement": message,
            "goalUnderstanding": {
                "intentState": "clear_goal",
                "understoodIntent": "用户要完成关西七天文化美食自由行",
                "knownFacts": {"departure": "上海", "duration": "7天", "budget": "2万元"},
                "uncertainties": [],
                "consistencyWarnings": [],
                "confidence": 0.95,
            },
        },
    )

    goal = UserGoalModel.model_validate(
        _hard_constraint_fragment_bypass_goal(),
        context={"goalModelingInput": payload.model_dump(by_alias=True)},
    )

    assert goal.can_proceed_to_evidence is True


def test_one_calendar_expression_is_not_mistaken_for_independent_commitments() -> None:
    payload = _exact_sparse_travel_input().model_copy(
        update={
            "conversation_history": [
                ConversationTurn(role="user", content="我想2026年10月去日本旅游")
            ]
        }
    )

    with pytest.raises(ValidationError, match="initial single-turn input"):
        UserGoalModel.model_validate(
            _hard_constraint_fragment_bypass_goal(),
            context={"goalModelingInput": payload.model_dump(by_alias=True)},
        )


@pytest.mark.parametrize("agent_type", [GoalIntelligenceAgent, GoalModelingAgent])
def test_exact_sparse_travel_bypass_uses_contract_repair_and_blocks(agent_type) -> None:
    llm = _SequencedLlm([_hard_constraint_fragment_bypass_goal(), _repaired_blocked_goal()])
    agent = agent_type(model=CognitiveModelClient(llm=llm))

    result = agent.run(_exact_sparse_travel_input())

    assert len(llm.calls) == 2
    repair_request = json.loads(llm.calls[1]["user"])
    assert repair_request["input"]["preExtractedFacts"]["goalUnderstanding"]["uncertainties"] == []
    assert any(
        "initial single-turn input" in error["message"]
        for error in repair_request["validationErrors"]
    )
    assert result.artifact.can_proceed_to_evidence is False
    assert result.artifact.questions
    assert any(
        item.priority == "blocking"
        for item in result.artifact.decision_relevant_unknowns
    )


def test_exact_sparse_repair_must_pair_question_with_blocking_unknown() -> None:
    incomplete_repair = _repaired_blocked_goal()
    incomplete_repair["decisionRelevantUnknowns"][0]["priority"] = "important"
    llm = _SequencedLlm([_hard_constraint_fragment_bypass_goal(), incomplete_repair])
    agent = GoalIntelligenceAgent(model=CognitiveModelClient(llm=llm))

    with pytest.raises(PlanningModelUnavailable) as exc_info:
        agent.run(_exact_sparse_travel_input())

    assert len(llm.calls) == 2
    assert exc_info.value.error.error_type == "invalid_model_output"
    assert exc_info.value.error.attempts[-1]["automaticRetry"] is True


def test_sparse_goal_guard_does_not_count_unsourced_fact_fragments_as_planning_shape() -> None:
    sparse = _sparse_ready_goal()
    sparse["knownFacts"] = [
        {"key": "destination", "statement": "Japan", "sourceText": "", "confidence": 1},
        {"key": "season", "statement": "autumn", "sourceText": "", "confidence": 1},
        {"key": "year", "statement": "this year", "sourceText": "", "confidence": 1},
        {"key": "purpose", "statement": "travel", "sourceText": "", "confidence": 1},
    ]
    sparse["currentKnowledge"] = ["The user wants to travel to Japan this autumn"]
    sparse["uncertainties"] = []

    with pytest.raises(ValidationError, match="sparse goal without enough decision-shaping evidence"):
        UserGoalModel.model_validate(sparse)


def test_sparse_goal_guard_rejects_many_fragments_citing_only_the_same_short_turn() -> None:
    sparse = _sparse_ready_goal()
    source = "I want to travel to Japan this autumn"
    sparse["knownFacts"] = [
        {"key": "destination", "statement": "Japan", "sourceText": source, "confidence": 1},
        {"key": "season", "statement": "autumn", "sourceText": source, "confidence": 1},
        {"key": "year", "statement": "this year", "sourceText": source, "confidence": 1},
        {"key": "purpose", "statement": "travel", "sourceText": source, "confidence": 1},
    ]
    sparse["currentKnowledge"] = [
        "The destination is Japan",
        "The season is autumn",
        "The purpose is travel",
    ]
    sparse["uncertainties"] = []
    sparse["assumptions"] = [
        {
            "statement": "This is a leisure trip",
            "confidence": 0.7,
            "needsUserConfirmation": False,
        }
    ]

    with pytest.raises(ValidationError, match="sparse goal without enough decision-shaping evidence"):
        UserGoalModel.model_validate(sparse)


def test_sparse_swimming_goal_cannot_use_nonblocking_unknowns_to_bypass_clarification() -> None:
    sparse = _sparse_ready_goal()
    sparse.update(
        {
            "goalStatement": "Learn swimming",
            "desiredChange": "Learn how to swim",
            "domain": "fitness",
            "possibleIntents": ["Learn basic swimming skills"],
            "currentKnowledge": ["The user wants to learn swimming"],
            "uncertainties": [
                "Current swimming ability",
                "Target swimming outcome",
                "Available training time",
                "Access to a safe pool",
                "Access to qualified instruction",
            ],
            "knownFacts": [
                {
                    "key": "activity",
                    "statement": "The user wants to learn swimming",
                    "sourceText": "我想学游泳",
                    "confidence": 1,
                }
            ],
            "decisionRelevantUnknowns": [
                {
                    "key": "currentAbility",
                    "description": "The user's current swimming ability",
                    "whyItChangesThePlan": "It changes the safe starting point and progression.",
                    "impact": "safety",
                    "priority": "important",
                },
                {
                    "key": "targetOutcome",
                    "description": "The swimming outcome the user wants",
                    "whyItChangesThePlan": "It changes milestones and training emphasis.",
                    "impact": "success_criteria",
                    "priority": "important",
                },
                {
                    "key": "schedule",
                    "description": "The time available for training",
                    "whyItChangesThePlan": "It changes training frequency and duration.",
                    "impact": "schedule",
                    "priority": "optional",
                },
            ],
            "questions": [],
            "canProceedToEvidence": True,
        }
    )

    with pytest.raises(ValidationError, match="sparse goal without enough decision-shaping evidence"):
        UserGoalModel.model_validate(sparse)


@pytest.mark.parametrize("agent_type", [GoalIntelligenceAgent, GoalModelingAgent])
def test_sparse_goal_uses_real_contract_repair_for_both_goal_agents(agent_type) -> None:
    llm = _SequencedLlm([_sparse_ready_goal(), _repaired_blocked_goal()])
    agent = agent_type(model=CognitiveModelClient(llm=llm))

    result = agent.run(_input())

    assert len(llm.calls) == 2
    assert "sparse goal evidence-ready" in llm.calls[0]["system"]
    repair_request = json.loads(llm.calls[1]["user"])
    assert repair_request["invalidOutput"]["uncertainties"] == _sparse_ready_goal()["uncertainties"]
    assert any(
        "sparse goal without enough decision-shaping evidence" in error["message"]
        for error in repair_request["validationErrors"]
    )
    assert result.artifact.can_proceed_to_evidence is False
    assert result.artifact.questions[0].question
    assert result.artifact.decision_relevant_unknowns[0].priority == "blocking"
    assert result.model_usage["localFallbackAllowed"] is False
    assert result.model_usage["attempts"][-1]["automaticRetry"] is True
    assert result.model_usage["attempts"][-1]["retryReason"] == "contract_validation"


def test_sparse_goal_contract_repair_fails_safe_when_model_repeats_invalid_claim() -> None:
    llm = _SequencedLlm([_sparse_ready_goal(), _sparse_ready_goal()])
    client = CognitiveModelClient(llm=llm)

    with pytest.raises(PlanningModelUnavailable) as exc_info:
        client.complete_contract(
            stage="goal_intelligence",
            task_type="planning_goal_model",
            feature="test_sparse_goal_safe_failure",
            system="Return the goal as JSON.",
            payload=_input().model_dump(by_alias=True),
            contract_type=UserGoalModel,
        )

    assert len(llm.calls) == 2
    assert exc_info.value.error.error_type == "invalid_model_output"
    assert exc_info.value.error.attempts[-1]["automaticRetry"] is True
    assert exc_info.value.error.attempts[-1]["retryReason"] == "contract_validation"


def test_complete_goal_with_optional_refinements_is_not_blocked_by_sparse_guard() -> None:
    complete = _sparse_ready_goal()
    complete["knownFacts"].extend(
        [
            {
                "key": "departureCity",
                "statement": "Depart from Shanghai",
                "sourceText": "Shanghai departure",
                "confidence": 1,
            },
            {
                "key": "tripDuration",
                "statement": "Seven days",
                "sourceText": "7 days",
                "confidence": 1,
            },
        ]
    )
    complete["uncertainties"] = ["The exact museum list can be refined after strategy selection"]

    goal = UserGoalModel.model_validate(complete)

    assert goal.can_proceed_to_evidence is True
    assert goal.questions == []
