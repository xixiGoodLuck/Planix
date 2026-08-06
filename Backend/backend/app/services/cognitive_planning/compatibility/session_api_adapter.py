from __future__ import annotations

from typing import Any

from ....db import get_conn
from ....schemas import (
    CognitivePlanningMetadata,
    PendingPlanningInput,
    PendingPlanningQuestion,
    PlanningLocalizedText,
    PlanningModelFailure,
    PlanningModelFailureAttempt,
    PlanningSessionResponse,
    UserNeedContract,
)
from ...planning_agent_runtime import PlanningAgentRuntime
from ..contracts import (
    EvidencePack,
    ExecutionBlueprint,
    GoalCompletionResult,
    PlanCritiqueReport,
    PlanningLearningUpdate,
    RealityAssessment,
    StrategyPortfolio,
    UserGoalModel,
)
from ..orchestration.persistence import json_list, json_object
from .legacy_schema_adapter import (
    evidence_to_memory,
    evidence_to_resources,
    execution_to_draft,
    goal_to_contract,
    learning_to_patch,
    strategy_to_design,
)


_SAFE_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "zhipu_glm": "GLM",
    "kimi": "Kimi",
    "openai": "OpenAI",
    "custom": "Custom",
    "mock": "Mock",
}
_SAFE_MODEL_ERROR_TYPES = {
    "auth_error",
    "bad_base_url",
    "bad_model",
    "bad_request",
    "insufficient_balance",
    "invalid_key_format",
    "invalid_model_output",
    "missing_api_key",
    "model_output_truncated",
    "network_error",
    "rate_limit",
    "timeout",
    "unknown",
}
_ZH_ERROR_LABELS = {
    "auth_error": "API Key 无效或已过期",
    "bad_base_url": "服务地址不可用",
    "bad_model": "模型名称不可用",
    "bad_request": "请求配置不受支持",
    "insufficient_balance": "余额或额度不足",
    "invalid_key_format": "API Key 格式无效",
    "invalid_model_output": "模型输出不符合结构化协议",
    "missing_api_key": "未配置 API Key",
    "model_output_truncated": "模型输出被截断",
    "network_error": "模型服务无法连接",
    "rate_limit": "模型服务触发频率限制",
    "timeout": "模型服务响应超时",
    "unknown": "模型服务未返回可用结果",
}
_EN_ERROR_LABELS = {
    "auth_error": "the API Key was invalid or expired",
    "bad_base_url": "the service endpoint was unavailable",
    "bad_model": "the configured model was unavailable",
    "bad_request": "the request configuration was unsupported",
    "insufficient_balance": "the account had insufficient balance or quota",
    "invalid_key_format": "the API Key format was invalid",
    "invalid_model_output": "the output did not satisfy the structured contract",
    "missing_api_key": "no API Key was configured",
    "model_output_truncated": "the model output was truncated",
    "network_error": "the model service could not be reached",
    "rate_limit": "the model service was rate-limited",
    "timeout": "the model service timed out",
    "unknown": "the model service did not return a usable result",
}
_RESUME_NODE_BY_STAGE = {
    "goal_intelligence": "goal_intelligence",
    "goal_modeling": "goal_intelligence",
    "goal_completion": "goal_completion",
    "reality_assessment": "reality",
    "context_evidence": "evidence",
    "evidence_synthesis": "evidence",
    "strategy_architecture": "strategy",
    "strategy_design": "strategy",
    "execution_design": "execution",
    "execution_critique": "critique",
    "planning_learning": "learning",
}


