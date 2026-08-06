from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from ..db import get_conn
from ..services.planning_agent_runtime import PlanningAgentRuntime
from .contracts import AgentContract, ArtifactKind, ArtifactRef, ArtifactStatus
from .state import HarnessCheckpoint


class ArtifactContractViolation(RuntimeError):
    """Raised when an Agent invocation would violate its declared contract."""


def _content(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, exclude_none=True)
    return dict(value) if isinstance(value, Mapping) else {}


class HarnessArtifactStore:
    """Artifact-first adapter over the existing immutable planning artifact log.

    The Harness keeps only :class:`ArtifactRef` values in checkpoints. Artifact
    bodies remain in ``planning_artifacts`` and are loaded only for an Agent
    invocation or a compatibility projection.
    """

    def __init__(self, runtime: PlanningAgentRuntime | None = None):
        self.runtime = runtime or PlanningAgentRuntime()

    def heads(self, session_id: str) -> dict[ArtifactKind, ArtifactRef]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM planning_artifacts
                WHERE session_id = ?
                ORDER BY artifact_type ASC, version DESC, created_at DESC, id DESC
                """,
                (session_id,),
            ).fetchall()
        result: dict[ArtifactKind, ArtifactRef] = {}
        for row in rows:
            kind = str(row["artifact_type"])
            if kind in result:
                continue
            try:
                ref = self._ref(row)
            except Exception:
                # Legacy-only artifacts are outside the cognitive Harness.
                continue
            result[ref.kind] = ref
        return result

    def latest(self, session_id: str, kind: ArtifactKind) -> ArtifactRef | None:
        return self.heads(session_id).get(kind)

    def load(self, ref: ArtifactRef) -> dict[str, Any]:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM planning_artifacts
                WHERE id = ? AND session_id = ? AND artifact_type = ? AND version = ?
                """,
                (ref.id, ref.session_id, ref.kind, ref.version),
            ).fetchone()
        if not row:
            raise ArtifactContractViolation(
                f"artifact ref is missing or stale: {ref.kind}@{ref.version}"
            )
        try:
            parsed = json.loads(row["content_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ArtifactContractViolation(
                f"artifact body is invalid JSON: {ref.kind}@{ref.version}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ArtifactContractViolation(
                f"artifact body is not an object: {ref.kind}@{ref.version}"
            )
        return parsed

    def required_inputs(
        self,
        contract: AgentContract,
        *,
        session_id: str,
    ) -> dict[ArtifactKind, ArtifactRef]:
        heads = self.heads(session_id)
        missing = [kind for kind in contract.input_artifacts if kind not in heads]
        if missing:
            names = ", ".join(missing)
            raise ArtifactContractViolation(
                f"{contract.agent_id} is missing required artifact input(s): {names}"
            )
        return {kind: heads[kind] for kind in contract.input_artifacts}

    def record_output(
        self,
        contract: AgentContract,
        *,
        session_id: str,
        content: Any,
        status: ArtifactStatus = "draft",
        input_refs: Sequence[ArtifactRef] = (),
    ) -> ArtifactRef:
        if not contract.output_artifact:
            raise ArtifactContractViolation(
                f"{contract.agent_id} declares no output artifact"
            )
        if "write_artifact" not in contract.permissions:
            raise ArtifactContractViolation(
                f"{contract.agent_id} lacks write_artifact permission"
            )
        required = set(contract.input_artifacts)
        supplied = {item.kind for item in input_refs}
        if not required.issubset(supplied):
            names = ", ".join(sorted(required - supplied))
            raise ArtifactContractViolation(
                f"{contract.agent_id} output is missing lineage input(s): {names}"
            )
        for ref in input_refs:
            if ref.session_id != session_id:
                raise ArtifactContractViolation("cross-session artifact handoff is forbidden")
            self.load(ref)
        item = self.runtime.record_artifact(
            session_id,
            owner_agent=contract.name,
            artifact_type=contract.output_artifact,
            content=_content(content),
            status=status,
        )
        return ArtifactRef(
            id=item.id,
            sessionId=session_id,
            kind=contract.output_artifact,
            version=item.version,
            owner=item.owner_agent,
            status=item.status,
        )

    def checkpoint(self, session_id: str) -> HarnessCheckpoint:
        refs = self.heads(session_id)
        return HarnessCheckpoint(
            artifactRefs=refs,
            artifactVersions={kind: ref.version for kind, ref in refs.items()},
        )

    @staticmethod
    def _ref(row) -> ArtifactRef:
        return ArtifactRef(
            id=row["id"],
            sessionId=row["session_id"],
            kind=row["artifact_type"],
            version=int(row["version"] or 1),
            owner=row["owner_agent"],
            status=row["status"],
        )


__all__ = [
    "ArtifactContractViolation",
    "HarnessArtifactStore",
]
