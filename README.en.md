# Planix

[简体中文](README.md) | English

Planix is a local-first AI planning workspace that turns ambiguous goals into executable plans backed by evidence, independent review, and explicit user approval.

It is not a single-prompt demo. Planix uses a persistent Agent Runtime to manage planning stages, Artifact versions, recovery, and approval state, while keeping the user in control of every Calendar change.

## What is Planix?

Planix is designed for individuals working toward real, long-running goals. It brings goal clarification, real-world constraints, supporting evidence, strategy selection, task design, independent critique, and Calendar execution into one resumable workspace.

Typical use cases include:

- Learning a skill, preparing for an exam, or building a training plan.
- Job searches, project delivery, portfolio work, and long-term career goals.
- Travel, events, and other plans constrained by time and resources.
- Grounding decisions in local notes, materials, previous plans, and Calendar context.

The default entry point is **P Mode**. Dashboard, Calendar, Notes, Goals, Materials, Settings, and Advanced Debug Trace provide execution, management, and audit surfaces.

## Core capabilities

- **Cognitive planning**: independent Goal, Reality, Evidence, Strategy, Execution, and Critic stages.
- **Persistent conversations**: stores goals, known facts, important unknowns, Artifacts, and wait states for later recovery.
- **Two-lane single-page planning**: P Mode can keep two independent Threads planning in the background; each Thread remains serial, and rate limiting reduces the page to one lane.
- **Local-material RAG**: searches TXT and Markdown content through SQLite FTS5 and BM25-style retrieval.
- **Safe Calendar execution**: creating, updating, or deleting Calendar data always requires explicit user confirmation.
- **Long-term user model**: automatically inferred durable rules must pass an independent Memory Evaluation before storage.
- **Multi-model routing**: selects primary, fallback, and custom compatible providers by task type.
- **Observable runtime**: records scheduling, Agent invocation, Artifact changes, model routing, recovery, and policy decisions.
- **Desktop delivery**: combines a React/Vite frontend, FastAPI sidecar, SQLite, and Tauri 2 in a Windows-first desktop application.

## Cognitive planning workflow

```text
User goal
  -> Goal Understanding
  -> Goal Intelligence
  -> Goal Completion
  -> Reality Assessment
  -> Evidence Synthesis
  -> Strategy Proposal
  -> User approves Strategy
  -> Execution Blueprint
  -> Independent Critic
  -> User approves Execution
  -> Calendar Preview
  -> User approves Calendar
```

Goal Completion blocks only on information that can materially change the plan. Ordinary missing information can be converted into best-effort assumptions with **Skip this step**. Goal conflicts, safety issues, and hard feasibility risks cannot be skipped. Skipping goal clarification never approves Strategy, Execution, or Calendar.

The Critic can route a targeted repair back to the responsible stage. Every new Execution version requires an independent Critique for that exact version, and repairs invalidate stale approvals.

The Critic is also bound by deterministic semantic policy: a half-time simulation cannot invent a hard weekly quota, and a missing verified `sourceRef` cannot force a URL or named provider. A violating review is automatically re-run once against the same Execution; a second violation fails closed at Critic without consuming an Execution repair round.

The initial Execution normally produces the complete Blueprint, including its Narrative, in one structured model call. Critic repairs use the Narrative → Blueprint flow with cumulative review history to prevent repair oscillation. Truncated or invalid structured output can also fall back to that two-call flow. Before persistence, each candidate must pass deterministic dependency-order, budget, and calculable-workload checks; only then is it sent to the independent Critic.

## Agent Runtime Harness

`Backend/backend/app/harness` is the control plane around the Cognitive Agents. LangGraph executes scheduling decisions but does not own product policy.

| Component | Responsibility |
| --- | --- |
| Scheduler | Decides which Agent to invoke and whether to continue, wait, repair, recover, or stop |
| Agent Contracts | Declare input/output Artifacts, responsibilities, permissions, failure conditions, and retry boundaries |
| Artifact Store | Persists typed Artifacts and maintains IDs, versions, and current references |
| Policy Engine | Governs planning progress, user waits, approvals, Calendar permissions, and Memory admission |
| Recovery Manager | Provides model switching, bounded retry, JSON syntax repair, checkpoint resume, and read-only degradation |
| Approval Controller | Binds Strategy, Execution, and Calendar approvals to exact Artifact versions |
| Critic Controller | Enforces a one-to-one relationship between an Execution version and its independent Critique |
| Memory Controller | Independently evaluates candidate durable rules and applies fail-closed admission |
| Observability | Persists Harness decisions, Agent invocations, Artifact changes, and recovery events |

The SQLite checkpoint stores the current stage, completed and pending Agents, Artifact versions, approvals, errors, and wait state. A restarted process resumes from the exact pending Agent instead of replaying the entire pipeline.

File-backed databases use WAL, a five-second `busy_timeout`, one full Schema initialization per process, and an adjacent lock file that serializes cross-process initialization, allowing two independent planning Sessions and Uvicorn reload to coexist safely.

## Safety boundaries

1. **No fabricated plans**: when a model is unavailable or returns invalid structured output, Planix saves completed work and blocks at the failed stage.
2. **Independent critique**: every Execution version must pass its matching Critic before approval.
3. **Version-bound approvals**: upstream Artifact or plan changes invalidate affected stale approvals.
4. **Calendar always requires confirmation**: creates, updates, and deletes are revalidated and approved before the first database write.
5. **Memory is fail-closed**: raw chat is not automatically promoted into a durable rule; automatic memory requires a source Artifact, independent evaluation, and Policy permission.
6. **Critical risks cannot be skipped**: goal conflicts, safety concerns, and hard feasibility blockers require user resolution.
7. **Minimized secret exposure**: Provider Keys remain in local settings, are not returned by the public settings API, and are redacted from logs and Harness events.

