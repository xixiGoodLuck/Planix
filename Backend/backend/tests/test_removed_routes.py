import pytest


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/runtime/run"),
        ("post", "/api/agent/plan"),
        ("post", "/api/agent/review"),
        ("post", "/api/agent/evaluate"),
        ("post", "/api/planning/goal-plan"),
        ("post", "/api/planning/refine-task"),
        ("post", "/api/planning/daily-review"),
        ("post", "/api/planning/replan/apply"),
        ("post", "/api/materials/ai-draft"),
        ("post", "/api/rag/query"),
    ],
)
def test_retired_routes_are_not_mounted(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 404
