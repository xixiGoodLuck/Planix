from __future__ import annotations

import pytest

from app.cognitive_planning.agents import AgentResult, UnderstandingAgent
from app.cognitive_planning.contracts import ConversationTurn
from app.cognitive_planning.planning import (
    SemanticItem,
    SemanticMergeService,
    SemanticOperation,
    UnderstandingContextCompactor,
    UnderstandingPatch,
    UnderstandingQuestion,
    UnderstandingReadiness,
    UnderstandingReadinessService,
    UnderstandingSnapshot,
)


def semantic(key: str, statement: str, source_ref: str = "turn-1", blocking_category: str = "important") -> SemanticItem:
    return SemanticItem(
        id=f"item-{key}",
        key=key,
        statement=statement,
        sourceType="user_confirmed",
        sourceRef=source_ref,
        blockingCategory=blocking_category,
    )


def snapshot() -> UnderstandingSnapshot:
    return UnderstandingSnapshot(
        artifactId="understanding-1",
        goalSummary="学习 Python 并完成作品",
        facts=[semantic("availability", "每天一小时")],
        constraints=[semantic("deadline", "四周内完成")],
        preferences=[semantic("style", "项目驱动")],
        successSignals=[semantic("success:1", "完成可运行作品")],
        unknowns=[semantic("unknown:safety", "是否存在安全限制", blocking_category="safety")],
        nextQuestion=UnderstandingQuestion(
            question="作品主要用于什么？",
            whyThisQuestionMatters="用途会改变项目范围。",
            expectedDecisionImpact="决定任务和验收标准。",
            priority="blocking",
            targetUnknownKey="unknown:safety",
        ),
        readiness=UnderstandingReadiness(
            questionRoundsUsed=1,
            questionBudget=2,
            complexity="standard",
        ),
    )


def test_stable_key_replacement_preserves_unrelated_fields_and_archives_old_value():
    current = snapshot()
    replacement = semantic("availability", "工作日一小时，周末三小时", "turn-2")
    patch = UnderstandingPatch(
        baseArtifactId=current.artifact_id,
        baseVersion=current.version,
        userMessageRef="turn-2",
        operations=[
            SemanticOperation(
                operation="replace_item",
                section="facts",
                key="availability",
                item=replacement,
            )
        ],
    )

    updated, archived = SemanticMergeService().apply(current, patch)

    assert [item.statement for item in updated.facts] == ["工作日一小时，周末三小时"]
    assert updated.facts[0].supersedes == "item-availability"
    assert updated.constraints == current.constraints
    assert updated.preferences == current.preferences
    assert archived.operations[0].item == replacement


def test_current_snapshot_context_does_not_contain_superseded_value():
    current = snapshot()
    replacement = semantic("availability", "工作日一小时，周末三小时", "turn-2")
    updated, _ = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn-2",
            operations=[
                SemanticOperation(
                    operation="replace_item",
                    section="facts",
                    key="availability",
                    item=replacement,
                )
            ],
        ),
    )

    context = UnderstandingContextCompactor().compact(
        updated,
        latest_user_message="按新时间规划",
        recent_messages=["无关旧聊天", "按新时间规划"],
    )
    serialized = context.model_dump_json()
    assert "工作日一小时，周末三小时" in serialized
    assert "每天一小时" not in serialized


def test_dynamic_question_is_snapshot_data_not_a_fixed_questionnaire():
    current = snapshot()
    assert current.next_question is not None
    assert current.next_question.question == "作品主要用于什么？"
    assert current.next_question.expected_decision_impact == "决定任务和验收标准。"
    assert len(current.next_question.answer_options) == 0


def test_question_budget_turns_noncritical_unknown_into_explicit_assumption():
    current = snapshot().model_copy(
        update={
            "unknowns": [semantic("route", "选择 Web 还是数据方向")],
            "readiness": UnderstandingReadiness(
                questionRoundsUsed=2,
                questionBudget=2,
                complexity="standard",
            ),
        }
    )
    assessed = UnderstandingReadinessService().assess(current, blocking_unknown_keys={"route"})
    assert assessed.readiness.ready_for_confirmation is True
    assert not assessed.unknowns
    assert assessed.assumptions[0].key == "route"
    assert assessed.assumptions[0].source_type == "model_assumption"


