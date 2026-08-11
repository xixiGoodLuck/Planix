#!/usr/bin/env python
"""Run one controlled Learning API smoke case with saved DeepSeek settings."""

from __future__ import annotations

import argparse
import json
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        settings = client.get("/api/ai/settings")
        settings.raise_for_status()
        configured = settings.json()
        if configured.get("provider") != "deepseek" or not configured.get("hasApiKey"):
            raise RuntimeError("Saved DeepSeek settings are not ready")

        created = client.post(
            "/api/learning/runs",
            json={
                "goal": "学习 FastAPI 并完成一个 CRUD API",
                "preferences": {
                    "target_result": "能够独立完成可运行的 FastAPI CRUD API",
                    "current_level": {"summary": "掌握 Python 基础"},
                    "content_budget": {"targetTotalMinutes": 180},
                    "language_preference": {"preferredLanguages": ["zh-CN"]},
                    "resourcePreference": {"freeOnly": True},
                    "confirmed": True,
                },
                "constraints": ["只推荐有可验证字幕证据的内容"],
            },
        )
        created.raise_for_status()
        run_id = created.json()["run_id"]

        deadline = time.monotonic() + args.timeout
        state = {}
        while time.monotonic() < deadline:
            response = client.get(f"/api/learning/runs/{run_id}")
            response.raise_for_status()
            state = response.json()
            if state["status"] in {"completed", "failed"}:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Learning run timed out")

        if state["status"] != "completed":
            raise RuntimeError(json.dumps(state, ensure_ascii=False))
        result = client.get(f"/api/learning/runs/{run_id}/result")
        result.raise_for_status()
        body = result.json()
        if not body["learning_quality_report"]["passed"]:
            raise RuntimeError("Learning quality validation did not pass")
        print(json.dumps({"run_id": run_id, "status": state["status"], "quality": "passed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
