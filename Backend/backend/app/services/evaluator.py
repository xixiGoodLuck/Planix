from datetime import date as date_type

from ..schemas import AiPayload


CRITERIA = [
    "goal_clarity",
    "material_grounding",
    "time_feasibility",
    "preference_personalization",
    "execution_loop",
    "portfolio_signal",
]

WEIGHTS = {
    "goal_clarity": 0.18,
    "material_grounding": 0.2,
    "time_feasibility": 0.16,
    "preference_personalization": 0.12,
    "execution_loop": 0.18,
    "portfolio_signal": 0.16,
}

PORTFOLIO_KEYWORDS = {
    "rag",
    "agent",
    "fastapi",
    "react",
    "typescript",
    "sqlite",
    "docker",
    "deploy",
    "evaluation",
    "eval",
    "prompt",
    "tool",
    "工具",
    "评测",
    "部署",
    "引用",
    "检索",
}


def _bounded(score: float) -> int:
    return max(1, min(5, round(score)))


def _text_len(value: str) -> int:
    return len(value.strip())


def _plans_from_payload(payload: AiPayload) -> list[dict[str, object]]:
    plans: list[dict[str, object]] = []
    for day in payload.data.values():
        if not isinstance(day, dict):
            continue
        day_plans = day.get("plans", [])
        if isinstance(day_plans, list):
            plans.extend([dict(plan) for plan in day_plans if isinstance(plan, dict)])
    return plans


def _days_until_deadline(payload: AiPayload) -> int | None:
    if not payload.deadline:
        return None
    try:
        deadline = date_type.fromisoformat(payload.deadline)
        start = date_type.fromisoformat(payload.date) if payload.date else date_type.today()
    except ValueError:
        return None
    return (deadline - start).days


class PlannerEvaluator:
    def run(self, payload: AiPayload) -> dict[str, object]:
        plans = _plans_from_payload(payload)
        done_count = len([plan for plan in plans if plan.get("done")])
        keyword_hits = self._portfolio_keyword_hits(payload)
        results = [
            self._goal_clarity(payload),
            self._material_grounding(payload),
            self._time_feasibility(payload),
            self._preference_personalization(payload),
            self._execution_loop(plans, done_count),
            self._portfolio_signal(keyword_hits),
        ]
        weighted = sum(item["score"] * WEIGHTS[item["key"]] for item in results)
        return {
            "mode": "api",
            "score": round(weighted, 1),
            "criteria": CRITERIA,
            "results": [
                {"case": item["case"], "score": item["score"], "reason": item["reason"]}
                for item in results
            ],
        }

    def _goal_clarity(self, payload: AiPayload) -> dict[str, object]:
        length = _text_len(payload.goal)
        if length >= 28:
            score = 5
            reason = "目标具体，适合作为规划和复盘的主锚点。"
        elif length >= 12:
            score = 4
            reason = "目标已经可用，但还可以补充岗位、期限或产出标准。"
        elif length > 0:
            score = 2
            reason = "目标偏短，AI 很难稳定拆成阶段计划。"
        else:
            score = 1
            reason = "缺少长期目标，规划没有明确方向。"
        return {"key": "goal_clarity", "case": "目标明确度", "score": score, "reason": reason}

    def _material_grounding(self, payload: AiPayload) -> dict[str, object]:
        length = _text_len(payload.materials)
        if length >= 160:
            score = 5
            reason = "资料足够支撑 RAG grounding，可提取岗位要求和证据来源。"
        elif length >= 60:
            score = 4
            reason = "已有可用上下文，建议继续补充 JD、课程资料或项目说明。"
        elif length > 0:
            score = 2
            reason = "资料过短，只能提供弱 grounding。"
        else:
            score = 1
            reason = "缺少资料输入，规划更像通用建议。"
        return {"key": "material_grounding", "case": "资料 grounding", "score": score, "reason": reason}

    def _time_feasibility(self, payload: AiPayload) -> dict[str, object]:
        days_left = _days_until_deadline(payload)
        hours = payload.daily_hours
        if days_left is not None and days_left >= 14 and 1 <= hours <= 8:
            score = 5
            reason = f"期限和每日 {hours:g} 小时投入较清晰，适合生成可执行节奏。"
        elif days_left is not None and days_left > 0 and hours > 0:
            score = 4
            reason = "有期限和时间投入，但需要更严格控制任务粒度。"
        elif hours > 0:
            score = 3
            reason = "有每日时间投入，但缺少明确截止日期。"
        else:
            score = 1
            reason = "缺少可用时间，计划难以判断可行性。"
        return {"key": "time_feasibility", "case": "时间可行性", "score": score, "reason": reason}

    def _preference_personalization(self, payload: AiPayload) -> dict[str, object]:
        length = _text_len(payload.preferences)
        if length >= 30:
            score = 5
            reason = "偏好信息充分，可用于安排高效时段和任务类型。"
        elif length >= 8:
            score = 4
            reason = "已有个人节奏信息，能支持基础个性化。"
        elif length > 0:
            score = 2
            reason = "偏好信息过短，个性化效果有限。"
        else:
            score = 1
            reason = "缺少偏好记忆，计划只能按通用节奏生成。"
        return {"key": "preference_personalization", "case": "偏好个性化", "score": score, "reason": reason}

    def _execution_loop(self, plans: list[dict[str, object]], done_count: int) -> dict[str, object]:
        if plans and 0 < done_count < len(plans):
            score = 5
            reason = "已有完成和未完成记录，可支撑复盘与重排闭环。"
        elif plans:
            score = 4
            reason = "已有任务记录，建议补充完成状态形成反馈闭环。"
        else:
            score = 2
            reason = "缺少历史任务数据，暂时无法评估执行反馈。"
        return {"key": "execution_loop", "case": "执行闭环", "score": score, "reason": reason}

    def _portfolio_signal(self, keyword_hits: int) -> dict[str, object]:
        score = _bounded(1 + keyword_hits)
        if score >= 5:
            reason = "目标和资料覆盖多个 AI 应用关键词，简历可讲性强。"
        elif score >= 3:
            reason = "已有部分 AI 应用关键词，建议补齐 RAG、Agent、评测或部署证据。"
        else:
            reason = "作品集信号较弱，建议补充工程关键词和可展示产出。"
        return {"key": "portfolio_signal", "case": "作品集信号", "score": score, "reason": reason}

    def _portfolio_keyword_hits(self, payload: AiPayload) -> int:
        text = f"{payload.goal} {payload.materials}".lower()
        return len([keyword for keyword in PORTFOLIO_KEYWORDS if keyword in text])
