# Planix V2 architecture

## Authority

- Entry: `Backend/backend/app/cognitive_planning.get_planning_orchestrator`
- Runtime: `CognitiveOSRuntime`
- Graph: `Backend/backend/app/cognitive_planning/graph/planning_graph.py`
- Contracts: `Backend/backend/app/cognitive_planning/contracts`
- Policy and control: `Backend/backend/app/harness`

```text
session_guard -> understanding -> understanding_readiness -> wait_for_understanding
-> compile_constraints -> build_context -> generate_plan -> validate_plan
-> semantic_review -> repair_plan -> validate_repaired_plan
-> generate_schedule -> validate_schedule -> repair_schedule
-> materialize_calendar -> wait_for_final_review
-> feedback_router / record_learning -> calendar_gate
```

Repair nodes execute only when required and have a maximum of two rounds. `QualityReport.passed` is code-owned: every hard rule must pass and no blocker or major issue may remain. Score is diagnostic only.

## Persistence and safety

New sessions store lifecycle state in `planning_sessions`; versioned bodies live in `planning_artifacts`. Existing retired database tables and columns are preserved but production code does not read or write them. Final approval binds the current Understanding, Constraint, Context, Plan, quality, Schedule, Calendar proposal, Calendar snapshot, and checkpoint versions. Calendar writes additionally require Command approval, Harness policy, permission checks, current versions, and idempotent `sourceKey` writes.

## Models

The model layer is OpenAI-compatible and supports DeepSeek, GLM, Kimi, OpenAI, Custom, and Local providers. Formal planning routes are limited to the four V2 task types. Provider testing is an internal Settings operation and never becomes a planning route.

## Independent capabilities

- Calendar persists manual and approved V2 events.
- Settings manages providers and the four V2 routing rules.
- Tauri packages the same FastAPI and Vite application.
