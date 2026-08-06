from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.db import get_conn
from app.schemas import CreatePlanningSessionRequest, MemoryCreate, PlanningSessionTextRequest
from app.services.cognitive_planning.agents import (
    AgentResult,
    CognitiveModelClient,
    ContextEvidenceAgent,
    CriticLearningAgent,
    ExecutionDesignerAgent,
    GoalModelingAgent,
    PlanningModelUnavailable,
    StrategyArchitectAgent,
)
from app.services.cognitive_planning.contracts import (
    EVIDENCE_AUTHORITY_POLICY_VERSION,
    CalendarReality,
    ConversationTurn,
    CritiqueDimensions,
    CritiqueIssue,
    CriticRepairRequest,
    DecisionRelevantUnknown,
    DomainEvidence,
    EvidencePack,
    EvidenceGap,
    EvidenceAuthorityPolicy,
    EvidenceInput,
    EvidencePlanningRule,
    EvidenceResourceCandidate,
    EvidenceResourceNeed,
    ExecutionBlueprint,
    ExecutionBlueprintTask,
    ExecutionBudgetSummary,
    ExecutionCheckpoint,
    ExecutionNarrative,
    ExecutionResource,
    FeasibilityJudgment,
    GoalQuestion,
    GoalCompletionResult,
    GoalModelingInput,
    GoalSuccessModel,
    KnownFact,
    LearningDiagnosis,
    PlanCritiqueReport,
    PlanningLearningUpdate,
    Preference,
    RealityAssessment,
    RealityRisk,
    SafePlanningError,
    StrategyOption,
    StrategyPhase,
    StrategyPortfolio,
    StrategyRationale,
    StrategyUserDecision,
    UserGoalModel,
    UserEvidence,
    UserModelHypothesisDraft,
    apply_evidence_authority_policy,
)
from app.services.cognitive_planning.orchestration.runtime import (
    CognitivePlanningRuntime,
    _normalize_learning_patch_target,
)
from app.services.cognitive_planning.compatibility.legacy_schema_adapter import execution_to_draft
from app.services.cognitive_planning.orchestration.graph import build_cognitive_graph
from app.services.cognitive_planning.evaluation import (
    CognitivePlanningShadowRunner,
    DeterministicGuardError,
    validate_execution_invariants,
)
from app.services.cognitive_planning.retrieval import (
    CognitiveMemoryRetriever,
    PlanningHistoryRetriever,
    PlanningHypothesisRepository,
    ResourceResearch,
)
from app.services.llm import LlmError, LlmResult
from app.services.deep_planning import DeepPlanningService, LegacyTemplatePlanningService
from app.services.planning_agent_runtime import PlanningAgentRuntime
from app.services.memory_store import MemoryService
from app.services.ai_settings import get_model_routing_rule
from app.cognitive_planning import CognitiveOSRuntime
from app.cognitive_planning.agents import (
    CriticAgent as CognitiveCriticAgent,
    EvidenceAgent as CognitiveEvidenceAgent,
    ExecutionAgent as CognitiveExecutionAgent,
    StrategyAgent as CognitiveStrategyAgent,
)
from app.cognitive_planning.agents.reality_agent import REALITY_SYSTEM
from app.cognitive_planning.evaluation import CognitiveCriticRuleError, validate_execution_blueprint
from app.cognitive_planning.memory import UserModelMemoryRepository


def _usage(task_type: str) -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "model": "stub-cognitive",
        "promptTokens": 100,
        "completionTokens": 50,
        "totalTokens": 150,
        "latencyMs": 5,
        "mode": "llm",
        "taskType": task_type,
        "fallbackUsed": False,
        "localFallbackAllowed": False,
        "attempts": [{"provider": "deepseek", "model": "stub-cognitive", "status": "success", "latencyMs": 5}],
    }


class StubCognitiveModel:
    def __init__(self, *, generic_task: bool = False, first_critique_needs_repair: bool = False, repair_target: str = "execution_designer"):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.generic_task = generic_task
        self.first_critique_needs_repair = first_critique_needs_repair
        self.repair_target = repair_target
        self.critique_calls = 0

    def complete_contract(self, *, task_type: str, payload: dict[str, Any], contract_type, **_: Any):
        self.calls.append((task_type, payload))
        if contract_type is UserGoalModel:
            return AgentResult(self._goal(payload), _usage(task_type))
        if contract_type is RealityAssessment:
            return AgentResult(self._reality(payload), _usage(task_type))
        if contract_type is EvidencePack:
            return AgentResult(self._evidence(payload), _usage(task_type))
        if contract_type is StrategyPortfolio:
            return AgentResult(self._strategy(payload), _usage(task_type))
        if contract_type is ExecutionNarrative:
            return AgentResult(self._narrative(), _usage(task_type))
        if contract_type is ExecutionBlueprint:
            return AgentResult(self._execution(payload), _usage(task_type))
        if contract_type is PlanCritiqueReport:
            return AgentResult(self._critique(), _usage(task_type))
        if contract_type is PlanningLearningUpdate:
            return AgentResult(self._learning(payload), _usage(task_type))
        raise AssertionError(f"Unexpected contract: {contract_type}")

    def _conversation_text(self, payload: dict[str, Any]) -> str:
        return " ".join(str(item.get("content") or "") for item in payload.get("conversationHistory", []))

    def _domain(self, text: str) -> str:
        if "游泳" in text:
            return "swimming"
        if "新疆" in text or "旅行" in text:
            return "travel"
        if "膝盖" in text or "健身" in text or "减脂" in text:
            return "fitness"
        if "四级" in text or "考试" in text:
            return "exam"
        if "客服" in text or "MVP" in text:
            return "software_project"
        return "python_career"

    def _goal(self, payload: dict[str, Any]) -> UserGoalModel:
        text = self._conversation_text(payload)
        domain = self._domain(text)
        vague_swimming = domain == "swimming" and not any(token in text for token in ("泳池", "教练", "安全", "连续游", "米"))
        questions = []
        unknowns = []
        if vague_swimming:
            unknowns = [
                DecisionRelevantUnknown(
                    key="safe_practice_access",
                    description="Whether a safe pool and qualified supervision are available",
                    whyItChangesThePlan="It changes the first safety phase and whether solo practice is appropriate.",
                    impact="safety",
                    priority="blocking",
                ),
                DecisionRelevantUnknown(
                    key="measurable_swim_outcome",
                    description="What observable swimming result counts as success",
                    whyItChangesThePlan="It determines milestones and assessment distance.",
                    impact="success_criteria",
                    priority="important",
                ),
            ]
            questions = [
                GoalQuestion(question="你是否能稳定使用有救生员或教练的泳池？", whyThisQuestionMatters="这会决定安全边界和第一阶段练习方式。", expectedDecisionImpact="safety and feasibility"),
                GoalQuestion(question="你希望最终能连续游多远，或掌握哪一种泳姿？", whyThisQuestionMatters="“熟练”需要转成可验收结果。", expectedDecisionImpact="success criteria"),
            ]
        return UserGoalModel(
            goalStatement={
                "swimming": "安全掌握基础游泳能力",
                "travel": "完成新疆 14 天旅行",
                "fitness": "在保护膝盖的前提下减脂",
                "exam": "两个月准备英语四级",
                "software_project": "两周交付客服自动回复 Agent MVP",
            }.get(domain, "30 天建立 Python AI 应用实习能力"),
            desiredChange="获得可验证的结果，而不是只完成泛化学习步骤。",
            domain=domain,
            userLanguage=[text],
            knownFacts=[KnownFact(key="input", statement=text, sourceText=text, confidence=1)],
            softPreferences=[
                Preference(
                    statement="Prefer the concrete, reviewable outcome encoded by this test model.",
                    sourceText=text,
                    confidence=1,
                )
            ],
            decisionRelevantUnknowns=unknowns,
            successModel=GoalSuccessModel(
                definition="Produce observable evidence matched to the domain.",
                measurableSignals=["A concrete deliverable or performance test is completed."],
                intermediateMilestones=["First safe, reviewable milestone completed."],
            ),
            feasibilityJudgment=FeasibilityJudgment(summary="Feasible if the explicit constraints are respected.", risks=[]),
            questions=questions,
            confidence=0.9 if not vague_swimming else 0.7,
            canProceedToEvidence=not vague_swimming,
        )

    def _evidence(self, payload: dict[str, Any]) -> EvidencePack:
        goal = payload["goalModel"]
        domain = str(goal["domain"])
        hypotheses = payload.get("planningHypotheses") or []
        learned = [str(item.get("statement") or "") for item in hypotheses]
        gaps = []
        if domain == "travel":
            gaps.append(
                EvidenceGap(
                    description="Transport schedules, opening rules, weather, and current prices require fresh verification.",
                    consequence="The route and budget remain provisional until authoritative current sources are checked.",
                    proposedResolution="web_research",
                )
            )
        return EvidencePack(
            planningRules=[
                EvidencePlanningRule(
                    rule=learned[0] if learned else "Keep workload aligned with the user's stated time budget.",
                    strength="hard",
                    evidence=["user statement"],
                    sourceIds=["current_goal:known_fact:input"],
                    sourceContext="current_goal",
                    confidence=0.9,
                )
            ],
            domainEvidence=[
                DomainEvidence(
                    claim=f"{domain} requires domain-specific evidence and risk handling.",
                    sourceType="model_knowledge",
                    sourceContext="model_knowledge",
                    relevance="Prevents cross-domain templates.",
                    credibility=0.85,
                )
            ],
            resourceNeeds=[EvidenceResourceNeed(purpose="Support the first concrete action", idealResourceType="official or qualified source", selectionCriteria=["credible", "actionable"])],
            resourceCandidates=[
                EvidenceResourceCandidate(
                    title=f"{domain} primary evidence source",
                    type="official_doc" if domain in {"python_career", "software_project"} else "coach_or_human",
                    sourceRef=None,
                    sourceContext="model_knowledge",
                    howItHelps="Supports the exact first action and its safety boundary.",
                    userFit="Matches the user's goal and current constraints.",
                    limitations=["Availability must be confirmed."],
                    credibility=0.85,
                )
            ],
            calendarReality=CalendarReality(availableWindows=["User-stated time window"], conflicts=[], loadWarnings=[]),
            gaps=gaps,
            synthesis=f"Evidence supports a {domain}-specific strategy and excludes unrelated templates.",
            confidence=0.86,
            canProceedToStrategy=True,
        )

    def _reality(self, payload: dict[str, Any]) -> RealityAssessment:
        goal = payload["goalModel"]
        domain = str(goal["domain"])
        risks = {
            "swimming": [RealityRisk(risk="水上安全与训练环境", consequence="无监督练习可能导致伤害", mitigation="仅在有救生员或教练的泳池训练", severity="blocker")],
            "travel": [RealityRisk(risk="季节与长距离交通", consequence="路线可能受天气和车程影响", mitigation="确认实时交通并保留机动日")],
        }.get(domain, [RealityRisk(risk="时间与目标范围", consequence="范围过大会降低完成率", mitigation="用可验收结果控制范围")])
        return RealityAssessment(
            goalRestatement=str(goal["goalStatement"]),
            feasibilitySummary="目标在明确现实边界后可行。",
            timeAssessment="投入与周期需要按可验收结果分配。",
            resourceAssessment="优先使用可验证资源；缺失信息必须显式标记。",
            hiddenRisks=risks,
            recommendedAdjustments=["保留缓冲并以实际反馈调整范围。"],
            assumptionsToValidate=[],
            importantQuestions=[],
            confidence=0.88,
            canProceedToEvidence=True,
        )

    def _strategy(self, payload: dict[str, Any]) -> StrategyPortfolio:
        domain = str(payload["goalModel"]["domain"])
        option = StrategyOption(
            id=f"{domain}-recommended",
            name=f"{domain} evidence-first route",
            coreIdea="Progress through domain-specific proof, review, and risk controls.",
            rationale=StrategyRationale(
                whyItFitsUser="It follows the user's constraints and the available evidence.",
                evidenceUsed=["goal model", "evidence pack"],
                assumptions=["The stated time remains available."],
            ),
            phases=[
                StrategyPhase(title=f"Validate the first {domain} constraint", purpose="Remove the largest feasibility risk.", outcome="A verified starting condition.", whyThisPhaseExists="The next actions depend on it."),
                StrategyPhase(title=f"Produce the first {domain} proof", purpose="Create observable progress.", outcome="A domain-specific deliverable.", whyThisPhaseExists="Evidence is stronger than passive completion."),
            ],
            tradeoffs=["Narrower initial scope for stronger evidence."],
            majorRisks=["The real-world resource may be unavailable."],
            expectedResults=["A checkable domain-specific result."],
            estimatedEffort="Fits the stated planning horizon.",
        )
        return StrategyPortfolio(
            recommendedStrategyId=option.id,
            strategies=[option],
            recommendationReason="This route best matches the goal and evidence while keeping risk visible.",
            userDecision=StrategyUserDecision(question="是否采用这条路线？", options=[option.name, "调整路线"], defaultRecommendation=option.name),
        )

    def _narrative(self) -> ExecutionNarrative:
        return ExecutionNarrative(
            executionLogic="Resolve dependencies first, then create observable proof and review it.",
            dependencyExplanation="Each task names the prior evidence it needs.",
            weeklyOrStageRhythm="Work, verify, then adjust at each checkpoint.",
            workloadReasoning="Task size follows the stated time budget.",
            riskHandling="Stop or downgrade when a safety, resource, or feasibility condition fails.",
        )

    def _execution(self, payload: dict[str, Any]) -> ExecutionBlueprint:
        domain = str(payload["goalModel"]["domain"])
        start = date.today() + timedelta(days=1)
        specs: dict[str, list[tuple[str, str, str, int]]] = {
            "swimming": [
                ("Complete a supervised water-safety assessment", "Confirm pool access, lifeguard or coach support, and safe-depth boundaries.", "signed safety and pool-access checklist", 60),
                ("Practice breathing, floating, and safe recovery", "Work only in the approved depth with qualified supervision.", "coach-checked breathing and floating evidence", 60),
                ("Build a measurable continuous-swim milestone", "Practice the selected stroke toward a concrete distance without unsafe fatigue.", "supervised 50-metre swim record", 60),
                ("Review technique and emergency boundaries", "Record technique feedback and when to stop or seek help.", "technique review and safety fallback card", 45),
            ],
            "python_career": [
                ("Implement Python data transformation scripts", "Write tested Python functions for JSON and CSV input.", "src/data_pipeline.py plus tests", 180),
                ("Build a FastAPI endpoint with validation", "Create GET and POST endpoints with typed request validation.", "working API and curl evidence", 180),
                ("Add SQLite persistence and error handling", "Persist records and test failure paths.", "database migration and integration tests", 180),
                ("Create an LLM structured-output feature", "Call a model through an interface and validate JSON output.", "model adapter and contract tests", 180),
                ("Add a small RAG retrieval path", "Index local text and return cited evidence.", "retrieval demo with source IDs", 180),
                ("Publish reviewable GitHub commits", "Split the work into reviewable commits and run CI checks.", "GitHub commit history and passing checks", 120),
                ("Write the project README walkthrough", "Document architecture, setup, tradeoffs, and a reproducible demo.", "README with screenshots and commands", 150),
                ("Rewrite resume project bullets", "Express scope, decisions, reliability, and measurable outcomes.", "three resume bullets", 120),
                ("Practice AI application interview questions", "Answer architecture, RAG, evaluation, and failure-mode questions.", "recorded answers and revision notes", 150),
                ("Run an end-to-end portfolio review", "Verify code, demo, README, resume, and interview evidence together.", "portfolio readiness checklist", 180),
            ],
            "travel": [
                ("Verify the 14-day route order and transfer times", "Compare Urumqi, Sayram Lake, and Kanas transfer constraints.", "route table with travel durations", 90),
                ("Allocate the CNY 10,000 budget", "Split flights, local transport, lodging, food, tickets, and contingency.", "budget sheet with contingency", 75),
                ("Check bookings and cancellation rules", "Verify flights, lodging, park access, and refundable alternatives.", "booking checklist with deadlines", 90),
                ("Prepare weather and route alternatives", "Create cold-weather, road-delay, and closed-attraction alternatives.", "weather fallback itinerary", 60),
            ],
            "fitness": [
                ("Confirm knee-safe training boundaries with a professional", "Document movements to avoid and pain stop rules.", "professional safety boundary note", 45),
                ("Establish a low-impact baseline", "Measure walking or cycling tolerance without provoking knee pain.", "baseline log with pain scale", 45),
                ("Complete four low-impact weekly sessions", "Alternate cardio and supported strength within the approved range.", "four-session training log", 180),
                ("Review fat-loss progress without unsafe load increases", "Review adherence, recovery, and symptoms before progression.", "weekly review and adjustment", 45),
            ],
            "exam": [
                ("Measure the CET-4 score gap", "Complete a timed baseline and classify errors by section.", "baseline score and error taxonomy", 120),
                ("Run focused listening drills", "Practice weak listening question types and transcribe errors.", "listening error log", 90),
                ("Run timed reading drills", "Measure speed and accuracy for matching and detailed reading.", "reading timing log", 90),
                ("Build reusable writing and translation feedback", "Draft, compare against criteria, and rewrite weak sentences.", "two revised essays and translation set", 90),
                ("Complete a full mock and error review", "Simulate the exam, score it, and schedule remediation.", "mock score plus corrected error set", 150),
            ],
            "software_project": [
                ("Freeze the customer-support MVP scope", "Define supported intents, escalation, latency, and non-goals.", "MVP scope and acceptance contract", 90),
                ("Create a privacy-safe evaluation dataset", "Prepare representative questions, expected answers, and refusal cases.", "versioned evaluation dataset", 150),
                ("Implement retrieval and response generation", "Build the smallest API path with cited source context.", "runnable service and API tests", 180),
                ("Evaluate quality, safety, and failure modes", "Measure answer quality, unsupported claims, latency, and escalation.", "evaluation report with thresholds", 150),
                ("Package and demonstrate the MVP", "Document setup, architecture, risks, and a repeatable demo.", "demo, README, and release checklist", 120),
            ],
        }
        selected = specs.get(domain, [(f"Verify and produce the first {domain} outcome", f"Create the first concrete {domain} proof.", f"A concrete {domain} result and review note.", 60)])
        if self.generic_task:
            selected = [("学习并复现", "Generic task that must be rejected.", "generic output", 60)]
        tasks: list[ExecutionBlueprintTask] = []
        for index, (title, purpose, deliverable, minutes) in enumerate(selected):
            task_id = f"task-{index + 1}"
            previous = [f"task-{index}"] if index else []
            extension: dict[str, Any] = {"domain": domain}
            if domain == "travel":
                extension.update({"budgetCny": 10000, "transport": "flight", "freshnessCheckRequired": True})
            elif domain == "swimming":
                extension.update({"qualifiedSupervision": True, "safeDepthRequired": True, "stopWhenDistressed": True})
            elif domain == "fitness":
                extension.update({"kneeInjury": True, "professionalBoundaryRequired": True, "stopOnPain": True})
            elif domain == "exam":
                extension.update({"currentScore": 350, "exam": "CET-4"})
            elif domain == "software_project":
                extension.update({"mvp": True, "evaluationRequired": True, "privacyRisk": True})
            tasks.append(
                ExecutionBlueprintTask(
                    id=task_id,
                    title=title,
                    purpose=purpose,
                    whyNow="It resolves the next dependency and produces evidence before more effort is spent.",
                    dependencies=previous,
                    actionSteps=["Confirm the prerequisite and boundary.", purpose, "Capture the deliverable and one failure note."],
                    estimatedMinutes=minutes,
                    difficulty="medium",
                    scheduledDate=(start + timedelta(days=index * 2)).isoformat(),
                    completionEvidence=[deliverable, "The result is dated and independently reviewable."],
                    deliverable=deliverable,
                    resources=[
                        ExecutionResource(
                            title=f"{domain} verified primary source",
                            type="official_doc" if domain in {"python_career", "software_project", "exam"} else "coach_or_human" if domain in {"fitness", "swimming"} else "route_info",
                            sourceRef="https://example.invalid/reference" if domain in {"python_career", "software_project"} else None,
                            exactUsage="Use only the section or verification step required for this task.",
                            expectedContribution="Provides evidence, instruction, or a safety boundary for the task.",
                            fallbackResource="Use a qualified person, official source, or a smaller verified example.",
                        )
                    ],
                    prerequisites=[],
                    risks=["Stop if the required safety, freshness, resource, or technical condition is not met."],
                    fallbackAction="Reduce scope to verifying the prerequisite and record the blocker.",
                    domainExtensions=extension,
                )
            )
        return ExecutionBlueprint(
            narrative=self._narrative(),
            tasks=tasks,
            checkpoints=[ExecutionCheckpoint(dateOrStage=f"after {tasks[-1].id}", questions=["What evidence changed?"], adjustmentRules=["Reduce scope if the prerequisite failed."])],
            assumptions=["The user's stated time remains available."],
            resourceCoverage="partial",
        )

    def _critique(self) -> PlanCritiqueReport:
        self.critique_calls += 1
        needs_repair = self.first_critique_needs_repair and self.critique_calls == 1
        return PlanCritiqueReport(
            status="needs_repair" if needs_repair else "passed",
            score=70 if needs_repair else 92,
            dimensions=CritiqueDimensions(
                userFit=90,
                goalAlignment=92,
                domainCorrectness=95,
                feasibility=88,
                safety=90,
                taskSpecificity=65 if needs_repair else 92,
                resourceActionability=88,
                scheduleFit=86,
                adaptability=85,
            ),
            strengths=["Tasks are concrete and checkable."] if not needs_repair else [],
            issues=[
                CritiqueIssue(
                    severity="major" if needs_repair else "minor",
                    description="Clarify the first action." if needs_repair else "No blocking issue.",
                    evidence="task-1",
                    responsibleAgent="execution_designer",
                )
            ],
            repairRequests=[
                {
                    "targetAgent": self.repair_target,
                    "instruction": "Make the first action and completion proof more specific.",
                    "expectedChange": "Task 1 names an observable action and result.",
                }
            ] if needs_repair else [],
            simulationSummary="The first task, failure path, reduced-time day, and domain risk were simulated.",
            remainingRisks=[] if not needs_repair else ["First action is still broad."],
            calendarWritable=not needs_repair,
            confidence=0.9,
        )

    def _learning(self, payload: dict[str, Any]) -> PlanningLearningUpdate:
        feedback = str(payload.get("feedback") or "")
        direction_feedback = "方向" in feedback
        return PlanningLearningUpdate(
            originalFeedback=feedback,
            diagnosis=LearningDiagnosis(
                failedAssumption="The proposed direction matched the user." if direction_feedback else "The initial resource format fit the user.",
                failureStage="strategy" if direction_feedback else "resource",
                rootCause="The strategy emphasis was wrong." if direction_feedback else "The resource was too abstract for the user's current context.",
            ),
            currentPlanPatch={
                "targetArtifact": "strategy_portfolio" if direction_feedback else "execution_blueprint",
                "instruction": "Revise the strategy around the user's corrected direction." if direction_feedback else "Replace the abstract resource with a smaller guided example.",
            },
            userModelHypothesis=UserModelHypothesisDraft(
                rule="Prefer guided examples before dense reference material in this domain.",
                domainScope=["python_career"],
                evidence=feedback,
                confidence=0.62,
            ),
            shouldPersist=True,
        )


