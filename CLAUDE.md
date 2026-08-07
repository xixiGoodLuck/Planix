# CLAUDE.md - Planix

## Positioning

Planix is an AI application portfolio project. The portfolio-facing documentation version is `v3.0.0`, and it presents a RIVA-style AI OS Shell on the frontend connected to a real backend Runtime stream while keeping planning, review, RAG, evaluation, and desktop packaging capabilities behind a clean menu-based workspace.

P Mode uses one formal planning runtime: dynamic Understanding, explicit Understanding confirmation, Constraint/Context compilation, Plan and Schedule validation/repair, Calendar Proposal, version-bound Final Review approval, Calendar permission, and execution feedback/learning. `Backend/backend/app/harness` owns scheduling, policy, persistent state, recovery, approvals, artifact binding, and observability; LangGraph executes the selected formal nodes. A required model failure preserves the last successful artifact and exposes `runtimeStatus="blocked_model"`; it never invokes an alternate graph or template planner.

**Phase 7.1 Goal Understanding + Cognitive UX is complete within that Phase 7 boundary.** It adds model-backed pre-routing goal understanding, ambiguity and consistency gates, a replayable user-facing planning overview, and an opt-in persisted Advanced Debug Mode without migrating Dashboard Runtime, Goals, manual Workbench, or legacy Planning Session compatibility paths.

**Phase 7.3 Planning State Progression is also complete within the same boundary.** It adds semantic goal-completion judgment, planning-control routing, durable business/runtime status separation, failed-stage resume, additive completion/status replay, and one live Planning Workspace without changing the portfolio-facing `v3.0.0` label.

The project is fully named Planix across frontend, backend, desktop, sidecar, installer, database path, environment variables, and documentation.

## Documentation Maintenance

Every completed implementation must update `README.md`, `AGENTS.md`, and `CLAUDE.md` before final reporting. Record relevant behavior, API/database changes, phase boundaries, and verification notes, but never record secrets, local API keys, Authorization headers, or machine-specific credentials.

`README.md` is the portfolio-facing project homepage. Keep internal maintenance rules, implementation notes, and historical development logs in `AGENTS.md`, `CLAUDE.md`, or `docs/`, not at the top of `README.md`.

The portfolio-facing documentation version is `v3.0.0`. Do not confuse this with `package.json`, `tauri.conf.json`, `Cargo.toml`, backend health responses, or installer build versions unless a separate release-version bump task is explicitly requested.

## Stack

- `Frontend`: React 18 + TypeScript + Vite
- `Frontend/src/shell`: Planix RIVA Shell, App Menu, Inspector, hash route
- `Frontend/src/pages`: Dashboard, Calendar, Notes, Goals, Settings
- `Frontend/src/pages/CommandPage.tsx`: minimal P Mode route
- `Frontend/src/components/command`: command composer, quick action bar, permission popover, workbench toggle, and inline thread/card rendering
- `Frontend/src/stores/commandAgentStore.ts`: P Mode frontend state for `auto | chat | workbench`, permission selection, thread history drawer, command streaming, runtime mini cards, decision/usage cards, draft cards, approval cards, note cards, and Calendar write/patch result cards
- `Frontend/src/components/command/PlanningOverviewCard.tsx`: live user-facing stage/goal/facts/unknowns/next-action aggregation plus the safe goal-clarification skip control
- `Frontend/src/lib/storage.ts`: persisted Advanced Debug Mode preference (`planix_advanced_agent_trace`), default off
- `Frontend/src/components/agent/flow`: Agent Flow Trace observability UI
- `Frontend/src/store/agentFlowStore.ts`: Runtime event to Trace state mapping
- `Frontend/src/i18n`: `zh-CN` / `en-US` text system
- `apps/desktop`: Tauri v2 desktop shell
- `Backend/backend/app`: FastAPI backend
- `Backend/backend/app/routers/command.py`: Phase 4.8/4.8.1 command chat, approval, thread list, thread replay, and thread deletion endpoints
- `Backend/backend/app/services/command_agent.py`: command threads/messages, active Planning Session continuation, command routing, cognitive and legacy planning event adapters, Calendar/memory actions, approval handling, LLM chat, and replay-safe event persistence
- `Backend/backend/app/services/goal_understanding.py`: Phase 7.1 model-backed `GoalUnderstandingResult`, literal fact extraction, ambiguity handling, and consistency gating before command routing
- `Backend/backend/app/cognitive_planning/agents/goal_completion_judge.py`: Phase 7.3 semantic sufficiency judge; only model-owned blocking unknowns stop progression
- `Backend/backend/app/services/cognitive_planning/contracts/goal_completion.py`: typed `GoalCompletionResult` contract
- `Backend/backend/app/services/cognitive_planning/control_intent.py`: active-session planning-control detection before Goal Intelligence
- `Backend/backend/app/services/cognitive_planning/orchestration`: persisted business/runtime status, resume-node recovery, and compatibility adapters
- `Backend/backend/app/harness`: Agent contracts, Scheduler, Artifact refs, persistent checkpoints, Policy/Recovery/Human Approval/Critic/Memory controllers, and secret-safe observability; existing cognitive code is invoked through adapters
- `Backend/backend/app/services/command_decision.py`: strict-JSON `CommandDecisionService`, fallback decision handling, and model usage payload helpers
- `Backend/backend/app/services/model_provider.py`: internal AI SDK layer with `ModelRouter`, provider adapters, URL normalization, usage parsing, standard error types, token caps, and local fallback semantics
- `Backend/backend/app/services/llm.py`: compatibility facade that preserves existing `LlmClient.complete()` / `stream_tokens()` behavior
- SQLite: plans, memories, month notes, planning goals/sessions, daily reviews, AI settings, local RAG compatibility records, FTS5 indexes, AI run logs, agent runs, agent events, plus additive `harness_states` and `harness_events`; Harness checkpoints/events commit together with CAS versioning and do not alter public replay payloads
- File-backed SQLite connections use WAL, `busy_timeout=5000`, per-process path/identity-aware one-time Schema initialization, and an adjacent cross-process initialization lock. `:memory:` tests still initialize directly; replacing or externally migrating a file invalidates the initialization cache.
- AI settings persistence: `ai_settings` stores the singular active provider and shared knobs; `ai_provider_configs` stores provider-specific base URL, model, and API Key state.
- AI client: internal ModelProvider layer for mock, DeepSeek, Kimi, Zhipu GLM, OpenAI, and custom OpenAI-compatible providers with local structured fallback
- Planning: Phase 7 Cognitive OS under `Backend/backend/app/cognitive_planning`; Phase 6 services and legacy `StructuredGoalPlan` helpers remain compatibility paths
- Runtime: `/api/runtime/run` streams NDJSON events from Planner, Memory, Tool Router, Stream Engine, and Runtime Orchestrator
- Maintenance: `/api/settings/ai-memory-cache/*` and related Settings maintenance endpoints clear AI memory/cache without touching formal user data
- RAG: SQLite FTS5/BM25, not Chroma/FAISS
- Sidecar: FastAPI packaged with PyInstaller as `planix-api.exe`

