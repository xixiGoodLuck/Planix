from fastapi import APIRouter

from ..services.maintenance import (
    get_memory_cache_stats,
    reset_all_ai_memory_cache,
    reset_history_memory,
    reset_planning_history,
    reset_preference_memory,
    reset_result,
    reset_runtime_runs,
)

router = APIRouter(prefix="/api/settings", tags=["settings-maintenance"])


@router.get("/ai-memory-cache/stats")
def read_ai_memory_cache_stats() -> dict[str, int]:
    return get_memory_cache_stats().to_dict()


@router.delete("/memory/preferences")
def delete_preference_memory() -> dict[str, object]:
    before = get_memory_cache_stats()
    deleted = reset_preference_memory()
    after = get_memory_cache_stats()
    return reset_result("偏好记忆已清空，模型设置和 API Key 已保留。", deleted, before, after)


@router.delete("/memory/history")
def delete_history_memory() -> dict[str, object]:
    before = get_memory_cache_stats()
    deleted = reset_history_memory()
    after = get_memory_cache_stats()
    return reset_result("历史记忆摘要已清空，Runtime 原始运行记录已保留。", deleted, before, after)


@router.delete("/runtime/runs")
def delete_runtime_runs() -> dict[str, object]:
    before = get_memory_cache_stats()
    deleted = reset_runtime_runs()
    after = get_memory_cache_stats()
    return reset_result("Runtime 运行记录已清空。", deleted, before, after)


@router.delete("/planning/history")
def delete_planning_history() -> dict[str, object]:
    before = get_memory_cache_stats()
    deleted = reset_planning_history()
    after = get_memory_cache_stats()
    return reset_result("规划历史/cache 已清空，正式计划已保留。", deleted, before, after)


@router.delete("/ai-memory-cache")
def delete_ai_memory_cache() -> dict[str, object]:
    before = get_memory_cache_stats()
    steps = reset_all_ai_memory_cache()
    after = get_memory_cache_stats()
    deleted: dict[str, int] = {}
    for values in steps.values():
        deleted.update(values)
    return reset_result("AI 记忆、Runtime 记录和规划历史/cache 已清空，正式数据和模型设置已保留。", deleted, before, after, steps)
