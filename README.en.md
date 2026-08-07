# Planix

[简体中文](README.md) | English

Planix is a local-first AI planning workspace that turns an ambiguous goal into a validated Plan, a feasible Schedule, and—only after explicit approval—real Calendar events.

Portfolio documentation version: **v3.0.0**.

## What it includes

- A single native formal planning runtime and LangGraph.
- Dynamic goal understanding with explicit user confirmation.
- Typed constraints, provenance-aware context, direct Plan generation, deterministic validation, semantic review, and bounded issue-scoped repair.
- Schedule generation and validation separated from Plan semantics.
- Version-bound Final Review and explicit Calendar permission.
- Execution feedback, scoped replanning, and independently evaluated long-term learning.
- Pure chat, Dashboard Runtime, manual Workbench, Goals, Calendar, Materials/RAG, Memory, Settings, and Windows desktop packaging as independent product capabilities.

## Formal planning workflow

```text
User input
→ UnderstandingSnapshot
→ Understanding readiness
→ User confirmation
→ ConstraintSet
→ ContextPack
→ PlanBlueprint
→ Hard validation
→ Semantic review
→ Scoped repair (only when required, at most two rounds)
→ ScheduleBlueprint
→ Schedule validation/repair
→ CalendarProposal
→ Final Review
→ FinalApprovalBundle
→ Calendar permission
→ Calendar write
→ Execution feedback and learning
```

There is no second formal runtime, alternate planning graph, compatibility projection, template fallback, or numeric approval threshold. A `QualityReport` passes only when all hard rules pass and no blocker or major issue remains; its score is diagnostic.

## Safety and data boundaries

- Model failure preserves current valid Artifacts and blocks at the failed native node; it never produces a fake plan.
- Planning bodies live in immutable, versioned `planning_artifacts`. New Sessions use `planning_sessions` only for lifecycle and request state.
- Existing databases are not destructively rewritten. The retired-session migration defaults to dry-run, backs up before apply, and archives rather than deletes.
- Final approval binds the current Understanding, constraints, context, Plan, quality, Schedule, Calendar proposal, Calendar snapshot, and checkpoint versions.
- Calendar writes additionally require the Command action/approval path, Harness policy, PermissionGate, current-version checks, and idempotent source keys.
- Raw feedback is never promoted directly to durable memory; independent Memory Evaluation is required.

## Technology

| Layer | Technology |
| --- | --- |
| Frontend | React 18, TypeScript, Vite |
| Backend | Python, FastAPI, Pydantic 2 |
| Planning runtime | LangGraph and Planix Harness |
| Storage | SQLite, FTS5, local files |
| Desktop | Tauri 2 and a packaged FastAPI sidecar |
| Tests | Pytest, Vitest, Testing Library, ESLint, TypeScript |

## Development

Backend:

```powershell
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

Frontend:

```powershell
cd Frontend
npm run dev -- --port 5176
```

Open:

- Frontend: `http://localhost:5176`
- Backend: `http://127.0.0.1:8003`
- API docs: `http://127.0.0.1:8003/docs`

## Repository layout

```text
Planix/
├─ Frontend/
├─ Backend/
│  └─ backend/app/
│     ├─ cognitive_planning/  # sole formal planning runtime
│     ├─ harness/             # policy, recovery, approvals, observability
│     └─ services/            # independent product services
├─ apps/desktop/
├─ docs/
├─ scripts/
└─ tools/migrations/
```

See [architecture](docs/architecture.md) and [formal runtime acceptance](docs/cognitive-planning-acceptance.md).
