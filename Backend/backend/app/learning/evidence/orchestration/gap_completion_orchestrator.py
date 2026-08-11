from __future__ import annotations

from uuid import uuid4

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators.base import artifact_ref
from ..coverage import CoverageReport
from ..providers import VideoSourceProvider
from ..qualification import CandidateQualifier
from ..retrieval import RetrievalExecutor, RetrievalPlanner, RetrievalRequest
from ..supplement import EvidenceSupplementResult, EvidenceSupplementer
from ..transcript import TranscriptAcquirer, TranscriptProvider
from .contracts import GapCompletionResult, GapCompletionRun
from .validators import GapCompletionValidator


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
        max_rounds: int = MAX_ROUNDS,
    ):
        self.transcript_provider = transcript_provider
        self.retrieval_planner = retrieval_planner or RetrievalPlanner()
        self.candidate_qualifier = candidate_qualifier or CandidateQualifier()
        self.evidence_supplementer = (
            evidence_supplementer or EvidenceSupplementer()
        )
        self.validator = validator or GapCompletionValidator()
        self.max_rounds = max_rounds

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

        if self.validator.required_coverage_full(knowledge_graph, current_report):
            return self._finish(
                knowledge_graph,
                initial_graph,
                initial_report,
                current_graph,
                current_report,
                run_id,
                rounds,
                status="COMPLETED",
                termination_reason="REQUIRED_COVERAGE_FULL",
            )

        for round_number in range(1, self.max_rounds + 1):
            before = current_report
            try:
                plans = self.retrieval_planner.plan(knowledge_graph, before)
                supplement = self._execute_one_gap(
                    knowledge_graph,
                    current_graph,
                    plans,
                    provider,
                )
                if supplement is None:
                    rounds.append(
                        self._round(
                            run_id,
                            round_number,
                            before,
                            before,
                            status="INCOMPLETE",
                            termination_reason="NO_EXECUTABLE_GAP",
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
                        status="INCOMPLETE",
                        termination_reason="NO_EXECUTABLE_GAP",
                    )
                current_graph = supplement.supplemented_graph
                current_report = supplement.coverage_after
                if self.validator.required_coverage_full(
                    knowledge_graph,
                    current_report,
                ):
                    status = "COMPLETED"
                    reason = "REQUIRED_COVERAGE_FULL"
                elif not self.validator.coverage_improved(before, current_report):
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
                    status="FAILED",
                    termination_reason="FAILED",
                    error=error,
                )

        raise RuntimeError("gap completion loop exited without a terminal result")

    def _execute_one_gap(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        plans,
        provider: VideoSourceProvider,
    ) -> EvidenceSupplementResult | None:
        if not plans:
            return None
        executor = RetrievalExecutor(provider, plans)
        transcript_acquirer = TranscriptAcquirer(self.transcript_provider)
        for plan in plans:
            request = RetrievalRequest.from_plan(plan)
            candidates = executor.execute(request)
            for candidate in candidates:
                qualified = self.candidate_qualifier.qualify(
                    candidate,
                    existing_resources=evidence_graph.resources,
                )
                if qualified.qualification_status == "rejected":
                    continue
                acquisition = transcript_acquirer.acquire(qualified)
                if acquisition.status == "TRANSCRIPT_UNAVAILABLE":
                    continue
                if acquisition.transcript is None:
                    continue
                return self.evidence_supplementer.supplement(
                    qualified,
                    acquisition.transcript,
                    knowledge_graph,
                    evidence_graph,
                )
        return None

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
        after_keys = {
            (item.knowledge_id, item.gap_type) for item in after.gaps
        }
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
        )
        self.validator.validate_result(knowledge_graph, initial_graph, result)
        return result


__all__ = ["GapCompletionOrchestrator"]