class UnavailableCognitiveModel:
    def complete_contract(self, *, stage: str, **_: Any):
        raise PlanningModelUnavailable(
            stage,
            SafePlanningError(stage=stage, errorType="auth_error", message="No configured model is available.", retryable=False, attempts=[]),
        )


class BlockingCriticModel(StubCognitiveModel):
    def __init__(self, issue: str):
        super().__init__()
        self.issue = issue

    def _critique(self) -> PlanCritiqueReport:
        self.critique_calls += 1
        return PlanCritiqueReport(
            status="blocked",
            score=35,
            dimensions=CritiqueDimensions(
                userFit=50,
                goalAlignment=45,
                domainCorrectness=30 if self.issue == "domain mismatch" else 80,
                feasibility=30 if self.issue == "time mismatch" else 75,
                safety=80,
                taskSpecificity=70,
                resourceActionability=25 if self.issue == "resource mismatch" else 75,
                scheduleFit=30 if self.issue == "time mismatch" else 75,
                adaptability=60,
            ),
            strengths=[],
            issues=[
                CritiqueIssue(
                    severity="blocker",
                    description=self.issue,
                    evidence="fixed semantic judge output",
                    responsibleAgent="execution_designer",
                )
            ],
            repairRequests=[],
            simulationSummary=f"Simulation exposed a blocking {self.issue}.",
            remainingRisks=[self.issue],
            calendarWritable=False,
            confidence=0.95,
        )


class RepairableBlockedCriticModel(StubCognitiveModel):
    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        if self.critique_calls > 1:
            return report
        return report.model_copy(
            update={
                "status": "blocked",
                "score": 45,
                "issues": [
                    CritiqueIssue(
                        severity="blocker",
                        description="The fallback drops a hard requirement.",
                        evidence="task-1 fallback",
                        responsibleAgent="execution_designer",
                    )
                ],
                "repair_requests": [
                    CriticRepairRequest(
                        targetAgent="execution_designer",
                        instruction="Replace the fallback while preserving the hard requirement.",
                        expectedChange="Every fallback still delivers the required outcome.",
                    )
                ],
                "calendar_writable": False,
            }
        )


class InconsistentPassingCriticModel(StubCognitiveModel):
    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        return report.model_copy(
            update={
                "issues": [
                    CritiqueIssue(
                        severity="blocker",
                        description="Unsafe unresolved dependency.",
                        evidence="task-1",
                        responsibleAgent="execution_designer",
                    )
                ],
                "repair_requests": [
                    CriticRepairRequest(
                        targetAgent="execution_designer",
                        instruction="Resolve the dependency.",
                        expectedChange="No unsafe dependency remains.",
                    )
                ],
                "simulation_summary": "The model incorrectly marked an unresolved blocker as passed.",
            }
        )


class MajorIssuePassingCriticModel(StubCognitiveModel):
    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        return report.model_copy(
            update={
                "issues": [
                    CritiqueIssue(
                        severity="major",
                        description="The weekly workload exceeds the user's limit.",
                        evidence="execution workload arithmetic",
                        responsibleAgent="execution_designer",
                    )
                ]
            }
        )


class RepairRequestPassingCriticModel(StubCognitiveModel):
    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        return report.model_copy(
            update={
                "repair_requests": [
                    CriticRepairRequest(
                        targetAgent="execution_designer",
                        instruction="Repair the unresolved execution concern.",
                        expectedChange="The concern is explicitly resolved.",
                    )
                ]
            }
        )


class PassedButNotWritableCriticModel(StubCognitiveModel):
    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        return report.model_copy(update={"calendar_writable": False})


class EvidenceBlockingModel(StubCognitiveModel):
    def _evidence(self, payload: dict[str, Any]) -> EvidencePack:
        evidence = super()._evidence(payload)
        return evidence.model_copy(
            update={
                "can_proceed_to_strategy": False,
                "gaps": [
                    EvidenceGap(
                        description="A safety-critical prerequisite is unknown.",
                        consequence="A credible strategy cannot be selected yet.",
                        proposedResolution="ask_user",
                        blockingBasis="new_evidence",
                        impact="safety",
                        sourceContext="new_evidence",
                        supportingSourceIds=["current_goal:known_fact:input"],
                    )
                ],
            }
        )


class FakeLlm:
    def __init__(self, result: LlmResult | None = None, error: LlmError | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def complete(self, *_args: Any, **kwargs: Any):
        self.calls.append(kwargs)
        return self.result, self.error


class FixedSessionCreator:
    def __init__(self, session):
        self.session = session

    def create_session(self, _payload):
        return self.session


class FakeWebResearchProvider:
    def __init__(self):
        self.calls: list[str] = []

    def search(self, query, _policy):
        self.calls.append(query)
        return [{"title": "Current official travel notice", "type": "official_notice", "sourceRef": "https://example.invalid/current"}]


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cognitive.db'}")
    return tmp_path


def test_model_unavailable_blocks_formal_planning_and_calendar(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=UnavailableCognitiveModel())
    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="blocked-thread", userInput="我要学游泳"))
    assert session.status == "needs_goal_clarification"
    assert session.cognitive_metadata.planning_mode == "blocked_model_unavailable"
    assert session.strategy_portfolio is None
    assert session.execution_blueprint is None
    assert session.design_proposal is None
    assert session.execution_draft is None
    with pytest.raises(HTTPException) as exc_info:
        runtime.prepare_calendar_write(session.session_id)
    assert exc_info.value.status_code == 409


def test_deep_planning_module_is_a_rollout_facade(monkeypatch):
    monkeypatch.delenv("PLANIX_USE_COGNITIVE_PLANNING", raising=False)
    legacy_facade = DeepPlanningService()
    assert isinstance(legacy_facade._delegate, LegacyTemplatePlanningService)
    assert legacy_facade.engine_version == "legacy-template-v1"

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    cognitive_facade = DeepPlanningService()
    assert isinstance(cognitive_facade._delegate, CognitivePlanningRuntime)
    assert cognitive_facade.engine_version == "cognitive-v2"


