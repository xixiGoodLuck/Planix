from __future__ import annotations

import json
from typing import Any

from app.db import get_conn
from app.harness.persistence import HarnessStateRepository
from app.services.cognitive_planning.agents import CognitiveModelClient
from app.services.cognitive_planning.contracts import PlanCritiqueReport
from app.services.planning_agent_runtime import PlanningAgentRuntime

from planning_evals.test_cognitive_kernel import StubCognitiveModel


class _ApprovalAwareModel(StubCognitiveModel):
    def __init__(self) -> None:
        super().__init__()
        self.critic_approval_payloads: list[dict[str, Any]] = []

    def complete_contract(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        contract_type,
        **kwargs: Any,
    ):
        if contract_type is PlanCritiqueReport:
            approval = dict(payload.get("strategyApproval") or {})
            self.critic_approval_payloads.append(approval)
            assert approval.get("status") == "approved"
            assert approval.get("approvedStrategyId")
            assert approval.get("approvedStrategyId") == approval.get(
                "recommendedStrategyId"
            )
            portfolio = payload.get("strategyProposal") or payload.get(
                "strategyPortfolio"
            )
            assert portfolio["status"] == "approved"
            assert portfolio["approvedStrategyId"] == portfolio["recommendedStrategyId"]
        return super().complete_contract(
            task_type=task_type,
            payload=payload,
            contract_type=contract_type,
            **kwargs,
        )


def _events(response) -> list[dict[str, Any]]:
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_command_confirmation_binds_recommended_strategy_before_critic(
    client,
    monkeypatch,
) -> None:
    model = _ApprovalAwareModel()

    def complete_contract(_self, **kwargs):
        return model.complete_contract(**kwargs)

    monkeypatch.setenv("PLANIX_COGNITIVE_MODE", "true")
    monkeypatch.setenv("PLANIX_USE_COGNITIVE_PLANNING", "true")
    monkeypatch.setattr(CognitiveModelClient, "complete_contract", complete_contract)

    started = _events(
        client.post(
            "/api/command/chat",
            json={
                "message": "Plan a 30-day Python project with three hours available daily",
                "mode": "auto",
                "permission": "low",
            },
        )
    )
    thread_id = started[-1]["threadId"]
    initial_strategy = next(
        item for item in started if item["type"] == "strategy_portfolio_ready"
    )
    session_id = initial_strategy["sessionId"]
    recommended_id = initial_strategy["data"]["recommendedStrategyId"]
    assert initial_strategy["data"]["status"] == "waiting_user_approval"
    assert initial_strategy["data"].get("approvedStrategyId") is None

    confirmed = _events(
        client.post(
            "/api/command/chat",
            json={
                "threadId": thread_id,
                "message": "确认方向",
                "mode": "auto",
                "permission": "low",
            },
        )
    )

    approved_strategy = next(
        item for item in confirmed if item["type"] == "strategy_portfolio_ready"
    )
    assert approved_strategy["data"]["status"] == "approved"
    assert approved_strategy["data"]["approvedStrategyId"] == recommended_id
    assert approved_strategy["data"]["recommendedStrategyId"] == recommended_id
    assert model.critic_approval_payloads
    assert all(
        item["approvedStrategyId"] == recommended_id
        for item in model.critic_approval_payloads
    )
    assert any(
        item.get("type") == "planning_session_status"
        and item.get("status") == "waiting_execution_approval"
        for item in confirmed
    )

    state = HarnessStateRepository().recover(session_id)
    strategy_approvals = [
        item
        for item in state.approvals
        if item.gate == "strategy" and item.status == "approved"
    ]
    assert len(strategy_approvals) == 1
    strategy_head = state.checkpoint.artifact_refs["strategy_portfolio"]
    assert strategy_approvals[0].artifact.same_version(strategy_head)
    strategy_decisions = [
        item
        for item in PlanningAgentRuntime().list_decisions(session_id)
        if item.agent == "Strategy Agent" and item.decision == "approve"
    ]
    assert len(strategy_decisions) == 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT approved_strategy_id FROM planning_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    assert row["approved_strategy_id"] == recommended_id

    replay = client.get(f"/api/command/thread/{thread_id}")
    assert replay.status_code == 200
    replay_strategy_cards = [
        item
        for item in replay.json()["messages"]
        if item.get("kind") == "strategy_portfolio_ready"
    ]
    assert replay_strategy_cards[-1]["payload"]["data"]["status"] == "approved"
    assert (
        replay_strategy_cards[-1]["payload"]["data"]["approvedStrategyId"]
        == recommended_id
    )
