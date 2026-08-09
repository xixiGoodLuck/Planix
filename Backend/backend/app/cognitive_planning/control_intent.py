from __future__ import annotations

import re

from ..schemas import PlanningControlIntent


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
    natural_approval = any(
        phrase in normalized
        for phrase in (
            "直接继续",
            "先按这些规划",
            "不用问了",
            "先生成看看",
            "按现在的信息继续",
            "按现在的信息规划",
            "按现在的信息先规划",
            "按当前信息继续",
            "其他先不填",
            "信息就这些",
            "可以开始规划",
            "不用补充",
            "不修改直接继续",
            "不用再改",
            "直接下一步",
            "写日历吧",
            "按这个理解继续",
            "按当前理解继续",
            "这个理解可以",
            "这个计划可以",
            "确认这个理解",
            "确认这个计划",
            "确认这个方案",
            "就按这个理解",
            "就按这个计划",
            "就按这个方案",
            "就这样",
            "按这个来",
        )
    )
    if normalized in {"确认", "确认方向", "确认执行计划", "approve", "confirm", "yes", "ok", "okay"} or natural_approval:
        return "approve_current_stage"
    if normalized in {"修改", "调整", "revise", "modify", "change"}:
        return "modify_current_stage"
    if normalized in {"重新开始", "重新规划", "从头来", "restart", "startover"}:
        return "restart_planning"
    if normalized in {"取消", "取消规划", "cancel", "stop"} or (
        "取消" in normalized and any(token in normalized for token in ("计划", "规划", "这个"))
    ):
        return "cancel_planning"
    return "provide_goal_information"


__all__ = ["detect_planning_control_intent"]
