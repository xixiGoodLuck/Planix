from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators.base import artifact_ref
from ..coverage import CoverageReport
from ..providers import VideoSourceProvider
from ..qualification import CandidateQualifier
from ..retrieval import RetrievalExecutor, RetrievalPlanner, RetrievalRequest
from ..supplement import EvidenceSupplementer
from ..transcript import TranscriptAcquirer, TranscriptProvider
from .contracts import (
    GapCompletionBudget,
    GapCompletionResult,
    GapCompletionRun,
)
from .validators import GapCompletionValidator


@dataclass
class _BudgetUsage:
    retrieval_plans: int = 0
    candidates: int = 0
    transcript_acquisitions: int = 0
    model_mapping_calls: int = 0
    searched_queries: list[str] = field(default_factory=list)
    searched_resource_refs: list[str] = field(default_factory=list)
    transcript_unavailable_resource_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _RoundExecution:
    graph: EvidenceGraph
    report: CoverageReport
    attempted: bool
    acquired: bool
    budget_exhausted: bool


class GapCompletionOrchestrator:
    MAX_ROUNDS = 3

    def __init__(
        self,
        transcript_provider: TranscriptProvider,
        *,
        retrieval_planner: RetrievalPlanner | None = None,
        candidate_qualifier: CandidateQualifier | None = None,
        evidence_supplementer: EvidenceSupplementer | None = None,
        validator: GapCompletionValidator | None = None,
        max_rounds: int | None = None,
        budget: GapCompletionBudget | None = None,
    ):
        self.transcript_provider = transcript_provider
        self.retrieval_planner = retrieval_planner or RetrievalPlanner()
        self.candidate_qualifier = candidate_qualifier or CandidateQualifier()
        self.evidence_supplementer = evidence_supplementer or EvidenceSupplementer()
        self.validator = validator or GapCompletionValidator()
        configured = budget or GapCompletionBudget()
        if max_rounds is not None:
            configured = configured.model_copy(update={"max_rounds": max_rounds})
        self.budget = configured
        self.max_rounds = configured.max_rounds

    def run(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
        provider: VideoSourceProvider,
    ) -> GapCompletionResult:
        self.validator.validate_inputs(
            knowledge_graph,
            evidence_graph,
            coverage_report,
            max_rounds=self.max_rounds,
        )
        run_id = f"gap-completion-{uuid4()}"
        initial_graph = evidence_graph.model_copy(deep=True)
        initial_report = coverage_report.model_copy(deep=True)
        current_graph = initial_graph
        current_report = initial_report
        rounds: list[GapCompletionRun] = []
        usage = _BudgetUsage()

        if self.validator.required_coverage_full(knowledge_graph, current_report):
            return self._finish(
                knowledge_graph,
                initial_graph,
                initial_report,
                current_graph,
                current_report,
                run_id,
                rounds,
                usage,
                status="COMPLETED",
                termination_reason="REQUIRED_COVERAGE_FULL",
            )

        for round_number in range(1, self.max_rounds + 1):
            before = current_report
            try:
                plans = sorted(
                    self.retrieval_planner.plan(knowledge_graph, before),
                    key=lambda item: (
                        {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[item.priority],
                        item.knowledge_id,
                        item.retrieval_plan_id,
                    ),
                )
                execution = self._execute_round(
                    knowledge_graph,
                    current_graph,
                    before,
                    plans,
                    provider,
                    usage,
                )
                current_graph = execution.graph
                current_report = execution.report
                improved = self.validator.coverage_improved(before, current_report)
                if self.validator.required_coverage_full(knowledge_graph, current_report):
                    status = "COMPLETED"
                    reason = "REQUIRED_COVERAGE_FULL"
                elif execution.budget_exhausted:
                    status = "INCOMPLETE"
                    reason = "BUDGET_EXHAUSTED"
                elif not execution.attempted or not execution.acquired:
                    status = "INCOMPLETE"
                    reason = "NO_EXECUTABLE_GAP"
                elif not improved:
                    status = "INCOMPLETE"
                    reason = "NO_COVERAGE_IMPROVEMENT"
                elif round_number == self.max_rounds:
                    status = "INCOMPLETE"
                    reason = "MAX_ROUNDS_REACHED"
                else:
                    status = "RUNNING"
                    reason = None
                rounds.append(
                    self._round(
                        run_id,
                        round_number,
                        before,
                        current_report,
                        status=status,
                        termination_reason=reason,
                    )
                )
                if status != "RUNNING":
                    return self._finish(
                        knowledge_graph,
                        initial_graph,
                        initial_report,
                        current_graph,
                        current_report,
                        run_id,
                        rounds,
                        usage,
                        status=status,
                        termination_reason=reason,
                    )
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                rounds.append(
                    self._round(
                        run_id,
                        round_number,
                        before,
                        before,
                        status="FAILED",
                        termination_reason="FAILED",
                        error=error,
                    )
                )
                return self._finish(
                    knowledge_graph,
                    initial_graph,
                    initial_report,
                    current_graph,
                    before,
                    run_id,
                    rounds,
                    usage,
                    status="FAILED",
                    termination_reason="FAILED",
                    error=error,
                )

        raise RuntimeError("gap completion loop exited without a terminal result")

    def _execute_round(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
        plans,
        provider: VideoSourceProvider,
        usage: _BudgetUsage,
    ) -> _RoundExecution:
        current_graph = evidence_graph
        current_report = coverage_report
        attempted = False
        acquired = False
        exhausted = False
        transcript_acquirer = TranscriptAcquirer(self.transcript_provider)

        for plan in plans:
            if usage.retrieval_plans >= self.budget.max_retrieval_plans:
                exhausted = True
                break
            usage.retrieval_plans += 1
            request = RetrievalRequest.from_plan(plan)
            usage.searched_queries.append(request.query)
            executor = RetrievalExecutor(provider, plans)
            candidates = executor.execute(request)
            attempted = True
            for candidate in candidates:
                if usage.candidates >= self.budget.max_candidates:
                    exhausted = True
                    break
                usage.candidates += 1
                qualified = self.candidate_qualifier.qualify(
                    candidate,
                    existing_resources=current_graph.resources,
                )
                if qualified.resource is not None:
                    usage.searched_resource_refs.append(qualified.resource.id)
                if qualified.qualification_status == "rejected":
                    continue
                if usage.transcript_acquisitions >= self.budget.max_transcript_acquisitions:
                    exhausted = True
                    break
                usage.transcript_acquisitions += 1
                acquisition = transcript_acquirer.acquire(qualified)
                if acquisition.status == "TRANSCRIPT_UNAVAILABLE":
                    if qualified.resource is not None:
                        usage.transcript_unavailable_resource_refs.append(
                            qualified.resource.id
                        )
                    continue
                if acquisition.transcript is None:
                    continue
                if usage.model_mapping_calls >= self.budget.max_model_mapping_calls:
                    exhausted = True
                    break
                usage.model_mapping_calls += 1
                supplement = self.evidence_supplementer.supplement(
                    qualified,
                    acquisition.transcript,
                    knowledge_graph,
                    current_graph,
                )
                acquired = True
                current_graph = supplement.supplemented_graph
                current_report = supplement.coverage_after
                break
            if exhausted or self.validator.required_coverage_full(
                knowledge_graph,
                current_report,
            ):
                break
        return _RoundExecution(
            graph=current_graph,
            report=current_report,
            attempted=attempted,
            acquired=acquired,
            budget_exhausted=exhausted,
        )

    @staticmethod
    def _round(
        run_id,
        round_number,
        before,
        after,
        *,
        status,
        termination_reason,
        error=None,
    ) -> GapCompletionRun:
        after_keys = {(item.knowledge_id, item.gap_type) for item in after.gaps}
        return GapCompletionRun(
            runId=run_id,
            roundNumber=round_number,
            status=status,
            beforeReport=before,
            afterReport=after,
            resolvedGaps=[
                item
                for item in before.gaps
                if (item.knowledge_id, item.gap_type) not in after_keys
            ],
            remainingGaps=after.gaps,
            terminationReason=termination_reason,
            error=error,
        )

    def _finish(
        self,
        knowledge_graph,
        initial_graph,
        initial_report,
        final_graph,
        final_report,
        run_id,
        rounds,
        usage,
        *,
        status,
        termination_reason,
        error=None,
    ) -> GapCompletionResult:
        result = GapCompletionResult(
            runId=run_id,
            status=status,
            maxRounds=self.max_rounds,
            initialGraphRef=artifact_ref("evidence_graph", initial_graph),
            initialReport=initial_report,
            finalGraph=final_graph,
            finalReport=final_report,
            rounds=rounds,
            terminationReason=termination_reason,
            error=error,
            budget=self.budget,
            retrievalPlanCount=usage.retrieval_plans,
            candidateCount=usage.candidates,
            transcriptAcquisitionCount=usage.transcript_acquisitions,
            modelMappingCallCount=usage.model_mapping_calls,
            searchedQueries=list(dict.fromkeys(usage.searched_queries)),
            searchedResourceRefs=list(dict.fromkeys(usage.searched_resource_refs)),
            transcriptUnavailableResourceRefs=list(
                dict.fromkeys(usage.transcript_unavailable_resource_refs)
            ),
        )
        self.validator.validate_result(knowledge_graph, initial_graph, result)
        return result


__all__ = ["GapCompletionOrchestrator"]
