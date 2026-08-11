# Planix Learning

Planix Learning turns a technical learning goal into a knowledge route, searches and analyzes real video resources, uses verifiable transcript evidence, selects exact viewing segments, and explains what to learn, why it matters, what each video covers, and how long to watch.

## Product capabilities

- Decompose a goal into outcomes, capabilities, and a knowledge graph.
- Retrieve real video metadata without letting a model invent URLs, video IDs, or duration.
- Create timestamped evidence only from validated transcripts.
- Analyze coverage, gaps, redundancy, and version conflicts, then run a bounded evidence-completion loop.
- Produce a traceable `LearningContentPlan` and a code-owned `LearningQualityReport`.
- Persist Learning runs, artifacts, checkpoints, resume events, and transcript evidence in PostgreSQL 17.

## Pages and APIs

The product pages are `#/learning` (default) and `#/settings`.

The product APIs are `/api/learning/*`, `/api/ai/*`, and `/health`.

## Local development

```powershell
docker compose up -d postgres
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm run dev -- --port 5176
```

Open `http://127.0.0.1:5176/#/learning`. See [docs/architecture.md](docs/architecture.md) for details.
