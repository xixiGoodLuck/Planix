#!/usr/bin/env python
"""Read-only auditor for browser-created Planix formal planning threads.

The browser drives every user action. This CLI only checks health and replays
existing Command threads with GET requests; it never accepts credentials or
mutates Planix data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8003"
SCENARIO_KEYS = (
    "travel",
    "go",
    "python",
    "swimming",
    "skiing",
    "spoken_english",
    "job_search",
    "fitness",
    "household_budget",
    "photography",
)
FORMAL_STATUS_FIELDS = (
    "understandingSnapshot",
    "constraintSet",
    "contextPack",
    "planBlueprint",
    "planQualityReport",
    "scheduleBlueprint",
    "scheduleQualityReport",
    "calendarProposal",
)
FINAL_STATUSES = {
    "waiting_final_review",
    "waiting_calendar_write_approval",
    "written_to_calendar",
    "learning_from_feedback",
}
SOURCE_FINGERPRINT_ROOTS = (
    Path("Backend/backend/app"),
    Path("Frontend/src"),
    Path("scripts/live_planning_e2e.py"),
)


class AuditError(RuntimeError):
    pass


def _messages(document: dict[str, Any]) -> list[dict[str, Any]]:
    value = document.get("messages")
    if not isinstance(value, list):
        raise AuditError("Thread replay did not contain a messages list")
    return [item for item in value if isinstance(item, dict)]


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    value = message.get("payload")
    return value if isinstance(value, dict) else {}


def _latest_status(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("kind") == "planning_session_status":
            return _payload(message)
    raise AuditError("Thread has no formal planning_session_status snapshot")


def _model_providers(messages: list[dict[str, Any]]) -> set[str]:
    providers: set[str] = set()
    for message in messages:
        if message.get("kind") != "model_usage":
            continue
        payload = _payload(message)
        provider = str(payload.get("provider") or payload.get("selectedProvider") or "").strip().lower()
        if provider:
            providers.add(provider)
    return providers


def audit_thread(
    client: httpx.Client,
    *,
    scenario: str,
    thread_id: str,
    required_provider: str | None,
) -> dict[str, Any]:
    response = client.get(f"/api/command/thread/{thread_id}")
    response.raise_for_status()
    messages = _messages(response.json())
    status = _latest_status(messages)
    session_id = str(status.get("sessionId") or "")
    current_status = str(status.get("status") or "")
    missing_fields = [field for field in FORMAL_STATUS_FIELDS if not status.get(field)]
    providers = _model_providers(messages)
    errors: list[str] = []
    if not session_id:
        errors.append("missing sessionId")
    if current_status not in FINAL_STATUSES:
        errors.append(f"unexpected final status: {current_status or '<empty>'}")
    if missing_fields:
        errors.append("missing formal snapshots: " + ", ".join(missing_fields))
    if required_provider and required_provider.lower() not in providers:
        errors.append(f"required provider not observed: {required_provider}")
    return {
        "scenario": scenario,
        "threadId": thread_id,
        "sessionId": session_id,
        "status": current_status,
        "businessStatus": status.get("businessStatus"),
        "runtimeStatus": status.get("runtimeStatus"),
        "formalSnapshots": {field: bool(status.get(field)) for field in FORMAL_STATUS_FIELDS},
        "providers": sorted(providers),
        "passed": not errors,
        "errors": errors,
    }


def current_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for root in SOURCE_FINGERPRINT_ROOTS:
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = sorted(path for path in root.rglob("*") if path.is_file())
        else:
            continue
        for path in paths:
            digest.update(path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _load_manifest(path: Path) -> tuple[dict[str, str], str | None]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AuditError("Manifest must be a JSON object")
    raw_threads = document.get("threads", document)
    if not isinstance(raw_threads, dict):
        raise AuditError("Manifest threads must be a scenario-to-threadId object")
    threads = {
        str(key): str(value)
        for key, value in raw_threads.items()
        if key in SCENARIO_KEYS and isinstance(value, str) and value.strip()
    }
    fingerprint = document.get("sourceFingerprint")
    return threads, str(fingerprint) if fingerprint else None


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-manifest", type=Path)
    parser.add_argument("--print-source-fingerprint", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--required-provider")
    parser.add_argument("--only", action="append", choices=SCENARIO_KEYS)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.print_source_fingerprint:
        print(current_source_fingerprint())
        return 0
    if args.audit_manifest is None:
        print("--audit-manifest is required unless --print-source-fingerprint is used.", file=sys.stderr)
        return 2
    try:
        threads, declared_fingerprint = _load_manifest(args.audit_manifest)
        selected = tuple(args.only or SCENARIO_KEYS)
        missing = [key for key in selected if key not in threads]
        if missing:
            raise AuditError("Manifest is missing scenarios: " + ", ".join(missing))
        with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
            health = client.get("/health")
            health.raise_for_status()
            results = [
                audit_thread(
                    client,
                    scenario=key,
                    thread_id=threads[key],
                    required_provider=args.required_provider,
                )
                for key in selected
            ]
    except (AuditError, httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
        print(f"Formal planning audit failed: {exc}", file=sys.stderr)
        return 2
    fingerprint = current_source_fingerprint()
    report = {
        "schemaVersion": 4,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "baseUrl": args.base_url,
        "sourceFingerprint": fingerprint,
        "declaredSourceFingerprint": declared_fingerprint,
        "sourceFingerprintMatches": bool(declared_fingerprint and declared_fingerprint == fingerprint),
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    report_path = args.report or Path("data") / "e2e-reports" / (
        f"planix-formal-planning-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