## Phase 7 Cognitive Planning Rules

- Canonical artifacts are `GoalUnderstandingArtifact`, `RealityAssessment`, `EvidencePack`, `StrategyProposal`, `ExecutionPlanArtifact`, `CriticReport`, and `PlanningLearningUpdate`.
- Each cognitive stage uses an explicit model task type and strict JSON contract. Do not merge all cognition into one prompt or expose hidden chain-of-thought.
- Model unavailability, invalid JSON, or contract failure retains compatibility `status="MODEL_UNAVAILABLE"`, preserves all successful canonical artifacts, keeps durable `businessStatus` at the pending stage, and sets public `runtimeStatus="blocked_model"`. Do not use a template/local fallback or fake Agent decision to claim a model-backed strategy or execution plan exists.
- Do not use domain templates, fixed domain question banks, static resource catalogs, or deterministic local fallback content to decide a formal plan. Rules may validate structure and safety but never invent content.
- Strategy creation requires a sufficiently grounded goal and evidence pack. Execution creation requires explicit strategy approval. Calendar preparation requires explicit execution approval and a writable critic report.
- Every Agent declares required/optional input Artifacts, one output Artifact, responsibility, permissions, failure conditions, and retry budget. Natural-language handoffs are audit records, not Agent inputs.
- Strategy, Execution, and Calendar approvals bind to exact Artifact IDs/versions. New upstream/Strategy/Execution/Critique versions invalidate affected approvals. Calendar actions retain the Execution ref used for their preview and revalidate it immediately before mutation.
- Every Execution version must have a matching independent Critique invocation before execution approval. A stale passed Critique cannot authorize a new Execution version.
- Initial Execution generation defaults to one structured `ExecutionBlueprint` call containing its Narrative. Critic-driven revisions use Narrative then Blueprint with the previous Critique and cumulative repair history; exhausted `model_output_truncated` or `invalid_model_output` failures may also fall back once to that flow. Public model usage records `generationMode="single_pass" | "two_pass_fallback" | "two_pass_repair"` and preserves sanitized attempts.
- An Execution candidate is not an Artifact version until it passes deterministic preflight. Preflight validates structure, unique/acyclic dependencies, explicit CNY caps, typed Reality permission, and only reliably calculable weekly capacity. It may request one complete model repair; a second failure blocks at Execution without invoking Critic or fabricating an Artifact.
- Automatic long-term planning memory may be written only by `MemoryController` after a versioned, independently bound `memory_evaluation`; feedback/Critic `shouldPersist` alone is never authority to write.
- Recovery records provider/model attempts and supports model routing, retry, deterministic JSON syntax repair without invented fields, checkpoint resume, and graceful read-only degradation. Formal schema failure remains fail-closed.
- The critic can request bounded repairs and veto Calendar. Repair loops are capped at two rounds.
- Critic output is checked against deterministic semantic policy before persistence. A half-time contingency cannot become an unsourced primary weekly quota, and absent unverified `sourceRef` values cannot create mandatory URL/provider repairs. One policy-invalid review is retried on the same Execution with explicit context; repeated violation fails closed at the Critic checkpoint without consuming an Execution repair round.
- Deterministic guards validate identifiers, dependencies, dates, resources, deliverables, evidence links, fallback steps, and forbidden template leakage; they do not invent planning content.
- Evidence may come from User Model Memory, Calendar, local material search, model knowledge, and explicitly approved web providers. Every claim must retain source, credibility, relevance, and limitations; evidence gaps remain visible.
- Cross-session User Model Memory includes facts, habits, preferences, constraints, failure patterns, and hypotheses. One observation is tentative, repeated support increases confidence, contradiction reduces confidence, and stale memories may expire.
- P Mode renders one live planning workspace rather than an Agent log or per-message planning timeline. Internal decisions/messages remain persisted for audit but are hidden from the primary card.
- New cognitive events/cards and status fields remain additive. Existing Planning Session and old Runtime/draft replay must continue to render.
- P Mode planning always uses the single formal planning runtime; Dashboard Runtime and Goals remain separate capabilities.
- Final Approval binds Understanding, Constraint, Context, Plan, Quality, Schedule, Calendar Proposal, Calendar snapshot, and checkpoint versions.
- Shadow comparison is explicit QA tooling only. `CognitivePlanningShadowRunner` isolates old/new thread IDs and persists safe metrics in `planning_shadow_runs`; normal P Mode must not double-call models.
- Cognitive Planning first-call output budgets are Goal `5400`, Reality `5400`, Evidence `6600`, Strategy `7200`, Execution `12000`, Critique `6600`, and Learning `5400` tokens. Their fixed task caps are respectively `10800`, `10800`, `13200`, `14400`, `24000`, `13200`, and `10800`; the matching `PLANIX_*_MAX_TOKENS` variables override only the first-call budget and clamp to those caps.
- Structured Cognitive Planning calls that fail with `model_output_truncated` retry the same Provider once at `min(first-call budget * 2, task cap)` before continuing the existing fallback chain. Each routed call gets at most one automatic truncation retry, fallback Providers use the original first-call budget, and this behavior does not change Provider ordering, task scoring, models, or API keys.
- Harness verification lives in `Backend/backend/tests/test_harness_{policy,persistence,runtime}.py` and `Backend/backend/tests/planning_evals/test_harness_acceptance.py`; the latter locks Strategy-only recovery, Critic repair, fresh-runtime checkpoint restore, unavailable-model no-fake behavior, and Execution-only repair for `任务太难`.

