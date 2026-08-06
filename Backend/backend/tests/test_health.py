def test_health_endpoint(client):
    for path in ("/health", "/api/health"):
        body = client.get(path).json()
        assert body["status"] == "ok"
        assert body["app"] == "planix-api"
        assert body["name"] == "planix-api"
        assert isinstance(body["pid"], int)
        assert body["version"] == "3.11-demo-reliability"
        assert body["startupTime"]
        assert body["features"] == {
            "planQualityGate": True,
            "contextAwareRefinement": True,
            "calendarDraftContextRecovery": True,
            "demoMetrics": True,
        }


def test_cors_allows_local_frontend_origins(client):
    allowed_origins = [
        "http://127.0.0.1:5198",
        "http://localhost:5198",
        "http://127.0.0.1:5176",
        "http://localhost:5176",
        "http://127.0.0.1:4173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]
    for origin in allowed_origins:
        response = client.options(
            "/api/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_cors_rejects_untrusted_origins(client):
    for origin in ("https://example.com", "http://192.168.1.8:5198"):
        response = client.options(
            "/api/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
