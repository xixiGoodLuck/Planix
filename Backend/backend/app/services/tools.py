AGENT_TOOLS = [
    {
        "name": "search_materials",
        "description": "Read relevant snippets from uploaded or pasted materials.",
        "parameters": {"query": "search query", "top_k": "number of snippets"},
    },
    {
        "name": "get_today_plans",
        "description": "Read calendar plans for a specific day without modifying them.",
        "parameters": {"date": "YYYY-MM-DD"},
    },
    {
        "name": "get_memory",
        "description": "Read saved preference memory and recent run summaries.",
        "parameters": {"user_id": "local user id"},
    },
    {
        "name": "enrich_with_model_knowledge",
        "description": "Add model knowledge enrichment without web search or writes.",
        "parameters": {"goal": "target goal", "triggerReason": "why enrichment was triggered"},
    },
    {
        "name": "propose_tasks",
        "description": "Preview calendar tasks without writing them to the calendar.",
        "parameters": {"goal": "target goal", "date": "YYYY-MM-DD", "preferences": "preference context"},
    },
]


def list_tools() -> list[dict[str, object]]:
    return AGENT_TOOLS
