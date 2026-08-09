# AGENTS.md - Planix

## Product identity

Planix is a local-first AI planning application. Portfolio documentation uses `v3.0.0`; package and installer versions are managed separately. Do not reintroduce former product, storage, sidecar, or environment-variable names.

## Canonical planning architecture

Formal P Mode planning has one authority:

- Entry: `Backend/backend/app/cognitive_planning.get_planning_orchestrator`
- Runtime: `Backend/backend/app/cognitive_planning/runtime.py::CognitiveOSRuntime`
- Graph: `Backend/backend/app/cognitive_planning/graph/planning_graph.py`
- Contracts: `Backend/backend/app/cognitive_planning/contracts`
- Artifact audit store: `Backend/backend/app/cognitive_planning/artifact_audit.py`
- Lifecycle persistence: `Backend/backend/app/cognitive_planning/persistence.py`
- Control plane: `Backend/backend/app/harness`

The only formal graph is:

```text
session_guard
→ understanding
→ understanding_readiness
→ wait_for_understanding
→ compile_constraints
→ build_context
→ generate_plan
→ validate_plan
→ semantic_review
→ repair_plan (at most two rounds when required)
→ validate_repaired_plan
→ generate_schedule
→ validate_schedule
→ repair_schedule (at most two rounds when required)
→ materialize_calendar
→ wait_for_final_review
→ feedback_router / record_learning
→ calendar_gate
```

Canonical Artifacts are `UnderstandingSnapshot`, `UnderstandingPatch`, `ConstraintSet`, `ContextPack`, `PlanBlueprint`, `QualityReport`, `RepairProposal`, `ScheduleBlueprint`, `CalendarProposal`, `FinalApprovalBundle`, `ExecutionOutcome`, `ReplanProposal`, `LearningObservation`, and `MemoryEvaluation`.

`QualityReport.passed` is code-owned: all hard rules must pass and no blocker or major issue may remain. `score` is diagnostic only and must never be an approval threshold.

## Required boundaries

- LangGraph coordinates nodes; typed contracts, PostgreSQL Artifacts, Harness policy, and deterministic validators own product decisions.
- Planning models use only `planning_understanding`, `planning_plan`, `planning_review`, and `planning_learning` routes.
- A formal model failure preserves valid Artifacts, sets `status="MODEL_UNAVAILABLE"` and `runtimeStatus="blocked_model"`, and resumes only the failed native node. Never use a template, mock plan, alternate graph, or legacy runtime as fallback.
- A new formal Session stores lifecycle fields in `planning_sessions`; all planning bodies live only in `planning_artifacts`.
- Retired Session columns may remain in an existing user database for preservation, but production code must not read or write them. Migration must be dry-run first, create a backup before apply, preserve raw metadata, and archive rather than delete.
- Final approval binds current Understanding, Constraint, Context, Plan, plan quality, Schedule, schedule quality, Calendar proposal, Calendar snapshot, and checkpoint versions.
- Calendar mutation additionally requires the current `FinalApprovalBundle`, current `CalendarProposal`, Command action/approval, Harness Calendar permission, PermissionGate, version checks, and idempotent `sourceKey` writes.
- Plan and Schedule repair are issue-scoped, preserve stable IDs, pass PatchGuard/regression validation, and invalidate downstream approvals.
- Automatic durable memory requires a versioned `LearningObservation`, independent `MemoryEvaluation`, and fail-closed Memory policy.

## Independent product features

Calendar, Settings/model routing, Tauri packaging, and desktop sidecar behavior remain separate product capabilities. They must never be selected as a formal planning fallback. Command input always uses the canonical formal runtime.

## Frontend rules

- React 18 + TypeScript + Vite live in `Frontend`; do not add `react-router`.
- `AppRoute` is the route source of truth and the default route is `#/command`.
- Static UI text goes through i18n. Never translate user or model content.
- P Mode keeps one live inline Planning Workspace and a bottom composer; do not add a fixed workspace panel.
- Default UI shows user-facing Understanding, plan quality, Schedule, Final Review, and Calendar state. Raw Agent/Harness/model diagnostics require Advanced Debug Mode.
- Command thread streams remain isolated; one thread is serial, at most two independent threads may run in the page, and rate limiting reduces concurrency.

## Repository entry points

- Frontend: `Frontend/src/App.tsx`, `Frontend/src/pages/CommandPage.tsx`, `Frontend/src/lib/api.ts`
- Backend: `Backend/backend/app/main.py`, `Backend/backend/app/routers/command.py`, `Backend/backend/app/routers/planning.py`
- Model layer: `Backend/backend/app/services/model_provider.py`, `Backend/backend/app/services/llm.py`
- Database: `Backend/backend/app/db.py`
- Desktop: `apps/desktop/src-tauri/src/main.rs`
- Tests: `Backend/backend/tests`, `Frontend/src/**/*.test.tsx`

## Change discipline

Preserve unrelated behavior, public protocols, existing user data, and Calendar/Memory safety gates. Prefer the smallest change that keeps the single native planning authority explicit. Do not add dependencies or database-destructive migrations for planning cleanup.