def test_safety_unknown_still_blocks_after_question_budget():
    current = snapshot().model_copy(
        update={
            "readiness": UnderstandingReadiness(
                questionRoundsUsed=2,
                questionBudget=2,
                complexity="standard",
            ),
        }
    )
    assessed = UnderstandingReadinessService().assess(
        current,
        blocking_unknown_keys={"unknown:safety"},
    )
    assert assessed.readiness.ready_for_confirmation is False
    assert assessed.readiness.blocking_reasons
    assert assessed.unknowns


class PatchModel:
    def __init__(self, patch: UnderstandingPatch):
        self.patch = patch
        self.payload = None

    def complete_contract(self, **kwargs):
        self.payload = kwargs["payload"]
        return AgentResult(self.patch, {"taskType": "planning_understanding"})


def test_understanding_agent_uses_version_bound_patch_and_preserves_unrelated_facts():
    current = snapshot()
    model = PatchModel(
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[
                SemanticOperation(
                    operation="replace_item",
                    section="facts",
                    key="availability",
                    item=semantic("availability", "Weekends only", "turn:2"),
                )
            ],
            readyForConfirmation=True,
        )
    )
    history = [ConversationTurn(role="user", content=f"turn {index}") for index in range(6)]
    result = UnderstandingAgent(model).run(history, previous=current).artifact

    assert result.facts[0].statement == "Weekends only"
    assert result.constraints == current.constraints
    assert len(model.payload["understandingContext"]["recentMessages"]) == 4


def test_understanding_agent_rejects_stale_model_patch():
    current = snapshot()
    stale = UnderstandingPatch(
        baseArtifactId=current.artifact_id,
        baseVersion=current.version + 1,
        userMessageRef="turn:2",
    )
    with pytest.raises(ValueError, match="stale"):
        UnderstandingAgent(PatchModel(stale)).run(
            [ConversationTurn(role="user", content="update")],
            previous=current,
        )


def test_missing_replacement_target_is_normalized_to_a_current_user_fact():
    current = snapshot().model_copy(update={"unknowns": []})
    item = semantic("experience_level", "The user is a beginner", "turn:2")
    updated, archived = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[SemanticOperation(operation="replace_item", section="facts", key=item.key, item=item)],
        ),
    )

    assert updated.facts[-1].statement == "The user is a beginner"
    assert archived.operations[0].operation == "add_item"


def test_answered_unknown_moves_out_of_unknowns_without_self_supersedes():
    current = snapshot().model_copy(
        update={"unknowns": [semantic("learning_style", "Which learning style?", "turn:1")]}
    )
    answer = semantic("learning_style", "The user prefers video lessons", "turn:2")
    updated, _ = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[SemanticOperation(operation="replace_item", section="unknowns", key=answer.key, item=answer)],
        ),
    )

    assert not updated.unknowns
    preference = next(item for item in updated.preferences if item.key == "learning_style")
    assert preference.statement == "The user prefers video lessons"
    assert preference.source_ref == "turn:2"


def test_current_turn_unknown_statement_is_not_promoted_to_fact():
    current = snapshot().model_copy(update={"unknowns": []})
    unresolved = semantic("experience_level", "用户当前的编程经验水平未知。", "turn:2")
    updated, _ = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[SemanticOperation(operation="add_item", section="unknowns", key=unresolved.key, item=unresolved)],
        ),
    )

    assert [item.key for item in updated.unknowns] == ["experience_level"]
    assert all(item.key != "experience_level" for item in updated.facts)