def _safe_failure_attempts(raw_attempts: object) -> tuple[list[PlanningModelFailureAttempt], bool]:
    if not isinstance(raw_attempts, list):
        return [], False
    attempts: list[PlanningModelFailureAttempt] = []
    automatic_retry_attempted = False
    truncated_providers: set[str] = set()
    for raw in raw_attempts:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        if provider not in _SAFE_PROVIDER_LABELS:
            continue
        status = str(raw.get("status") or "error").strip().lower()
        if status not in {"success", "error", "skipped"}:
            status = "error"
        error_type = str(raw.get("errorType") or raw.get("error_type") or "").strip().lower()
        if status != "success" and error_type not in _SAFE_MODEL_ERROR_TYPES:
            error_type = "unknown"
        if status == "success":
            error_type = ""
        if bool(raw.get("automaticRetry") or raw.get("automatic_retry")):
            automatic_retry_attempted = True
        if provider in truncated_providers:
            automatic_retry_attempted = True
        if error_type == "model_output_truncated":
            truncated_providers.add(provider)
        attempts.append(
            PlanningModelFailureAttempt(
                provider=provider,
                status=status,
                errorType=error_type or None,
            )
        )
    return attempts, automatic_retry_attempted


def _failure_copy(
    attempts: list[PlanningModelFailureAttempt],
    *,
    fallback_error_type: str,
    automatic_retry_attempted: bool,
) -> tuple[PlanningLocalizedText, PlanningLocalizedText]:
    labels: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for attempt in attempts:
        error_type = attempt.error_type or ("unknown" if attempt.status != "success" else "")
        if not error_type:
            continue
        key = (attempt.provider, error_type)
        if key in seen:
            continue
        seen.add(key)
        labels.append(key)
    fallback_is_safe = fallback_error_type in _SAFE_MODEL_ERROR_TYPES
    safe_fallback = fallback_error_type if fallback_is_safe else "unknown"
    covered_error_types = {error_type for _, error_type in labels}
    if not labels or (fallback_is_safe and safe_fallback not in covered_error_types):
        # Contract validation happens after a provider-level request succeeds.
        # Preserve that final error without falsely assigning it to a Provider.
        labels.append(("", safe_fallback))

    zh_details = "；".join(
        f"{_SAFE_PROVIDER_LABELS[provider]}：{_ZH_ERROR_LABELS[error_type]}"
        if provider
        else _ZH_ERROR_LABELS[error_type]
        for provider, error_type in labels
    )
    en_details = "; ".join(
        f"{_SAFE_PROVIDER_LABELS[provider]}: {_EN_ERROR_LABELS[error_type]}"
        if provider
        else _EN_ERROR_LABELS[error_type]
        for provider, error_type in labels
    )
    summary = PlanningLocalizedText(
        zh=f"当前规划阶段未完成；上一次已确认的事实和产物已保留。{zh_details}。",
        en=f"The current planning stage did not complete; the last confirmed facts and artifacts were preserved. {en_details}.",
    )

    error_types = {error_type for _, error_type in labels}
    configuration_errors = {
        "auth_error",
        "bad_base_url",
        "bad_model",
        "bad_request",
        "insufficient_balance",
        "invalid_key_format",
        "missing_api_key",
    }
    if error_types & configuration_errors:
        zh_action = "请在设置中检查不可用的 Provider，或选择其他已配置模型，然后重试当前阶段。"
        en_action = "Check the unavailable providers in Settings or select another configured model, then retry the current stage."
    elif error_types & {"model_output_truncated", "invalid_model_output"}:
        zh_action = "请重试当前阶段；若仍失败，请切换到其他已配置模型。"
        en_action = "Retry the current stage; if it still fails, select another configured model."
    else:
        zh_action = "请稍后重试当前阶段；若仍失败，请检查模型设置与网络。"
        en_action = "Retry the current stage shortly; if it still fails, check model Settings and the network."
    if automatic_retry_attempted:
        zh_action = f"系统已自动重试一次。{zh_action}"
        en_action = f"Planix already retried once automatically. {en_action}"
    return summary, PlanningLocalizedText(zh=zh_action, en=en_action)


