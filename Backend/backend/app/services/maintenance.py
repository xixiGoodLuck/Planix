from dataclasses import dataclass

from ..db import get_conn


PREFERENCE_KEY = "preferences:local-user"


@dataclass(frozen=True)
class MemoryCacheStats:
    preference_memory: int
    history_summaries: int
    agent_runs: int
    agent_events: int
    planning_goals: int
    plans: int

    def to_dict(self) -> dict[str, int]:
        return {
            "preferenceMemory": self.preference_memory,
            "historySummaries": self.history_summaries,
            "agentRuns": self.agent_runs,
            "agentEvents": self.agent_events,
            "planningGoals": self.planning_goals,
            "plans": self.plans,
        }


def preserved_flags() -> dict[str, bool]:
    return {
        "plans": True,
        "goals": True,
        "calendar": True,
        "notes": True,
        "documents": True,
        "aiSettings": True,
    }


def get_memory_cache_stats() -> MemoryCacheStats:
    with get_conn() as conn:
        preference_memory = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM user_preferences
            WHERE key = ? AND TRIM(value) != ''
            """,
            (PREFERENCE_KEY,),
        ).fetchone()["total"]
        history_summaries = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM agent_runs
            WHERE output_summary IS NOT NULL AND TRIM(output_summary) != ''
            """
        ).fetchone()["total"]
        agent_runs = conn.execute("SELECT COUNT(*) AS total FROM agent_runs").fetchone()["total"]
        agent_events = conn.execute("SELECT COUNT(*) AS total FROM agent_events").fetchone()["total"]
        planning_goals = conn.execute("SELECT COUNT(*) AS total FROM planning_goals").fetchone()["total"]
        plans = conn.execute("SELECT COUNT(*) AS total FROM plans").fetchone()["total"]

    return MemoryCacheStats(
        preference_memory=int(preference_memory),
        history_summaries=int(history_summaries),
        agent_runs=int(agent_runs),
        agent_events=int(agent_events),
        planning_goals=int(planning_goals),
        plans=int(plans),
    )


def reset_preference_memory() -> dict[str, int]:
    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS total FROM user_preferences WHERE key = ? AND TRIM(value) != ''",
            (PREFERENCE_KEY,),
        ).fetchone()["total"]
        conn.execute("DELETE FROM user_preferences WHERE key = ?", (PREFERENCE_KEY,))
    return {"preferenceMemory": int(before)}


def reset_history_memory() -> dict[str, int]:
    with get_conn() as conn:
        before = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM agent_runs
            WHERE output_summary IS NOT NULL AND TRIM(output_summary) != ''
            """
        ).fetchone()["total"]
        conn.execute(
            """
            UPDATE agent_runs
            SET output_summary = ''
            WHERE output_summary IS NOT NULL AND TRIM(output_summary) != ''
            """
        )
    return {"historySummaries": int(before)}


def reset_runtime_runs() -> dict[str, int]:
    with get_conn() as conn:
        agent_events = conn.execute("SELECT COUNT(*) AS total FROM agent_events").fetchone()["total"]
        agent_runs = conn.execute("SELECT COUNT(*) AS total FROM agent_runs").fetchone()["total"]
        conn.execute("DELETE FROM agent_events")
        conn.execute("DELETE FROM agent_runs")
    return {"agentRuns": int(agent_runs), "agentEvents": int(agent_events)}


def reset_planning_history() -> dict[str, int]:
    with get_conn() as conn:
        planning_goals = conn.execute("SELECT COUNT(*) AS total FROM planning_goals").fetchone()["total"]
        conn.execute("DELETE FROM planning_goals")
    return {"planningGoals": int(planning_goals)}


def reset_all_ai_memory_cache() -> dict[str, dict[str, int]]:
    return {
        "preferences": reset_preference_memory(),
        "historySummaries": reset_history_memory(),
        "runtimeRuns": reset_runtime_runs(),
        "planningHistory": reset_planning_history(),
    }


def reset_result(
    message: str,
    deleted: dict[str, int],
    before: MemoryCacheStats,
    after: MemoryCacheStats,
    steps: dict[str, dict[str, int]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "deleted": deleted,
        "preserved": preserved_flags(),
        "message": message,
    }
    if steps is not None:
        result["steps"] = steps
    return result