@pytest.mark.parametrize("error_type", ["auth_error", "timeout"])
def test_cognitive_model_errors_are_safe_and_never_create_artifacts(error_type):
    llm = FakeLlm(error=LlmError(f"{error_type} from provider", error_type, attempts=[{"provider": "deepseek", "model": "stub", "status": "error", "errorType": error_type, "latencyMs": 9}]))
    client = CognitiveModelClient(llm=llm)
    with pytest.raises(PlanningModelUnavailable) as exc_info:
        client.complete_contract(
            stage="goal_modeling",
            task_type="planning_goal_model",
            feature="test_cognitive_failure",
            system="Return JSON.",
            payload={"conversationHistory": []},
            contract_type=UserGoalModel,
        )
    assert exc_info.value.error.error_type == error_type
    assert exc_info.value.error.attempts[0]["errorType"] == error_type


def test_cognitive_invalid_json_is_blocked_without_template_fallback():
    llm = FakeLlm(result=LlmResult(content="not-json", provider="deepseek", model="stub", attempts=[]))
    client = CognitiveModelClient(llm=llm)
    with pytest.raises(PlanningModelUnavailable) as exc_info:
        client.complete_contract(
            stage="goal_modeling",
            task_type="planning_goal_model",
            feature="test_invalid_json",
            system="Return JSON.",
            payload={"conversationHistory": []},
            contract_type=UserGoalModel,
        )
    assert exc_info.value.error.error_type == "invalid_model_output"


def test_cognitive_contract_validation_uses_one_model_repair_call():
    invalid = LlmResult(
        content=json.dumps(
            {
                "complete": True,
                "blockingUnknowns": [{"question": "Q", "impact": "changes strategy"}],
                "optionalUnknowns": [],
                "nextStage": "strategy",
            }
        ),
        provider="deepseek",
        model="stub",
        usage={"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
        latency_ms=4,
        attempts=[{"provider": "deepseek", "model": "stub", "status": "success"}],
    )
    repaired = LlmResult(
        content=json.dumps(
            {
                "complete": True,
                "blockingUnknowns": [],
                "optionalUnknowns": [],
                "nextStage": "strategy",
            }
        ),
        provider="deepseek",
        model="stub",
        usage={"promptTokens": 12, "completionTokens": 4, "totalTokens": 16},
        latency_ms=5,
        attempts=[{"provider": "deepseek", "model": "stub", "status": "success"}],
    )

    class SequencedLlm:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self.results = [invalid, repaired]

        def complete(self, *_args: Any, **kwargs: Any):
            self.calls.append(kwargs)
            return self.results.pop(0), None

    llm = SequencedLlm()
    result = CognitiveModelClient(llm=llm).complete_contract(
        stage="goal_completion",
        task_type="planning_goal_model",
        feature="test_contract_repair",
        system="Return JSON.",
        payload={"goal": "test"},
        contract_type=GoalCompletionResult,
    )

    assert result.artifact.complete is True
    assert len(llm.calls) == 2
    assert result.model_usage["totalTokens"] == 31
    assert result.model_usage["attempts"][-1]["automaticRetry"] is True
    assert result.model_usage["attempts"][-1]["retryReason"] == "contract_validation"


def test_inconsistent_passed_critique_uses_model_contract_repair():
    valid = StubCognitiveModel()._critique().model_dump(by_alias=True)
    invalid = {
        **valid,
        "issues": [
            {
                "severity": "major",
                "description": "The schedule exceeds the user's weekly limit.",
                "evidence": "workload arithmetic",
                "responsibleAgent": "execution_designer",
            }
        ],
    }
    results = [
        LlmResult(
            content=json.dumps(invalid),
            provider="deepseek",
            model="stub",
            attempts=[{"provider": "deepseek", "model": "stub", "status": "success"}],
        ),
        LlmResult(
            content=json.dumps(valid),
            provider="deepseek",
            model="stub",
            attempts=[{"provider": "deepseek", "model": "stub", "status": "success"}],
        ),
    ]

    class SequencedCriticLlm:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def complete(self, *_args: Any, **kwargs: Any):
            self.calls.append(kwargs)
            return results.pop(0), None

    llm = SequencedCriticLlm()
    result = CognitiveModelClient(llm=llm).complete_contract(
        stage="critique",
        task_type="planning_critique",
        feature="test_critique_contract_repair",
        system="Return the independent critique as JSON.",
        payload={"execution": "test"},
        contract_type=PlanCritiqueReport,
    )

    assert result.artifact.status == "passed"
    assert len(llm.calls) == 2
    assert result.model_usage["attempts"][-1]["automaticRetry"] is True
    assert result.model_usage["attempts"][-1]["retryReason"] == "contract_validation"


def test_cognitive_fallback_provider_success_keeps_real_attempt_trace():
    artifact = StubCognitiveModel()._goal({"conversationHistory": [{"role": "user", "content": "30天准备 Python AI 实习"}]})
    llm = FakeLlm(
        result=LlmResult(
            content=artifact.model_dump_json(by_alias=True),
            provider="deepseek",
            model="fallback-model",
            usage={"promptTokens": 80, "completionTokens": 40, "totalTokens": 120},
            latency_ms=15,
            attempts=[
                {"provider": "kimi", "model": "primary", "status": "error", "errorType": "timeout", "latencyMs": 10},
                {"provider": "deepseek", "model": "fallback-model", "status": "success", "latencyMs": 5},
            ],
            fallback_used=True,
            local_fallback_allowed=False,
        )
    )
    result = CognitiveModelClient(llm=llm).complete_contract(
        stage="goal_modeling",
        task_type="planning_goal_model",
        feature="test_cloud_fallback",
        system="Return JSON.",
        payload={"conversationHistory": []},
        contract_type=UserGoalModel,
    )
    assert result.artifact.goal_statement
    assert result.model_usage["fallbackUsed"] is True
    assert result.model_usage["localFallbackAllowed"] is False
    assert [item["provider"] for item in result.model_usage["attempts"]] == ["kimi", "deepseek"]


def test_cognitive_stage_uses_its_configured_token_cap(monkeypatch):
    artifact = StubCognitiveModel()._goal({"conversationHistory": [{"role": "user", "content": "30天准备 Python AI 实习"}]})
    llm = FakeLlm(result=LlmResult(content=artifact.model_dump_json(by_alias=True), provider="deepseek", model="stub"))
    monkeypatch.setenv("PLANIX_GOAL_MODEL_MAX_TOKENS", "777")
    CognitiveModelClient(llm=llm).complete_contract(
        stage="goal_modeling",
        task_type="planning_goal_model",
        feature="test_token_cap",
        system="Return JSON.",
        payload={"conversationHistory": []},
        contract_type=UserGoalModel,
    )
    assert llm.calls[0]["max_tokens"] == 777
    assert llm.calls[0]["max_token_cap"] == 10800
    assert llm.calls[0]["task_type"] == "planning_goal_model"


@pytest.mark.parametrize(("configured_tokens", "expected_tokens"), [(None, 5400), ("20000", 10800)])
def test_goal_model_has_5400_default_and_independent_10800_hard_cap(monkeypatch, configured_tokens, expected_tokens):
    artifact = StubCognitiveModel()._goal({"conversationHistory": [{"role": "user", "content": "30澶╁噯澶?Python AI 瀹炰範"}]})
    llm = FakeLlm(result=LlmResult(content=artifact.model_dump_json(by_alias=True), provider="deepseek", model="stub"))
    if configured_tokens is None:
        monkeypatch.delenv("PLANIX_GOAL_MODEL_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("PLANIX_GOAL_MODEL_MAX_TOKENS", configured_tokens)

    CognitiveModelClient(llm=llm).complete_contract(
        stage="goal_modeling",
        task_type="planning_goal_model",
        feature="test_goal_token_budget",
        system="Return JSON.",
        payload={"conversationHistory": []},
        contract_type=UserGoalModel,
    )

    assert llm.calls[0]["max_tokens"] == expected_tokens
    assert llm.calls[0]["max_token_cap"] == 10800


def test_strategy_cognitive_stage_uses_expanded_budget_and_cap(monkeypatch):
    artifact = StubCognitiveModel()._strategy({"goalModel": {"domain": "python_career"}})
    llm = FakeLlm(result=LlmResult(content=artifact.model_dump_json(by_alias=True), provider="deepseek", model="stub"))
    monkeypatch.delenv("PLANIX_STRATEGY_MAX_TOKENS", raising=False)

    CognitiveModelClient(llm=llm).complete_contract(
        stage="strategy",
        task_type="planning_strategy",
        feature="test_strategy_token_budget",
        system="Return JSON.",
        payload={},
        contract_type=StrategyPortfolio,
    )

    assert llm.calls[0]["max_tokens"] == 7200
    assert llm.calls[0]["max_token_cap"] == 14400


@pytest.mark.parametrize(
    ("task_type", "env_name", "default_tokens", "hard_cap"),
    [
        ("planning_goal_model", "PLANIX_GOAL_MODEL_MAX_TOKENS", 5400, 10800),
        ("planning_reality", "PLANIX_REALITY_MAX_TOKENS", 5400, 10800),
        ("planning_evidence", "PLANIX_EVIDENCE_MAX_TOKENS", 6600, 13200),
        ("planning_strategy", "PLANIX_STRATEGY_MAX_TOKENS", 7200, 14400),
        ("planning_execution", "PLANIX_EXECUTION_MAX_TOKENS", 12000, 24000),
        ("planning_critique", "PLANIX_CRITIQUE_MAX_TOKENS", 6600, 13200),
        ("planning_learning", "PLANIX_LEARNING_MAX_TOKENS", 5400, 10800),
    ],
)
def test_all_cognitive_stages_use_task_budget_env_override_and_hard_cap(
    monkeypatch,
    task_type,
    env_name,
    default_tokens,
    hard_cap,
):
    artifact = StubCognitiveModel()._goal(
        {"conversationHistory": [{"role": "user", "content": "Learn Python for data analysis"}]}
    )
    llm = FakeLlm(
        result=LlmResult(
            content=artifact.model_dump_json(by_alias=True),
            provider="deepseek",
            model="stub",
        )
    )
    client = CognitiveModelClient(llm=llm)

    def invoke(feature: str):
        client.complete_contract(
            stage="token_budget",
            task_type=task_type,
            feature=feature,
            system="Return JSON.",
            payload={"conversationHistory": []},
            contract_type=UserGoalModel,
        )

    monkeypatch.delenv(env_name, raising=False)
    invoke("test_default_token_budget")
    assert llm.calls[-1]["max_tokens"] == default_tokens
    assert llm.calls[-1]["max_token_cap"] == hard_cap
    assert llm.calls[-1]["task_type"] == task_type

    monkeypatch.setenv(env_name, "777")
    invoke("test_env_token_budget")
    assert llm.calls[-1]["max_tokens"] == 777
    assert llm.calls[-1]["max_token_cap"] == hard_cap

    monkeypatch.setenv(env_name, str(hard_cap + 1))
    invoke("test_clamped_token_budget")
    assert llm.calls[-1]["max_tokens"] == hard_cap
    assert llm.calls[-1]["max_token_cap"] == hard_cap


def test_all_cognitive_routing_tasks_default_to_no_local_content_fallback(isolated_db):
    for task_type in (
        "planning_goal_model",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
        "planning_learning",
    ):
        rule = get_model_routing_rule(task_type, "deepseek")
        assert rule.local_fallback_enabled is False


def test_strategy_contract_rejects_unknown_or_duplicate_strategy_ids():
    model = StubCognitiveModel()
    valid = model._strategy({"goalModel": {"domain": "python_career"}}).model_dump(by_alias=True)
    unknown = {**valid, "recommendedStrategyId": "missing-strategy"}
    with pytest.raises(ValidationError, match="recommendedStrategyId"):
        StrategyPortfolio.model_validate(unknown)

    duplicate = dict(valid)
    duplicate["strategies"] = [valid["strategies"][0], valid["strategies"][0]]
    with pytest.raises(ValidationError, match="strategy ids must be unique"):
        StrategyPortfolio.model_validate(duplicate)


def test_blocked_goal_contract_requires_a_decision_relevant_question():
    model = StubCognitiveModel()
    valid = model._goal({"conversationHistory": [{"role": "user", "content": "我要学游泳"}]}).model_dump(by_alias=True)
    contradictory = {**valid, "canProceedToEvidence": True}
    with pytest.raises(ValidationError, match="must stop evidence planning"):
        UserGoalModel.model_validate(contradictory)
    valid["questions"] = []
    with pytest.raises(ValidationError, match="must ask at least one"):
        UserGoalModel.model_validate(valid)


def test_execution_guard_rejects_blank_task_and_resource_semantics():
    model = StubCognitiveModel()
    blueprint = model._execution({"goalModel": {"domain": "python_career"}})
    first = blueprint.tasks[0]
    blank_resource = first.resources[0].model_copy(update={"exact_usage": " "})
    blank_task = first.model_copy(update={"title": " ", "resources": [blank_resource]})
    invalid = blueprint.model_copy(update={"tasks": [blank_task, *blueprint.tasks[1:]]})
    with pytest.raises(DeterministicGuardError) as exc_info:
        validate_execution_invariants(invalid)
    assert f"{first.id} has no title" in exc_info.value.issues
    assert f"{first.id} resource 1 has no exact usage" in exc_info.value.issues


def test_execution_task_moves_relative_date_to_schedule_window():
    task = StubCognitiveModel()._execution({"goalModel": {"domain": "go_backend"}}).tasks[0]
    payload = task.model_dump(by_alias=True)
    payload["scheduledDate"] = "Week 1, Day 1"
    payload["scheduleWindow"] = None

    normalized = ExecutionBlueprintTask.model_validate(payload)

    assert normalized.scheduled_date is None
    assert normalized.schedule_window == "Week 1, Day 1"


def test_execution_task_rejects_an_invalid_iso_shaped_date():
    task = StubCognitiveModel()._execution({"goalModel": {"domain": "go_backend"}}).tasks[0]
    payload = task.model_dump(by_alias=True)
    payload["scheduledDate"] = "2026-02-30"

    with pytest.raises(ValidationError, match="valid ISO calendar date"):
        ExecutionBlueprintTask.model_validate(payload)


def test_execution_budget_summary_requires_unique_allocations_within_limit():
    valid = {
        "spendingLimitCny": 20_000,
        "allocations": [
            {"category": "transport", "amountCny": 6_000},
            {"category": "lodging", "amountCny": 4_000},
            {"category": "food", "amountCny": 4_000},
            {"category": "activities", "amountCny": 2_000},
            {"category": "contingency", "amountCny": 4_000},
        ],
    }
    summary = ExecutionBudgetSummary.model_validate(valid)
    assert sum(item.amount_cny for item in summary.allocations) == 20_000

    duplicate = {**valid, "allocations": [*valid["allocations"], {"category": "food", "amountCny": 1}]}
    with pytest.raises(ValidationError, match="categories must be unique"):
        ExecutionBudgetSummary.model_validate(duplicate)

    overspent = {**valid, "allocations": [*valid["allocations"], {"category": "visa", "amountCny": 1}]}
    with pytest.raises(ValidationError, match="cannot exceed"):
        ExecutionBudgetSummary.model_validate(overspent)


def test_execution_task_accepts_multi_day_work_package_minutes():
    task = StubCognitiveModel()._execution({"goalModel": {"domain": "go_backend"}}).tasks[0]
    payload = task.model_dump(by_alias=True)
    payload["estimatedMinutes"] = 1800

    normalized = ExecutionBlueprintTask.model_validate(payload)

    assert normalized.estimated_minutes == 1800


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risks", []),
        ("fallbackAction", ""),
        ("deliverable", ""),
    ],
)
def test_execution_task_requires_auditable_risk_and_delivery_fields(field: str, value: Any):
    task = StubCognitiveModel()._execution({"goalModel": {"domain": "go_backend"}}).tasks[0]
    payload = task.model_dump(by_alias=True)
    payload[field] = value

    with pytest.raises(ValidationError):
        ExecutionBlueprintTask.model_validate(payload)


