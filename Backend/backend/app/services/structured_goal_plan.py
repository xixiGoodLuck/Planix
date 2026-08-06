from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from ..schemas import (
    GoalMilestone,
    GoalPlanTask,
    PhaseItem,
    PlanDensityPolicy,
    PlanHorizon,
    PlannerTask,
    RagSource,
    ReviewPlan,
    StructuredGoalPlan,
)


def is_python_goal(text: str) -> bool:
    normalized = text.lower()
    return "python" in normalized or "py " in normalized or " py" in normalized


def build_local_structured_plan(
    goal: str,
    *,
    date: str = "",
    deadline: str = "",
    daily_hours: float = 2,
    source_count: int = 0,
    horizon: PlanHorizon | None = None,
    policy: PlanDensityPolicy | None = None,
) -> StructuredGoalPlan:
    cleaned_goal = goal.strip() or "提升 AI 应用开发能力"
    duration_days = horizon.duration_days if horizon else _duration_days(date, deadline, 28 if is_python_goal(cleaned_goal) else 21)
    start_date = _parse_date_or_none(horizon.start_date if horizon else date)
    daily_minutes = max(30, min(int(float(daily_hours or 2) * 60), 480))
    min_total_tasks = policy.min_total_tasks if policy else (8 if is_python_goal(cleaned_goal) else 6)

    if is_python_goal(cleaned_goal):
        title = "Python 入门到项目实战"
        description = f"用 {duration_days} 天建立 Python 基础、完成可展示的小项目，并形成可复盘的学习证据。"
        milestones = _fallback_milestones(
            duration_days=duration_days,
            min_total_tasks=min_total_tasks,
            start_date=start_date,
            daily_minutes=daily_minutes,
            monthly_titles=["基础语法与工具", "项目能力强化", "整合输出与复盘"],
            weekly_actions=[
                ("搭建 Python 环境并完成变量、条件判断练习", "安装环境，写出 3 个小练习，记录输入、输出和报错处理。"),
                ("练习循环、函数、列表和字典", "用 for/while、函数拆分和 list/dict 完成一个可运行脚本。"),
                ("完成文件读写和 JSON/CSV 小练习", "读取本地文件、清洗简单数据，并保存结构化结果。"),
                ("整理阶段复盘和错题清单", "记录语法卡点、常见报错和下一阶段要补的知识点。"),
                ("实现命令行 Todo 小项目", "完成新增、查看、完成、删除任务，并把数据保存到本地文件。"),
                ("补充异常处理和模块拆分", "用 try/except、函数和模块结构提升脚本可维护性。"),
                ("扩展一个 FastAPI 小接口", "把 Todo 或学习记录封装成 API，练习请求、响应和数据校验。"),
                ("为项目补测试样例和 README", "写出运行方式、核心接口、截图说明和后续改进。"),
                ("整理作品集项目叙述", "把项目目标、技术栈、关键难点和解决方式写成简历素材。"),
                ("模拟讲解项目并修正薄弱点", "用 3 分钟讲清项目，标记讲不顺的技术点并补练。"),
                ("完成最终复盘和下一轮规划", "汇总完成证据、卡点、后续学习路线和可展示材料。"),
                ("保留一项综合输出", "将代码、README、截图或演示说明整理到同一个作品目录。"),
            ],
        )
    else:
        title = cleaned_goal[:42]
        description = f"围绕“{cleaned_goal}”生成 {duration_days} 天的结构化执行计划，按阶段推进并保留可检查产出。"
        milestones = _fallback_milestones(
            duration_days=duration_days,
            min_total_tasks=min_total_tasks,
            start_date=start_date,
            daily_minutes=daily_minutes,
            monthly_titles=["基础建立", "强化推进", "整合输出"],
            weekly_actions=[
                ("明确目标验收标准和时间边界", "写清目标、当前基础、每日可用时间和最终可检查产出。"),
                ("整理资料、约束和参考样例", "收集已有资料、历史复盘或任务约束，形成一页资料清单。"),
                ("完成第一个最小可验证产出", "用文档、代码、清单或截图证明目标已经开始推进。"),
                ("复盘本周卡点并调整任务顺序", "记录卡点、风险和下一周最应该优先推进的动作。"),
                ("推进一个核心能力或核心模块", "选择最影响目标达成的一项能力，完成一次集中练习或实现。"),
                ("产出阶段性证据材料", "整理阶段结果，让它可以被复查、展示或继续迭代。"),
                ("补齐短板并做一次小测验", "针对薄弱点完成练习、检查清单或模拟问答。"),
                ("根据反馈优化计划", "根据完成情况删减低价值任务，保留最能推动目标的动作。"),
                ("整合前期输出", "把分散材料合并成一个结构清晰的版本。"),
                ("完成最终检查和风险补救", "检查遗漏任务、延期风险和需要外部资料确认的部分。"),
                ("准备展示或交付说明", "用简短说明讲清目标、过程、结果和下一步。"),
                ("完成总结和下一阶段规划", "沉淀经验、保留证据，并生成下一轮计划。"),
            ],
        )

    if source_count:
        description = f"{description} 本次规划参考了 {source_count} 条资料片段。"

    return StructuredGoalPlan(
        goalTitle=title,
        goalDescription=description,
        durationDays=duration_days,
        milestones=milestones,
        reviewPlan=ReviewPlan(
            frequency="daily",
            questions=[
                "今天完成了哪些可验证产出？",
                "哪些任务卡住了，原因是什么？",
                "明天最应该优先推进哪一步？",
            ],
        ),
    )


