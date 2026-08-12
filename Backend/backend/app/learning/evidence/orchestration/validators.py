from __future__ import annotations

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators.base import artifact_ref
from ..coverage import CoverageReport, CoverageReportValidator
from .contracts import GapCompletionResult


class GapCompletionValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class GapCompletionValidator:
    _STRENGTH = {"MISSING": 0, "WEAK": 1, "PARTIAL": 2, "FULL": 3}

    def __init__(self, coverage_validator: CoverageReportValidator | None = None):
        self.coverage_validator = coverage_validator or CoverageReportValidator()

    def validate_inputs(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
        *,
        max_rounds: int,
    ) -> None:
        if max_rounds < 1 or max_rounds > 10:
            self._fail(
                "max_rounds",
                "maxRounds",
                "MAX_ROUNDS must be between one and ten",
            )
        self.coverage_validator.validate_report(
            knowledge_graph,
            evidence_graph,
            coverage_report,
        )

    def validate_result(
        self,
        knowledge_graph: KnowledgeGraph,
        initial_graph: EvidenceGraph,
        result: GapCompletionResult,
    ) -> None:
        if result.initial_graph_ref != artifact_ref("evidence_graph", initial_graph):
            self._fail(
                "artifact_lineage",
                "initialGraphRef",
                "result does not reference the initial EvidenceGraph version",
            )
        if len(result.rounds) > result.max_rounds:
            self._fail(
                "max_rounds",
                "rounds",
                "gap completion exceeded MAX_ROUNDS",
            )
        self.coverage_validator.validate_report(
            knowledge_graph,
            initial_graph,
            result.initial_report,
        )

        previous = result.initial_report
        for index, round_run in enumerate(result.rounds, start=1):
            path = f"rounds.{index - 1}"
            if round_run.run_id != result.run_id:
                self._fail(
                    "round_run_id",
                    f"{path}.runId",
                    "all rounds must belong to the same completion run",
                )
            if round_run.round_number != index:
                self._fail(
                    "round_sequence",
                    f"{path}.roundNumber",
                    "round numbers must be continuous and one-based",
                )
            if round_run.before_report != previous:
                self._fail(
                    "round_report_chain",
                    f"{path}.beforeReport",
                    "round before report must equal the previous after report",
                )
            before_ref = round_run.before_report.evidence_graph_ref
            after_ref = round_run.after_report.evidence_graph_ref
            if (
                after_ref.artifact_id != before_ref.artifact_id
                or after_ref.version < before_ref.version
            ):
                self._fail(
                    "round_artifact_lineage",
                    f"{path}.afterReport.evidenceGraphRef",
                    "a round must preserve the EvidenceGraph identity and monotonic version lineage",
                )
            self._validate_gap_delta(round_run, path)
            is_last = index == len(result.rounds)
            if not is_last:
                if round_run.status != "RUNNING" or round_run.termination_reason is not None:
                    self._fail(
                        "round_terminal_state",
                        path,
                        "only the last round may be terminal",
                    )
                if not self.coverage_improved(
                    round_run.before_report,
                    round_run.after_report,
                ):
                    self._fail(
                        "coverage_no_improvement_continue",
                        path,
                        "a round without coverage improvement cannot continue",
                    )
            previous = round_run.after_report

        if result.final_report != previous:
            self._fail(
                "final_report",
                "finalReport",
                "final report must equal the latest round report",
            )
        self.coverage_validator.validate_report(
            knowledge_graph,
            result.final_graph,
            result.final_report,
        )
        if result.rounds:
            terminal = result.rounds[-1]
            if (
                terminal.status != result.status
                or terminal.termination_reason != result.termination_reason
                or terminal.error != result.error
            ):
                self._fail(
                    "terminal_state",
                    "rounds",
                    "last round and result terminal state must match",
                )

        required_full = self.required_coverage_full(
            knowledge_graph,
            result.final_report,
        )
        if result.status == "RUNNING":
            self._fail(
                "terminal_state",
                "status",
                "a returned GapCompletionResult must be terminal",
            )
        if result.status == "COMPLETED":
            if not required_full or result.termination_reason != "REQUIRED_COVERAGE_FULL":
                self._fail(
                    "completed_status",
                    "status",
                    "COMPLETED requires all required knowledge to be FULL",
                )
            if result.error:
                self._fail(
                    "completed_status",
                    "error",
                    "COMPLETED result cannot contain an error",
                )
        elif required_full:
            self._fail(
                "completed_status",
                "status",
                "all required knowledge is FULL but result is not COMPLETED",
            )
        if result.status == "FAILED":
            if result.termination_reason != "FAILED" or not result.error:
                self._fail(
                    "failed_status",
                    "status",
                    "FAILED requires a failure reason and error",
                )
        elif result.status == "INCOMPLETE" and result.termination_reason not in {
            "NO_COVERAGE_IMPROVEMENT",
            "MAX_ROUNDS_REACHED",
            "NO_EXECUTABLE_GAP",
            "BUDGET_EXHAUSTED",
        }:
            self._fail(
                "incomplete_status",
                "terminationReason",
                "INCOMPLETE requires a normal incomplete termination reason",
            )
        elif result.error:
            self._fail(
                "failed_status",
                "error",
                "only FAILED result may contain an error",
            )
        if result.termination_reason == "MAX_ROUNDS_REACHED" and (
            len(result.rounds) != result.max_rounds
        ):
            self._fail(
                "max_rounds",
                "terminationReason",
                "MAX_ROUNDS_REACHED requires exactly MAX_ROUNDS rounds",
            )
        if result.termination_reason == "NO_COVERAGE_IMPROVEMENT":
            if not result.rounds or self.coverage_improved(
                result.rounds[-1].before_report,
                result.rounds[-1].after_report,
            ):
                self._fail(
                    "coverage_improvement",
                    "terminationReason",
                    "NO_COVERAGE_IMPROVEMENT requires an unimproved final round",
                )
        if (
            result.retrieval_plan_count > result.budget.max_retrieval_plans
            or result.candidate_count > result.budget.max_candidates
            or result.transcript_acquisition_count
            > result.budget.max_transcript_acquisitions
            or result.model_mapping_call_count > result.budget.max_model_mapping_calls
        ):
            self._fail(
                "gap_completion_budget",
                "budget",
                "gap completion exceeded a code-owned execution budget",
            )

    def _validate_gap_delta(self, round_run, path: str) -> None:
        after_keys = {
            (item.knowledge_id, item.gap_type)
            for item in round_run.after_report.gaps
        }
        expected_resolved = [
            item
            for item in round_run.before_report.gaps
            if (item.knowledge_id, item.gap_type) not in after_keys
        ]
        if round_run.resolved_gaps != expected_resolved:
            self._fail(
                "resolved_gap",
                f"{path}.resolvedGaps",
                "resolved gaps must exist before and disappear after the round",
            )
        if round_run.remaining_gaps != round_run.after_report.gaps:
            self._fail(
                "remaining_gap",
                f"{path}.remainingGaps",
                "remaining gaps must come from the round after report",
            )

    @classmethod
    def coverage_improved(
        cls,
        before: CoverageReport,
        after: CoverageReport,
    ) -> bool:
        before_strength = {
            item.knowledge_id: cls._STRENGTH[item.coverage_strength]
            for item in before.knowledge_coverage
        }
        after_strength = {
            item.knowledge_id: cls._STRENGTH[item.coverage_strength]
            for item in after.knowledge_coverage
        }
        strength_improved = any(
            after_strength.get(knowledge_id, -1) > strength
            for knowledge_id, strength in before_strength.items()
        )
        before_gaps = {
            (item.knowledge_id, item.gap_type) for item in before.gaps
        }
        after_gaps = {
            (item.knowledge_id, item.gap_type) for item in after.gaps
        }
        return strength_improved or after_gaps < before_gaps

    @staticmethod
    def required_coverage_full(
        knowledge_graph: KnowledgeGraph,
        report: CoverageReport,
    ) -> bool:
        coverage = {
            item.knowledge_id: item.coverage_strength
            for item in report.knowledge_coverage
        }
        return all(
            coverage.get(node.id) == "FULL"
            for node in knowledge_graph.nodes
            if node.importance == "required"
        )

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise GapCompletionValidationError(rule, path, message)


__all__ = ["GapCompletionValidationError", "GapCompletionValidator"]