def test_legacy_execution_projection_accepts_multi_day_work_package_minutes():
    model = StubCognitiveModel()
    execution = model._execution({"goalModel": {"domain": "go_backend"}})
    first = execution.tasks[0].model_copy(update={"estimated_minutes": 1800})
    execution = execution.model_copy(update={"tasks": [first, *execution.tasks[1:]]})
    strategy = model._strategy({"goalModel": {"domain": "go_backend"}})
    critique = model._critique()

    draft = execution_to_draft(execution, strategy, critique)

    assert draft.tasks[0].estimated_minutes == 1800


def test_blocked_critique_requires_an_explicit_blocker():
    valid = StubCognitiveModel()._critique().model_dump(by_alias=True)
    invalid = {
        **valid,
        "status": "blocked",
        "score": 92,
        "issues": [],
        "repairRequests": [],
        "calendarWritable": False,
    }

    with pytest.raises(ValidationError, match="must identify at least one blocker"):
        PlanCritiqueReport.model_validate(invalid)


def test_passed_critique_requires_calendar_writable():
    valid = StubCognitiveModel()._critique().model_dump(by_alias=True)
    invalid = {**valid, "calendarWritable": False}

    with pytest.raises(ValidationError, match="must be calendarWritable"):
        PlanCritiqueReport.model_validate(invalid)


@pytest.mark.parametrize("severity", ["major", "blocker"])
def test_passed_critique_rejects_major_and_blocker_issues(severity: str):
    valid = StubCognitiveModel()._critique().model_dump(by_alias=True)
    invalid = {
        **valid,
        "issues": [
            {
                "severity": severity,
                "description": "An unresolved high-severity finding remains.",
                "evidence": "task-1",
                "responsibleAgent": "execution_designer",
            }
        ],
    }

    with pytest.raises(ValidationError, match="cannot include major/blocker issues"):
        PlanCritiqueReport.model_validate(invalid)


def test_passed_critique_rejects_repair_requests():
    valid = StubCognitiveModel()._critique().model_dump(by_alias=True)
    invalid = {
        **valid,
        "repairRequests": [
            {
                "targetAgent": "execution_designer",
                "instruction": "Repair the unresolved concern.",
                "expectedChange": "The concern is resolved.",
            }
        ],
    }

    with pytest.raises(ValidationError, match="cannot include repair requests"):
        PlanCritiqueReport.model_validate(invalid)


def test_goal_agent_asks_domain_specific_high_value_questions(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="swim-thread", userInput="我要学游泳，零基础，每天一小时，达到熟练"))
    assert session.status == "needs_goal_clarification"
    assert session.goal_model["domain"] == "swimming"
    questions = [item["question"] for item in session.goal_model["questions"]]
    assert 1 <= len(questions) <= 3
    assert any("泳池" in item or "救生员" in item for item in questions)
    assert not any(term in json.dumps(session.goal_model, ensure_ascii=False) for term in ("README", "GitHub", "Python"))


def test_context_evidence_agent_can_veto_strategy_design(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=EvidenceBlockingModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="evidence-block", userInput="30天准备 Python AI 实习，每天3小时")
    )
    assert session.status == "needs_goal_clarification"
    assert session.evidence_pack["canProceedToStrategy"] is False
    assert session.strategy_portfolio is None
    assert session.user_need_contract.can_move_to_design is False
    assert "A safety-critical prerequisite is unknown." in session.user_need_contract.missing_information
    row = runtime.persistence.get_row(session.session_id)
    history = runtime.persistence.conversation(row)
    assert [turn.role for turn in history] == ["user", "assistant"]
    assert history[-1].content == "A safety-critical prerequisite is unknown."


def test_full_model_backed_flow_preserves_gates_and_source_keys(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="python-thread",
            userInput="零基础学 Python，每天3小时，30天准备 AI 应用实习，要有项目和可检查代码产出",
        )
    )
    assert session.status == "waiting_design_approval"
    assert session.strategy_portfolio
    assert session.execution_blueprint is None

    session = runtime.approve_design(session.session_id)
    assert session.status == "waiting_execution_approval"
    assert session.approved_strategy_id == session.strategy_portfolio["recommendedStrategyId"]
    assert session.execution_blueprint
    assert len(session.execution_blueprint["tasks"]) >= 8
    assert session.critique_report["status"] == "passed"
    assert session.execution_draft.quality_status == "passed"
    artifact_type_by_id = {artifact.id: artifact.artifact_type for artifact in session.artifacts}
    critic_decision = next(
        decision
        for decision in reversed(session.decisions)
        if decision.agent == "Independent Critic & Learning Agent" and decision.output_artifact_ids
    )
    assert {
        artifact_type_by_id[artifact_id]
        for artifact_id in critic_decision.input_artifact_ids
    } == {"user_goal_model", "evidence_pack", "strategy_portfolio", "execution_blueprint"}
    assert artifact_type_by_id[critic_decision.output_artifact_ids[0]] == "critique_report"
    strategy_approval = next(
        decision
        for decision in reversed(session.decisions)
        if decision.agent == "Strategy Architect Agent" and decision.decision == "approve"
    )
    assert [artifact_type_by_id[item] for item in strategy_approval.input_artifact_ids] == ["strategy_portfolio"]

    session = runtime.approve_execution(session.session_id)
    assert session.status == "ready_to_write_calendar"
    artifact_type_by_id = {artifact.id: artifact.artifact_type for artifact in session.artifacts}
    execution_approval = next(
        decision
        for decision in reversed(session.decisions)
        if decision.agent == "Execution Designer Agent" and decision.decision == "approve"
    )
    assert [artifact_type_by_id[item] for item in execution_approval.input_artifact_ids] == [
        "strategy_portfolio",
        "execution_blueprint",
        "critique_report",
    ]
    session = runtime.prepare_calendar_write(session.session_id)
    assert session.status == "waiting_calendar_write_approval"
    plan = runtime.execution_to_structured_plan(session)
    assert plan["milestones"][0]["tasks"][0]["sourceKey"] == f"planning-session:{session.session_id}:t0"
    assert {task for task, _ in model.calls} >= {
        "planning_goal_model",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
    }


def test_calendar_gate_requires_persisted_strategy_approval(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="approval-proof", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    session = runtime.approve_execution(session.session_id)
    assert session.status == "ready_to_write_calendar"
    with get_conn() as conn:
        conn.execute("UPDATE planning_sessions SET approved_strategy_id = '' WHERE id = ?", (session.session_id,))
    with pytest.raises(HTTPException) as exc_info:
        runtime.prepare_calendar_write(session.session_id)
    assert exc_info.value.status_code == 422


def test_critic_repair_loop_is_bounded_and_rechecks(isolated_db):
    model = StubCognitiveModel(first_critique_needs_repair=True)
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="repair-thread", userInput="两周做一个客服自动回复 Agent MVP，需要数据、评测和交付物"))
    session = runtime.approve_design(session.session_id)
    assert session.critique_report["status"] == "passed"
    assert session.cognitive_metadata.repair_count == 1
    assert model.critique_calls == 2
    execution_calls = [payload for task, payload in model.calls if task == "planning_execution"]
    # The initial candidate is single-pass; a Critic repair deliberately uses
    # Narrative -> Blueprint so the reasoning and concrete task fields are
    # recomputed together from the cumulative review history.
    assert len(execution_calls) == 3
    assert "previousExecutionBlueprint" not in execution_calls[0]
    assert all(
        payload.get("previousExecutionBlueprint", {}).get("tasks")
        for payload in execution_calls[1:]
    )
    assert all(payload.get("repairInstructions") for payload in execution_calls[1:])
    assert all(payload.get("previousCritiqueReport") for payload in execution_calls[1:])
    assert all(payload.get("repairHistory") for payload in execution_calls[1:])

    artifacts = runtime.agent_runtime.list_artifacts(session.session_id)
    executions = [item for item in artifacts if item.artifact_type == "execution_blueprint"]
    critiques = [item for item in artifacts if item.artifact_type == "critique_report"]
    assert len(executions) == len(critiques) == 2
    assert all(
        payload["previousExecutionBlueprint"] == executions[0].content_json
        for payload in execution_calls[1:]
    )
    critic_decisions = [
        item
        for item in runtime.agent_runtime.list_decisions(session.session_id)
        if item.agent == "Independent Critic & Learning Agent"
    ]
    assert len(critic_decisions) == 2
    reviewed_execution_ids: set[str] = set()
    produced_critique_ids: set[str] = set()
    for decision in critic_decisions:
        matching_execution_ids = {
            execution.id
            for execution in executions
            if execution.id in decision.input_artifact_ids
        }
        matching_critique_ids = {
            critique.id
            for critique in critiques
            if critique.id in decision.output_artifact_ids
        }
        assert len(matching_execution_ids) == 1
        assert len(matching_critique_ids) == 1
        reviewed_execution_ids.update(matching_execution_ids)
        produced_critique_ids.update(matching_critique_ids)
    assert reviewed_execution_ids == {execution.id for execution in executions}
    assert produced_critique_ids == {critique.id for critique in critiques}


@pytest.mark.parametrize(
    ("repair_target", "repeated_task"),
    [
        ("goal_modeling", "planning_goal_model"),
        ("context_evidence", "planning_evidence"),
        ("strategy_architect", "planning_strategy"),
    ],
)
def test_critic_routes_revision_to_responsible_cognitive_agent(isolated_db, repair_target, repeated_task):
    model = StubCognitiveModel(first_critique_needs_repair=True, repair_target=repair_target)
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId=f"repair-{repair_target}", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    assert session.critique_report["status"] == "passed"
    assert session.cognitive_metadata.repair_count == 1
    assert [task for task, _ in model.calls].count(repeated_task) == 2


def test_forbidden_template_task_is_never_calendar_writable(isolated_db):
    model = StubCognitiveModel(generic_task=True)
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="template-thread", userInput="零基础学 Python，每天3小时，30天找实习"))
    session = runtime.approve_design(session.session_id)
    assert session.status == "needs_goal_clarification"
    assert session.cognitive_metadata.planning_mode == "blocked_model_unavailable"
    assert session.execution_blueprint is None
    assert session.critique_report is None
    assert model.critique_calls == 0
    assert any(
        "deterministic preflight" in item.reason
        for item in runtime.agent_runtime.list_messages(session.session_id)
        if item.message_type == "block"
    )
    assert not [
        item
        for item in runtime.agent_runtime.list_artifacts(session.session_id)
        if item.artifact_type in {"execution_blueprint", "critique_report"}
    ]
    with pytest.raises(HTTPException) as exc_info:
        runtime.approve_execution(session.session_id)
    assert exc_info.value.status_code == 409


@pytest.mark.parametrize("issue", ["domain mismatch", "time mismatch", "resource mismatch"])
def test_independent_semantic_critic_blocks_mismatched_plans(isolated_db, issue):
    runtime = CognitivePlanningRuntime(model_client=BlockingCriticModel(issue))
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId=f"critic-{issue}", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    assert session.status == "execution_revision"
    assert session.critique_report["status"] == "blocked"
    assert session.critique_report["calendarWritable"] is False
    with pytest.raises(HTTPException) as approve_error:
        runtime.approve_execution(session.session_id)
    assert approve_error.value.status_code == 409
    with pytest.raises(HTTPException) as calendar_error:
        runtime.prepare_calendar_write(session.session_id)
    assert calendar_error.value.status_code == 409


def test_internally_inconsistent_critic_pass_is_normalized_and_blocked(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=InconsistentPassingCriticModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="critic-inconsistent", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    assert session.status == "execution_revision"
    assert session.critique_report["status"] == "blocked"
    assert session.critique_report["calendarWritable"] is False
    assert any("internally inconsistent" in item for item in session.critique_report["remainingRisks"])


@pytest.mark.parametrize(
    "model",
    [MajorIssuePassingCriticModel(), RepairRequestPassingCriticModel()],
    ids=["major-issue", "repair-request"],
)
def test_inconsistent_pass_from_model_copy_never_reaches_execution_approval(isolated_db, model):
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId=f"critic-inconsistent-{model.__class__.__name__}",
            userInput="Learn Go with a safe, feasible execution plan",
        )
    )

    session = runtime.approve_design(session.session_id)

    assert session.status == "execution_revision"
    assert session.critique_report["status"] == "blocked"
    assert session.critique_report["calendarWritable"] is False
    assert any(
        issue["severity"] == "blocker"
        for issue in session.critique_report["issues"]
    )
    assert any(
        "internally inconsistent" in risk
        for risk in session.critique_report["remainingRisks"]
    )


