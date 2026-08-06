from __future__ import annotations

import re

from ...schemas import PlanningControlIntent


def detect_planning_control_intent(text: str) -> PlanningControlIntent:
    normalized = re.sub(r"[\s。！？!?，,]+", "", (text or "").strip().lower())
    if normalized in {
        "跳过这一步",
        "跳过当前步骤",
        "按现有内容继续",
        "跳过这一步根据现有内容直接继续下一步",
        "skip",
        "skipthisstep",
        "skipcurrentstep",
        "skipthisstepandcontinuewiththeinformationalreadyprovided",
    }:
        return "skip_current_stage"
    if normalized in {
        "下一步",
        "继续",
        "开始规划",
        "请重试当前深度规划",
        "重试深度规划",
        "重试当前阶段",
        "next",
        "continue",
        "proceed",
        "startplanning",
        "retrydeepplanning",
        "retrythecurrentdeepplanningsession",
        "retrycurrentdeepplanning",
        "retrycurrentstage",
    }:
        return "continue_current_stage"
    if normalized in {"确认", "确认方向", "确认执行计划", "approve", "confirm", "yes", "ok", "okay"}:
        return "approve_current_stage"
    if normalized in {"修改", "调整", "revise", "modify", "change"}:
        return "modify_current_stage"
    if normalized in {"重新开始", "重新规划", "从头来", "restart", "startover"}:
        return "restart_planning"
    if normalized in {"取消", "取消规划", "cancel", "stop"}:
        return "cancel_planning"
    return "provide_goal_information"


__all__ = ["detect_planning_control_intent"]
