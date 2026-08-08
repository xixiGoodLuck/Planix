# Pure Planix V2 acceptance

1. A raw Command input creates a fresh formal V2 Session through `get_planning_orchestrator()`.
2. Understanding is dynamic, bounded, stored as a versioned Artifact, and explicitly confirmed by the user.
3. Constraint, Context, Plan, plan quality, Schedule, schedule quality, and Calendar proposal are produced only by the canonical graph.
4. Plan and Schedule repair are issue-scoped, stable-ID preserving, regression-validated, and limited to two rounds.
5. `QualityReport.passed` depends on hard rules and issue severity, never score.
6. Model failure preserves valid Artifacts, records `MODEL_UNAVAILABLE` / `blocked_model`, and resumes only the failed native node.
7. Final approval binds every current Artifact and checkpoint version.
8. Calendar mutation requires the current approval and proposal, explicit Command approval, Harness Calendar permission, version checks, and idempotency.
9. Durable learning requires a versioned observation, independent evaluation, and fail-closed policy.
10. Public model routing returns exactly the four V2 task types; removed HTTP routes return 404.