def test_critic_pass_without_calendar_permission_is_normalized_to_blocked(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=PassedButNotWritableCriticModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="critic-not-writable", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    assert session.status == "execution_revision"
    assert session.critique_report["status"] == "blocked"
    assert session.critique_report["calendarWritable"] is False
    assert any("not Calendar writable" in item for item in session.critique_report["remainingRisks"])


def test_session_context_reaches_evidence_research_policy(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO plans(id, date, time, content, estimated_minutes) VALUES (?, ?, ?, ?, ?)",
            ("calendar-constraint", "2026-07-12", "10:00", "Existing appointment", 90),
        )
    runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="context-thread",
            userInput="2026年9月去新疆14天，预算1万元，飞机，赛里木湖和喀纳斯",
            context={
                "date": "2026-07-10",
                "webResearchAllowed": True,
                "webResearchApproved": True,
                "freshnessRequired": True,
            },
        )
    )
    evidence_payload = next(payload for task, payload in model.calls if task == "planning_evidence")
    assert evidence_payload["researchPolicy"] == {
        "allowWebResearch": True,
        "requireUserApproval": False,
        "freshnessRequired": True,
        "sourceTypesAllowed": ["official_doc", "user_material", "local_catalog", "official_notice", "web_search"],
    }
    assert evidence_payload["calendarConstraints"] == [
        {"date": "2026-07-12", "statement": "10:00 Existing appointment (90 min)", "sourceId": "calendar-constraint"}
    ]


def test_evidence_agent_reads_memory_rag_and_planning_history_separately(isolated_db):
    memory = MemoryService()
    memory.create_memory(MemoryCreate(kind="preference", title="Python learning preference", content="Prefer guided Python project examples."))
    memory.create_memory(MemoryCreate(kind="review", title="Python review", content="Long Python theory sessions reduced completion."))
    memory.create_memory(MemoryCreate(kind="material", title="Python internship JD", content="Python FastAPI RAG testing requirements."))
    memory.create_memory(
        MemoryCreate(
            kind="planning_history",
            title="Prior Python plan",
            content="Previous Python internship plan was too dense.",
            source="ai",
            sourceKey="history:python",
        )
    )
    model = StubCognitiveModel()
    CognitivePlanningRuntime(model_client=model).create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="memory-evidence", userInput="30天准备 Python AI 应用实习，每天3小时")
    )
    evidence_payload = next(payload for task, payload in model.calls if task == "planning_evidence")
    kinds = {item["kind"] for item in evidence_payload["existingMaterials"]}
    assert {"preference", "review", "material", "planning_history"} <= kinds


def test_web_research_requires_explicit_approval(isolated_db):
    model = StubCognitiveModel()
    goal = model._goal({"conversationHistory": [{"role": "user", "content": "2026年9月去新疆14天"}]})
    provider = FakeWebResearchProvider()
    agent = ContextEvidenceAgent(model=model, research=ResourceResearch(provider))
    agent.run(goal, web_research_allowed=True, web_research_approved=False, freshness_required=True)
    assert provider.calls == []
    agent.run(goal, web_research_allowed=True, web_research_approved=True, freshness_required=True)
    assert len(provider.calls) == 1
    evidence_payload = [payload for task, payload in model.calls if task == "planning_evidence"][-1]
    assert any(item.get("title") == "Current official travel notice" for item in evidence_payload["resourceCandidates"])


def test_feedback_persists_tentative_hypothesis_and_next_evidence_reads_it(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    first = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="learn-thread", userInput="零基础学 Python，每天3小时，30天找实习"))
    first = runtime.approve_design(first.session_id)
    runtime.revise_execution(first.session_id, PlanningSessionTextRequest(text="这个资料太理论，看不懂"))
    hypotheses = PlanningHypothesisRepository().relevant("python_career")
    assert hypotheses
    assert hypotheses[0].status == "tentative"

    model.calls.clear()
    runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId="next-thread", userInput="继续准备 Python AI 应用实习，做一个可展示项目"))
    evidence_calls = [payload for task, payload in model.calls if task == "planning_evidence"]
    assert evidence_calls
    assert any("guided examples" in str(item.get("statement")) for item in evidence_calls[-1]["planningHypotheses"])


def test_design_feedback_runs_learning_before_strategy_revision(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="design-feedback", userInput="30天准备 Python AI 实习，每天3小时")
    )
    revised = runtime.revise_design(
        session.session_id,
        PlanningSessionTextRequest(text="这个方向不对，我更重视可展示的后端项目"),
    )
    assert revised.status == "waiting_design_approval"
    assert revised.approved_strategy_id is None
    assert revised.execution_blueprint is None
    assert revised.planning_learning_update["diagnosis"]["failureStage"] == "strategy"
    assert revised.cognitive_metadata.repair_count == 0
    assert [task for task, _ in model.calls].count("planning_learning") == 1
    assert [task for task, _ in model.calls].count("planning_strategy") == 2


def test_feedback_patch_is_consumed_once_without_using_critic_repair_budget(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="feedback-once", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    before_execution_calls = [task for task, _ in model.calls].count("planning_execution")
    updated = runtime.revise_execution(session.session_id, PlanningSessionTextRequest(text="资料太理论，请换成引导示例"))
    after_execution_calls = [task for task, _ in model.calls].count("planning_execution")
    assert updated.status == "waiting_execution_approval"
    assert updated.cognitive_metadata.repair_count == 0
    # Feedback is routed once. Execution generation may use its single bounded
    # internal preflight-repair call before the independent Critic review.
    assert 1 <= after_execution_calls - before_execution_calls <= 2
    assert [task for task, _ in model.calls].count("planning_learning") == 1


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("executionPlan", "execution_blueprint"),
        ("execution_plan", "execution_blueprint"),
        ("strategyPlan", "strategy_portfolio"),
        ("evidencePack", "evidence_pack"),
        ("realityAssessment", "reality_assessment"),
        ("userGoalModel", "user_goal_model"),
    ],
)
def test_feedback_patch_target_aliases_are_normalized(target, expected):
    assert _normalize_learning_patch_target(target) == expected


def test_execution_plan_feedback_alias_routes_to_execution_revision(isolated_db):
    class ExecutionPlanFeedbackModel(StubCognitiveModel):
        def _learning(self, payload: dict[str, Any]) -> PlanningLearningUpdate:
            update = super()._learning(payload)
            assert update.current_plan_patch is not None
            return update.model_copy(
                update={
                    "current_plan_patch": update.current_plan_patch.model_copy(
                        update={"target_artifact": "executionPlan"}
                    )
                }
            )

    model = ExecutionPlanFeedbackModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="feedback-execution-plan-alias",
            userInput="30天准备 Python AI 实习，每天2小时",
        )
    )
    session = runtime.approve_design(session.session_id)
    before_execution_calls = [task for task, _ in model.calls].count("planning_execution")

    revised = runtime.revise_execution(
        session.session_id,
        PlanningSessionTextRequest(text="资料太理论，请换成引导示例"),
    )

    assert revised.status == "waiting_execution_approval"
    assert revised.runtime_status == "idle"
    assert revised.cognitive_metadata.repair_count == 0
    after_execution_calls = [task for task, _ in model.calls].count("planning_execution")
    assert 1 <= after_execution_calls - before_execution_calls <= 2


def test_user_feedback_resets_repair_budget_for_the_new_draft(isolated_db):
    model = StubCognitiveModel(first_critique_needs_repair=True)
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="repair-reset", userInput="30天准备 Python AI 实习，每天3小时")
    )
    session = runtime.approve_design(session.session_id)
    assert session.cognitive_metadata.repair_count == 1
    revised = runtime.revise_execution(session.session_id, PlanningSessionTextRequest(text="资料太理论，请换成引导示例"))
    assert revised.status == "waiting_execution_approval"
    assert revised.cognitive_metadata.repair_count == 0


def test_hypothesis_requires_distinct_evidence_and_conflict_lowers_status(isolated_db):
    repository = PlanningHypothesisRepository()
    first = UserModelHypothesisDraft(
        rule="Prefer guided examples before dense documentation.",
        domainScope=["python_career"],
        evidence="feedback-1",
        confidence=0.72,
    )
    hypothesis = repository.upsert(first, positive=True)
    assert hypothesis.status == "tentative"
    assert repository.relevant("travel") == []
    duplicate = repository.upsert(first, positive=True)
    assert duplicate.evidence_count == 1
    confirmed = repository.upsert(
        first.model_copy(update={"evidence": "feedback-2", "domain_scope": ["python_career", "software_project"]}),
        positive=True,
    )
    assert confirmed.status == "confirmed"
    assert confirmed.evidence_count == 2
    conflicted = repository.upsert(
        first.model_copy(update={"evidence": "feedback-3"}),
        positive=False,
    )
    assert conflicted.status == "conflicted"
    assert conflicted.confidence < confirmed.confidence
    assert set(conflicted.domain_scope) == {"python_career", "software_project"}


def test_expired_hypothesis_is_removed_from_relevant_results(isolated_db):
    repository = PlanningHypothesisRepository()
    expired = repository.upsert(
        UserModelHypothesisDraft(
            rule="Temporary evening-only schedule.",
            domainScope=["all"],
            evidence="temporary constraint",
            confidence=0.8,
            expiresAt="2000-01-01T00:00:00Z",
        ),
        positive=True,
    )
    assert expired.status == "tentative"
    assert repository.relevant("python_career") == []
    with get_conn() as conn:
        status = conn.execute("SELECT status FROM user_planning_hypotheses WHERE id = ?", (expired.id,)).fetchone()["status"]
    assert status == "expired"


@pytest.mark.parametrize(
    ("prompt", "expected_domain", "forbidden_term"),
    [
        ("2026年9月去新疆14天，预算1万元，飞机，赛里木湖和喀纳斯", "travel", "README"),
        ("想减脂，每周4次，膝盖旧伤", "fitness", "GitHub"),
        ("两个月准备英语四级，当前约350分，每天90分钟", "exam", "FastAPI"),
        ("两周做一个客服自动回复 Agent MVP，要有数据和评测", "software_project", "泳池"),
    ],
)
def test_cross_domain_golden_scenarios_do_not_share_templates(isolated_db, prompt, expected_domain, forbidden_term):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", threadId=f"thread-{expected_domain}", userInput=prompt))
    assert session.goal_model["domain"] == expected_domain
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(
        {"goal": session.goal_model, "strategy": session.strategy_portfolio, "execution": session.execution_blueprint},
        ensure_ascii=False,
    )
    assert forbidden_term not in visible
    assert session.critique_report["status"] == "passed"


def test_swimming_golden_scenario_keeps_safety_and_measurable_outcome(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitivePlanningRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="swim-golden", userInput="我要学游泳，零基础，每天1小时，达到熟练")
    )
    assert session.status == "needs_goal_clarification"
    session = runtime.clarify(
        session.session_id,
        PlanningSessionTextRequest(text="我能稳定去有救生员和教练的泳池，目标是安全连续游50米"),
    )
    assert session.status == "waiting_design_approval"
    goal_payloads = [payload for task_type, payload in model.calls if task_type == "planning_goal_model"]
    assert len(goal_payloads) == 2
    follow_up_history = goal_payloads[-1]["conversationHistory"]
    assert [turn["role"] for turn in follow_up_history] == ["user", "assistant", "user"]
    assert "泳池" in follow_up_history[1]["content"]
    assert "安全连续游50米" in follow_up_history[2]["content"]
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(session.execution_blueprint, ensure_ascii=False)
    assert all(term not in visible for term in ("README", "GitHub", "FastAPI"))
    assert all(term in visible for term in ("supervised", "safe", "50"))
    assert any(item["domainExtensions"].get("qualifiedSupervision") for item in session.execution_blueprint["tasks"])


def test_python_career_golden_scenario_has_portfolio_and_interview_evidence(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="python-golden",
            userInput="零基础学 Python，每天3小时，目标是找 AI 应用实习，计划30天",
        )
    )
    session = runtime.approve_design(session.session_id)
    tasks = session.execution_blueprint["tasks"]
    visible = json.dumps(tasks, ensure_ascii=False).lower()
    assert len(tasks) >= 8
    assert all(item["estimatedMinutes"] <= 180 for item in tasks)
    assert sum(item["estimatedMinutes"] for item in tasks) >= 1200
    assert all(term in visible for term in ("python", "github", "readme", "resume", "interview"))
    assert any("tests" in str(item["deliverable"]).lower() for item in tasks)


def test_travel_golden_scenario_exposes_freshness_route_and_budget(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="travel-golden",
            userInput="2026年9月去新疆14天，预算1万元，飞机，赛里木湖和喀纳斯",
        )
    )
    assert any(item["proposedResolution"] == "web_research" for item in session.evidence_pack["gaps"])
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(session.execution_blueprint, ensure_ascii=False).lower()
    assert all(term in visible for term in ("route", "budget", "booking", "weather"))
    assert "readme" not in visible
    assert any(item["domainExtensions"].get("budgetCny") == 10000 for item in session.execution_blueprint["tasks"])


def test_fitness_golden_scenario_prioritizes_knee_safety_and_professional_boundary(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="fitness-golden", userInput="想减脂，每周4次，膝盖旧伤")
    )
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(session.execution_blueprint, ensure_ascii=False).lower()
    assert all(term in visible for term in ("knee", "professional", "low-impact"))
    assert "high-impact" not in visible
    assert all(item["domainExtensions"].get("stopOnPain") for item in session.execution_blueprint["tasks"])


def test_exam_golden_scenario_allocates_sections_and_mock_review(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="exam-golden", userInput="两个月准备英语四级，当前约350分，每天90分钟")
    )
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(session.execution_blueprint, ensure_ascii=False).lower()
    assert all(term in visible for term in ("listening", "reading", "writing", "mock", "error"))
    assert all(item["estimatedMinutes"] <= 150 for item in session.execution_blueprint["tasks"])


def test_mvp_golden_scenario_has_scope_data_evaluation_risk_and_delivery(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="mvp-golden", userInput="两周做一个客服自动回复 Agent MVP")
    )
    session = runtime.approve_design(session.session_id)
    visible = json.dumps(session.execution_blueprint, ensure_ascii=False).lower()
    assert all(term in visible for term in ("scope", "dataset", "evaluation", "risk", "demo"))
    assert all(item["domainExtensions"].get("mvp") for item in session.execution_blueprint["tasks"])


