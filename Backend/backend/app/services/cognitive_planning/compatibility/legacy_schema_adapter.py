from __future__ import annotations

from typing import Any
from uuid import uuid4

from ....schemas import (
    ExecutionPlanDraft,
    ExecutionPlanQualityChecks,
    ExecutionPlanQualityReport,
    ExecutionTask,
    ExecutionTaskResourceCoverage,
    LearningImmediatePatch,
    LearningPatch,
    LearningReflection,
    LongTermLearning,
    MemoryHit,
    MemoryInsightBrief,
    MemoryInsightHits,
    PendingPlanningQuestion,
    PlanDesignPhase,
    PlanDesignProposal,
    PlanningInsights,
    ResourceBrief,
    ResourceCandidate,
    ResourceCoverage,
    ResourceFitScore,
    TaskLearningResource,
    TaskResourceBundle,
    UserNeedContract,
)
from ..contracts import (
    EvidencePack,
    ExecutionBlueprint,
    ExecutionResource,
    PlanCritiqueReport,
    PlanningLearningUpdate,
    StrategyPortfolio,
    UserGoalModel,
)


def goal_to_contract(goal: UserGoalModel, raw_input: str) -> UserNeedContract:
    questions = [item.question for item in goal.questions[:3]]
    missing = [item.key for item in goal.decision_relevant_unknowns if item.priority in {"blocking", "important"}]
    pending = None
    if questions:
        pending = PendingPlanningQuestion(
            askedFields=missing[:3],
            expectedAnswerType="decision_relevant_context",
            questionText=questions[0],
            questions=questions,
        )
    return UserNeedContract(
        rawUserInput=raw_input,
        interpretedGoal=goal.goal_statement,
        desiredOutcome=goal.desired_change,
        hardConstraints=[item.statement for item in goal.hard_constraints],
        softPreferences=[item.statement for item in goal.soft_preferences],
        missingInformation=missing,
        userWordsThatMustBeRespected=goal.user_language,
        canMoveToDesign=goal.can_proceed_to_evidence,
        clarificationQuestions=questions,
        pendingQuestion=pending,
    )


def _memory_hit(item) -> MemoryHit:
    kind = item.kind if item.kind in {"note", "material", "planning_history", "preference", "review"} else "note"
    return MemoryHit(
        id=item.source_id or "",
        kind=kind,
        title=item.statement[:100],
        summary=item.statement,
        relevance=item.why_relevant,
    )


def evidence_to_memory(evidence: EvidencePack) -> MemoryInsightBrief:
    grouped: dict[str, list[MemoryHit]] = {key: [] for key in ("preference", "review", "planning_history", "material", "note")}
    for item in evidence.user_evidence:
        hit = _memory_hit(item)
        grouped[hit.kind].append(hit)
    return MemoryInsightBrief(
        memoryHits=MemoryInsightHits(
            preferences=grouped["preference"],
            reviews=grouped["review"],
            planningHistory=grouped["planning_history"],
            materials=grouped["material"],
            notes=grouped["note"],
        ),
        planningInsights=PlanningInsights(
            userStyleRules=[item.rule for item in evidence.planning_rules if item.strength == "soft"],
            pastFailureWarnings=[item.rule for item in evidence.planning_rules if item.strength == "hard"],
            positivePatterns=[],
            constraintsToRespect=[item.rule for item in evidence.planning_rules],
        ),
        calendarConstraints=[*evidence.calendar_reality.conflicts, *evidence.calendar_reality.load_warnings],
        confidence=evidence.confidence,
        missingMemoryWarning=None if evidence.user_evidence else "No relevant long-term user memory was found; explicit assumptions remain visible.",
    )


def _source_type(value: str) -> str:
    aliases = {
        "doc": "official_doc",
        "documentation": "official_doc",
        "catalog": "built_in_catalog",
        "practice": "practice_bank",
        "human": "coach_or_human",
    }
    normalized = aliases.get(value, value)
    allowed = {
        "user_material", "memory_note", "official_doc", "tutorial", "coach_or_human",
        "built_in_catalog", "project_template", "practice_bank", "practice_drill",
        "safety_checklist", "route_info", "tool", "example", "web_search", "github",
        "video", "book", "ai_generated", "search_keyword",
    }
    return normalized if normalized in allowed else "search_keyword"