def _model_failure(
    *,
    status: str,
    metadata: CognitivePlanningMetadata | None,
    messages: list[Any],
) -> PlanningModelFailure | None:
    if status != "MODEL_UNAVAILABLE":
        return None
    block = next(
        (
            message
            for message in reversed(messages)
            if message.message_type == "block" and not message.resolved
        ),
        None,
    )
    if block is None:
        return None
    payload = block.payload_json if isinstance(block.payload_json, dict) else {}
    attempts, derived_retry = _safe_failure_attempts(payload.get("attempts"))
    automatic_retry_attempted = bool(payload.get("automaticRetryAttempted")) or derived_retry
    fallback_error_type = str(payload.get("errorType") or "unknown").strip().lower()
    summary, action = _failure_copy(
        attempts,
        fallback_error_type=fallback_error_type,
        automatic_retry_attempted=automatic_retry_attempted,
    )
    stage = str((metadata.current_stage if metadata else "") or "goal_intelligence")
    resume_node = str(payload.get("resumeNode") or _RESUME_NODE_BY_STAGE.get(stage) or stage)
    return PlanningModelFailure(
        stage=stage,
        resumeNode=resume_node,
        # Public retryability means the saved checkpoint can be resumed after
        # transient recovery or a Settings change. Agent-level auth failures
        # are not immediately retryable, but the stage itself remains so.
        retryable=True,
        automaticRetryAttempted=automatic_retry_attempted,
        attempts=attempts,
        summary=summary,
        action=action,
    )


def _latest_unresolved_block(session_id: str, messages: list[Any]) -> Any | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM agent_messages
            WHERE session_id = ? AND message_type = 'block' AND resolved = 0
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if row:
        match = next((message for message in messages if message.id == row["id"]), None)
        if match is not None:
            return match
    return next(
        (
            message
            for message in reversed(messages)
            if message.message_type == "block" and not message.resolved
        ),
        None,
    )


def _pending_input(row, model_failure: PlanningModelFailure | None) -> PendingPlanningInput | None:
    if (
        row["status"] != "MODEL_UNAVAILABLE"
        or model_failure is None
        or model_failure.resume_node not in {"goal_intelligence", "goal_completion"}
    ):
        return None
    history = json_list(row["conversation_history_json"] if "conversation_history_json" in row.keys() else "[]")
    for turn in reversed(history):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        text = str(turn.get("content") or "").strip()
        if text:
            return PendingPlanningInput(text=text, applied=False)
    fallback = str(row["user_input"] or "").strip()
    return PendingPlanningInput(text=fallback, applied=False) if fallback else None


