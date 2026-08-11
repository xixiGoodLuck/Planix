# Planix Learning architecture

## Product boundary

Planix has one business domain: Learning. The frontend exposes only Learning and Settings. The backend exposes Learning, AI Settings, and Health APIs.

## Runtime flow

```text
LearningScope
-> LearningOutcome + CapabilityGraph
-> KnowledgeGraph
-> Video metadata + verified Transcript Evidence
-> EvidenceGraph + CoverageReport
-> bounded Gap Completion
-> ContentSelection
-> LearningContentPlan
-> LearningQualityReport
```

The model owns semantic decomposition and mapping. Code owns IDs, versions, references, timestamps, validation, coverage strength, quality pass/fail, and bounded retries.

## Evidence boundary

`ContentSegment` is the only Learning artifact that owns recommended start/end seconds. Those values must originate from a validated transcript. Metadata and model output cannot create exact time ranges. A search result remains a candidate until qualification, transcript validation, semantic mapping, and coverage validation complete.

## Runtime reliability

`LearningRuntime` is assembled by `LearningRuntimeFactory` during FastAPI lifespan startup. Versioned artifacts use `ArtifactStore`; production uses the PostgreSQL repository. Checkpoints, recovery, resume decisions, atomic resume commits, and progress events remain isolated inside `backend/app/learning/runtime`.

## Persistence

PostgreSQL 17 is required. Learning tables are isolated from AI Settings tables. Alembic is the schema authority; runtime DDL and embedded database fallbacks are prohibited.

## Model routing

All current semantic Learning calls use the single `learning_semantic` route. Settings retains provider selection, fallback ordering, Local/OpenAI-compatible support, secure secret storage, and global non-thinking behavior. Production configuration never silently falls back to Mock.