def test_five_cognitive_agents_run_as_independent_contract_boundaries(isolated_db):
    model = StubCognitiveModel()
    goal_result = GoalModelingAgent(model).run(
        GoalModelingInput(
            conversationHistory=[ConversationTurn(role="user", content="30天准备 Python AI 应用实习，每天3小时")],
            preExtractedFacts={"duration": "30 days"},
        )
    )
    evidence_result = ContextEvidenceAgent(model=model).run(goal_result.artifact)
    strategy_result = StrategyArchitectAgent(model).run(goal_result.artifact, evidence_result.artifact)
    selected = strategy_result.artifact.strategies[0]
    execution_result = ExecutionDesignerAgent(model).run(goal_result.artifact, evidence_result.artifact, selected)
    critique_result = CriticLearningAgent(model).critique(
        goal_result.artifact,
        evidence_result.artifact,
        strategy_result.artifact,
        execution_result.artifact,
    )
    assert goal_result.artifact.goal_statement
    assert evidence_result.artifact.planning_rules
    assert strategy_result.artifact.user_decision.question
    assert execution_result.artifact.tasks[0].completion_evidence
    assert execution_result.model_usage["promptTokens"] == 100
    assert execution_result.model_usage["completionTokens"] == 50
    assert execution_result.model_usage["totalTokens"] == 150
    assert execution_result.model_usage["latencyMs"] == 5
    assert len(execution_result.model_usage["attempts"]) == 1
    assert execution_result.model_usage["generationMode"] == "single_pass"
    assert critique_result.artifact.calendar_writable is True
    assert [task for task, _ in model.calls] == [
        "planning_goal_model",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
    ]


def _evidence_authority_goal() -> UserGoalModel:
    return UserGoalModel(
        goalStatement="Learn Python for independent data-analysis projects",
        desiredChange="Independently complete a data-analysis project",
        domain="python_data_analysis",
        knownFacts=[
            KnownFact(
                key="purpose",
                statement="The current purpose is data analysis",
                sourceText="数据分析",
                confidence=1,
            )
        ],
        decisionRelevantUnknowns=[
            DecisionRelevantUnknown(
                key="dataset_domain",
                description="The preferred dataset domain is not specified",
                whyItChangesThePlan="It can change the example project but not the learning strategy",
                impact="strategy",
                priority="important",
            )
        ],
        successModel=GoalSuccessModel(
            definition="Complete one reviewable data-analysis project independently",
            measurableSignals=["A reproducible analysis and written findings exist"],
        ),
        confidence=0.95,
        canProceedToEvidence=True,
    )


def _evidence_pack_with_gap(gap: EvidenceGap, *, can_proceed: bool = False) -> EvidencePack:
    return EvidencePack(
        gaps=[gap],
        synthesis="The supplied context contains one unresolved item.",
        confidence=0.8,
        canProceedToStrategy=can_proceed,
    )


def test_evidence_authority_policy_is_backward_compatible_and_fails_closed_without_blocker_provenance():
    goal = _evidence_authority_goal()
    legacy_payload = EvidenceInput(goalModel=goal, memoryQueryContext="python data analysis")
    assert legacy_payload.authority_policy.current_goal_is_authoritative is True
    assert legacy_payload.authority_policy.blocking_unknown_priorities == ["blocking"]
    assert legacy_payload.authority_policy.non_blocking_unknown_priorities == ["important", "optional"]

    stale_gap = EvidenceGap(
        description="Older planning history says the purpose may be web development",
        consequence="The old context conflicts with the current Goal",
        proposedResolution="ask_user",
    )
    normalized = apply_evidence_authority_policy(
        goal,
        _evidence_pack_with_gap(stale_gap),
        EvidenceAuthorityPolicy(),
    )
    assert normalized.can_proceed_to_strategy is False
    assert normalized.gaps[0].proposed_resolution == "make_explicit_assumption"


def test_evidence_authority_moves_historical_conflicts_to_superseded_context():
    goal = _evidence_authority_goal()
    historical_gap = EvidenceGap(
        description="A prior plan assumed web development",
        consequence="It contradicts the current data-analysis Goal",
        proposedResolution="ask_user",
        sourceContext="historical_context",
    )
    normalized = apply_evidence_authority_policy(
        goal,
        _evidence_pack_with_gap(historical_gap),
        EvidenceAuthorityPolicy(),
    )
    assert normalized.can_proceed_to_strategy is True
    assert normalized.gaps == []
    assert normalized.superseded_context == ["A prior plan assumed web development"]


def test_evidence_authority_preserves_supported_new_safety_blockers():
    goal = _evidence_authority_goal()
    safety_gap = EvidenceGap(
        description="The supplied runtime is executing untrusted notebooks without isolation",
        consequence="Executing the project could expose local credentials",
        proposedResolution="ask_user",
        blockingBasis="new_evidence",
        impact="safety",
        sourceContext="new_evidence",
        supportingSourceIds=["runtime:sandbox-inspection"],
    )
    normalized = apply_evidence_authority_policy(
        goal,
        _evidence_pack_with_gap(safety_gap, can_proceed=True),
        EvidenceAuthorityPolicy(),
        valid_evidence_source_ids={"runtime:sandbox-inspection"},
    )
    assert normalized.can_proceed_to_strategy is False
    assert normalized.gaps[0].proposed_resolution == "ask_user"


def test_evidence_authority_drops_every_unattributed_or_forged_decision_input():
    goal = _evidence_authority_goal()
    poison = "POISONED legacy recommendation"
    evidence = EvidencePack(
        userEvidence=[
            UserEvidence(
                kind="review",
                statement=poison,
                whyRelevant="No provenance",
                confidence=0.9,
            )
        ],
        planningRules=[
            EvidencePlanningRule(
                rule=poison,
                strength="soft",
                evidence=[],
                confidence=0.9,
            )
        ],
        domainEvidence=[
            DomainEvidence(
                claim=poison,
                sourceType="unknown",
                relevance="No provenance",
                credibility=0.9,
            )
        ],
        resourceCandidates=[
            EvidenceResourceCandidate(
                title=poison,
                type="official_doc",
                howItHelps="Claims to be external without a source",
                userFit="Unknown",
                credibility=0.9,
                sourceContext="model_knowledge",
            )
        ],
        gaps=[
            EvidenceGap(
                description=poison,
                consequence="Unknown",
                proposedResolution="make_explicit_assumption",
                blockingBasis="new_evidence",
                impact="safety",
                sourceContext="new_evidence",
                supportingSourceIds=["forged:source"],
            )
        ],
        synthesis=poison,
        confidence=0.8,
        canProceedToStrategy=True,
    )
    normalized = apply_evidence_authority_policy(
        goal,
        evidence,
        EvidenceAuthorityPolicy(),
        valid_evidence_source_ids={"real:source"},
    )
    assert normalized.is_authority_normalized is True
    assert all(item.source_context == "current_goal" for item in normalized.user_evidence)
    assert normalized.planning_rules == []
    assert normalized.domain_evidence == []
    assert normalized.resource_candidates == []
    assert normalized.gaps == []
    assert poison not in json.dumps(normalized.decision_view().model_dump(by_alias=True))


def _remove_evidence_authority_marker(session_id: str) -> None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT evidence_pack_json FROM planning_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        evidence = json.loads(row["evidence_pack_json"])
        evidence.pop("authorityPolicyVersion", None)
        conn.execute(
            "UPDATE planning_sessions SET evidence_pack_json = ? WHERE id = ?",
            (json.dumps(evidence, ensure_ascii=False), session_id),
        )


def test_legacy_evidence_projection_is_quarantined_and_continue_reruns_evidence(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="legacy-evidence-resume",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    old_artifact_count = len(
        [item for item in runtime.agent_runtime.list_artifacts(session.session_id) if item.artifact_type == "evidence_pack"]
    )
    _remove_evidence_authority_marker(session.session_id)

    quarantined = runtime.get_session(session.session_id)
    assert quarantined.status == "MODEL_UNAVAILABLE"
    assert quarantined.model_failure is not None
    assert quarantined.model_failure.resume_node == "evidence"
    assert quarantined.evidence_pack is None
    assert quarantined.strategy_portfolio is None

    model.calls.clear()
    resumed = runtime.continue_current_stage(session.session_id)
    assert resumed.status == "waiting_design_approval"
    assert resumed.evidence_pack["authorityPolicyVersion"] == EVIDENCE_AUTHORITY_POLICY_VERSION
    assert resumed.approved_strategy_id is None
    assert [task for task, _payload in model.calls] == ["planning_evidence", "planning_strategy"]
    evidence_artifacts = [
        item for item in runtime.agent_runtime.list_artifacts(session.session_id) if item.artifact_type == "evidence_pack"
    ]
    assert len(evidence_artifacts) == old_artifact_count + 1


def test_legacy_evidence_approval_does_not_record_stale_strategy_approval(isolated_db):
    runtime = CognitiveOSRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="legacy-evidence-approval",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    _remove_evidence_authority_marker(session.session_id)

    refreshed = runtime.approve_design(session.session_id)
    assert refreshed.status == "waiting_design_approval"
    assert refreshed.approved_strategy_id is None


def test_legacy_refresh_strategy_failure_cannot_reexpose_old_downstream_artifacts(isolated_db):
    class StrategyFailureModel(StubCognitiveModel):
        def complete_contract(self, *, task_type: str, **kwargs: Any):
            if task_type == "planning_strategy":
                raise PlanningModelUnavailable(
                    "strategy_design",
                    SafePlanningError(
                        stage="strategy_design",
                        errorType="timeout",
                        message="strategy model timed out",
                        retryable=True,
                        attempts=[],
                    ),
                )
            return super().complete_contract(task_type=task_type, **kwargs)

    runtime = CognitiveOSRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="legacy-evidence-strategy-failure",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    _remove_evidence_authority_marker(session.session_id)
    failure_model = StrategyFailureModel()
    runtime.evidence_agent.model = failure_model
    runtime.strategy_agent.model = failure_model

    blocked = runtime.continue_current_stage(session.session_id)
    assert blocked.model_failure is not None
    assert blocked.model_failure.resume_node == "strategy"
    assert blocked.evidence_pack is not None
    assert blocked.strategy_portfolio is None
    assert blocked.execution_blueprint is None
    assert blocked.critique_report is None
    assert blocked.approved_strategy_id is None


def test_legacy_authority_refresh_overrides_pending_strategy_checkpoint(isolated_db):
    class InitialStrategyFailureModel(StubCognitiveModel):
        def complete_contract(self, *, task_type: str, **kwargs: Any):
            if task_type == "planning_strategy":
                raise PlanningModelUnavailable(
                    "strategy_design",
                    SafePlanningError(
                        stage="strategy_design",
                        errorType="timeout",
                        message="strategy model timed out",
                        retryable=True,
                        attempts=[],
                    ),
                )
            return super().complete_contract(task_type=task_type, **kwargs)

    runtime = CognitiveOSRuntime(model_client=InitialStrategyFailureModel())
    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="legacy-after-strategy-failure",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    assert blocked.model_failure is not None
    assert blocked.model_failure.resume_node == "strategy"
    assert runtime.harness.repository.recover(blocked.session_id).pending_agent == "strategy"

    _remove_evidence_authority_marker(blocked.session_id)
    healthy_model = StubCognitiveModel()
    runtime.evidence_agent.model = healthy_model
    runtime.strategy_agent.model = healthy_model

    resumed = runtime.continue_current_stage(blocked.session_id)
    assert [task for task, _payload in healthy_model.calls] == [
        "planning_evidence",
        "planning_strategy",
    ]
    assert resumed.status == "waiting_design_approval"
    assert resumed.evidence_pack["authorityPolicyVersion"] == EVIDENCE_AUTHORITY_POLICY_VERSION


def test_both_evidence_agents_apply_the_same_authority_gate(isolated_db):
    class FixedEvidenceModel:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def complete_contract(self, **kwargs: Any):
            self.calls.append(kwargs)
            return AgentResult(
                _evidence_pack_with_gap(
                    EvidenceGap(
                        description="The important dataset preference is unanswered",
                        consequence="Examples may need a different domain",
                        proposedResolution="ask_user",
                        blockingBasis="goal_unknown",
                        relatedGoalUnknownKey="dataset_domain",
                        sourceContext="current_goal",
                    )
                ),
                _usage("planning_evidence"),
            )

    goal = _evidence_authority_goal()
    reality = RealityAssessment(
        goalRestatement=goal.goal_statement,
        feasibilitySummary="Feasible",
        timeAssessment="Adequate",
        resourceAssessment="Adequate",
        confidence=0.9,
        canProceedToEvidence=True,
    )
    models = [FixedEvidenceModel(), FixedEvidenceModel()]
    results = [
        ContextEvidenceAgent(model=models[0]).run(goal),
        CognitiveEvidenceAgent(model=models[1]).run(goal, reality),
    ]

    for model, result in zip(models, results, strict=True):
        assert result.artifact.can_proceed_to_strategy is True
        assert result.artifact.gaps[0].proposed_resolution == "make_explicit_assumption"
        assert model.calls[0]["payload"]["authorityPolicy"]["currentGoalIsAuthoritative"] is True
        assert "current goalModel is authoritative" in model.calls[0]["system"]

    assert "current goalModel as authoritative" in REALITY_SYSTEM


def test_memory_retrievers_preserve_provenance_and_minimize_historical_content(isolated_db):
    memory = MemoryService()
    review = memory.create_memory(
        MemoryCreate(
            kind="review",
            title="Prior Python review",
            content="A prior session assumed zero experience and seven hours per week.",
            source="ai",
            sourceId="prior-session",
            sourceKey="review:prior-session",
            metadata={
                "planningSessionId": "prior-session",
                "structuredPlan": {"tasks": ["must not enter model context"]},
                "longTermLearning": "must not enter model context",
            },
        )
    )
    history = memory.create_memory(
        MemoryCreate(
            kind="planning_history",
            title="Prior Python plan",
            content="A prior plan used a different language and learning target.",
            source="ai",
            sourceId="prior-plan",
            sourceKey="history:prior-plan",
            metadata={
                "planningSessionId": "prior-session",
                "structuredPlan": {"tasks": ["must not enter model context"]},
            },
        )
    )
    goal = _evidence_authority_goal()

    review_doc = next(item for item in CognitiveMemoryRetriever(memory).retrieve(goal) if item.id == review.id)
    history_doc = next(item for item in PlanningHistoryRetriever(memory).retrieve(goal) if item.id == history.id)
    for document in (review_doc, history_doc):
        assert document.context_role == "historical_context"
        assert document.content == ""
        assert document.source == "ai"
        assert document.source_key
        assert document.metadata["planningSessionId"] == "prior-session"
        assert "structuredPlan" not in document.metadata
        assert "longTermLearning" not in document.metadata


def test_historical_gap_sources_are_superseded_and_mixed_sources_cannot_authorize_blocker():
    goal = _evidence_authority_goal()
    historical_only = EvidenceGap(
        description="An older session requested a different learning path",
        consequence="It conflicts with the current Goal",
        proposedResolution="ask_user",
        blockingBasis="new_evidence",
        impact="feasibility",
        sourceContext="unspecified",
        supportingSourceIds=["history:one"],
    )
    superseded = apply_evidence_authority_policy(
        goal,
        _evidence_pack_with_gap(historical_only),
        EvidenceAuthorityPolicy(),
        historical_source_ids={"history:one"},
        valid_evidence_source_ids={"current:one"},
    )
    assert superseded.can_proceed_to_strategy is True
    assert superseded.gaps == []

    mixed = historical_only.model_copy(
        update={
            "source_context": "new_evidence",
            "supporting_source_ids": ["history:one", "current:one"],
        }
    )
    rejected = apply_evidence_authority_policy(
        goal,
        _evidence_pack_with_gap(mixed),
        EvidenceAuthorityPolicy(),
        historical_source_ids={"history:one"},
        valid_evidence_source_ids={"current:one"},
    )
    assert rejected.can_proceed_to_strategy is True
    assert rejected.gaps == []


def test_stale_review_evidence_is_removed_before_strategy_payload():
    goal = _evidence_authority_goal()
    stale_source_id = "review:other-session"
    evidence = EvidencePack(
        userEvidence=[
            UserEvidence(
                sourceId=stale_source_id,
                kind="review",
                statement="The user has zero programming experience and seven hours per week",
                whyRelevant="Copied from an older session",
                confidence=0.9,
                sourceContext="historical_context",
            )
        ],
        planningRules=[
            EvidencePlanningRule(
                rule="Design for a zero-experience learner with seven hours per week",
                strength="hard",
                evidence=["older review"],
                sourceIds=[stale_source_id],
                sourceContext="historical_context",
                confidence=0.9,
            )
        ],
        domainEvidence=[
            DomainEvidence(
                claim="A prior Go plan should determine the project language",
                sourceType="planning_history",
                sourceRef=stale_source_id,
                sourceContext="historical_context",
                relevance="Copied from another session",
                credibility=0.7,
            )
        ],
        gaps=[
            EvidenceGap(
                description="The preferred dataset domain remains open",
                consequence="Only the example branch changes",
                proposedResolution="ask_user",
                blockingBasis="goal_unknown",
                relatedGoalUnknownKey="dataset_domain",
                sourceContext="current_goal",
            )
        ],
        synthesis="The user is a zero-experience Go learner with seven hours per week.",
        confidence=0.8,
        canProceedToStrategy=False,
    )
    normalized = apply_evidence_authority_policy(
        goal,
        evidence,
        EvidenceAuthorityPolicy(),
        historical_source_ids={stale_source_id},
    )
    assert normalized.can_proceed_to_strategy is True

    model = StubCognitiveModel()
    StrategyArchitectAgent(model).run(goal, normalized)
    strategy_payload = next(payload for task, payload in model.calls if task == "planning_strategy")
    visible = json.dumps(strategy_payload, ensure_ascii=False)
    assert "zero programming experience" not in visible
    assert "seven hours" not in visible
    assert "prior Go plan" not in visible
    assert "data analysis" in visible
    assert stale_source_id not in visible


def test_superseded_context_never_reenters_any_downstream_model_payload():
    stale_text = "AUDIT-ONLY stale plan: zero experience, seven hours, Go project"
    goal = _evidence_authority_goal()
    evidence = EvidencePack(
        synthesis="Current Goal evidence is authoritative.",
        supersededContext=[stale_text],
        confidence=0.9,
        canProceedToStrategy=True,
        authorityPolicyVersion=EVIDENCE_AUTHORITY_POLICY_VERSION,
    )

    compatibility_model = StubCognitiveModel()
    strategy = StrategyArchitectAgent(compatibility_model).run(goal, evidence).artifact
    execution = ExecutionDesignerAgent(compatibility_model).run(
        goal, evidence, strategy.strategies[0]
    ).artifact
    critique = CriticLearningAgent(compatibility_model).critique(
        goal, evidence, strategy, execution
    ).artifact
    CriticLearningAgent(compatibility_model).learn(
        "Use a smaller example",
        goal=goal,
        evidence=evidence,
        strategy=strategy,
        execution=execution,
        critique=critique,
    )

    cognitive_model = StubCognitiveModel()
    cognitive_strategy = CognitiveStrategyAgent(cognitive_model).run(goal, evidence).artifact
    cognitive_execution = CognitiveExecutionAgent(cognitive_model).run(
        goal, evidence, cognitive_strategy.strategies[0]
    ).artifact
    cognitive_critique = CognitiveCriticAgent(cognitive_model).critique(
        goal, evidence, cognitive_strategy, cognitive_execution
    ).artifact
    CognitiveCriticAgent(cognitive_model).learn(
        "Use a smaller example",
        goal=goal,
        evidence=evidence,
        strategy=cognitive_strategy,
        execution=cognitive_execution,
        critique=cognitive_critique,
    )

    for task, payload in [*compatibility_model.calls, *cognitive_model.calls]:
        if task in {
            "planning_strategy",
            "planning_execution",
            "planning_critique",
            "planning_learning",
        }:
            assert stale_text not in json.dumps(payload, ensure_ascii=False)


def test_cognitive_graph_contains_all_human_wait_and_critic_nodes(isolated_db):
    graph = build_cognitive_graph(CognitivePlanningRuntime(model_client=StubCognitiveModel())).get_graph()
    assert {
        "session_guard",
        "goal_modeling",
        "wait_for_goal_answer",
        "context_evidence",
        "strategy_architect",
        "wait_for_strategy_approval",
        "execution_designer",
        "independent_critic",
        "repair_router",
        "wait_for_execution_approval",
        "feedback_learning",
        "calendar_gate",
    } <= set(graph.nodes)


@pytest.mark.parametrize(
    ("artifact_type", "owner", "wrong_owner"),
    [
        ("user_goal_model", "Goal Modeling Agent", "Execution Designer Agent"),
        ("evidence_pack", "Context & Evidence Agent", "Goal Modeling Agent"),
        ("strategy_portfolio", "Strategy Architect Agent", "Context & Evidence Agent"),
        ("execution_blueprint", "Execution Designer Agent", "Strategy Architect Agent"),
        ("critique_report", "Independent Critic & Learning Agent", "Execution Designer Agent"),
        ("planning_learning_update", "Independent Critic & Learning Agent", "Goal Modeling Agent"),
    ],
)
def test_cognitive_artifact_ownership_rejects_cross_agent_writes(isolated_db, artifact_type, owner, wrong_owner):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="ownership-thread", userInput="30天准备 Python AI 实习，每天3小时")
    )
    with pytest.raises(ValueError, match=f"owner is {owner}"):
        PlanningAgentRuntime().record_artifact(
            session.session_id,
            owner_agent=wrong_owner,
            artifact_type=artifact_type,
            content=session.goal_model,
        )


