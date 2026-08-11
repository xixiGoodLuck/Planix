import pytest

from app.db import get_conn


def _retired(segment: str, suffix: str = "") -> str:
    return "/api/" + segment + suffix


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", _retired("comm" + "and", "/chat")),
        ("post", _retired("plann" + "ing", "/sessions")),
        ("get", _retired("plans")),
        ("get", _retired("month" + "-notes")),
        ("get", _retired("settings", "/context")),
    ],
)
def test_removed_product_routes_are_not_mounted(client, method, path):
    assert client.request(method.upper(), path, json={}).status_code == 404


def test_openapi_contains_only_learning_settings_and_health_business_routes(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/health" in paths
    assert "/api/learning/health" in paths
    assert "/api/ai/settings" in paths
    assert all(path.startswith(("/api/learning", "/api/ai", "/health", "/api/health")) for path in paths)

    with get_conn() as conn:
        tables = {
            row["table_name"]
            for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()
        }
    assert not ({"plans", "month_notes"} & tables)
    assert all(not table.startswith(("command_", "planning_", "harness_")) for table in tables)