def normalize_structured_plan(raw: object, fallback: StructuredGoalPlan) -> StructuredGoalPlan:
    if not isinstance(raw, dict):
        return fallback

    milestones = _normalize_milestones(raw.get("milestones"), fallback.milestones)
    review_plan = _normalize_review_plan(raw.get("reviewPlan") or raw.get("review_plan"), fallback.review_plan)
    candidate = {
        "goalTitle": _text(raw.get("goalTitle") or raw.get("goal_title") or raw.get("title"), fallback.goal_title),
        "goalDescription": _text(raw.get("goalDescription") or raw.get("goal_description") or raw.get("summary"), fallback.goal_description),
        "durationDays": _int(raw.get("durationDays") or raw.get("duration_days"), fallback.duration_days, 1, 3650),
        "milestones": [milestone.model_dump(by_alias=True) for milestone in milestones],
        "reviewPlan": review_plan.model_dump(by_alias=True),
    }
    try:
        return StructuredGoalPlan.model_validate(candidate)
    except ValidationError:
        return fallback


def derive_phase_items(plan: StructuredGoalPlan) -> list[PhaseItem]:
    return [PhaseItem(title=milestone.title, detail=milestone.description) for milestone in plan.milestones]


def derive_planner_tasks(plan: StructuredGoalPlan) -> list[PlannerTask]:
    times = ["09:00", "14:30", "20:30"]
    tasks: list[PlannerTask] = []
    for milestone in plan.milestones:
        for task in milestone.tasks:
            tasks.append(
                PlannerTask(
                    time=times[len(tasks) % len(times)],
                    title=task.title,
                    reason=f"{milestone.title}: {task.description}",
                )
            )
            if len(tasks) >= 3:
                return tasks
    return tasks