def test_shadow_runner_compares_and_persists_legacy_template_leakage(isolated_db):
    runtime = CognitivePlanningRuntime(model_client=StubCognitiveModel())
    cognitive = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="shadow-source", userInput="30天准备 Python AI 实习，每天3小时")
    )
    cognitive = runtime.approve_design(cognitive.session_id)
    draft = cognitive.execution_draft
    legacy_task = draft.tasks[0].model_copy(update={"title": "学习并复现"})
    legacy = cognitive.model_copy(
        update={
            "session_id": "legacy-shadow-session",
            "execution_draft": draft.model_copy(update={"tasks": [legacy_task, *draft.tasks[1:]]}),
            "cognitive_metadata": None,
            "goal_model": None,
            "evidence_pack": None,
            "strategy_portfolio": None,
            "execution_blueprint": None,
            "critique_report": None,
        }
    )
    runner = CognitivePlanningShadowRunner(
        legacy=FixedSessionCreator(legacy),
        cognitive=FixedSessionCreator(cognitive),
    )
    comparison = runner.run_create(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="shadow-run", userInput="30天准备 Python AI 实习，每天3小时")
    )
    assert comparison.cognitive_planning_mode == "model_backed"
    assert comparison.cognitive_critic_status == "passed"
    assert "学习并复现" in comparison.forbidden_template_hits
    with get_conn() as conn:
        row = conn.execute("SELECT comparison_json FROM planning_shadow_runs").fetchone()
    assert row and "legacy-shadow-session" in row["comparison_json"]


def test_p_mode_streams_cognitive_artifacts_without_legacy_runtime(client, monkeypatch):
    model = StubCognitiveModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    response = client.post(
        "/api/command/chat",
        json={
            "message": "零基础学 Python，每天3小时，30天准备 AI 应用实习，要有项目和代码产出",
            "mode": "auto",
            "permission": "low",
            "context": {"date": date.today().isoformat()},
        },
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    event_types = {item["type"] for item in events}
    assert {
        "planning_session_started",
        "goal_model_updated",
        "evidence_pack_ready",
        "strategy_portfolio_ready",
        "planning_session_status",
    } <= event_types
    assert "execution_blueprint_ready" not in event_types
    assert "runtime_started" not in event_types
    assert "runtime_event" not in event_types
    assert "draft_created" not in event_types
    assert next(item for item in events if item["type"] == "planning_session_status")["status"] == "waiting_design_approval"
    thread_id = events[-1]["threadId"]
    replay = client.get(f"/api/command/thread/{thread_id}")
    assert replay.status_code == 200
    replay_kinds = {item.get("kind") for item in replay.json()["messages"]}
    assert {
        "goal_model_updated",
        "evidence_pack_ready",
        "strategy_portfolio_ready",
        "planning_session_status",
    } <= replay_kinds


def test_p_mode_critic_block_stays_in_active_session_and_cannot_be_confirmed(client, monkeypatch):
    model = BlockingCriticModel("resource mismatch")

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    created = client.post(
        "/api/command/chat",
        json={
            "message": "30天准备 Python AI 应用实习，每天3小时，要有代码和项目产出",
            "mode": "auto",
            "permission": "low",
            "context": {"date": date.today().isoformat()},
        },
    )
    created_events = [json.loads(line) for line in created.text.splitlines() if line.strip()]
    thread_id = created_events[-1]["threadId"]
    approved_design = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认方向", "mode": "auto", "permission": "low", "context": {"date": date.today().isoformat()}},
    )
    design_events = [json.loads(line) for line in approved_design.text.splitlines() if line.strip()]
    assert any(item.get("type") == "planning_session_status" and item.get("status") == "execution_revision" for item in design_events)

    blocked = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认执行计划", "mode": "auto", "permission": "low", "context": {"date": date.today().isoformat()}},
    )
    blocked_events = [json.loads(line) for line in blocked.text.splitlines() if line.strip()]
    assert not any(item.get("type") == "command_decision" for item in blocked_events)
    assert not any(item.get("type") in {"runtime_started", "draft_created", "calendar_plan_preview"} for item in blocked_events)
    decision = next(item for item in blocked_events if item.get("type") == "agent_decision")
    assert decision["data"]["agent"] == "Independent Critic & Learning Agent"
    assert decision["data"]["decision"] == "block"
    assert any(item.get("type") == "planning_session_status" and item.get("status") == "execution_revision" for item in blocked_events)


def test_p_mode_model_failure_is_visible_and_never_emits_formal_plan(client, monkeypatch):
    def unavailable(_self, *, stage: str, **_kwargs):
        raise PlanningModelUnavailable(
            stage,
            SafePlanningError(
                stage=stage,
                errorType="timeout",
                message="Primary and backup planning models timed out.",
                retryable=True,
                attempts=[
                    {"provider": "kimi", "model": "primary", "status": "error", "errorType": "timeout", "latencyMs": 900},
                    {"provider": "deepseek", "model": "backup", "status": "error", "errorType": "timeout", "latencyMs": 900},
                ],
            ),
        )

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", unavailable)
    response = client.post(
        "/api/command/chat",
        json={"message": "30天准备 Python AI 应用实习，每天3小时", "mode": "auto", "permission": "low"},
    )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    event_types = {item.get("type") for item in events}
    assert "planning_session_started" in event_types
    assert "agent_decision" in event_types
    assert "agent_message" in event_types
    assert "strategy_portfolio_ready" not in event_types
    assert "execution_blueprint_ready" not in event_types
    assert "runtime_started" not in event_types
    assert "draft_created" not in event_types
    block_message = next(item for item in events if item.get("type") == "agent_message")
    assert block_message["data"]["payloadJson"]["errorType"] == "timeout"
    assert len(block_message["data"]["payloadJson"]["attempts"]) == 2


def test_cognitive_p_mode_calendar_write_still_requires_permission_gate(client, monkeypatch):
    model = StubCognitiveModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    created = client.post(
        "/api/command/chat",
        json={"message": "30天准备 Python AI 应用实习，每天3小时，要有代码和项目产出", "mode": "auto", "permission": "low"},
    )
    created_events = [json.loads(line) for line in created.text.splitlines() if line.strip()]
    thread_id = created_events[-1]["threadId"]
    session_id = next(item for item in created_events if item.get("type") == "planning_session_started")["sessionId"]

    client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认方向", "mode": "auto", "permission": "low"},
    )
    ready = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认执行计划", "mode": "auto", "permission": "low"},
    )
    ready_events = [json.loads(line) for line in ready.text.splitlines() if line.strip()]
    assert any(item.get("type") == "planning_session_status" and item.get("status") == "ready_to_write_calendar" for item in ready_events)

    write = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "写入日历", "mode": "auto", "permission": "low"},
    )
    write_events = [json.loads(line) for line in write.text.splitlines() if line.strip()]
    assert any(item.get("type") == "calendar_plan_preview" for item in write_events)
    approval = next(item for item in write_events if item.get("type") == "approval_required")
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0

    approved = client.post(
        "/api/command/approve",
        json={"threadId": thread_id, "actionId": approval["actionId"], "decision": "approve", "permission": "low"},
    )
    approved_events = [json.loads(line) for line in approved.text.splitlines() if line.strip()]
    result = next(item for item in approved_events if item.get("type") == "calendar_write_result")
    assert result["created"] >= 8
    with get_conn() as conn:
        rows = conn.execute("SELECT source_key FROM plans ORDER BY date, time").fetchall()
        session_status = conn.execute("SELECT status FROM planning_sessions WHERE id = ?", (session_id,)).fetchone()["status"]
    assert rows
    assert rows[0]["source_key"] == f"planning-session:{session_id}:t0"
    assert all(row["source_key"].startswith(f"planning-session:{session_id}:t") for row in rows)
    assert session_status == "written_to_calendar"


