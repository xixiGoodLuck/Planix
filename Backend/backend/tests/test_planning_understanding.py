from __future__ import annotations

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


def semantic(key: str, statement: str, source_ref: str = "turn-1") -> SemanticItem:
    return SemanticItem(
        id=f"item-{key}",
        key=key,
        statement=statement,
        sourceType="user_confirmed",
        sourceRef=source_ref,
    )


def snapshot() -> UnderstandingSnapshot:
    return UnderstandingSnapshot(
        artifactId="understanding-1",
        goalSummary="学习 Python 并完成作品",
        facts=[semantic("availability", "每天一小时")],
        constraints=[semantic("deadline", "四周内完成")],
        preferences=[semantic("style", "项目驱动")],
        successSignals=[semantic("success:1", "完成可运行作品")],
        unknowns=[semantic("unknown:safety", "是否存在安全限制")],
        nextQuestion=UnderstandingQuestion(
            question="作品主要用于什么？",
            whyThisQuestionMatters="用途会改变项目范围。",
            expectedDecisionImpact="决定任务和验收标准。",
            priority="blocking",
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
