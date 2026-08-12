# Planix Learning

Planix Learning is a local-first technical learning content agent. It understands a natural-language goal, builds a knowledge route, searches Bilibili videos, recommends exact viewing segments backed by verifiable transcripts, and explains what to watch, why, and for how long. When evidence is insufficient, Planix enters recoverable `waiting_evidence` instead of fabricating content or timestamps.

## Product capabilities

- Decompose a goal into outcomes, capabilities, and a knowledge graph.
- Retrieve real video metadata without allowing a model to invent URLs, IDs, or duration.
- Create exact viewing ranges only from validated SRT/VTT transcript cues.
- Check required coverage, gaps, redundancy, and version compatibility with bounded evidence completion.
- Produce a traceable `LearningContentPlan` and code-owned `LearningQualityReport`.
- Persist runs, artifacts, checkpoints, recovery events, and transcript evidence in PostgreSQL 17.
- Recover from page refresh, temporary SSE loss, and backend restart using backend-authoritative state.

## Local development

Python 3.11+, Node.js 20+, PostgreSQL 17, and a PostgreSQL `DATABASE_URL` are required.

```powershell
docker compose up -d postgres
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm ci
npm run dev -- --port 5176
```

Open `http://127.0.0.1:5176/#/learning`. The only product routes are `#/learning` (default) and `#/settings`.

See the [user guide](docs/user-guide.md), [architecture](docs/architecture.md), and [demo guide](docs/demo.md).