def test_answer_to_current_question_clears_target_unknown_with_different_fact_key():
    current = snapshot().model_copy(
        update={
            "unknowns": [semantic("current_level", "Current programming level is unknown", "turn:1")],
            "next_question": UnderstandingQuestion(
                question="What is your programming level?",
                whyThisQuestionMatters="Needed to set the starting point",
                expectedDecisionImpact="Sets the learning baseline",
                priority="important",
                targetUnknownKey="current_level",
            ),
        }
    )
    unresolved = semantic("current_level", "What is the user's current programming level?", "turn:2")
    answer = semantic("user_zero_basis", "The user is a complete beginner", "turn:2")
    updated, _ = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[
                SemanticOperation(operation="replace_item", section="unknowns", key=unresolved.key, item=unresolved),
                SemanticOperation(operation="add_item", section="facts", key=answer.key, item=answer),
            ],
        ),
    )

    assert all(item.key != "current_level" for item in updated.unknowns)
    assert any(item.key == "user_zero_basis" for item in updated.facts)


def test_success_question_promotes_differently_keyed_user_fact_to_success_signal():
    current = snapshot().model_copy(
        update={
            "success_signals": [],
            "unknowns": [semantic("success_criteria", "Need to determine a verifiable outcome", "turn:1")],
            "next_question": UnderstandingQuestion(
                question="What verifiable result do you want and by when?",
                whyThisQuestionMatters="Defines completion",
                expectedDecisionImpact="Sets deliverable and horizon",
                priority="important",
                targetUnknownKey="success_criteria",
            ),
        }
    )
    answer = semantic("user_success_criteria", "Build a working Todo API in four weeks", "turn:2")
    updated, _ = SemanticMergeService().apply(
        current,
        UnderstandingPatch(
            baseArtifactId=current.artifact_id,
            baseVersion=current.version,
            userMessageRef="turn:2",
            operations=[SemanticOperation(operation="add_item", section="facts", key=answer.key, item=answer)],
        ),
    )

    assert not updated.unknowns
    assert [item.key for item in updated.success_signals] == ["user_success_criteria"]
    assert all(item.key != "user_success_criteria" for item in updated.facts)


def test_missing_success_outcome_becomes_an_explicit_default_without_blocking_review():
    current = snapshot().model_copy(
        update={
            "success_signals": [],
            "unknowns": [],
            "next_question": None,
            "readiness": UnderstandingReadiness(questionRoundsUsed=2, questionBudget=2, complexity="standard"),
        }
    )

    assessed = UnderstandingReadinessService().assess(current)

    assert assessed.readiness.ready_for_confirmation is True
    assert assessed.success_signals[0].source_type == "model_assumption"
    assert assessed.success_signals[0].source_ref == "readiness:bounded_default"


def test_success_outcome_answer_converges_understanding_after_bounded_question():
    current = snapshot().model_copy(
        update={
            "success_signals": [],
            "unknowns": [],
            "unknowns": [semantic("success_criteria", "Need a verifiable outcome", "turn:1")],
            "next_question": UnderstandingQuestion(
                question="What verifiable result do you want?",
                whyThisQuestionMatters="Defines completion",
                expectedDecisionImpact="Sets the deliverable",
                priority="important",
                targetUnknownKey="success_criteria",
            ),
            "readiness": UnderstandingReadiness(questionRoundsUsed=2, questionBudget=2, complexity="standard"),
        }
    )
    prompted = current
    answer = semantic("success_criteria", "Build a working Todo API within four weeks", "turn:4")
    answered, _ = SemanticMergeService().apply(
        prompted,
        UnderstandingPatch(
            baseArtifactId=prompted.artifact_id,
            baseVersion=prompted.version,
            userMessageRef="turn:4",
            operations=[SemanticOperation(operation="replace_item", section="unknowns", key=answer.key, item=answer)],
        ),
    )
    answered = answered.model_copy(
        update={"readiness": answered.readiness.model_copy(update={"question_rounds_used": 3})}
    )

    assessed = UnderstandingReadinessService().assess(answered)

    assert assessed.readiness.ready_for_confirmation is True
    assert assessed.success_signals[0].source_ref == "turn:4"
    assert assessed.next_question is not None