def render_goal_plan_markdown(
    plan: StructuredGoalPlan,
    sources: list[RagSource] | list[dict[str, Any]] | None = None,
    *,
    local_template: bool = False,
    today_plan_count: int = 0,
    source_type: str = "",
) -> str:
    lines = []
    if source_type in {"insufficient_context", "model_knowledge"}:
        lines.append("我没有在本地资料中找到足够依据，因此下面只能作为通用建议，不代表你的资料库事实。")
        lines.append("")
    if local_template:
        lines.append("当前使用本地规划模板生成，后端 Runtime 未连接或模型输出未通过质量校验。")
        lines.append("")
    lines.extend(
        [
            f"## {plan.goal_title}",
            plan.goal_description,
            f"周期：{plan.duration_days} 天",
            "",
        ]
    )
    for index, milestone in enumerate(plan.milestones, start=1):
        lines.append(f"### {index}. {milestone.title}")
        if milestone.description:
            lines.append(milestone.description)
        for task in milestone.tasks:
            due = f"，截止 {task.due_date}" if task.due_date else ""
            lines.append(
                f"- [{task.priority}] {task.title}（约 {task.estimated_minutes} 分钟{due}）：{task.description}"
            )
        lines.append("")

    if today_plan_count:
        lines.append(f"当前日程中已有 {today_plan_count} 条今日任务，建议写入前先确认负载。")
        lines.append("")

    frequency = "每日" if plan.review_plan.frequency == "daily" else "每周"
    lines.append(f"复盘频率：{frequency}")
    for question in plan.review_plan.questions:
        lines.append(f"- {question}")

    source_titles = _source_titles(sources or [])
    if source_titles:
        lines.append("")
        lines.append("参考资料：")
        for index, title in enumerate(source_titles, start=1):
            lines.append(f"{index}. {title}")

    return "\n".join(lines).strip()


