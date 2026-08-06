from __future__ import annotations

from typing import Any


# These entries are evidence candidates only. The Context & Evidence Agent must
# decide whether any candidate fits the user's goal before it reaches a plan.
STATIC_RESOURCE_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "title": "Python Tutorial",
        "type": "official_doc",
        "sourceRef": "https://docs.python.org/3/tutorial/",
        "domains": ["python", "programming", "software"],
        "topics": ["syntax", "control flow", "functions", "modules"],
        "credibility": 0.98,
    },
    {
        "title": "FastAPI Tutorial",
        "type": "official_doc",
        "sourceRef": "https://fastapi.tiangolo.com/tutorial/",
        "domains": ["fastapi", "python", "backend", "api"],
        "topics": ["http api", "request body", "validation", "testing"],
        "credibility": 0.98,
    },
    {
        "title": "React Learn",
        "type": "official_doc",
        "sourceRef": "https://react.dev/learn",
        "domains": ["react", "frontend", "software"],
        "topics": ["components", "state", "effects"],
        "credibility": 0.98,
    },
    {
        "title": "TypeScript Handbook",
        "type": "official_doc",
        "sourceRef": "https://www.typescriptlang.org/docs/handbook/",
        "domains": ["typescript", "frontend", "software"],
        "topics": ["types", "narrowing", "generics"],
        "credibility": 0.98,
    },
    {
        "title": "SQLite Documentation",
        "type": "official_doc",
        "sourceRef": "https://sqlite.org/docs.html",
        "domains": ["sqlite", "database", "software"],
        "topics": ["sql", "transactions", "indexes"],
        "credibility": 0.98,
    },
)


def candidate_pool(query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    lowered = query.lower()
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in STATIC_RESOURCE_CANDIDATES:
        terms = [*item.get("domains", []), *item.get("topics", [])]
        score = sum(1 for term in terms if str(term).lower() in lowered)
        if score:
            ranked.append((score, dict(item)))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("title", ""))))
    return [item for _, item in ranked[:limit]]
