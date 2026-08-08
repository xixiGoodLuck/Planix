# Planix engineering guide

Planix portfolio documentation is `v3.0.0`. Formal planning is implemented by one native runtime, `CognitiveOSRuntime`, and one LangGraph in `Backend/backend/app/cognitive_planning`.

## Formal flow

```text
UnderstandingSnapshot
→ user confirmation
→ ConstraintSet
→ ContextPack
→ PlanBlueprint
→ deterministic validation
→ semantic review
→ issue-scoped repair when needed
→ ScheduleBlueprint
→ schedule validation/repair
→ CalendarProposal
→ FinalApprovalBundle
→ Calendar permission and write
→ ExecutionOutcome / ReplanProposal / LearningObservation
```

The user gates are Understanding confirmation, Final Review approval, and Calendar permission. There is no runtime selector, second formal graph, compatibility adapter, planning template fallback, or score-based approval gate.

## Invariants

- `QualityReport.passed` means hard rules passed and no blocker/major remains. Score is diagnostic.
- Plan and Schedule repair budgets are two rounds and every operation is issue-scoped and regression-validated.
- Planning Artifact bodies are persisted only in `planning_artifacts`; `planning_sessions` stores lifecycle, conversation, request context, metadata, and counters.
- Existing databases are preserved. Retired Sessions are archived with a dry-run/backup migration; historical columns are not destructive-migrated.
- Model failures fail closed at the failed V2 node and preserve prior current Artifacts.
- `FinalApprovalBundle` and Calendar actions bind exact current versions. Calendar writes require Harness policy, Command approval, PermissionGate, version checks, and idempotent source keys.
- Learning observations are not durable memory until an independent Memory Evaluation passes.
- Command planning uses only the formal runtime. Calendar, Materials/RAG, Notes/Memory, and Settings remain independent capabilities, never planning fallbacks.

## Model routing

Formal task types are `planning_understanding`, `planning_plan`, `planning_review`, and `planning_learning`. Structured truncation may retry the same Provider once within the task cap, then follows the configured provider chain. No business template may replace a failed formal call.

## Validation

Backend verification covers Understanding merge/readiness, Plan hard rules, semantic issue severity, issue-scoped repair, Schedule invariants, version-bound Final Approval, Calendar idempotency, blocked-model recovery, native Artifact-only persistence, and retired-session archival.

Frontend verification covers routing settings, Planning Workspace state, Command stream/replay, Calendar approval actions, type checking, lint, and production build. Desktop verification keeps the Tauri sidecar and packaging checks unchanged.