## Phase 7.1 Goal Understanding and P Mode UX

- For new default-`auto` routing input, a model-backed `GoalUnderstandingResult` runs before generic `CommandDecision`. Its only intent states are `clear_goal`, `ambiguous_goal`, `normal_chat`, and `command`: clear goals enter Cognitive OS, ambiguous goals ask `nextQuestion`, normal chat remains conversational, and operational commands continue through the existing command router. Active Planning Session follow-ups retain their existing continuation path.
- A destination alone never supplies purpose. `我要去北京` and `我要去乌鲁木齐` preserve the literal city, return `ambiguous_goal`, and ask why the user is going; they must not become `unknown`, an inferred travel goal, or a local travel template.
- Non-empty `consistencyWarnings` force `ambiguous_goal` and block formal planning. A statement such as `我要学滑雪 / 零基础 2小时 做项目` must ask the user to resolve the mismatch and must not persist project, portfolio, or README semantics as the skiing purpose.
- `extract_obvious_goal_facts` may copy only explicit locations, dates, durations, time expressions, skills, and constraints. It must not infer a domain or purpose. Semantic local fallback is disabled for the `goal_understanding` route; unavailable or invalid model output must not fabricate an intent state or clarification question.
- Surfaced understanding results stream as the additive `goal_understanding` NDJSON event, persist as `command_messages.kind="goal_understanding"`, and replay through the existing command-thread endpoint before later planning cards.
- Phase 7.1 introduced a single friendly overview and stage mapping. Phase 7.3 supersedes its original collapsed-process presentation with the live Planning Workspace contract below; internal status identifiers still stay out of the default overview.
- Raw Agent names, handoffs, artifacts, model usage, route attempts, and fallback details render only when the persisted Advanced Debug Mode setting is enabled. The default remains off, and the disclosure must not become separate foreground trace/workspace panels.
- Phase 7.1 compatibility acceptance covers Beijing and Urumqi purpose clarification, same-thread purpose follow-up into planning, skiing/project consistency blocking, literal-only extraction, `goal_understanding` stream/replay, friendly stage mapping, and Advanced Debug Mode-only diagnostics.

## Phase 7.3 Goal Completion and Planning State Progression

