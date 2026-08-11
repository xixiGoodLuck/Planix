# AGENTS.md - Planix Learning

## Product identity

Planix Learning is a local-first technical learning content Agent requiring PostgreSQL 17. Its only business domain is Learning.

## Canonical architecture

- Contracts and validators: `Backend/backend/app/learning/contracts`, `Backend/backend/app/learning/validators`
- Pipeline assembly: `Backend/backend/app/learning/assembly`
- Evidence: `Backend/backend/app/learning/evidence`
- Selection and quality: `Backend/backend/app/learning/selection`, `Backend/backend/app/learning/quality`
- Runtime, persistence, recovery, and bootstrap: `Backend/backend/app/learning/runtime`
- API: `Backend/backend/app/routers/learning.py`
- Model routing: `learning_semantic`

The product flow is LearningScope -> outcomes/capabilities -> KnowledgeGraph -> verified EvidenceGraph -> coverage/gap completion -> ContentSelection -> LearningContentPlan -> LearningQualityReport.

## Required boundaries

- Models may perform semantic decomposition, summaries, query hints, and coverage mapping.
- Code owns IDs, lineage, versions, timestamps, validation, priority, coverage strength, and quality pass/fail.
- Exact viewing ranges must originate from validated transcript segments; metadata or model output cannot invent them.
- A candidate is not evidence until transcript, mapping, and coverage validation pass.
- Production must fail clearly when a required provider, model, transcript source, or artifact store is unavailable. Never silently substitute Mock.
- Do not write secrets to PostgreSQL, frontend storage, logs, or API responses.
- Do not add runtime DDL or embedded database fallbacks. Alembic and PostgreSQL 17 are authoritative.

## Frontend rules

- React 18 + TypeScript + Vite live in `Frontend`; do not add `react-router`.
- `AppRoute` is the route source of truth. Only `learning` and `settings` are valid, and `learning` is the default.
- Static UI text uses i18n. User and model content is never translated.
- The Learning workspace presents stages, evidence, final content, and quality status rather than a chat interface.

## Change discipline

Preserve Learning artifact lineage, evidence integrity, PostgreSQL data, AI Settings, secret safety, and existing public Learning protocols. Prefer the smallest validated change and do not add dependencies without a concrete need.