## Technology

| Layer | Technology |
| --- | --- |
| Web | React 18, TypeScript 5.7, Vite 6 |
| Desktop | Tauri 2, Rust, WebView2 |
| Backend | Python 3.11, FastAPI, Pydantic 2 |
| Agent Runtime | LangGraph, Planix Harness, NDJSON streaming |
| Storage | SQLite, FTS5, local files |
| Testing | Pytest, Vitest, Testing Library, ESLint, TypeScript |
| Packaging | PyInstaller sidecar, NSIS, MSI |

## Repository layout

```text
Planix/
├─ apps/
│  ├─ web/                 React/Vite user interface
│  └─ desktop/             Tauri desktop shell
├─ backend/
│  ├─ app/
│  │  ├─ cognitive_planning/  Cognitive Agents and LangGraph execution graph
│  │  ├─ harness/             Scheduling, policy, recovery, approvals, and audit
│  │  ├─ routers/             FastAPI routes
│  │  └─ services/            Compatibility services and product capabilities
│  └─ tests/               Backend and planning acceptance tests
├─ docs/                   Deeper design and acceptance documentation
├─ scripts/                Development, validation, and packaging scripts
└─ data/                   Local development database
```

## Quick start

### Prerequisites

- Python 3.11
- Node.js 20 and npm
- Windows PowerShell
- Desktop development additionally requires Rust stable, Microsoft C++ Build Tools, and WebView2

### Get the source

```powershell
git clone https://github.com/ab2956955606-cmyk/Planix.git
cd Planix
```

### Start the backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

The API runs at `http://127.0.0.1:8003` by default. Verify it with:

```powershell
Invoke-RestMethod http://127.0.0.1:8003/api/health
```

### Start the Web application

Open another PowerShell window:

```powershell
cd Frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5176`. Vite proxies local `/api` requests to FastAPI.

### Start desktop development

From the repository root, start the backend and Web services:

```powershell
.\scripts\dev-desktop.ps1
```

Then open another PowerShell window:

```powershell
cd apps\desktop
npm ci
npm run dev
```

## Configure model providers

`.env.example` uses `mock` by default, so the local development environment can start without an external key. Real model capabilities require a configured Provider, either through environment variables or Settings.

| Provider | `AI_PROVIDER` | Key environment variable |
| --- | --- | --- |
| Mock | `mock` | Not required |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Kimi | `kimi` | `MOONSHOT_API_KEY` |
| Zhipu GLM | `zhipu_glm` | `ZHIPU_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| OpenAI-compatible | `custom` | `AI_API_KEY`, plus `AI_API_BASE` and `AI_MODEL` |

Settings supports task-specific primary Providers and fallbacks. Formal Cognitive Planning Artifacts always depend on valid structured model output; local templates are never presented as model-generated results.

Planix has one formal planning flow with two user-facing interaction phases: Understanding confirmation and Final Review. `CognitiveOSRuntime` runs the canonical graph directly, while Harness checkpoints, version-bound final approval, and the Calendar permission gate preserve recovery and write safety. Retired sessions are read-only and never resume an old runtime.

## Data and privacy

- Following the quick start with `.env.example` uses `data/mynotes.db`; without `DATABASE_URL`, the backend code defaults to `data/planix.db`.
- The desktop application uses `%APPDATA%\Planix\planix.db` by default.
- `DATABASE_URL` or `PLANIX_DB_PATH` can override the database location.
- Plans, materials, Memory, Harness checkpoints, and audit events are stored in local SQLite by default.
- External model calls remain subject to the selected Provider's network and data policies.

## Validation

### Backend

```powershell
cd Backend
..\.venv\Scripts\python.exe -m compileall backend
..\.venv\Scripts\python.exe -m pytest backend\tests
```

### Web

```powershell
cd Frontend
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run test
npm.cmd run build
```

### Desktop

```powershell
.\scripts\check-desktop-config.ps1
.\scripts\check-packaging-toolchain.ps1
```

Real-model acceptance Threads must first be created and advanced through the `/command` page. The audit script only reads replay data and never sends messages on behalf of the page:

```powershell
python scripts/live_planning_e2e.py --print-source-fingerprint
python scripts/live_planning_e2e.py --audit-manifest data/e2e-manifest.json --required-provider deepseek
```

The manifest uses `{"sourceFingerprint":"<pre-batch output>","threads":{"travel":"..."}}`. Schema v3 permits `fullAcceptancePassed=true` only for GET-only replay, an unchanged source fingerprint, a completed travel canary before the other nine runs, and a final Critic score of at least 90 in every scenario. It separates browser request intervals from human wait time, aggregates actual model calls and latency by stage, and records two-lane utilization, rate limits, truncations, automatic repairs, and Execution generation modes. Skipped routes are not counted as requests.

GitHub Actions validates the Backend and Web with Python 3.11 and Node.js 20 on Ubuntu runners, then runs the desktop configuration check on a Windows runner. The packaging toolchain check remains a local pre-release validation.

## Further documentation

- [Cognitive Planning Kernel acceptance boundaries](docs/cognitive-planning-acceptance.md)
- [Cognitive Planning OS behavior and golden scenarios](docs/cognitive-os-acceptance.md)
- [Legacy Planning Template inventory](docs/planning-template-inventory.md)

## Current scope

- Packaged desktop builds target Windows first; Web development can run independently.
- The root Dockerfile builds the FastAPI API only, not a complete Web or desktop image.
- Planix uses a single-user, local-first data model rather than a multi-tenant cloud service.
- P Mode runs at most two independent Threads concurrently by default; after a DeepSeek rate-limit response, that page falls back to one lane.
- Real planning quality and availability depend on the selected model, API quota, and network conditions.