- Run the Control Intent Router before Goal Intelligence for every active Planning Session turn. `下一步` / `继续` / `开始规划` map to `continue_current_stage`; `确认` maps to `approve_current_stage`; `修改` maps to `modify_current_stage`; `重新开始` maps to `restart_planning`; `取消` maps to `cancel_planning`. Only `provide_goal_information` is appended to the goal conversation.
- `跳过这一步` and its fixed English UI message map to `skip_current_stage`. This control applies only to ordinary incomplete Goal Completion blockers: keep the current Goal/Known Facts, turn those blockers into optional assumptions, write an approved Goal Completion result, and resume at Reality without another Goal Intelligence call. Consistency warnings plus blocking `safety` or `feasibility` unknowns remain non-skippable, and no approval/write gate is bypassed.
- Run `GoalCompletionJudge` after Goal Intelligence. `GoalCompletionResult` contains `complete`, `blockingUnknowns[{question, impact}]`, `optionalUnknowns`, and `nextStage: goal_clarification | evidence | strategy`. A result is complete only when no blocking unknown remains; optional unknowns never stop progression.
- Goal completion is semantic and derives from `UserGoalModel`, not a fixed missing-subject/missing-purpose/missing-duration list. The multi-turn Go example (`我要学go语言`, `为了web开发`, Python/Web background, job plus personal-project purpose, `每周20小时`) is sufficient for Go Web development Strategy even if the first-project deadline is still optional.
- `PlanningSessionResponse.businessStatus` is durable business progress: `goal_clarification | goal_understood | planning | calendar_pending | completed | blocked | cancelled`. `runtimeStatus` is execution health: `idle | running | blocked_model | retry_required`.
- `blocked_model_unavailable` is not a public runtime status. Keep it only as the compatibility `cognitiveMetadata.planningMode` value, alongside legacy top-level `status="MODEL_UNAVAILABLE"`.
- A model failure must persist the current resume node, confirmed Understanding, and every prior successful formal artifact. On recovery, `continue_current_stage` retries only the failed formal stage.
- On a failed model call, it is valid to report that the goal model and known facts are saved and Planix is waiting for the model. Do not emit a Strategy/Execution/Critic artifact, fake Agent decision, local plan, or planning-completed claim for the failed stage.
- Default P Mode renders exactly one live **Planning Workspace** for the active session with Current Stage, Goal Understanding, Known Facts, Important Unknowns, and Next Action. Update that card across user turns; do not render `规划过程 · 已折叠` or one collapsed planning timeline per message. Advanced Debug Mode may still show the underlying replay cards.
- P Mode state is isolated per local Command workspace. The active workspace is only a projection: up to two different Threads may retain independent background NDJSON streams, while one Thread never has more than one request. Navigation and new-Thread creation must not redirect stream events into the newly active workspace. A third submission is rejected before adding a user message, and a persisted DeepSeek `rate_limit` attempt lowers that page to one lane.
- Stream and persist additive `goal_completion_updated`. Add `businessStatus`, `runtimeStatus`, and `goalCompletion` to `planning_session_status` without removing legacy event/status fields. Command-thread replay must restore both the completion card and split statuses.
- SQLite startup migration additively ensures `planning_sessions.goal_completion_json`, `business_status`, and `runtime_status`, then backfills older sessions from existing status/artifacts. Never destructively rebuild a user's Planning Session data.
- Targeted tests are `Backend/backend/tests/test_goal_completion_hotfix.py`, `Backend/backend/tests/planning_evals/test_phase73_hotfix.py`, `Frontend/src/components/command/PlanCommandCards.test.tsx`, and `Frontend/src/stores/commandAgentStore.test.tsx`. They cover semantic Go progression, control routing, model-failure persistence/recovery, no fake stage output, additive stream/replay, and the single live workspace.

## Runtime Shape

Development:

```text
Vite dev server -> fetch NDJSON / JSON APIs -> FastAPI at 127.0.0.1:8003 -> SQLite
```

Desktop:

```text
Tauri window -> bundled resources/index.html -> proxy_api IPC for JSON APIs -> 127.0.0.1:8003 -> FastAPI sidecar -> SQLite user data dir
Tauri window -> stream_agent_runtime IPC for NDJSON Runtime -> FastAPI sidecar -> Runtime events -> Agent Trace
```

The frontend uses Tauri IPC for desktop API calls so WebView2 does not block local HTTP requests as mixed content. Runtime streaming uses a thin Rust pass-through bridge and keeps state in the frontend/backend only.

## Planning Contract

`GoalPlanOut` keeps legacy compatibility fields and adds `structuredPlan`.

Optional quality and source fields:

- `planHorizon`: detected raw text, duration days, horizon type, start/end date, expected milestones, expected minimum tasks, and expected covered weeks
- `qualityReport`: validator status, score, task/milestone/week coverage counts, date span, issue list, and optional `metrics`
- `qualityStatus`: `passed | repaired | local_fallback`
- `sourceType`: `local_context | model_knowledge | local_fallback | insufficient_context`
- `localRelevance`: `high | medium | low`

`structuredPlan` fields:

- `goalTitle`
- `goalDescription`
- `durationDays`
- `milestones[].title`
- `milestones[].description`
- `milestones[].tasks[].title`
- `milestones[].tasks[].description`
- `milestones[].tasks[].estimatedMinutes`
- `milestones[].tasks[].dueDate`
- `milestones[].tasks[].priority`
- `reviewPlan.frequency`
- `reviewPlan.questions`

Rules:

