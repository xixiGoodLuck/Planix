from __future__ import annotations

from ...generators.base import generated_id
from ..providers import VideoSearchQuery, VideoSourceProvider, VideoSourceProviderError
from .candidate import CandidateRetrievalSource, EvidenceCandidate, RetrievalRequest
from .candidate_validator import CandidateValidationError, CandidateValidator
from .contracts import RetrievalGapPlan


class RetrievalExecutionError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


class RetrievalExecutor:
    """Executes metadata retrieval only; it never creates transcript or evidence objects."""

    def __init__(
        self,
        provider: VideoSourceProvider,
        retrieval_plans: list[RetrievalGapPlan],
        *,
        maximum_results: int = 5,
        validator: CandidateValidator | None = None,
    ):
        if maximum_results < 1 or maximum_results > 20:
            raise ValueError("maximum_results must be between 1 and 20")
        self.provider = provider
        self.maximum_results = maximum_results
        self.validator = validator or CandidateValidator()
        self._plans = {item.retrieval_plan_id: item for item in retrieval_plans}
        if len(self._plans) != len(retrieval_plans):
            raise ValueError("retrieval plan ids must be unique")

    def execute(self, request: RetrievalRequest) -> list[EvidenceCandidate]:
        plan = self._validate_request(request)
        try:
            hits = self.provider.search(
                VideoSearchQuery(
                    knowledgeTerms=[request.query],
                    maximumResults=self.maximum_results,
                )
            )
            candidates: list[EvidenceCandidate] = []
            seen: set[tuple[str, str]] = set()
            for index, hit in enumerate(hits):
                identity = (hit.provider, hit.external_id)
                if identity in seen:
                    continue
                seen.add(identity)
                metadata = self.provider.fetch_metadata(hit.external_id)
                if (
                    metadata.provider != hit.provider
                    or metadata.external_id != hit.external_id
                ):
                    raise RetrievalExecutionError(
                        "candidate_metadata",
                        "provider metadata identity does not match the search result",
                    )
                candidate = EvidenceCandidate(
                    candidateId=generated_id(
                        "candidate",
                        request.retrieval_plan_id,
                        index,
                        f"{metadata.provider}:{metadata.external_id}:{metadata.content_fingerprint}",
                    ),
                    provider=metadata.provider,
                    externalId=metadata.external_id,
                    url=metadata.canonical_url,
                    title=metadata.title,
                    durationSeconds=metadata.duration_seconds,
                    contentFingerprint=metadata.content_fingerprint,
                    technologyVersions=metadata.technology_versions,
                    retrievalSource=CandidateRetrievalSource(
                        retrievalPlanId=plan.retrieval_plan_id,
                        knowledgeId=plan.knowledge_id,
                        query=request.query,
                    ),
                    status="candidate",
                )
                self.validator.validate(candidate, request=request)
                candidates.append(candidate)
            return candidates
        except RetrievalExecutionError:
            raise
        except (CandidateValidationError, VideoSourceProviderError, ValueError) as exc:
            raise RetrievalExecutionError("retrieval_execution", str(exc)) from exc

    def _validate_request(self, request: RetrievalRequest) -> RetrievalGapPlan:
        plan = self._plans.get(request.retrieval_plan_id)
        if plan is None:
            raise RetrievalExecutionError(
                "retrieval_request",
                "retrieval request references a missing RetrievalGapPlan",
            )
        if request.knowledge_id != plan.knowledge_id:
            raise RetrievalExecutionError(
                "retrieval_request",
                "retrieval request knowledge does not match its plan",
            )
        if request.query not in plan.query_hints:
            raise RetrievalExecutionError(
                "retrieval_request",
                "retrieval request query does not come from its plan",
            )
        if request.constraints != plan.constraints:
            raise RetrievalExecutionError(
                "retrieval_request",
                "retrieval request constraints do not match its plan",
            )
        return plan


__all__ = ["RetrievalExecutionError", "RetrievalExecutor"]
