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

The frontend stores only a versioned active pointer containing `intakeId` and `runId`. On load it fetches the intake and run again; browser state never overrides PostgreSQL. An SSE error triggers an immediate status query followed by bounded exponential-backoff polling. Terminal status loads the result, intervention, or safe failure view. Event IDs prevent duplicate history and only one EventSource is active per run.

Internal stages map to eight user-facing stages: understand the learning goal, build the knowledge route, find and analyze resources, check knowledge coverage, supplement missing evidence, select learning content, validate quality, and complete. Internal runtime and validator names are not presented as product concepts.

## Persistence

PostgreSQL 17 is required. Learning tables are isolated from AI Settings tables. Alembic is the schema authority; runtime DDL and embedded database fallbacks are prohibited.

## Model routing

All current semantic Learning calls use the single `learning_semantic` route. Settings retains provider selection, fallback ordering, Local/OpenAI-compatible support, secure secret storage, and global non-thinking behavior. Production configuration never silently falls back to Mock.

## Safe operational metadata

Progress timestamps may be used to calculate stage and total latency. Logs and UI may contain stage names, elapsed time, provider readiness, call counts, and transcript lookup counts. They must not contain prompts, chain-of-thought, API keys, authorization headers, or transcript bodies.