- `priority` is only `low | medium | high`.
- `dueDate` is `string | null`.
- `estimatedMinutes` is a number.
- LLM output must be parsed, schema-validated, and repaired with local fallback defaults when incomplete.
- Goals planning prompts the model for `summary + structuredPlan` only. `phases` and `tasks` are derived by the backend for legacy compatibility.
- Shared Planning Service must detect plan horizon, build a density policy, validate `structuredPlan`, attempt at most one model repair, and then use local fallback if quality still fails.
- Horizon detection precedence is explicit duration in the goal text, then valid deadline, then long-term keywords defaulting to 30 days, otherwise 14 days.
- Static tiny generation limits should not be reintroduced. 90-day plans should have at least 24 tasks and cover at least 10 distinct weeks.
- Local fallback should spread due dates across the full horizon, cap estimated minutes by daily availability, and pass the same quality gate when possible.
- Phase 3.11 exposes optional `qualityReport.metrics` for demo reliability: duration/task/milestone/week counts, date span, weak/missing/out-of-range task counts, repair/fallback booleans, quality status, source type, and local relevance. Metrics are diagnostics only and do not change Calendar schema or write permissions.
- Local retrieved sources become `local_context` only when core or strong expanded keyword evidence exists; weak generic keywords alone keep local relevance low.
- Phase 3.10 task refinement is context-aware. `RefineTaskRequest` may include optional compact `planContext`, and `RefinedTask` may include optional `timeBlocks`, `learningResources`, `budgetExplanation`, and `planFitCheck`.
- Refinement must not pass huge full plans into the model. Build compact context from plan title/summary, duration, quality status, daily budget, current milestone/task, previous/next task, same-milestone task titles, and top source summaries.
- Refinement time blocks must be backend-normalized so every block is <= 30 minutes. 120-minute tasks should become four 30-minute blocks or equivalent <=30-minute blocks.
- Resource URLs in refined tasks must be official/authoritative allowlisted domains only; non-allowlisted URLs become search keywords.
- Refined task output must stay in the task domain implied by compact context. For example, a skiing balance task must not become a yoga-pose plan; obvious domain drift should trigger same-domain local fallback.
- `PLANIX_GOAL_PLAN_MAX_TOKENS` controls the Goals planning token budget. It defaults to `4096` and clamps to `8000`.
- If an OpenAI-compatible response ends with `finish_reason="length"`, report `errorType="model_output_truncated"` and use the local structured fallback.
- A saved API key enables live model calls automatically unless the provider is `mock`.
- Provider settings support `mock`, `deepseek`, `kimi`, `zhipu_glm`, `openai`, and `custom`. Settings may auto-fill default Base URLs when switching provider only if the old value is empty or still the old provider default.
- Provider API Keys are persisted per provider. Settings may show saved-key chips and delete one provider key at a time; deleting a key must not switch the active provider or affect other provider keys.
- Phase 4.9B.1 `ModelRouter` selects by task type when routing rules exist, tries primary then fallback providers, records safe attempts, and leaves business local fallback content to the caller.
- ModelProvider request payloads must respect `response_format_json` and `max_token_cap`; moving code into the provider layer must not bypass existing model-output truncation or token-budget safeguards.
- Model errors should map to standard `errorType` values including `auth_error`, `bad_model`, `bad_base_url`, `network_error`, `timeout`, `rate_limit`, `insufficient_balance`, `invalid_key_format`, `invalid_model_output`, and `model_output_truncated`.
- Planning fallback must expose safe diagnostics with `fallbackReason`, `errorType`, and host-only `baseUrlHost`; never expose API keys, headers, path/query secrets, or raw request payloads.
- `structuredPlan` is the fact source for Runtime preview and final output.
- `planning_goals` stores generated planning history/cache only; it is not a confirmed Goals/Tasks execution table.

## Naming Contract

- Product name: `Planix`
- Tauri main binary: `planix`
- Sidecar binary: `planix-api.exe`
- API health app: `planix-api`
- Intended portfolio installer artifact: `Planix-v3.0.0-windows-x64-setup.exe`
- Intended backup MSI artifact: `Planix-v3.0.0-windows-x64.msi`
- Environment namespace: `PLANIX_*`
- User database path: `%APPDATA%\Planix\planix.db`
- Local database path: `data/planix.db`
- Frontend storage prefix: `planix_*`

Do not use or restore old names, and do not add compatibility fallbacks for old environment variables.

## Main Files