class SessionApiAdapter:
    def __init__(self, agent_runtime: PlanningAgentRuntime | None = None):
        self.agent_runtime = agent_runtime or PlanningAgentRuntime()

    def from_row(self, row) -> PlanningSessionResponse:
        goal_raw = json_object(row["goal_model_json"])
        completion_raw = json_object(row["goal_completion_json"]) if "goal_completion_json" in row.keys() else {}
        evidence_raw = json_object(row["evidence_pack_json"])
        reality_raw = json_object(row["reality_assessment_json"]) if "reality_assessment_json" in row.keys() else {}
        strategy_raw = json_object(row["strategy_portfolio_json"])
        execution_raw = json_object(row["execution_blueprint_json"])
        critique_raw = json_object(row["critique_report_json"])
        learning_raw = json_object(row["planning_learning_update_json"])
        metadata_raw = json_object(row["cognitive_metadata_json"])

        goal = UserGoalModel.model_validate(goal_raw) if goal_raw else None
        completion = GoalCompletionResult.model_validate(completion_raw) if completion_raw else None
        evidence = EvidencePack.model_validate(evidence_raw) if evidence_raw else None
        reality = RealityAssessment.model_validate(reality_raw) if reality_raw else None
        strategy = StrategyPortfolio.model_validate(strategy_raw) if strategy_raw else None
        row_approved_strategy_id = (
            (row["approved_strategy_id"] or None)
            if "approved_strategy_id" in row.keys()
            else None
        )
        if (
            strategy is not None
            and row_approved_strategy_id
            and row_approved_strategy_id == strategy.recommended_strategy_id
        ):
            strategy = strategy.model_copy(
                update={
                    "approved_strategy_id": row_approved_strategy_id,
                    "status": "approved",
                }
            )
            strategy_raw = strategy.model_dump(by_alias=True)
        execution = ExecutionBlueprint.model_validate(execution_raw) if execution_raw else None
        critique = PlanCritiqueReport.model_validate(critique_raw) if critique_raw else None
        learning = PlanningLearningUpdate.model_validate(learning_raw) if learning_raw else None
        metadata = CognitivePlanningMetadata.model_validate(metadata_raw) if metadata_raw else None
        legacy_evidence = bool(evidence and not evidence.is_authority_normalized)
        public_evidence = evidence.decision_view() if evidence and not legacy_evidence else None
        if legacy_evidence and metadata:
            metadata = metadata.model_copy(update={"current_stage": "evidence_synthesis"})

        design_approved = bool(execution or row["status"] in {"waiting_execution_approval", "ready_to_write_calendar", "waiting_calendar_write_approval", "written_to_calendar"})
        execution_approved = row["status"] in {"ready_to_write_calendar", "waiting_calendar_write_approval", "written_to_calendar"}
        contract = goal_to_contract(goal, row["user_input"]) if goal else None
        if contract and completion:
            if completion.complete:
                contract = contract.model_copy(
                    update={
                        "can_move_to_design": True,
                        "missing_information": [],
                        "clarification_questions": [],
                        "pending_question": None,
                    }
                )
            else:
                questions = [item.question for item in completion.blocking_unknowns]
                impacts = [item.impact for item in completion.blocking_unknowns]
                contract = contract.model_copy(
                    update={
                        "can_move_to_design": False,
                        "missing_information": impacts,
                        "clarification_questions": questions,
                        "pending_question": PendingPlanningQuestion(
                            askedFields=["blocking_unknown"],
                            expectedAnswerType="goal_clarification",
                            questionText=questions[0],
                            questions=questions,
                        ),
                    }
                )
        memory = evidence_to_memory(public_evidence) if public_evidence else None
        resources = evidence_to_resources(public_evidence, goal.domain if goal else "") if public_evidence else None
        design = (
            strategy_to_design(strategy, goal, approved=design_approved)
            if strategy and goal and not legacy_evidence
            else None
        )
        draft = (
            execution_to_draft(execution, strategy, critique, approved=execution_approved)
            if execution and strategy and not legacy_evidence
            else None
        )
        patch = learning_to_patch(learning) if learning else None
        if contract and evidence and not evidence.can_proceed_to_strategy:
            gaps = [item.description for item in evidence.gaps]
            questions = [item.description for item in evidence.gaps if item.proposed_resolution == "ask_user"][:3]
            contract = contract.model_copy(
                update={
                    "can_move_to_design": False,
                    "missing_information": [*contract.missing_information, *gaps],
                    "clarification_questions": questions or contract.clarification_questions,
                    "pending_question": PendingPlanningQuestion(
                        askedFields=["evidence_gap"],
                        expectedAnswerType="evidence_gap",
                        questionText=(questions or gaps or ["More evidence is required before strategy design."])[0],
                        questions=questions or gaps[:3],
                    ),
                }
            )
        if contract and legacy_evidence:
            contract = contract.model_copy(
                update={
                    "can_move_to_design": False,
                    "missing_information": ["evidence_authority_refresh"],
                    "clarification_questions": [],
                    "pending_question": None,
                }
            )
        if not contract:
            legacy_contract = json_object(row["user_need_contract_json"])
            contract = UserNeedContract.model_validate(legacy_contract) if legacy_contract else None
        if (
            contract
            and metadata
            and metadata.planning_mode == "blocked_model_unavailable"
            and not (completion and completion.complete)
        ):
            contract = contract.model_copy(update={"can_move_to_design": False})

        messages = self.agent_runtime.list_messages(row["id"])
        latest_block = _latest_unresolved_block(row["id"], messages)
        model_failure = _model_failure(
            status=row["status"],
            metadata=metadata,
            messages=[latest_block] if latest_block is not None else [],
        )
        if legacy_evidence:
            model_failure = PlanningModelFailure(
                stage="evidence_authority",
                resumeNode="evidence",
                retryable=True,
                automaticRetryAttempted=False,
                attempts=[],
                summary=PlanningLocalizedText(
                    zh="现有 Evidence 来自旧版权威策略，已停止向后续规划传递。",
                    en="The saved Evidence predates the current authority policy and is quarantined.",
                ),
                action=PlanningLocalizedText(
                    zh="请重试当前阶段；Planix 将保留旧 Artifact 审计并重新生成 Evidence。",
                    en="Retry the current stage to regenerate Evidence while preserving the old audit artifact.",
                ),
            )

        public_strategy_raw = (strategy_raw or None) if not legacy_evidence else None
        public_execution_raw = (execution_raw or None) if not legacy_evidence else None
        public_critique_raw = (critique_raw or None) if not legacy_evidence else None
        public_learning_raw = (learning_raw or None) if not legacy_evidence else None
        public_approved_strategy_id = (
            row_approved_strategy_id
            if (
                not legacy_evidence
                and strategy is not None
                and strategy.status == "approved"
                and strategy.approved_strategy_id == strategy.recommended_strategy_id
            )
            else None
        )
        failure_resume_node = model_failure.resume_node if model_failure else None
        if failure_resume_node in {"goal_intelligence", "goal_completion", "reality", "evidence"}:
            public_evidence = None
            memory = None
            resources = None
            design = None
            draft = None
            patch = None
            public_strategy_raw = None
            public_execution_raw = None
            public_critique_raw = None
            public_learning_raw = None
            public_approved_strategy_id = None
        elif failure_resume_node == "strategy":
            design = None
            draft = None
            patch = None
            public_strategy_raw = None
            public_execution_raw = None
            public_critique_raw = None
            public_learning_raw = None
            public_approved_strategy_id = None
        elif failure_resume_node in {"execution", "critic"}:
            draft = None
            patch = None
            public_execution_raw = None
            public_critique_raw = None
            public_learning_raw = None
        elif failure_resume_node == "learning":
            patch = None
            public_learning_raw = None

        response_status = "MODEL_UNAVAILABLE" if legacy_evidence else row["status"]
        response_business_status = (
            "evidence_pending"
            if legacy_evidence
            else (row["business_status"] or "goal_clarification")
            if "business_status" in row.keys()
            else "goal_clarification"
        )
        response_runtime_status = (
            "idle"
            if legacy_evidence
            else (row["runtime_status"] or "idle")
            if "runtime_status" in row.keys()
            else "idle"
        )

        return PlanningSessionResponse(
            sessionId=row["id"],
            threadId=row["thread_id"],
            entryPoint=row["entry_point"],
            status=response_status,
            businessStatus=response_business_status,
            runtimeStatus=response_runtime_status,
            userInput=row["user_input"],
            userNeedContract=contract,
            pendingQuestion=contract.pending_question if contract else None,
            memoryInsight=memory,
            resourceBrief=resources,
            designProposal=design,
            executionDraft=draft,
            learningPatch=patch,
            cognitiveMetadata=metadata,
            goalModel=goal_raw or None,
            goalCompletion=completion.model_dump(by_alias=True) if completion else None,
            realityAssessment=reality.model_dump(by_alias=True) if reality else None,
            evidencePack=public_evidence.model_input_view() if public_evidence else None,
            strategyPortfolio=public_strategy_raw,
            executionBlueprint=public_execution_raw,
            critiqueReport=public_critique_raw,
            planningLearningUpdate=public_learning_raw,
            approvedStrategyId=public_approved_strategy_id,
            modelFailure=model_failure,
            pendingInput=_pending_input(row, model_failure),
            artifacts=self.agent_runtime.list_artifacts(row["id"]),
            decisions=self.agent_runtime.list_decisions(row["id"]),
            messages=messages,
            version=int(row["version"] or 1),
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )
