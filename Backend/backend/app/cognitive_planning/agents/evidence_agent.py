from __future__ import annotations

from datetime import date
from typing import Protocol

from ...services.cognitive_planning.contracts import (
    EvidenceAuthorityPolicy,
    EvidenceInput,
    EvidencePack,
    ResearchPolicy,
    apply_evidence_authority_policy,
)
from ...services.cognitive_planning.retrieval import (
    CalendarContextRetriever,
    CognitiveMemoryRetriever,
    PlanningHistoryRetriever,
)
from ..contracts import GoalUnderstandingArtifact, RealityAssessment
from ..memory import UserModelMemoryRepository
from .base import AgentResult, CognitiveModelClient


class WebEvidenceProvider(Protocol):
    def search(self, query: str, policy: ResearchPolicy) -> list[dict]: ...


class DisabledWebEvidenceProvider:
    def search(self, query: str, policy: ResearchPolicy) -> list[dict]:
        return []


EVIDENCE_SYSTEM = """
You are Planix Evidence Agent. Build an Evidence Pack that can change decisions. Use the supplied user-model
memories, raw user materials and notes, planning history, Calendar reality, and explicitly approved research.
Do not treat a raw note, a catalog entry, or a model recollection as a user preference. Every evidence item
must name its source type, credibility, relevance, and exactly why it changes this plan.

The current goalModel is authoritative. Its known facts, constraints, preferences, assumptions, and success model
override contradictory memory or planning history. Mark such older contradictions as supersededContext; never turn
them into an ask_user gap. Only a goal unknown whose priority is blocking, or genuinely new supported safety or
feasibility evidence, may block strategy. Important and optional unknowns must become explicit assumptions or
strategy branches and must not reopen a completed Goal. For every potentially blocking gap, populate blockingBasis,
impact, sourceContext, supportingSourceIds, and relatedGoalUnknownKey when applicable. Every userEvidence item must
set sourceId and sourceContext; every planningRule must set sourceIds and sourceContext. Never upgrade an
unattributed or historical rule to hard strength. Every domainEvidence item must set sourceContext and use
sourceRef for a supplied source; historical domain evidence is superseded and cannot reach Strategy.
Every resourceCandidate must identify a valid supplied source through sourceRef/sourceContext. Model knowledge
may describe a resource need, but must never create a concrete resourceCandidate without a supplied source.

When supplied sources are incomplete, you may state conservative model-knowledge inferences with a lower
credibility and no invented URL, price, policy, availability, or current fact. Record current-information needs
as gaps. Never output empty statistics such as 'found 4 memories'. Static resource catalogs are not decisions.
Block strategy when evidence is too weak for a safe or credible recommendation. Return only the requested
JSON and never hidden chain-of-thought.
""".strip()


class EvidenceAgent:
    name = "Evidence Agent"
    artifact_type = "evidence_pack"

    def __init__(
        self,
        model: CognitiveModelClient | None = None,
        *,
        memory: CognitiveMemoryRetriever | None = None,
        calendar: CalendarContextRetriever | None = None,
        planning_history: PlanningHistoryRetriever | None = None,
        user_model: UserModelMemoryRepository | None = None,
        web_provider: WebEvidenceProvider | None = None,
    ):
        self.model = model or CognitiveModelClient()
        self.memory = memory or CognitiveMemoryRetriever()
        self.calendar = calendar or CalendarContextRetriever()
        self.planning_history = planning_history or PlanningHistoryRetriever()
        self.user_model = user_model or UserModelMemoryRepository()
        self.web_provider = web_provider or DisabledWebEvidenceProvider()

    def run(
        self,
        goal: GoalUnderstandingArtifact,
        reality: RealityAssessment,
        *,
        context_date: str | None = None,
        web_research_allowed: bool = False,
        web_research_approved: bool = False,
        freshness_required: bool = False,
    ) -> AgentResult[EvidencePack]:
        query = " ".join(value for value in (goal.goal_statement, goal.desired_change, goal.domain) if value)
        policy = ResearchPolicy(
            allowWebResearch=web_research_allowed,
            requireUserApproval=not web_research_approved,
            freshnessRequired=freshness_required,
            sourceTypesAllowed=[
                "user_material",
                "memory_note",
                "planning_history",
                "calendar",
                "model_knowledge",
                *(["official_notice", "web_search"] if web_research_allowed and web_research_approved else []),
            ],
        )
        web_candidates = self.web_provider.search(query, policy) if web_research_allowed and web_research_approved else []
        authority_policy = EvidenceAuthorityPolicy()
        retrieved_memories = [item for item in self.memory.retrieve(goal) if item.kind != "preference"]
        content_memories = [
            item.model_copy(
                update={
                    "context_role": "historical_context" if item.kind == "review" else "supporting_context"
                }
            )
            for item in retrieved_memories
        ]
        planning_history = [
            item.model_copy(update={"context_role": "historical_context"})
            for item in self.planning_history.retrieve(goal)
        ]
        user_model_memories = self.user_model.relevant(goal.domain)
        calendar_constraints = self.calendar.retrieve(context_date or date.today().isoformat())
        historical_sources = {
            source_id
            for item in [*content_memories, *planning_history]
            if item.context_role == "historical_context"
            for source_id in item.provenance_ids()
        }
        valid_sources = {
            source_id
            for item in content_memories
            if item.context_role == "supporting_context"
            for source_id in item.provenance_ids()
        }
        valid_sources.update(item.id for item in user_model_memories)
        valid_sources.update(item.source_id for item in calendar_constraints if item.source_id)
        valid_sources.update(
            str(value)
            for item in web_candidates
            for value in (item.get("id"), item.get("sourceId"), item.get("sourceRef"))
            if value
        )
        payload = EvidenceInput(
            goalModel=goal,
            memoryQueryContext=query,
            userModelMemories=[item.model_dump(by_alias=True) for item in user_model_memories],
            existingMaterials=[*content_memories, *planning_history],
            calendarConstraints=calendar_constraints,
            resourceCandidates=web_candidates,
            planningHypotheses=[],
            researchPolicy=policy,
            authorityPolicy=authority_policy,
        )
        result = self.model.complete_contract(
            stage="evidence_synthesis",
            task_type="planning_evidence",
            feature="cognitive_os_evidence_synthesis",
            system=EVIDENCE_SYSTEM,
            payload={
                **payload.model_dump(by_alias=True),
                "realityAssessment": reality.model_dump(by_alias=True),
            },
            contract_type=EvidencePack,
            temperature=0.15,
        )
        return AgentResult(
            apply_evidence_authority_policy(
                goal,
                result.artifact,
                authority_policy,
                historical_source_ids=historical_sources,
                valid_evidence_source_ids=valid_sources,
            ),
            result.model_usage,
        )