- `Frontend/src/App.tsx`: global state, route switch, business callbacks
- `Frontend/src/shell/useAppRoute.ts`: hash route source of truth
- `Frontend/src/shell/RivaShell.tsx`: main shell composition
- `Frontend/src/shell/AppMenu.tsx`: collapsible top-left menu and language switch
- `Frontend/src/shell/InspectorPanel.tsx`: read-only Inspector snapshot UI
- `Frontend/src/pages/DashboardPage.tsx`: Agent workspace and Runtime-triggered Trace entry
- `Frontend/src/pages/CommandPage.tsx`: Codex-like P Mode command surface
- `Frontend/src/components/command`: AgentThread, CommandComposer, PermissionPopover, WorkbenchToggle
- `Frontend/src/stores/commandAgentStore.ts`: command thread UI state, permission state, workbench trigger, and inline write approval flow
- `Frontend/src/components/agent/flow/AgentFlowTrace.tsx`: Agent execution observability panel
- `Frontend/src/store/agentFlowStore.ts`: Runtime event consumer with demo fallback
- `Frontend/src/components/AIWorkspace.tsx`: Notes, Goals, and Settings feature sections
- `Frontend/src/components/CalendarPanel.tsx`: compact calendar, note UI, visible selected-day clearing, and full calendar plan clearing
- `Frontend/src/components/PlanList.tsx`: daily task UI
- `Frontend/src/lib/api.ts`: API and Tauri IPC proxy client
- `apps/desktop/src-tauri/src/main.rs`: Tauri startup, sidecar lifecycle, health preflight, IPC proxy
- `Backend/backend/app/main.py`: FastAPI app and CORS
- `Backend/backend/app/schemas.py`: API contracts
- `Backend/backend/app/services/structured_goal_plan.py`: structured planning normalization and markdown rendering
- `Backend/backend/app/services/planning_quality.py`: horizon detection, density policy, quality validation, and source grounding assessment
- `Backend/backend/app/routers/runtime.py`: NDJSON Runtime endpoint
- `Backend/backend/app/services/runtime.py`: Planner, Memory, Tool Router, Stream Engine, Runtime Orchestrator
- `Backend/backend/app/routers/command.py`: P Mode command endpoints
- `Backend/backend/app/services/command_agent.py`: command persistence and NDJSON chat fallback
- `Backend/backend/app/services/permission_gate.py`: low / medium / high approval matrix

## Frontend Constraints

