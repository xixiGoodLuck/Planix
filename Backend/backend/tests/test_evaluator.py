def test_planner_evaluator_scores_complete_context_higher(client):
    minimal = client.post("/api/eval/planner", json={})
    assert minimal.status_code == 200

    complete = client.post(
        "/api/eval/planner",
        json={
            "goal": "Build a portfolio-grade AI application planner for Beijing internship applications",
            "deadline": "2026-09-30",
            "dailyHours": 3,
            "materials": (
                "The JD asks for React, TypeScript, FastAPI, SQLite, RAG, Agent tool calling, "
                "Prompt Engineering, evaluation, deployment, and source citations."
            ),
            "preferences": "Deep work in the morning, review at night, long implementation tasks on weekends.",
            "date": "2026-07-01",
            "data": {
                "2026-07-01": {
                    "plans": [
                        {"title": "Implement upload RAG", "done": True},
                        {"title": "Write evaluator tests", "done": False},
                    ]
                }
            },
        },
    )
    assert complete.status_code == 200
    minimal_body = minimal.json()
    complete_body = complete.json()
    assert complete_body["score"] > minimal_body["score"]
    assert len(complete_body["results"]) == 6
    assert complete_body["criteria"] == [
        "goal_clarity",
        "material_grounding",
        "time_feasibility",
        "preference_personalization",
        "execution_loop",
        "portfolio_signal",
    ]