def _fallback_milestones(
    *,
    duration_days: int,
    min_total_tasks: int,
    start_date: date_type | None,
    daily_minutes: int,
    monthly_titles: list[str],
    weekly_actions: list[tuple[str, str]],
) -> list[GoalMilestone]:
    milestone_count = _fallback_milestone_count(duration_days, min_total_tasks)
    total_tasks = max(min_total_tasks, milestone_count * 2)
    tasks_per_milestone = _distribute(total_tasks, milestone_count)
    offsets = _distributed_offsets(duration_days, total_tasks)
    milestones: list[GoalMilestone] = []
    task_cursor = 0
    for milestone_index in range(milestone_count):
        phase_name = monthly_titles[milestone_index % len(monthly_titles)]
        milestone_tasks: list[GoalPlanTask] = []
        for local_index in range(tasks_per_milestone[milestone_index]):
            action_title, action_description = weekly_actions[task_cursor % len(weekly_actions)]
            week_number = (offsets[task_cursor] // 7) + 1
            title = f"第 {week_number} 周：{action_title}" if duration_days >= 30 else action_title
            priority = "high" if task_cursor < max(2, total_tasks // 4) else "medium"
            milestone_tasks.append(
                _task(
                    title,
                    action_description,
                    min(daily_minutes, 90 if priority == "high" else 60),
                    _due(start_date, offsets[task_cursor]),
                    priority,
                )
            )
            task_cursor += 1
        milestones.append(
            GoalMilestone(
                title=f"第 {milestone_index + 1} 阶段：{phase_name}",
                description=f"围绕 {phase_name} 推进，保留每周可检查的进展证据。",
                tasks=milestone_tasks,
            )
        )
    return milestones


def _fallback_milestone_count(duration_days: int, min_total_tasks: int) -> int:
    if duration_days <= 7:
        return 1
    if duration_days <= 14:
        return 2
    if duration_days <= 30:
        return 4
    if duration_days <= 90:
        return 3
    return max(6, min(12, (min_total_tasks + 5) // 6))


def _distribute(total: int, bucket_count: int) -> list[int]:
    base = total // bucket_count
    remainder = total % bucket_count
    return [base + (1 if index < remainder else 0) for index in range(bucket_count)]


def _distributed_offsets(duration_days: int, total_tasks: int) -> list[int]:
    if total_tasks <= 1:
        return [duration_days]
    if duration_days <= 7:
        return [max(1, min(duration_days, index + 1)) for index in range(total_tasks)]
    step = max(1, duration_days / total_tasks)
    return [max(1, min(duration_days, round((index + 1) * step))) for index in range(total_tasks)]


def _task(title: str, description: str, minutes: int, due_date: str | None, priority: str) -> GoalPlanTask:
    return GoalPlanTask(
        title=title,
        description=description,
        estimatedMinutes=minutes,
        dueDate=due_date,
        priority=priority,
    )


def _duration_days(start: str, deadline: str, fallback: int) -> int:
    start_date = _parse_date_or_none(start)
    deadline_date = _parse_date_or_none(deadline)
    if start_date and deadline_date and deadline_date > start_date:
        return max(1, min((deadline_date - start_date).days, 3650))
    return fallback


def _parse_date_or_none(value: str) -> date_type | None:
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _due(start: date_type | None, offset: int) -> str | None:
    if not start:
        return None
    return (start + timedelta(days=max(offset, 0))).isoformat()


def _text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = str(value).strip()
    return cleaned or fallback


def _int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(parsed, maximum))


def _normalize_milestones(raw: object, fallback: list[GoalMilestone]) -> list[GoalMilestone]:
    if not isinstance(raw, list):
        return fallback
    milestones: list[GoalMilestone] = []
    for index, item in enumerate(raw[:12]):
        if not isinstance(item, dict):
            continue
        fallback_milestone = fallback[min(index, len(fallback) - 1)]
        tasks = _normalize_tasks(item.get("tasks"), fallback_milestone.tasks)
        if not tasks:
            tasks = fallback_milestone.tasks
        milestones.append(
            GoalMilestone(
                title=_text(item.get("title"), fallback_milestone.title),
                description=_text(item.get("description") or item.get("detail"), fallback_milestone.description),
                tasks=tasks,
            )
        )
    return milestones or fallback


def _normalize_tasks(raw: object, fallback: list[GoalPlanTask]) -> list[GoalPlanTask]:
    if not isinstance(raw, list):
        return fallback
    tasks: list[GoalPlanTask] = []
    for index, item in enumerate(raw[:12]):
        if not isinstance(item, dict):
            continue
        fallback_task = fallback[min(index, len(fallback) - 1)] if fallback else None
        priority_value = item.get("priority")
        if not priority_value and fallback_task:
            priority_value = fallback_task.priority
        priority = str(priority_value or "medium").lower()
        if priority not in {"low", "medium", "high"}:
            priority = fallback_task.priority if fallback_task else "medium"
        due_date = item.get("dueDate") or item.get("due_date")
        due_date = due_date if isinstance(due_date, str) and _parse_date_or_none(due_date[:10]) else None
        task = GoalPlanTask(
            title=_text(item.get("title"), fallback_task.title if fallback_task else "执行任务"),
            description=_text(
                item.get("description") or item.get("reason"),
                fallback_task.description if fallback_task else "推进当前目标。",
            ),
            estimatedMinutes=_int(
                item.get("estimatedMinutes") or item.get("estimated_minutes"),
                fallback_task.estimated_minutes if fallback_task else 45,
                1,
                1440,
            ),
            dueDate=due_date[:10] if isinstance(due_date, str) else None,
            priority=priority,
        )
        tasks.append(task)
    return tasks or fallback


def _normalize_review_plan(raw: object, fallback: ReviewPlan) -> ReviewPlan:
    if not isinstance(raw, dict):
        return fallback
    frequency = str(raw.get("frequency") or fallback.frequency).lower()
    if frequency not in {"daily", "weekly"}:
        frequency = fallback.frequency
    questions_raw = raw.get("questions")
    questions = []
    if isinstance(questions_raw, list):
        questions = [str(item).strip() for item in questions_raw[:6] if str(item).strip()]
    return ReviewPlan(frequency=frequency, questions=questions or fallback.questions)


def _source_titles(sources: list[RagSource] | list[dict[str, Any]]) -> list[str]:
    titles = []
    for source in sources:
        title = source.title if isinstance(source, RagSource) else str(source.get("title") or "")
        if title and title not in titles:
            titles.append(title)
    return titles[:5]