- Do not add `react-router`; keep lightweight hash routing.
- `AppRoute` is the only active-page state.
- Language is `zh-CN | en-US`, persisted with the `planix_lang` key.
- All static UI text should use `t("namespace.key")`.
- Agent Trace is an observability layer driven by Runtime events, not a replacement for the Workspace.
- Runtime fallback must clearly show `当前使用本地规划模板生成，后端 Runtime 未连接`.
- Do not reintroduce old demo markers such as `plan_context_lookup`, `ui-mock`, or static UI mock wording.
- Runtime output should answer the user's goal directly, such as producing a readable Python learning plan for a Python learning prompt.
- Internal `reasoning` nodes must display as `Plan` / `执行计划`; do not expose hidden chain-of-thought.
- Goals should render `structuredPlan` when present and keep old task-apply behavior based on legacy `tasks`.
- Goals calendar writes should show immediate writing feedback, visible pressed/writing button styling, and final created/updated/failed counts.
- Goals and Calendar task refinement should pass real task `estimatedMinutes`; Calendar refinement must not hardcode 60 minutes when the task has its own estimate.
- Calendar refinement for AI tasks should use `command-draft:*` source keys to recover the original compact draft context when available; do not rely only on a generic Calendar title if the source draft can be read.
- AI Calendar writes should preserve the generated task description in `plans.result`, plus source key, priority, and estimated minutes. Existing AI rows may only receive a description when their result is empty.
- Refined task UI should display optional time blocks, learning resources, budget explanation, and plan-fit checks while keeping old refined task payloads renderable.
- Dashboard Runtime proposals may be written to Calendar only after the user clicks `写入日历`; valid `llm` and `local_fallback` structuredPlan outputs are both writable, and Runtime execution itself must not auto-write Calendar data.
- Dashboard Runtime proposal cards, P Mode plan-detail cards, and Goals plan previews may show concise quality label, horizon duration, task count, covered weeks, date span, and source type, but should not show raw validator JSON or full quality score.
- Command/P Mode is a minimal Codex-like route. `CommandPage` may contain only the conversation thread and bottom composer; do not add fixed `PWorkspacePanel`, Calendar draft panes, Goal draft panes, Materials draft panes, or Notes draft panes.
- The Phase 7.3 Planning Workspace is one inline live card inside the conversation, not a fixed page panel. It must replace per-message planning timelines and update from the latest goal-completion/business state.
- The top-left Planix `P` brand mark and the menu P entry both route to Command/P Mode; collapsed menu states must still show a visible P letter and should never leave only a colored background.
- When no explicit hash route exists, the frontend should default to `#/command`. The P composer should use normal bottom anchoring, start around two text rows, let text begin from the left edge of the composer, grow with input, and scroll internally only after about five rows.
- Keep the P page visually loose: empty state content should sit slightly above center, the thread should have breathing room, and no fixed workspace or draft panel should be introduced.
- P Mode may expose a hidden right-side conversation drawer for new chat, history, and deleting command threads. The drawer must remain a conversation manager, not a workspace preview or persistent draft panel.
- P Workspace is an internal draft/audit concept, not a page layout. Phase 4.4 may write hidden `calendar_plan` drafts and permission-gated Calendar write actions only.
- Command mode is a single `auto | chat | workbench` value. In default `auto`, an active Cognitive OS session or clear goal-shaped request is handled before generic `CommandDecision`; other commands still use the LLM-first router. Auto `create_plan` enters Cognitive OS and creates no hidden draft. Forced `chat` is discussion-only, while manual `workbench` forces legacy Runtime planning.
- Chat and Runtime planning may use recent text messages from the current command thread as context. New chats must start clean and must not inherit context from previous threads.
- After a valid Workbench Runtime draft is created, legacy replay may show its compact summary and full plan. Cognitive OS instead shows Goal, Reality, Evidence, Strategy, Execution, and Critic artifacts.
- P Mode Runtime execution events should render as one collapsible inline execution chain after completion. The thread should use a lightweight center arrow toggle for collapse/expand, may show execution details on click, and must not introduce a fixed Trace or Workspace panel.
- P Mode execution chain groups should blend into the page background instead of using a gray block.
- P Mode task refinement commands such as `细化任务`, `细化计划`, or `refine all tasks` refine the current hidden `calendar_plan` draft. Store results in `command_drafts.payload_json.refinements` and render them inline; do not write Calendar from refinement alone.
- P Mode `refine all tasks` should avoid unbounded LLM calls; first version caps a batch at 5 tasks and prioritizes the current/first milestone.
- P Mode plan query commands such as "today's plans" use `query_plan` in `/api/command/chat`. They search Calendar plans only, emit `plan_search_results`, and must not run Runtime or create a draft.
- P Mode Calendar patch commands such as "把明天的任务改到后天", "改成 30 分钟", or "删除周五计划" use `patch_calendar_plan` in `/api/command/chat`. They preview title/date/time/duration/delete changes through `command_actions` before execution.
- P Mode memory query commands use `query_memory` in `/api/command/chat` and emit `memory_search_results` sourced only from `memories`. `query_notes` is compatibility shorthand for note-only memory search.
- P Mode memory-save commands use `save_memory` in `/api/command/chat`; they create `memory_write_preview` and `command_actions(target="memory")`, then write the approved item to `memories`. `save_note` and legacy `target="notes"` map to `kind="note"` memories.
- Phase 4.8.1 QuickActionBar and result row actions must send fixed natural-language messages back through `/api/command/chat`; they must not directly call Calendar or Notes APIs.
- Phase 4.8.1 result card actions such as note-to-plan references are follow-up chat instructions only. They do not directly write Calendar or Notes data.
- Calendar patch updates may change only title/content, date, time, and estimated duration. They must preserve `done`, `result/completion`, `source`, and `sourceKey`; deletes and rejected updates must leave the database unchanged.
- Patch targeting may use the latest `plan_search_results` card for ordinal references such as "first" or "第一个"; ambiguous multi-candidate matches should return a selection/search card and no action.
- Forced chat mode is discussion-only and must not run Dashboard Runtime, create drafts, write Calendar data, or execute any instruction.
- Forced workbench mode remains the manual legacy Runtime entry; auto planning uses Cognitive OS and must not run backend Runtime for `create_plan`.
- Command permission state is `low | medium | high`; low confirms writes/deletes, medium confirms deletes/dangerous actions, and high confirms dangerous actions only.
- Cognitive P Mode Calendar writes start from an approved Planning Session and use `planning-session:` source keys. Legacy Workbench writes may start from a hidden `calendar_plan` draft and use `command-draft:` keys. Neither path may overwrite manual plans or `completion/result/done`.
- In a command thread with a current `calendar_plan` draft, phrases like `写入计划`, `保存计划`, `保存`, and `确认写入` should route to Calendar writing through PermissionGate instead of starting a new Runtime planning run.
- P Mode Calendar writes should include matching refined task payloads from the draft in `plans.refined_task_json`, while preserving `completion/result/done`.
- P Mode Calendar write failures from `/api/command/chat` or `/api/command/approve` should use the Calendar-specific write error. They must not be reported as draft-save failures.
- P Mode memory writes must use preview, approval, and result cards; rejected memory actions must not write to `memories`.
- Command table startup migrations must preserve old local SQLite data and add `command_actions.draft_id`, `command_actions.error_message`, and `command_approvals.decision` without destructive rebuilds.
- Calendar month view should load all plans for the visible month so dates with plans are highlighted before the user clicks them.
- Calendar full-plan clearing prefers `DELETE /api/plans/all`; when an older backend returns 404, the frontend may fall back to deleting known plans individually and should retain failed deletions in state.
- Settings model input is free text with provider-specific recommendations for DeepSeek, Kimi, Zhipu GLM, OpenAI, custom, and mock. Do not restore legacy marketing model display names.
- Do not change existing request payloads or response schemas unless explicitly required by the phase plan.

## Runtime Constraints