def test_planning_calendar_action_rejects_stale_execution_artifact(client, monkeypatch):
    model = StubCognitiveModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    created = client.post(
        "/api/command/chat",
        json={"message": "30天准备 Python AI 应用实习，每天3小时，要有代码和项目产出", "mode": "auto", "permission": "low"},
    )
    events = [json.loads(line) for line in created.text.splitlines() if line.strip()]
    thread_id = events[-1]["threadId"]
    session_id = next(item for item in events if item.get("type") == "planning_session_started")["sessionId"]
    client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认方向", "mode": "auto", "permission": "low"},
    )
    client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "确认执行计划", "mode": "auto", "permission": "low"},
    )
    preview = client.post(
        "/api/command/chat",
        json={"threadId": thread_id, "message": "写入日历", "mode": "auto", "permission": "low"},
    )
    preview_events = [json.loads(line) for line in preview.text.splitlines() if line.strip()]
    action_id = next(item for item in preview_events if item.get("type") == "approval_required")["actionId"]
    with get_conn() as conn:
        execution = conn.execute(
            """
            SELECT owner_agent, content_json
            FROM planning_artifacts
            WHERE session_id = ? AND artifact_type = 'execution_blueprint'
            ORDER BY version DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    assert execution is not None
    PlanningAgentRuntime().record_artifact(
        session_id,
        owner_agent=execution["owner_agent"],
        artifact_type="execution_blueprint",
        content=json.loads(execution["content_json"]),
    )

    approved = client.post(
        "/api/command/approve",
        json={"threadId": thread_id, "actionId": action_id, "decision": "approve", "permission": "low"},
    )
    approved_events = [json.loads(line) for line in approved.text.splitlines() if line.strip()]
    assert any(item.get("type") == "error" for item in approved_events)
    assert not any(item.get("type") == "calendar_write_result" for item in approved_events)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM plans").fetchone()["count"] == 0


class GoClarificationModel(StubCognitiveModel):
    def _goal(self, payload: dict[str, Any]) -> UserGoalModel:
        text = self._conversation_text(payload)
        if "Go" not in text and "go" not in text:
            return super()._goal(payload)
        questions = [
            GoalQuestion(
                question="你希望 Go 最终帮你实现什么结果或进入哪类岗位？",
                whyThisQuestionMatters="目标用途会改变应优先学习的工程能力和成功标准。",
                expectedDecisionImpact="strategy and success criteria",
            ),
            GoalQuestion(
                question="你已经能使用哪些编程语言或后端技术？",
                whyThisQuestionMatters="已有技术决定哪些概念可以迁移、哪些需要从头建立。",
                expectedDecisionImpact="scope and resources",
            ),
            GoalQuestion(
                question="你能稳定投入多少时间，希望何时看到可验证结果？",
                whyThisQuestionMatters="投入与期限决定目标范围是否现实。",
                expectedDecisionImpact="schedule and feasibility",
            ),
        ]
        return UserGoalModel(
            goalStatement="理解用户学习 Go 的真实目标",
            desiredChange="从模糊兴趣转成可验证、可执行的目标",
            domain="go_learning",
            possibleIntents=["职业能力", "项目开发", "兴趣学习"],
            currentKnowledge=[text],
            uncertainties=["目标用途", "已有技术", "时间与期限"],
            knownFacts=[KnownFact(key="request", statement="用户想学习 Go", sourceText=text, confidence=1)],
            decisionRelevantUnknowns=[
                DecisionRelevantUnknown(
                    key="purpose",
                    description="学习 Go 的目标用途",
                    whyItChangesThePlan="它决定路线、资源和验收标准。",
                    impact="strategy",
                    priority="blocking",
                )
            ],
            successModel=GoalSuccessModel(definition="尚待用户定义可验证结果"),
            questions=questions,
            confidence=0.55,
            canProceedToEvidence=False,
        )


def test_phase7_go_goal_asks_high_value_questions_before_any_plan(isolated_db):
    runtime = CognitiveOSRuntime(model_client=GoClarificationModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="phase7-go", userInput="我要学Go")
    )
    assert session.status == "needs_goal_clarification"
    assert session.reality_assessment is None
    assert session.strategy_portfolio is None
    assert session.execution_blueprint is None
    question_text = json.dumps(session.goal_model["questions"], ensure_ascii=False)
    assert "最终帮你实现什么结果" in question_text
    assert "编程语言或后端技术" in question_text
    assert "投入多少时间" in question_text
    assert "30天Go计划" not in json.dumps(session.model_dump(by_alias=True), ensure_ascii=False)


def test_phase7_swimming_discovers_reality_and_never_leaks_project_templates(isolated_db):
    runtime = CognitiveOSRuntime(model_client=StubCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="phase7-swim", userInput="我要学游泳，零基础，每天1小时")
    )
    assert session.status == "needs_goal_clarification"
    assert session.strategy_portfolio is None
    session = runtime.clarify(
        session.session_id,
        PlanningSessionTextRequest(text="可以稳定使用有救生员的泳池，目标三个月连续游200米"),
    )
    assert session.status == "waiting_design_approval"
    assert session.reality_assessment
    reality_text = json.dumps(session.reality_assessment, ensure_ascii=False)
    assert "水上安全" in reality_text
    assert "训练环境" in reality_text
    visible = json.dumps(
        {"goal": session.goal_model, "reality": session.reality_assessment, "strategy": session.strategy_portfolio},
        ensure_ascii=False,
    )
    for forbidden in ("找实习", "README", "做项目"):
        assert forbidden not in visible


def test_phase7_travel_uses_model_judgment_not_static_catalog(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase7-travel",
            userInput="2026年9月去新疆14天，预算1万元，飞机，想去赛里木湖和喀纳斯",
        )
    )
    assert session.status == "waiting_design_approval"
    assert session.reality_assessment
    assert session.evidence_pack
    evidence_call = next(payload for task, payload in model.calls if task == "planning_evidence")
    assert evidence_call["resourceCandidates"] == []
    visible = json.dumps(session.model_dump(by_alias=True), ensure_ascii=False)
    assert "季节" in visible
    assert "长距离交通" in visible
    assert "README" not in visible


def test_phase7_model_failure_is_recoverable_and_never_fakes_ai_planning(isolated_db):
    runtime = CognitiveOSRuntime(model_client=UnavailableCognitiveModel())
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="phase7-unavailable", userInput="我要学游泳")
    )
    assert session.status == "MODEL_UNAVAILABLE"
    assert session.business_status == "goal_clarification"
    assert session.runtime_status == "blocked_model"
    assert session.cognitive_metadata.engine_version == "cognitive-os-v1"
    assert session.cognitive_metadata.planning_mode == "blocked_model_unavailable"
    assert session.strategy_portfolio is None
    assert session.execution_blueprint is None
    assert not session.decisions
    with pytest.raises(HTTPException) as exc_info:
        runtime.prepare_calendar_write(session.session_id)
    assert exc_info.value.status_code == 409


def test_phase7_feedback_persists_evidence_backed_user_model_memory(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="phase7-learning", userInput="零基础学 Python，每天3小时，30天找实习")
    )
    session = runtime.approve_design(session.session_id)
    runtime.revise_execution(session.session_id, PlanningSessionTextRequest(text="资料太理论，看不懂"))
    memories = UserModelMemoryRepository().relevant("python_career")
    assert memories
    assert memories[0].category == "planning_hypothesis"
    assert memories[0].evidence
    assert memories[0].confidence < 1


def test_phase7_critic_repair_loop_remains_bounded_to_two_rounds(isolated_db):
    model = StubCognitiveModel(first_critique_needs_repair=True)
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="phase7-repair", userInput="零基础学 Python，每天3小时，30天找实习")
    )
    session = runtime.approve_design(session.session_id)
    assert session.status == "waiting_execution_approval"
    assert model.critique_calls == 2
    assert session.critique_report["status"] == "passed"
    execution_calls = [payload for task, payload in model.calls if task == "planning_execution"]
    assert len(execution_calls) == 3
    assert "previousExecutionBlueprint" not in execution_calls[0]
    assert all(
        payload.get("previousExecutionBlueprint", {}).get("tasks")
        for payload in execution_calls[1:]
    )
    assert all(payload.get("repairInstructions") for payload in execution_calls[1:])
    assert all(payload.get("previousCritiqueReport") for payload in execution_calls[1:])
    assert all(payload.get("repairHistory") for payload in execution_calls[1:])
    artifacts = runtime.agent_runtime.list_artifacts(session.session_id)
    executions = [item for item in artifacts if item.artifact_type == "execution_blueprint"]
    critiques = [item for item in artifacts if item.artifact_type == "critique_report"]
    assert len(executions) == len(critiques) == 2
    assert all(
        payload["previousExecutionBlueprint"] == executions[0].content_json
        for payload in execution_calls[1:]
    )
    critic_decisions = [
        item
        for item in runtime.agent_runtime.list_decisions(session.session_id)
        if item.agent == "Critic Agent"
    ]
    assert len(critic_decisions) == 2
    reviewed_execution_ids: set[str] = set()
    produced_critique_ids: set[str] = set()
    for decision in critic_decisions:
        matching_execution_ids = {
            execution.id
            for execution in executions
            if execution.id in decision.input_artifact_ids
        }
        matching_critique_ids = {
            critique.id
            for critique in critiques
            if critique.id in decision.output_artifact_ids
        }
        assert len(matching_execution_ids) == 1
        assert len(matching_critique_ids) == 1
        reviewed_execution_ids.update(matching_execution_ids)
        produced_critique_ids.update(matching_critique_ids)
    assert reviewed_execution_ids == {execution.id for execution in executions}
    assert produced_critique_ids == {critique.id for critique in critiques}


def test_phase7_preflight_failure_never_persists_or_runs_critic(isolated_db):
    model = StubCognitiveModel(generic_task=True)
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase7-preflight-block",
            userInput="Build a reviewable Python project in 30 days with three hours available daily",
        )
    )

    session = runtime.approve_design(session.session_id)

    assert session.status == "MODEL_UNAVAILABLE"
    assert session.model_failure is not None
    assert session.model_failure.resume_node == "execution"
    assert session.execution_blueprint is None
    assert session.critique_report is None
    assert model.critique_calls == 0
    assert not [
        item
        for item in runtime.agent_runtime.list_artifacts(session.session_id)
        if item.artifact_type in {"execution_blueprint", "critique_report"}
    ]


def test_repairable_blocked_critic_enters_repair_loop(isolated_db):
    model = RepairableBlockedCriticModel()
    runtime = CognitiveOSRuntime(model_client=model)
    session = runtime.create_session(
        CreatePlanningSessionRequest(entryPoint="p_mode", threadId="repairable-blocked", userInput="Learn Go")
    )

    session = runtime.approve_design(session.session_id)

    assert session.status == "waiting_execution_approval"
    assert session.critique_report["status"] == "passed"
    assert model.critique_calls == 2
    assert session.cognitive_metadata.repair_count == 1


def test_phase7_canonical_critic_rules_enforce_execution_invariants(isolated_db):
    blueprint = StubCognitiveModel()._execution({"goalModel": {"domain": "travel"}})
    validate_execution_blueprint(blueprint)

    duplicate = blueprint.model_copy(update={"tasks": [blueprint.tasks[0], blueprint.tasks[0]]})
    with pytest.raises(CognitiveCriticRuleError, match="task ids must be unique"):
        validate_execution_blueprint(duplicate)


def test_phase7_goal_understanding_handoff_preserves_current_user_role_and_typed_context(isolated_db):
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="phase7-understanding-handoff",
            userInput="旅游",
            context={
                "goalUnderstanding": {
                    "intentState": "clear_goal",
                    "understoodIntent": "用户想去北京旅游。",
                    "possibleDomains": ["travel"],
                    "knownFacts": {"location": "北京", "purpose": "旅游"},
                    "uncertainties": [],
                    "consistencyWarnings": [],
                    "confidence": 0.9,
                }
            },
        )
    )

    goal_payload = next(payload for task_type, payload in model.calls if task_type == "planning_goal_model")
    assert goal_payload["conversationHistory"] == [{"role": "user", "content": "旅游"}]
    assert goal_payload["preExtractedFacts"]["goalUnderstanding"]["knownFacts"] == {
        "location": "北京",
        "purpose": "旅游",
    }


def test_phase7_p_mode_stream_keeps_user_artifacts_and_advanced_trace_data(client, monkeypatch):
    model = StubCognitiveModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)
    response = client.post(
        "/api/command/chat",
        json={
            "message": "2026年9月去新疆14天，预算1万元，飞机，想去赛里木湖和喀纳斯",
            "mode": "auto",
            "permission": "low",
        },
    )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    event_types = {item.get("type") for item in events}
    assert {
        "planning_session_started",
        "goal_model_updated",
        "goal_completion_updated",
        "reality_assessment_ready",
        "evidence_pack_ready",
        "strategy_portfolio_ready",
        "planning_session_status",
    }.issubset(event_types)
    completion_event = next(item for item in events if item.get("type") == "goal_completion_updated")
    assert completion_event["data"]["complete"] is True
    assert completion_event["data"]["nextStage"] == "strategy"
    status_event = next(item for item in reversed(events) if item.get("type") == "planning_session_status")
    assert status_event["businessStatus"] == "strategy_pending"
    assert status_event["runtimeStatus"] == "idle"
    assert status_event["goalCompletion"]["complete"] is True
    assert "command_decision" not in event_types
    assert "agent_decision" in event_types
    assert "agent_message" in event_types
    assert "runtime_started" not in event_types
    assert "draft_created" not in event_types
    assert "user_need_contract" not in event_types
    assert "memory_insight_brief" not in event_types
    assert "resource_brief" not in event_types
    replay = client.get(f"/api/command/thread/{events[-1]['threadId']}")
    assert replay.status_code == 200
    assert "goal_completion_updated" in {item.get("kind") for item in replay.json()["messages"]}


def test_phase7_p_mode_model_unavailable_has_no_fake_plan_and_keeps_debug_diagnostics(client, monkeypatch):
    def unavailable(_self, *, stage: str, **_kwargs):
        raise PlanningModelUnavailable(
            stage,
            SafePlanningError(
                stage=stage,
                errorType="auth_error",
                message="No planning model is available.",
                retryable=False,
            ),
        )

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", unavailable)
    response = client.post(
        "/api/command/chat",
        json={"message": "我要学Go", "mode": "auto", "permission": "low"},
    )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(
        item.get("type") == "planning_session_status"
        and item.get("status") == "MODEL_UNAVAILABLE"
        and item.get("businessStatus") == "goal_clarification"
        and item.get("runtimeStatus") == "blocked_model"
        for item in events
    )
    forbidden = {
        "command_decision",
        "strategy_portfolio_ready",
        "execution_blueprint_ready",
        "runtime_started",
        "draft_created",
        "calendar_plan_preview",
    }
    assert not forbidden.intersection({item.get("type") for item in events})
    assert not any(item.get("type") == "agent_decision" for item in events)
    assert any(item.get("type") == "agent_message" for item in events)