def evidence_to_resources(evidence: EvidencePack, domain: str) -> ResourceBrief:
    candidates: list[ResourceCandidate] = []
    for index, item in enumerate(evidence.resource_candidates):
        credibility = max(0, min(100, round(item.credibility * 100)))
        candidates.append(
            ResourceCandidate(
                id=f"evidence-{index}",
                title=item.title,
                sourceType=_source_type(item.type),
                url=item.source_ref if item.source_ref and item.source_ref.startswith("http") else None,
                searchKeyword=None if item.source_ref and item.source_ref.startswith("http") else item.source_ref or item.title,
                domain=domain,
                topics=[],
                difficulty="beginner",
                language="mixed",
                estimatedMinutes=30,
                howToUse=item.how_it_helps,
                expectedOutput=item.user_fit,
                fallbackIfTooHard="Use a smaller example or ask for a more accessible source.",
                fitScore=ResourceFitScore(
                    total=credibility,
                    credibility=credibility,
                    actionability=75,
                    reasons=[item.user_fit, item.how_it_helps],
                    risks=item.limitations,
                ),
            )
        )
    unresolved = [gap.description for gap in evidence.gaps]
    status = "strong" if evidence.confidence >= 0.8 and not unresolved else "partial" if candidates else "missing"
    fallback = "optional_web_search" if any(gap.proposed_resolution == "web_research" for gap in evidence.gaps) else "ask_user"
    return ResourceBrief(
        resourceCandidates=candidates,
        coverage=ResourceCoverage(
            status=status,
            missingTopics=unresolved,
            explanation=evidence.synthesis,
            fallbackStrategy=fallback,
        ),
        resourceRulesForThisPlan=[item.rule for item in evidence.planning_rules],
    )


def strategy_to_design(strategy: StrategyPortfolio, goal: UserGoalModel, *, approved: bool = False) -> PlanDesignProposal:
    selected = next((item for item in strategy.strategies if item.id == strategy.recommended_strategy_id), strategy.strategies[0])
    return PlanDesignProposal(
        designId=selected.id,
        strategyName=selected.name,
        targetOutcome=goal.desired_change,
        planStyle="custom",
        phases=[
            PlanDesignPhase(
                title=phase.title,
                purpose=phase.purpose,
                expectedOutput=phase.outcome,
                resourcesToUse=[],
                whyNeeded=phase.why_this_phase_exists,
            )
            for phase in selected.phases
        ],
        designRationale=f"{selected.core_idea}\n{selected.rationale.why_it_fits_user}\n{strategy.recommendation_reason}",
        assumptions=selected.rationale.assumptions,
        userBenefits=selected.expected_results,
        tradeoffs=[*selected.tradeoffs, *selected.major_risks],
        questionsForUser=[strategy.user_decision.question],
        status="approved" if approved else "waiting_user_approval",
    )


def _learning_resource(resource: ExecutionResource, task_minutes: int) -> TaskLearningResource:
    source_type = _source_type(resource.type)
    source_ref = resource.source_ref or ""
    return TaskLearningResource(
        title=resource.title,
        sourceType=source_type,
        url=source_ref if source_ref.startswith("http") else None,
        searchKeyword=None if source_ref.startswith("http") else source_ref or resource.title,
        useStep=resource.exact_usage,
        estimatedMinutes=max(1, min(task_minutes, 240)),
        whyThisResource=resource.expected_contribution,
        expectedOutput=resource.expected_contribution,
        fallbackIfTooHard=resource.fallback_resource or "Use a smaller example or request a replacement resource.",
    )


