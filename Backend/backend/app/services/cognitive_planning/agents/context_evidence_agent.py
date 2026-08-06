from __future__ import annotations

from datetime import date

from ..contracts import (
    EvidenceAuthorityPolicy,
    EvidenceInput,
    EvidencePack,
    ResearchPolicy,
    UserGoalModel,
    apply_evidence_authority_policy,
)
from ..retrieval import (
    CalendarContextRetriever,
    CognitiveMemoryRetriever,
    PlanningHistoryRetriever,
    PlanningHypothesisRepository,
    ResourceResearch,
)
from .base import AgentResult, CognitiveModelClient


EVIDENCE_SYSTEM = """
You are Planix Context & Evidence Agent. Synthesize memory, planning hypotheses, Calendar reality, local
materials, and resource candidates into evidence that can change planning decisions. Separate facts from
inference. Explain exactly how evidence affects the plan. Static resource entries are candidates, never a
decision. Do not invent links, current facts, availability, prices, policies, or credentials. When current or
authoritative research is needed but web research is not approved, record an explicit gap and limitation.
The current goalModel is authoritative. Its known facts, constraints, preferences, assumptions, and success model
override contradictory memory or planning history. Mark older contradictions as supersededContext, never ask_user.
Only a goal unknown with priority blocking, or genuinely new supported safety or feasibility evidence, may block
strategy. Important and optional unknowns become explicit assumptions or strategy branches. For every potentially
blocking gap, populate blockingBasis, impact, sourceContext, supportingSourceIds, and relatedGoalUnknownKey. Every
userEvidence item must set sourceId and sourceContext; every planningRule must set sourceIds and sourceContext.
Never upgrade an unattributed or historical rule to hard strength. Every domainEvidence item must set
sourceContext and use sourceRef for a supplied source; historical domain evidence cannot reach Strategy.
Every resourceCandidate must identify a valid supplied source through sourceRef/sourceContext. Model knowledge
may describe a resource need, but must never create a concrete resourceCandidate without a supplied source.
Block strategy when evidence is insufficient for a safe or credible plan. Return only the requested JSON and
never hidden chain-of-thought.
""".strip()


class ContextEvidenceAgent:
    name = "Context & Evidence Agent"
    artifact_type = "evidence_pack"

    def __init__(
        self,
        model: CognitiveModelClient | None = None,
        memory: CognitiveMemoryRetriever | None = None,
        calendar: CalendarContextRetriever | None = None,
        planning_history: PlanningHistoryRetriever | None = None,
        research: ResourceResearch | None = None,
        hypotheses: PlanningHypothesisRepository | None = None,
    ):
        self.model = model or CognitiveModelClient()
        self.memory = memory or CognitiveMemoryRetriever()
        self.calendar = calendar or CalendarContextRetriever()
        self.planning_history = planning_history or PlanningHistoryRetriever()
        self.research = research or ResourceResearch()
        self.hypotheses = hypotheses or PlanningHypothesisRepository()

    def run(
        self,
        goal: UserGoalModel,
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
                "official_doc",
                "user_material",
                "local_catalog",
                *(["official_notice", "web_search"] if web_research_allowed else []),
            ],
        )
        authority_policy = EvidenceAuthorityPolicy()
        memories = [
            item.model_copy(
                update={
                    "context_role": "historical_context" if item.kind == "review" else "supporting_context"
                }
            )
            for item in self.memory.retrieve(goal)
        ]
        planning_history = [
            item.model_copy(update={"context_role": "historical_context"})
            for item in self.planning_history.retrieve(goal)
        ]
        calendar_constraints = self.calendar.retrieve(context_date or date.today().isoformat())
        resource_candidates = self.research.candidates(query, policy)
        planning_hypotheses = self.hypotheses.relevant(goal.domain)
        historical_sources = {
            source_id
            for item in [*memories, *planning_history]
            if item.context_role == "historical_context"
            for source_id in item.provenance_ids()
        }
        valid_sources = {
            source_id
            for item in memories
            if item.context_role == "supporting_context"
            for source_id in item.provenance_ids()
        }
        valid_sources.update(item.source_id for item in calendar_constraints if item.source_id)
        valid_sources.update(
            str(value)
            for item in resource_candidates
            for value in (item.get("id"), item.get("sourceId"), item.get("sourceRef"))
            if value
        )
        valid_sources.update(item.id for item in planning_hypotheses)
        payload = EvidenceInput(
            goalModel=goal,
            memoryQueryContext=query,
            existingMaterials=[*memories, *planning_history],
            calendarConstraints=calendar_constraints,
            resourceCandidates=resource_candidates,
            planningHypotheses=[item.model_dump(by_alias=True) for item in planning_hypotheses],
            researchPolicy=policy,
            authorityPolicy=authority_policy,
        )
        result = self.model.complete_contract(
            stage="context_evidence",
            task_type="planning_evidence",
            feature="cognitive_evidence_synthesis",
            system=EVIDENCE_SYSTEM,
            payload=payload.model_dump(by_alias=True),
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
