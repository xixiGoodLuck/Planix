# Planix

[简体中文](README.md) | English

Planix is a local-first AI planning application. Portfolio documentation version: **v3.0.0**.

## Pure Planix V2

Formal P Mode has one native planning runtime. A user goal moves through Understanding, constraint and context compilation, Plan generation and validation, Schedule generation and validation, Final Review, and an explicitly approved Calendar write. Model failures preserve valid Artifacts and stop at the failed node; no template, mock, or retired runtime replaces a formal model call.

The only model routes are `planning_understanding`, `planning_plan`, `planning_review`, and `planning_learning`.

Calendar, Settings, and desktop packaging remain independent product capabilities, not alternate planning paths. V2 Context uses only traceable request context, Calendar snapshots, and formal learning memory.

## Run locally

Planix requires PostgreSQL. Start the pinned local service and apply the Alembic schema first:

```powershell
docker compose up -d postgres
$env:DATABASE_URL="postgresql://planix:planix@127.0.0.1:5432/planix"
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
```

```powershell
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm run dev -- --port 5176
```

- Frontend: `http://localhost:5176`
- Backend: `http://127.0.0.1:8003`
- API docs: `http://127.0.0.1:8003/docs`

See [Architecture](docs/architecture.md) and [V2 acceptance](docs/cognitive-planning-acceptance.md).
