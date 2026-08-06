from __future__ import annotations

import re

from ...services.cognitive_planning.contracts import (
    DecisionRelevantUnknown,
    GoalCompletionBlockingUnknown,
    GoalCompletionResult,
    GoalQuestion,
    UserGoalModel,
)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _normalized(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE).replace("_", "")


def _semantic_score(question: GoalQuestion, unknown: DecisionRelevantUnknown) -> int:
    """Score a structured question against an unknown without relying on list order."""

    expected = _normalized(question.expected_decision_impact)
    key = _normalized(unknown.key)
    impact = _normalized(unknown.impact)
    score = 0
    if expected and key:
        if expected == key:
            score += 100
        elif expected in key or key in expected:
            score += 60
    if expected and impact:
        if expected == impact:
            score += 90
        elif expected in impact or impact in expected:
            score += 50

    question_fields = (
        _normalized(question.question),
        _normalized(question.why_this_question_matters),
        expected,
    )
    unknown_fields = (
        key,
        _normalized(unknown.description),
        _normalized(unknown.why_it_changes_the_plan),
        impact,
    )
    for question_field in question_fields:
        for unknown_field in unknown_fields:
            if not question_field or not unknown_field:
                continue
            if question_field == unknown_field:
                score += 30
            elif min(len(question_field), len(unknown_field)) >= 3 and (
                question_field in unknown_field or unknown_field in question_field
            ):
                score += 10
    return score


def _matching_question(
    unknown: DecisionRelevantUnknown,
    questions: list[GoalQuestion],
    used: set[int],
) -> tuple[int, GoalQuestion] | None:
    scored = [
        (_semantic_score(question, unknown), index, question)
        for index, question in enumerate(questions)
        if index not in used
    ]
    if not scored:
        return None
    score, index, question = max(scored, key=lambda item: (item[0], -item[1]))
    return (index, question) if score > 0 else None


class GoalCompletionJudge:
    """Judge semantic sufficiency from the model-owned goal contract, never from a fixed slot list."""

    name = "Goal Completion Judge"
    artifact_type = "goal_completion"

    def evaluate(self, goal: UserGoalModel) -> GoalCompletionResult:
        blocking_items = [
            item for item in goal.decision_relevant_unknowns if item.priority == "blocking"
        ]
        questions = list(goal.questions)
        used_question_indexes: set[int] = set()
        blockers: list[GoalCompletionBlockingUnknown] = []
        for item in blocking_items:
            match = _matching_question(item, questions, used_question_indexes)
            answer_options: list[str] = []
            if match:
                question_index, matched_question = match
                used_question_indexes.add(question_index)
                question = matched_question.question
                answer_options = matched_question.answer_options
            else:
                question = item.description
            blockers.append(
                GoalCompletionBlockingUnknown(
                    question=question,
                    impact=item.why_it_changes_the_plan,
                    answerOptions=answer_options,
                )
            )

        # Questions explicitly request a user decision. If the model also
        # says it can proceed, fail closed on that contradiction without
        # introducing a fixed domain slot list.
        if goal.can_proceed_to_evidence and questions:
            for question_index, item in enumerate(questions):
                if question_index in used_question_indexes:
                    continue
                used_question_indexes.add(question_index)
                blockers.append(
                    GoalCompletionBlockingUnknown(
                        question=item.question,
                        impact=item.why_this_question_matters,
                        answerOptions=item.answer_options,
                    )
                )

        if goal.consistency_warnings and not blockers:
            for warning in goal.consistency_warnings:
                warning_normalized = _normalized(warning)
                answer_options = []
                matches = [
                    (index, question)
                    for index, question in enumerate(questions)
                    if index not in used_question_indexes
                    and warning_normalized
                    and (
                        warning_normalized in _normalized(question.question)
                        or warning_normalized in _normalized(question.why_this_question_matters)
                        or _normalized(question.question) in warning_normalized
                    )
                ]
                if matches:
                    question_index, matched_question = matches[0]
                    used_question_indexes.add(question_index)
                    question = matched_question.question
                    impact = matched_question.why_this_question_matters
                    answer_options = matched_question.answer_options
                else:
                    question = warning
                    impact = warning
                blockers.append(
                    GoalCompletionBlockingUnknown(
                        question=question,
                        impact=impact,
                        answerOptions=answer_options,
                    )
                )

        optional_unknowns = _dedupe(
            [
                item.description
                for item in goal.decision_relevant_unknowns
                if item.priority != "blocking"
            ]
            + [
                item.question
                for index, item in enumerate(questions)
                if index not in used_question_indexes
            ]
        )
        complete = not blockers
        return GoalCompletionResult(
            complete=complete,
            blockingUnknowns=blockers,
            optionalUnknowns=optional_unknowns,
            nextStage="strategy" if complete else "goal_clarification",
        )


__all__ = ["GoalCompletionJudge"]