- `/api/runtime/run` is the Runtime entrypoint and returns `application/x-ndjson`.
- Planner decomposes work only; it must not execute tools or manage streaming.
- Tool Router selects only approved tools.
- Stream Engine turns Runtime state into events only.
- Runtime Orchestrator is the only coordinator.
- Tools are read-only or preview-only: `search_materials`, `get_today_plans`, `get_memory`, `propose_tasks`.
- Runtime builds one internal Context Pack from goal, explicit constraints, preference memory, history memory, today plans, materials, and output language.
- Preference memory is a separate context layer and has higher priority than history memory.
- `get_memory` returns `preferenceMemory` and `historyMemory`; do not add `get_preferences`.
- `payload.preferences` must merge field-by-field with saved preferences, not replace the full object.
- `propose_tasks` returns preview-only `structuredPlan`, tasks, sources, diagnostics, `memoryContextSummary`, `planHorizon`, `qualityReport`, `qualityStatus`, `sourceType`, and `localRelevance`; `qualityReport.metrics` should be included when available.
- `propose_tasks` must not write to `plans`, Goals, Calendar, or Notes; automatic writes are reserved for a later confirmed phase.
- `/api/health` and `/health` expose the demo reliability guard: `name="planix-api"`, `version="3.11-demo-reliability"`, `startupTime`, and feature flags for `planQualityGate`, `contextAwareRefinement`, `calendarDraftContextRecovery`, and `demoMetrics`.
- `structuredPlan` drives Runtime preview and final output rendering.
- If Runtime lacks sufficient local material evidence, final output should begin with `本地资料不足，下面是通用建议，不代表资料库事实.`
- Runtime must clean Context Pack history before retrieval and planning. `historyMemory.recentProgress` should be compressed to `{ title, summary, relevanceToGoal }` objects, `search_materials.input.query` should be short and goal-focused, and `memoryContextSummary` should never include full historical Markdown.
- Medium-relevance history can inform the memory summary, but should not enter material search unless it shares clear goal-domain keywords.
- Rust `stream_agent_runtime` must remain a thin pass-through bridge.
- If true LLM streaming is unavailable, do not split a completed LLM response into fake token chunks; use Runtime step events plus final output or local structured fallback.
- `/api/command/chat` retains the Phase 4.8 NDJSON protocol and additively streams Phase 7.1 `goal_understanding` plus Phase 7.3 `goal_completion_updated`. Its `planning_session_status` additively carries `businessStatus`, `runtimeStatus`, and `goalCompletion`; all earlier event names and fields remain replay-compatible. Legacy `note_*`, Runtime, draft, and older Planning Session events must continue to render.
- `/api/command/approve` approves or rejects pending Calendar write actions. `/api/command/threads` lists command thread summaries, `/api/command/thread/{thread_id}` replays saved messages and may return the current hidden draft, and `DELETE /api/command/thread/{thread_id}` deletes command-thread data without deleting Calendar plans.
- Phase 4.8 stores `command_threads`, `command_messages`, hidden `command_drafts`, Calendar and Memory write/patch `command_actions`, and `command_approvals`. Do not register `/api/command/drafts` or use `command_outputs` until a later phase explicitly enables them.
- Phase 4.9B.1 is an acceptance and observability pass for task-level routing. Do not add model voting, dynamic model-list fetching, WriteIntent/Undo, operation logs, or direct memory-to-plan writes in this phase.

## Settings Maintenance

Settings has a "Memory & Runtime Data" maintenance area backed by these endpoints:

- `GET /api/settings/ai-memory-cache/stats`
- `DELETE /api/settings/memory/preferences`
- `DELETE /api/settings/memory/history`
- `DELETE /api/settings/runtime/runs`
- `DELETE /api/settings/planning/history`
- `DELETE /api/settings/ai-memory-cache`

Rules:

- Clearing preference memory only removes Runtime preference memory and must not alter API Key, provider, model, or Base URL.
- Clearing history memory only clears `agent_runs.output_summary`; it does not delete `agent_runs` or `agent_events`.
- Clearing Runtime runs deletes `agent_events` before `agent_runs`.
- Clearing planning history deletes `planning_goals`, which is planning history/cache only.
- All maintenance actions must preserve formal `plans`, Calendar, Notes, documents, and AI settings.

## Verification Commands

```powershell
cd Frontend
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run test
npm.cmd run build
```

```powershell
cd Backend
..\.venv\Scripts\python.exe -m compileall backend
..\.venv\Scripts\python.exe -m pytest backend\tests
```

Phase 7.1 targeted verification should include the Beijing/Urumqi ambiguity cases, destination follow-up context, skiing/project `consistencyWarnings`, literal-only extraction and disabled semantic fallback, `goal_understanding` stream/replay, friendly stage rendering, and persisted Advanced Debug Mode disclosure.

Formal planning verification should cover dynamic questions, Understanding revision and confirmation, Plan/Schedule validation and repair, version-bound Final Approval, stale approval rejection, Calendar idempotency, failed-stage-only recovery, archived-session handling, and one live Planning Workspace.

The formal ten-direction real-model batch is advanced only through the existing `/command` page. `scripts/live_planning_e2e.py` remains a GET-only manifest auditor; its schema-v4 report validates formal snapshots, observed providers, and a credential-free source fingerprint.

```powershell
.\scripts\verify-demo.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-desktop-config.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check-packaging-toolchain.ps1
```

Portfolio release naming uses `release\Planix-v3.0.0-windows-x64-setup.exe` as the primary Setup.exe and `release\Planix-v3.0.0-windows-x64.msi` as the backup MSI. Each installer should have its own `.sha256` checksum. Release binaries remain ignored by Git and should be attached through release tooling rather than committed.

## Documentation Maintenance

`README.md`, `AGENTS.md`, and `CLAUDE.md` must be kept current whenever the project changes meaningfully. This includes phase status, frontend shell, i18n, API behavior, database behavior, AI strategy, packaging, release artifacts, screenshots, and portfolio positioning.