def execution_to_draft(
    execution: ExecutionBlueprint,
    strategy: StrategyPortfolio,
    critique: PlanCritiqueReport | None,
    *,
    approved: bool = False,
) -> ExecutionPlanDraft:
    selected_id = strategy.recommended_strategy_id
    tasks: list[ExecutionTask] = []
    for task in execution.tasks:
        resources = [_learning_resource(item, task.estimated_minutes) for item in task.resources]
        bundle = TaskResourceBundle(
            primary=resources[0] if resources else None,
            support=resources[1] if len(resources) > 1 else None,
            practice=next((item for item in resources if item.source_type in {"practice_bank", "practice_drill", "project_template"}), None),
            fallback=resources[-1] if len(resources) > 2 else None,
        )
        tasks.append(
            ExecutionTask(
                title=task.title,
                description=task.purpose,
                dueDate=task.scheduled_date,
                scheduledDate=task.scheduled_date,
                estimatedMinutes=task.estimated_minutes,
                priority="high" if task.difficulty == "high" else "low" if task.difficulty == "low" else "medium",
                whyThisTaskMatters=task.why_now,
                actionSteps=task.action_steps,
                acceptanceCriteria=task.completion_evidence,
                deliverable=task.deliverable,
                fallbackAdjustment=task.fallback_action,
                riskNotes=task.risks,
                knowledgePoints=task.prerequisites,
                resourceBundle=bundle,
                resourceCoverage=ExecutionTaskResourceCoverage(
                    status="strong" if len(resources) >= 2 else "partial" if resources else "missing",
                    explanation="Resources were selected from the evidence pack for this exact task.",
                ),
            )
        )
    passed = bool(critique and critique.status == "passed" and critique.calendar_writable)
    issues = critique.issues if critique else []
    report = ExecutionPlanQualityReport(
        status="passed" if passed else "blocked" if critique and critique.status == "blocked" else "needs_repair",
        score=critique.score if critique else 0,
        blockers=[item.description for item in issues if item.severity == "blocker"],
        warnings=[item.description for item in issues if item.severity in {"major", "minor"}],
        repairSuggestions=[item.instruction for item in (critique.repair_requests if critique else [])],
        checks=ExecutionPlanQualityChecks(
            goalAlignment=passed,
            timeFit=passed,
            taskSpecificity=passed,
            resourceDiversity=passed,
            deliverableQuality=passed,
            calendarWritable=passed,
        ),
    )
    return ExecutionPlanDraft(
        designId=selected_id,
        tasks=tasks,
        reviewCadence=execution.narrative.weekly_or_stage_rhythm,
        riskPlan=[execution.narrative.risk_handling, *execution.assumptions],
        scheduleSummary=execution.narrative.execution_logic,
        resourceCoverageSummary=f"Evidence-backed resource coverage: {execution.resource_coverage}.",
        status="approved" if approved else "waiting_user_approval",
        qualityReport=report,
        qualityStatus=report.status,
    )


def learning_to_patch(update: PlanningLearningUpdate) -> LearningPatch:
    stage = update.diagnosis.failure_stage
    if stage == "strategy":
        target, action = "design", "revise_design"
    elif stage == "resource":
        target, action = "resource", "replace_resource"
    elif stage == "schedule":
        target, action = "schedule", "change_schedule"
    else:
        target, action = "execution_task", "split_task"
    immediate = None
    if update.current_plan_patch:
        immediate = LearningImmediatePatch(target=target, action=action, instruction=update.current_plan_patch.instruction)
    long_term = None
    if update.user_model_hypothesis:
        hypothesis = update.user_model_hypothesis
        long_term = LongTermLearning(
            newRule=hypothesis.rule,
            confidence=hypothesis.confidence,
            evidence=hypothesis.evidence,
            appliesToDomains=hypothesis.domain_scope,
            expiresAt=hypothesis.expires_at,
        )
    return LearningPatch(
        originalFeedback=update.original_feedback,
        feedbackType="negative",
        affectedScope="current_plan" if immediate else "future_plans",
        insight=update.diagnosis.root_cause,
        reflection=LearningReflection(
            whatWentWrong=update.diagnosis.failed_assumption,
            whyItHappened=update.diagnosis.root_cause,
            howToAvoidNextTime=long_term.new_rule if long_term else "Repair the responsible planning artifact before continuing.",
        ),
        immediatePatch=immediate,
        longTermLearning=long_term,
        memoryUpdates=[],
    )
