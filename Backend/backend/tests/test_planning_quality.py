from datetime import date, timedelta

from app.services.planning_quality import (
    assess_local_source_grounding,
    build_density_policy,
    detect_plan_horizon,
    validate_structured_plan_quality,
)


def _plan(start: str, *, milestones: int = 3, tasks_per_milestone: int = 8, spacing_days: int = 4):
    start_date = date.fromisoformat(start)
    counter = 0
    result = {"goalTitle": "Python plan", "goalDescription": "Plan", "durationDays": 90, "milestones": [], "reviewPlan": {"frequency": "daily", "questions": []}}
    for milestone_index in range(milestones):
        milestone = {"title": f"Month {milestone_index + 1}", "description": "", "tasks": []}
        for _ in range(tasks_per_milestone):
            counter += 1
            milestone["tasks"].append(
                {
                    "title": f"Build Python output {counter}",
                    "description": "Create a concrete practice artifact.",
                    "estimatedMinutes": 60,
                    "dueDate": (start_date + timedelta(days=counter * spacing_days)).isoformat(),
                    "priority": "medium",
                }
            )
        result["milestones"].append(milestone)
    return result


def test_detect_plan_horizon_three_month_python_goal():
    horizon = detect_plan_horizon("三个月学习 Python", "2026-07-07")

    assert horizon.duration_days == 90
    assert horizon.horizon_type == "quarterly"
    assert horizon.expected_min_task_count == 24
    assert horizon.expected_week_count == 10


def test_detect_plan_horizon_defaults():
    assert detect_plan_horizon("整理明天安排", "2026-07-07").duration_days == 14
    assert detect_plan_horizon("准备 AI 实习项目", "2026-07-07").duration_days == 30


def test_quality_report_fails_sparse_90_day_plan():
    horizon = detect_plan_horizon("三个月学习 Python", "2026-07-07")
    policy = build_density_policy(horizon)
    sparse = _plan("2026-07-07", milestones=1, tasks_per_milestone=5, spacing_days=1)

    report = validate_structured_plan_quality(sparse, horizon, policy)

    assert report.ok is False
    assert report.total_tasks == 5
    assert any(issue.code == "too_few_tasks" for issue in report.issues)


def test_quality_report_fails_90_day_plan_that_only_covers_first_week():
    horizon = detect_plan_horizon("三个月学习 Python", "2026-07-07")
    policy = build_density_policy(horizon)
    first_week_only = _plan("2026-07-07", milestones=3, tasks_per_milestone=8, spacing_days=0)

    report = validate_structured_plan_quality(first_week_only, horizon, policy)

    assert report.ok is False
    assert any(issue.code in {"insufficient_weekly_coverage", "date_span_too_short"} for issue in report.issues)


def test_quality_report_passes_dense_90_day_plan():
    horizon = detect_plan_horizon("三个月学习 Python", "2026-07-07")
    policy = build_density_policy(horizon)
    good = _plan("2026-07-07", milestones=3, tasks_per_milestone=8, spacing_days=3)

    report = validate_structured_plan_quality(good, horizon, policy)

    assert report.ok is True
    assert report.total_tasks == 24
    assert report.covered_week_count >= 10


def test_quality_report_flags_weak_generic_task_titles():
    horizon = detect_plan_horizon("三个月学习 Python", "2026-07-07")
    policy = build_density_policy(horizon)
    weak = _plan("2026-07-07", milestones=3, tasks_per_milestone=8, spacing_days=3)
    for milestone in weak["milestones"]:
        for task in milestone["tasks"]:
            task["title"] = "继续学习"

    report = validate_structured_plan_quality(weak, horizon, policy)

    assert report.ok is False
    assert any(issue.code == "weak_task_titles" for issue in report.issues)


def test_generic_weak_keywords_do_not_create_local_context_grounding():
    grounding = assess_local_source_grounding(
        "我去新疆旅游",
        [
            {"title": "安全计划", "chunk": "安全、预算、计划和时间安排"},
            {"title": "旅行准备泛用建议", "chunk": "建议提前安排时间和目标"},
        ],
    )

    assert grounding["localRelevance"] == "low"
    assert grounding["sourceType"] == "insufficient_context"
