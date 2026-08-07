# AGENTS.md - Planix

## Project Identity

Planix is a portfolio-grade AI planning application for learning, job search, and long-term execution. It combines a RIVA-style AI OS frontend shell, calendar planning, structured goal decomposition, daily review, grounded local RAG, deterministic planner evaluation, a real Agent Runtime event stream, and Windows desktop packaging.

The project is fully renamed to Planix. Do not reintroduce former product, storage, sidecar, or environment-variable names.

Portfolio-facing documentation version: `v3.0.0`. This is a presentation label for README and related showcase docs; do not treat it as a package, Cargo, Tauri, backend health, or installer build version unless a separate release-version task explicitly says so.

## Current Phase

The architecture boundary remains **Phase 7: Cognitive Planning OS** for P Mode, with the **Phase 7.1 Goal Understanding + Cognitive UX** refinement, **Phase 7.3 Planning State Progression** hotfix, and the Agent Runtime Harness completed inside it. The canonical Cognitive Agents live under `Backend/backend/app/cognitive_planning`; the control plane lives under `Backend/backend/app/harness`. Goal Intelligence, Goal Completion Judge, Reality, Evidence, Strategy, Execution, and Critic make independent judgments over typed artifacts. Harness Scheduler/Policy own lifecycle and product decisions; LangGraph executes the selected nodes only. Users approve strategy and execution before Calendar preview, and every Calendar mutation requires a version-bound Calendar approval. If a required formal-planning model call fails or violates its contract, durable `businessStatus` remains at the last valid stage, public `runtimeStatus` becomes `blocked_model`, and compatibility fields retain exact `status="MODEL_UNAVAILABLE"` plus `planningMode="blocked_model_unavailable"`. Understood facts and successful artifacts persist, and the failed stage may resume without restarting goal collection. Existing Planning Session APIs/events, Phase 6 compatibility code, Workbench Runtime, Dashboard Runtime, and Goals remain compatible.

Allowed in this phase:

- Retrieve local materials with SQLite FTS5/BM25.
- Generate and validate `structuredPlan`.
- Save generated planning results to `planning_goals` as history/cache.
- Show structured previews in Goals and Runtime Trace.
- Render Runtime final output from the same `structuredPlan`.
- Clean Runtime Context Pack history before retrieval and planning.
- Provide Settings maintenance controls for AI memory/cache cleanup.
- Keep P Mode Codex-like: outputs appear as inline conversation cards, not as a fixed workspace preview panel.
- Keep P Mode defaulting to `auto`: normal commands remain conversational and planning requests enter the single formal Planning Session runtime. Forced `chat` remains non-executing; manual `workbench` remains a separate legacy product mode and is never a planning-runtime fallback.
- Keep P Mode context thread-local: recent user/assistant text from the current thread may inform chat and planning, but new chats must not inherit prior thread context.
- Let Phase 7.1 run a model-backed `GoalUnderstandingResult` before generic `CommandDecision` routing for new `auto`-mode input. The only intent states are `clear_goal`, `ambiguous_goal`, `normal_chat`, and `command`; there is no goal-understanding `unknown` state.
- Treat destination-only goals such as `我要去北京` and `我要去乌鲁木齐` as `ambiguous_goal`: preserve the literal location, ask for purpose, and never infer travel or select a local travel template from the city name alone.
- Keep `extract_obvious_goal_facts` literal-only for explicit locations, dates, durations, time expressions, skills, and constraints. It must not assign domains or semantic purpose, and the `goal_understanding` model route must keep semantic local fallback disabled.
- Treat every non-empty `consistencyWarnings` list as a planning blocker. An incompatible purpose such as `学滑雪` plus `做项目` must remain unresolved and must not be stored as a project, portfolio, or README outcome.
- Run `GoalCompletionJudge` immediately after Goal Intelligence. It determines semantic sufficiency from the model-owned goal contract: only unknowns marked `blocking` stop progression; optional unknowns remain visible but do not prevent `complete=true` or advancement to Evidence/Strategy. Do not reduce completion to required subject/purpose/duration slots.
- Preserve multi-turn goal meaning. The sequence `我要学go语言` / `为了web开发` / `学过python也做过web开发` / `找工作和个人项目` / `每周20小时` is sufficient to retain Go Web development, Python/Web background, job/personal-project purpose, and 20 hours/week, then advance to Strategy even if a first-project deadline remains optional.
- Route planning-control text before Goal Intelligence: `下一步`/`继续`/`开始规划` -> `continue_current_stage`, `确认` -> `approve_current_stage`, `修改` -> `modify_current_stage`, `重新开始` -> `restart_planning`, and `取消` -> `cancel_planning`. These messages are controls, not new goal facts.
- `跳过这一步` / `Skip this step and continue with the information already provided` -> `skip_current_stage`. It is valid only for an incomplete Goal Completion result with ordinary blockers: preserve Goal/Known Facts, downgrade those blockers to optional assumptions, persist an approved Goal Completion update, and continue from Reality. Never allow it to bypass consistency warnings, blocking `safety`/`feasibility` unknowns, Strategy approval, Execution approval, or Calendar permission.
- Keep default P Mode to one live inline Planning Workspace with Current Stage, Goal Understanding, Known Facts, Important Unknowns, and Next Action. Update the same card across turns; do not render the old per-message `规划过程 · 已折叠` timeline. Raw Agent names, handoffs, artifacts, model usage, routing, and fallback diagnostics require the persisted Advanced Debug Mode preference and remain hidden by default.
- Keep Command frontend state isolated per local workspace. The active workspace is a projection only: up to two independent Threads may retain background NDJSON streams, one Thread remains strictly serial, and switching/new Thread must never redirect events into the active workspace. Reject a third submission before appending it; a DeepSeek `rate_limit` attempt lowers that page to one lane.
- Persist surfaced `goal_understanding` cards in `command_messages`, stream them as the additive `goal_understanding` event, and restore them through command-thread replay.
- Persist `GoalCompletionResult` and expose additive `goal_completion_updated`; `planning_session_status` must add `businessStatus`, `runtimeStatus`, and `goalCompletion` without removing legacy fields or replay kinds.
- Keep business progress separate from runtime health. A formal planning model failure leaves `businessStatus="planning"`, sets public `runtimeStatus="blocked_model"`, and preserves every valid artifact and the failed-stage resume node.
- Let P Mode write Calendar plans only after Understanding confirmation, version-bound Final Approval, deterministic Calendar guards, `command_actions`, `command_approvals`, and PermissionGate.
- Let P Mode refine tasks in the current hidden `calendar_plan` draft through the existing planning refinement service. Refinement results stay in the command draft until the user writes the plan to Calendar.
- Let P Mode query Calendar plans only through inline `plan_search_results` cards without running Runtime or creating a draft.
- Let P Mode query and write long-term context through Memory Agent, `memory_search_results`, `memory_write_preview`, `memory_write_result`, and `command_actions(target="memory")`.
- Let P Mode preview Calendar plan updates/deletes through `command_actions` before execution. Patch actions may update title/date/time/estimated duration only; they must not overwrite `done`, `result/completion`, `source`, or `sourceKey`.
- Keep legacy `note_search_results`, `note_write_preview`, `note_write_result`, and `command_actions(target="notes")` replay-compatible by mapping them to `kind="note"` memories.
- Let Phase 4.8.1 polish P Mode copy and cards: fixed QuickActionBar messages, direct empty-state examples, user-facing decision summaries, plan/note row action buttons that send natural-language messages back through `/api/command/chat`, scenario-specific approval labels, and compact model-usage display.
- Let Phase 4.8.2 store long-term context in `memories` with kinds `note`, `material`, `planning_history`, `preference`, and `review`; Calendar plans remain separate formal action data.
- Let Phase 4.9A standardize model calls through `Backend/backend/app/services/model_provider.py`, supporting `mock`, `deepseek`, `kimi`, `zhipu_glm`, `openai`, and `custom` providers behind `ModelRouter` while preserving the public `LlmClient` facade.
- Let Phase 4.9A.1 persist provider API Keys independently in `ai_provider_configs`; Settings may show saved-key chips and delete one provider key at a time.
- Let Phase 4.9B.1 route model calls by task type, including `command_decision`, `plan_generation`, `task_refinement`, `calendar_patch`, `memory_query`, `memory_write`, `model_knowledge`, and `chat`, with fallback attempts surfaced in model usage cards.
- Let Phase 6 route cognitive stages with distinct task types: `planning_goal_model`, `planning_evidence`, `planning_strategy`, `planning_execution`, `planning_critique`, and `planning_learning`. Cognitive routing rules default to no business local fallback.
- Let Phase 7 add `planning_reality` and treat `GoalUnderstandingArtifact`, `RealityAssessment`, `EvidencePack`, `StrategyProposal`, `ExecutionPlanArtifact`, `CriticReport`, and evidence-backed `UserModelMemory` as the canonical Cognitive OS contracts.
- Cognitive Planning first-call output budgets are Goal `5400`, Reality `5400`, Evidence `6600`, Strategy `7200`, Execution `12000`, Critique `6600`, and Learning `5400` tokens. Their fixed task caps are respectively `10800`, `10800`, `13200`, `14400`, `24000`, `13200`, and `10800`; the matching `PLANIX_*_MAX_TOKENS` variables override only the first-call budget and clamp to those caps.
- Generate the initial Execution as one structured `ExecutionBlueprint` (including Narrative). Critic-driven revisions use Narrative then Blueprint with the previous Critique and cumulative repair history; exhausted truncation/invalid-structure failures may use the same two-call fallback. Expose `generationMode="single_pass" | "two_pass_fallback" | "two_pass_repair"` and sanitized attempts. Before returning an Artifact, run deterministic structure, acyclic and provably ordered dependencies, explicit CNY cap, typed Reality, and reliably calculable capacity preflight. One internal model repair is allowed; a second failure blocks Execution before persistence or Critic.
- For structured Cognitive Planning calls only, `model_output_truncated` may trigger one same-Provider retry at `min(first-call budget * 2, task cap)` before the existing fallback chain continues. A routed call gets at most one such extra invocation, fallback Providers retain the original first-call budget, and this recovery must not change Provider ordering, task scoring, models, or API keys.
- Let `Backend/backend/app/harness` own Agent contracts, scheduling, persistent checkpoints, policy, recovery, version-bound approvals, Critic binding, Memory Evaluation, and durable observability. Checkpoints store Artifact refs/versions, never copied chat or Artifact bodies.
- Persist additive `harness_states` and `harness_events`. Every state/event checkpoint is one CAS-protected SQLite transaction; API and frontend replay shapes remain unchanged.
- Require every automatic long-term planning rule to pass an independent `memory_evaluation` Artifact and Memory policy. Raw chat, approval text, evaluator failure, rejected/mismatched evidence, and conflicted/expired observations must not become active planning memory.
- Bind Strategy, Execution, Critique, and Calendar decisions to exact Artifact IDs/versions. A repaired Artifact invalidates affected approvals; a Calendar action also retains the Execution ref that created its preview and rechecks it before the first write.
- Keep P Mode user-facing: show goal understanding, reality, evidence, strategy, execution, critic result, and actionable model-unavailable guidance. Do not show raw Agent decision/message lists, runtime internals, confidence plumbing, or hidden reasoning in the primary workspace.
- Let User Model Memory store facts, habits, preferences, constraints, failure patterns, and tentative planning hypotheses with evidence, contradictions, observation count, confidence, lifecycle status, and optional expiry. Never promote one observation to a permanent fact.
- Treat `UserGoalModel`, `EvidencePack`, `StrategyPortfolio`, `ExecutionBlueprint`, `PlanCritiqueReport`, and `PlanningLearningUpdate` as the Phase 6 source-of-truth contracts. Compatibility snapshots may be derived from them but must not overwrite them.
- Keep static resource catalogs, Memory Store hits, Calendar context, and optional web providers as evidence candidates. An agent must explain relevance and gaps before evidence influences a strategy or task.
- Limit critic-driven repair to two rounds per execution draft. A non-writable critique after the limit must block Calendar and surface remaining risks.
- Validate Critic semantics before persistence. Half-time is a contingency rather than an invented primary quota, and missing unverified `sourceRef` values cannot justify mandatory URLs or provider names. Retry one invalid Critique against the same Execution with machine-readable policy context; a second violation blocks at `resumeNode=critic` without incrementing the Execution repair counter.
- Store learned planning rules as tentative hypotheses. One feedback item must not become a permanent fact; confidence rises with repeated support and falls with contradictory evidence.
- Phase 3.10 may refine tasks with compact plan context, short time blocks, official/authoritative learning resources, budget explanation, and plan-fit checks.
- Phase 3.11 demo reliability metrics may be shown in Dashboard proposals, P Mode plan-detail cards, Goals previews, and Settings health/version diagnostics.

Forbidden in this phase:

- Auto-writing generated tasks to Calendar from Runtime without an explicit P command.
- Writing Goals, Settings, or arbitrary non-Calendar data from P Mode. Phase 4.8.2 allows memory-save actions only through the preview/action/approval path into `memories`.
- Letting P Mode bypass `command_actions`, `command_approvals`, or PermissionGate for Calendar writes.
- Letting P Mode patch or delete Calendar rows without an inline preview, approval path when required, and replayable result card.
- Turning P Workspace into a foreground layout panel or persistent Calendar/Goals/Materials/Notes draft area.
- Changing `/api/runtime/run` event protocol.
- Changing Tauri Windows installer sidecar mechanics.
- Implementing model voting, dynamic model lists, WriteIntent/Undo, operation logs, or direct memory-to-plan writes in Phase 4.9B.1.
- Generating a formal Phase 6 strategy, execution blueprint, critique pass, or Calendar-ready plan from deterministic templates when the model is unavailable.
- Using domain templates, fixed domain question banks, static resource catalogs, or local fallback plans to make Phase 7 planning decisions.
- Mapping a literal city, skill, date, duration, or constraint to semantic intent locally, or routing a city-only goal to `unknown`/travel fallback instead of asking its purpose.
- Allowing a non-empty `consistencyWarnings` result to start formal planning or silently accepting its incompatible purpose.
- Showing raw Agent/handoff/artifact/model-routing/fallback details in default P Mode without the persisted Advanced Debug Mode setting.
- Treating optional unknowns as blockers, or requiring a fixed subject/purpose/duration checklist after semantic goal information is already sufficient.
- Passing planning controls such as `下一步`, `确认`, `重新开始`, or `取消` into Goal Intelligence as new goal content.
- Treating `skip_current_stage` as ordinary clarification text, rerunning Goal Intelligence after a skip, or allowing it to override consistency, safety, feasibility, or write-approval gates.
- Converting a runtime model failure into an ordinary goal clarification, erasing the last valid business stage, or restarting goal collection. Only retry/model-settings guidance and failed-stage resume are allowed until a model-backed stage succeeds.
- Generating a fake strategy, fake Agent decision, execution artifact, critique, or completed-planning claim when a model stage fails. It is allowed to report that the goal/facts were saved and Planix is waiting for the model.
- Letting LangGraph own business decisions, hidden reasoning, or durable product state. It coordinates nodes; Planix contracts and SQLite remain the source of truth.
- Allowing a strategy to bypass user approval, an execution blueprint to bypass user approval, or a critic failure to bypass the Calendar gate.
- Letting an Agent, LangGraph edge, command permission level, stale preview, or legacy adapter bypass Harness Policy or a version-bound approval.
- Passing natural-language handoff messages as Agent computation input; Agent collaboration uses declared typed Artifacts, while messages remain audit-only.
- Saving an inferred long-term rule directly from feedback/Critic output without an independent Memory Evaluation and fail-closed policy decision.

## Architecture

- Frontend: React 18 + TypeScript + Vite in `Frontend`.
- Frontend shell: hash-route Planix RIVA AI OS Shell in `Frontend/src/shell`.
- Frontend pages: `Frontend/src/pages`.
- i18n: `Frontend/src/i18n`, default `zh-CN`, supports `en-US`.
- Agent observability: `Frontend/src/components/agent/flow` renders Runtime events as the Dashboard trace.
- Agent flow state: `Frontend/src/store/agentFlowStore.ts`.
- Command Agent UI: `Frontend/src/pages/CommandPage.tsx` and `Frontend/src/components/command`.
- Command Agent state: `Frontend/src/stores/commandAgentStore.ts`.
- Phase 7.1 goal understanding: `Backend/backend/app/services/goal_understanding.py` and `GoalUnderstandingResult` in `Backend/backend/app/schemas.py`.
- Phase 7.3 completion/control: `Backend/backend/app/cognitive_planning/agents/goal_completion_judge.py`, `Backend/backend/app/services/cognitive_planning/contracts/goal_completion.py`, and `Backend/backend/app/services/cognitive_planning/control_intent.py`.
- Phase 7.3 persistent progression/recovery: `Backend/backend/app/services/cognitive_planning/contracts/state.py`, orchestration `runtime.py`/`persistence.py`, compatibility adapters, `Backend/backend/app/db.py`, and public Planning Session schemas.
- Phase 7.1/7.3 P Mode live workspace/debug disclosure: `Frontend/src/components/command/PlanningOverviewCard.tsx`, `AgentThread.tsx`, Settings, and the persisted `planix_advanced_agent_trace` preference.
- Desktop shell: Tauri v2 in `apps/desktop`.
- Backend: FastAPI in `Backend/backend/app`.
- Database: SQLite with FTS5/BM25 for local RAG. File databases use WAL, `busy_timeout=5000`, path/identity-aware one-time Schema initialization per process, and an adjacent cross-process initialization lock; `:memory:` remains directly initialized for tests.
- AI: internal ModelProvider layer plus `LlmClient` compatibility facade for mock, DeepSeek, Kimi, Zhipu GLM, OpenAI, and custom OpenAI-compatible providers with local structured fallback.
- Planning: Phase 7 typed artifacts under `Backend/backend/app/cognitive_planning` are the source of truth for Cognitive OS P Mode sessions; `StructuredGoalPlan` remains the compatibility/Calendar projection and the source of truth for legacy flows.
- Runtime: `/api/runtime/run` returns NDJSON events from Planner, Memory, Tool Router, Stream Engine, and Runtime Orchestrator.
- Desktop runtime: Tauri window loads bundled web resources and starts the PyInstaller sidecar `planix-api.exe`.
- Desktop API access: normal JSON calls use Tauri IPC `proxy_api`; Runtime streaming uses `stream_agent_runtime`.

## Entry Points

- Web entry: `Frontend/index.html`
- Web app: `Frontend/src/App.tsx`
- Web API layer: `Frontend/src/lib/api.ts`
- Agent Trace: `Frontend/src/components/agent/flow/AgentFlowTrace.tsx`
- Agent Runtime store: `Frontend/src/store/agentFlowStore.ts`
- Shell: `Frontend/src/shell/RivaShell.tsx`
- Route hook: `Frontend/src/shell/useAppRoute.ts`
- i18n entry: `Frontend/src/i18n/index.ts`
- Desktop Rust entry: `apps/desktop/src-tauri/src/main.rs`
- Backend app: `Backend/backend/app/main.py`
- Backend schemas: `Backend/backend/app/schemas.py`
- Structured planning helper: `Backend/backend/app/services/structured_goal_plan.py`
- Planning quality gate: `Backend/backend/app/services/planning_quality.py`
- Runtime route: `Backend/backend/app/routers/runtime.py`
- Runtime service: `Backend/backend/app/services/runtime.py`
- Command route: `Backend/backend/app/routers/command.py`
- Command service: `Backend/backend/app/services/command_agent.py`
- Model provider layer: `Backend/backend/app/services/model_provider.py`
- LLM compatibility facade: `Backend/backend/app/services/llm.py`
- Cognitive planning kernel: `Backend/backend/app/services/cognitive_planning`
- Phase 7 Cognitive OS: `Backend/backend/app/cognitive_planning`
- Agent Runtime Harness: `Backend/backend/app/harness` (`scheduler.py`, `state.py`, `registry.py`, `policy.py`, `recovery.py`, `controllers.py`, `persistence.py`, `observability.py`, and compatibility adapters)
- Phase 7 agents: `Backend/backend/app/cognitive_planning/agents`
- Phase 7 graph: `Backend/backend/app/cognitive_planning/graph/planning_graph.py`
- Phase 7 memory: `Backend/backend/app/cognitive_planning/memory/user_model.py`
- Shared execution and Calendar invariants: `Backend/backend/app/services/cognitive_planning/evaluation/deterministic_guards.py`
- Planning compatibility facade: `Backend/backend/app/services/deep_planning.py`; frozen rollout-off implementation: `Backend/backend/app/services/legacy_deep_planning.py`
- Cognitive contracts: `Backend/backend/app/services/cognitive_planning/contracts`
- Cognitive agents: `Backend/backend/app/services/cognitive_planning/agents`
- Cognitive orchestration: `Backend/backend/app/services/cognitive_planning/orchestration`
- Cognitive retrieval/evaluation: `Backend/backend/app/services/cognitive_planning/retrieval` and `Backend/backend/app/services/cognitive_planning/evaluation`
- QA-only shadow comparison: `CognitivePlanningShadowRunner` writes safe metrics to `planning_shadow_runs` using isolated shadow thread IDs; it is never automatic in normal P Mode.
- Settings maintenance route: `Backend/backend/app/routers/maintenance.py`
- Settings maintenance service: `Backend/backend/app/services/maintenance.py`
- SQLite setup: `Backend/backend/app/db.py`
- Backend tests: `Backend/backend/tests`
- Packaging scripts: `scripts`

## Naming Rules

- Product name: `Planix`
- Backend health app: `planix-api`
- Sidecar executable: `planix-api.exe`
- Desktop executable: `planix.exe`
- Intended portfolio installer artifact: `Planix-v3.0.0-windows-x64-setup.exe`
- Intended backup MSI artifact: `Planix-v3.0.0-windows-x64.msi`
- Environment namespace: `PLANIX_*`
- Desktop database: `%APPDATA%\Planix\planix.db`
- Development database: `data/planix.db`
- Frontend localStorage prefix: `planix_*`

There is no compatibility fallback for old names or old environment variables.

## Frontend Rules

- Do not introduce `react-router`; use the existing hash-route model.
- `AppRoute` must remain the single source of truth for active pages.
- `AppMenu` may store only UI expansion state, never active route state.
- All static frontend UI text must go through `t("namespace.key")`.
- Do not translate user input, AI output, or existing database content.
- Dashboard Agent Trace consumes Runtime events first and falls back to local demo flow only when Runtime is unavailable.
- Legacy Dashboard/Workbench Runtime fallback must clearly show `当前使用本地规划模板生成，后端 Runtime 未连接`; Cognitive OS must never use or display this fallback as formal planning.
- Runtime success and fallback must not display old `plan_context_lookup`, `ui-mock`, or static UI mock wording.
- Runtime output should answer the user's prompt directly; for a Python learning prompt, return a concrete Python learning plan summary.
- Goals should display `structuredPlan` when present while keeping legacy task apply flows based on `tasks`.
- Goals calendar writes must show immediate writing feedback, a visible pressed/writing button state, and final created/updated/failed counts.
- Dashboard Runtime proposals may be written to Calendar only after the user clicks `写入日历`; valid `llm` and `local_fallback` structuredPlan outputs are both writable, and Runtime execution itself must not auto-write Calendar data.
- Dashboard Runtime proposal metadata may show plan quality label, horizon duration, task count, and coverage range, but must not show raw validator JSON or full quality score.
- Legacy Workbench hidden `calendar_plan` draft payloads should preserve `planHorizon`, `qualityReport`, `qualityStatus`, `sourceType`, and `localRelevance`; Cognitive OS auto planning does not create these drafts.
- Refined task cards should display optional `timeBlocks`, `learningResources`, `budgetExplanation`, and `planFitCheck` when present while remaining compatible with older refined tasks.
- Calendar and Goals task refinement must pass the task's real `estimatedMinutes`; do not hardcode Calendar refinement to 60 minutes.
- Calendar refinement for AI plans with `command-draft:{draftId}:m{milestoneIndex}:t{taskIndex}` source keys must resolve the original hidden draft and build compact plan context from its `structuredPlan`; fall back to the current Calendar row only when that lookup is unavailable.
- AI Calendar writes must preserve task `description` in `plans.result`, plus `estimatedMinutes`, `priority`, and `sourceKey`. Existing AI rows may be topped up only when `result` is empty; never overwrite user-entered completion/result.
- Command/P Mode must stay Codex-like and minimal: `CommandPage` contains the Agent thread and bottom composer only. Do not add a fixed `PWorkspacePanel` or persistent draft panels.
- The top-left Planix `P` brand mark and the menu P entry both route to Command/P Mode; collapsed menu states must still show a visible P letter and should never leave only a colored background.
- When no explicit hash route exists, the frontend should default to `#/command`. The P composer should use normal bottom anchoring while feeling spacious: start around two text rows, begin text from the left edge, grow with input, and scroll internally only after about five rows.
- Keep the P page visually loose: empty state content should sit slightly above center, the thread should have breathing room, and no fixed workspace or draft panel should be introduced.
- P Workspace is an internal draft/audit concept, not a foreground layout. Phase 4.4 may write hidden `calendar_plan` drafts and Calendar write actions only.
- The right-side P conversation drawer is allowed for thread history and deletion only. It must not become a fixed workspace preview or persistent draft panel.
- Command mode is a single `auto | chat | workbench` value. Do not represent it with separate boolean flags.
- Auto mode is the P default. For new routing input, `GoalUnderstandingResult` runs before generic `CommandDecision`: `clear_goal` enters Cognitive OS, `ambiguous_goal` asks its `nextQuestion`, `normal_chat` stays conversational, and `command` continues to the LLM-first command router. Active Planning Session follow-ups first pass through the Phase 7.3 Control Intent Router; only `provide_goal_information` enters Goal Intelligence. Auto planning must not run legacy Runtime or create a hidden draft. Forced `chat` mode does not execute commands, and manual `workbench` mode still forces legacy Runtime planning.
- Workbench planning should include current-thread context in the backend Runtime input so follow-up phrases like "帮我做个规划" can inherit the current topic. Do not include messages from other threads.
- After a valid Workbench draft is created, legacy P Mode replay may show the summary and full plan inline; Cognitive OS shows its user-facing artifacts instead.
- Chat mode is a safety lock: it must not run Dashboard Runtime, create drafts, write Calendar data, or execute any instruction.
- Workbench mode remains the only manual forced legacy Runtime entry. Auto planning uses Cognitive OS and must not run backend Runtime when the intent is `create_plan`.
- Command permission state is `low | medium | high`; low asks before writes/deletes, medium auto-runs ordinary writes but asks before deletes, high auto-runs ordinary writes/deletes while dangerous actions still require confirmation.
- Cognitive P Mode Calendar writes come from the approved Planning Session and use `planning-session:` source keys. Legacy Workbench writes may come from a hidden draft and use `command-draft:` keys. Neither path may overwrite manual plans or `completion/result/done`.
- P Mode `query_plan` and `patch_calendar_plan` are handled through `/api/command/chat`; do not add public query/patch REST routes or a fixed P Workspace panel.
- P Mode `query_memory` and `save_memory` are handled through `/api/command/chat`; `query_notes` maps to note-only memory search and `save_note` maps to note memory creation for compatibility.
- P Mode date words for plan query/patch use `context.date` first, then backend local date. Supported ranges include today, tomorrow, the day after tomorrow, this week, next week, this month, and explicit `YYYY-MM-DD`.
- P Mode patch commands may target the most recent `plan_search_results` card by ordinal phrases such as "first" or "第一个"; ambiguous multi-candidate matches should return a selection/search card rather than creating an action.
- P Mode patch actions use `command_actions.target = "calendar"`, `operation = "update" | "delete"`, and `risk = "write" | "delete"` with payload `before`, `after`, and `changes`.
- P Mode memory-save actions use `command_actions.target = "memory"`, `operation = "create" | "update" | "delete"`, and `risk = "write" | "delete"` with payload `kind`, `title`, `content`, `summary`, `tags`, `source`, and `metadata`. Legacy `target="notes"` actions are mapped to `kind="note"` memories.
- In a thread with a current `calendar_plan` draft, P Mode phrases such as `写入计划`, `保存计划`, `保存`, and `确认写入` mean writing the current draft to Calendar through PermissionGate; they must not start a new Runtime planning run.
- P Mode Calendar writes may carry `refinedTask` values from `command_drafts.payload_json.refinements` into `plans.refined_task_json`; this must never be mixed into `completion/result/done`.
- P Mode Calendar write failures, including approval execution failures, must show the Calendar-specific write error and must not fall back to draft-save failure wording.
- Command/planning startup migrations must preserve old local SQLite data. In addition to the existing command-action columns, Phase 7.3 additively creates `planning_sessions.goal_completion_json`, `business_status`, and `runtime_status`, then backfills old sessions from their status and successful artifacts without destructive rebuilds.
- P Mode Runtime execution cards should be grouped as one collapsible inline execution chain after output completes. The collapsed row should use a lightweight center arrow toggle, not a heavy gray trace panel. Do not reintroduce a fixed Trace panel in P Mode.
- P Mode execution chain groups should blend into the page background instead of using a gray block.
- Cognitive P Mode planning cards should aggregate into one live default Planning Workspace. Map internal session/business states to Understand Goal, Confirm Direction, Design Plan, Optimize Plan, Waiting Confirmation, Write Calendar, or Review & Learn; runtime failure must not overwrite a later business stage in the user-facing heading.
- The default Planning Workspace shows Goal Understanding, Known Facts, Important Unknowns, and Next Action and updates in place across user turns. Do not restore the old collapsed five-step/per-message planning timeline. Technical cards and standalone model-routing diagnostics render only when Advanced Debug Mode is enabled.
- Calendar month view should load all plans for the visible month so dates with plans are highlighted before the user clicks them.
- Calendar full-plan clearing should prefer `DELETE /api/plans/all`; if an older backend returns 404, the frontend may fall back to deleting known plans one by one and must keep any failed deletions visible.
- Settings model input is free text. Built-in recommendations may be provider-specific for DeepSeek, Kimi, Zhipu GLM, OpenAI, custom, and mock; do not restore legacy marketing model display names.
- Keep Agent Trace visually secondary to the Workspace; it must not replace the prompt input or dominate the Dashboard.
- Internal `reasoning` nodes must display as `Plan` / `执行计划`; do not expose hidden chain-of-thought.
- Keep Calendar, Notes, Goals, and Settings functionality available through the menu.

## Backend Rules

- Keep AI features demoable without an API key.
- A saved key enables live model calls automatically unless the provider is `mock`.
- Provider settings support `mock`, `deepseek`, `kimi`, `zhipu_glm`, `openai`, and `custom`. Switching providers may fill the provider default Base URL only when the existing Base URL is empty or still the old provider default.
- Provider API Keys are stored per provider in `ai_provider_configs`; active provider stays singular in `ai_settings`. Deleting one provider key must not delete another provider's key or switch the active provider.
- `ModelRouter` selects by task type when a routing rule exists, tries primary then up to two fallback providers, skips missing keys with safe attempts, and returns local-fallback eligibility to the business layer instead of generating business fallback content itself.
- `ModelCallRequest.response_format_json` must map to OpenAI-compatible `response_format={"type":"json_object"}` only for existing JSON-output call paths, and model max tokens must stay within the configured cap.
- Model errors should map to standard `errorType` values such as `auth_error`, `bad_model`, `bad_base_url`, `network_error`, `timeout`, `rate_limit`, `insufficient_balance`, `invalid_key_format`, `invalid_model_output`, and `model_output_truncated`.
- `record_ai_run` may store provider, model, feature, input/output summaries, and safe error text only. It must never store API keys, Authorization headers, raw headers, or URLs containing secrets.
- Never expose full API keys in read endpoints, logs, screenshots, or docs.
- Preserve source-grounded RAG behavior.
- LLM-generated planning output must be parsed, schema-validated, and completed with fallback defaults.
- Goal planning asks the model only for `summary + structuredPlan`; legacy `phases` and `tasks` are derived from `structuredPlan`.
- `PLANIX_GOAL_PLAN_MAX_TOKENS` controls Goals planning output budget. Default is `4096`; values above `8000` must clamp to `8000`.
- OpenAI-compatible `finish_reason="length"` must map to `errorType="model_output_truncated"` rather than generic invalid JSON.
- `GoalPlanOut` must keep `summary`, `phases`, `tasks`, and `sources` while adding `structuredPlan`, `planHorizon`, `qualityReport`, `qualityStatus`, `sourceType`, and `localRelevance`.
- `qualityReport.metrics` may add optional demo reliability fields derived from the existing quality report and plan metadata: duration days, task/milestone/week counts, date span, weak/missing/out-of-range task counts, repair/fallback booleans, quality status, source type, and local relevance. These fields are diagnostic only and must not change Calendar task schema or write permissions.
- Planning quality must run in the shared Planning Service after model parsing/normalization. Detect the horizon from explicit duration first, then valid deadline, then long-term keywords, otherwise default to 14 days.
- Horizon-aware planning instructions and validation should replace static tiny limits. 90-day plans should prefer monthly milestones, at least 24 tasks, and at least 10 covered weeks.
- If quality validation has error-severity issues, call at most one repair prompt. If repair still fails, return the stronger local structured fallback with `qualityStatus="local_fallback"`.
- Local fallback must spread due dates across the full horizon, keep `estimatedMinutes` capped by available daily minutes, and generate 90-day plans with 3+ milestones, 24+ tasks, non-identical due dates, and 10+ covered weeks.
- Quality validation should flag missing milestones/tasks, too few tasks, insufficient week coverage, first-week-only long plans, missing/out-of-range due dates, empty titles, and weak generic titles such as `继续学习`, `保持练习`, or `完成任务`.
- Planning fallback must be transparent with safe diagnostics: `fallbackReason`, `errorType`, and host-only `baseUrlHost`.
- `planning_goals` is planning result history/cache, not a confirmed execution table.
- The Phase 3.9 quality fields require no database migration; `planning_goals` continues storing the final `structured_plan_json`.
- Local retrieved materials count as `sourceType="local_context"` only when core or strong expanded keywords match. Generic weak keywords alone must never produce strong local relevance.
- When local context is insufficient, Runtime final output should prepend `本地资料不足，下面是通用建议，不代表资料库事实.`
- Runtime tools are restricted to read-only or preview-only behavior in this phase.
- Runtime must build one internal Context Pack containing goal, explicit constraints, preference memory, history memory, today plans, materials, and output language.
- Preference memory is separate from history memory and has higher planning priority.
- `payload.preferences` must merge field-by-field with saved preferences; never overwrite the whole preference object when only one field is provided.
- Runtime must clean history before retrieval and planning. Expose `historyMemory.recentProgress` as short `{ title, summary, relevanceToGoal }` objects; do not pass full Markdown, full historical output, or full `structuredPlan` blobs to `search_materials` or Planning prompts.
- `search_materials.input.query` must stay short and goal-focused. For a swimming goal, it should contain swimming terms and preferences, not Python/AI internship/skiing long-form history.
- `memoryContextSummary` must be deterministic, short, and safe for display.
- `search_materials`, `get_today_plans`, and `get_memory` are read-only.
- `get_memory` returns `preferenceMemory` and `historyMemory`; do not add a separate `get_preferences` tool.
- `propose_tasks` may return structured task previews with `memoryContextSummary`, `planHorizon`, `qualityReport`, `qualityStatus`, `sourceType`, and `localRelevance`, including optional `qualityReport.metrics`, but must not write to `plans`, Goals, Calendar, or Notes.
- `/api/health` and `/health` should expose `name="planix-api"`, `version="3.11-demo-reliability"`, `startupTime`, and feature flags for `planQualityGate`, `contextAwareRefinement`, `calendarDraftContextRecovery`, and `demoMetrics` so stale backend processes are easy to identify.
- `structuredPlan` is the fact source; Runtime final output should be rendered from it.
- `RuntimeOrchestrator` is the only component that coordinates Planner, Memory, Tool Router, and Stream Engine.
- Rust/Tauri streaming bridge must stay a thin pass-through; do not put Runtime state or business logic in Rust.
- Settings maintenance endpoints under `/api/settings/*` may clear AI memory/cache only. They must not delete formal `plans`, Calendar data, Notes/materials, documents, or AI settings.
- Phase 4.8 command endpoints expose `POST /api/command/chat`, `POST /api/command/approve`, `GET /api/command/threads`, `GET /api/command/thread/{thread_id}`, and `DELETE /api/command/thread/{thread_id}`. They store `command_threads`, `command_messages`, hidden `command_drafts`, Calendar and Notes write/patch `command_actions`, and `command_approvals`.
- Phase 4.8 command streams and replay messages may include `command_decision`, `model_usage`, `clarify_question`, `memory_search_results`, `memory_write_preview`, and `memory_write_result` in addition to the existing Runtime, draft, Calendar write, plan search, and Calendar patch cards. Phase 7.1 additively streams/replays `goal_understanding`; Phase 7.3 additively streams/replays `goal_completion_updated`, and `planning_session_status` includes `businessStatus`, `runtimeStatus`, and `goalCompletion`. Legacy `note_*`, Planning Session, and Runtime cards remain replay-compatible.
- `PlanningSessionResponse` exposes durable `businessStatus` (`goal_clarification`, `goal_understood`, `planning`, `calendar_pending`, `completed`, `blocked`, or `cancelled`) separately from `runtimeStatus` (`idle`, `running`, `blocked_model`, or `retry_required`).
- A failed cognitive model stage must persist its resume node and all earlier canonical artifacts. `continue_current_stage` retries only that failed stage; it must not recollect a completed goal, rerun completed evidence stages, or create an Agent decision/artifact for a model call that failed.
- Phase 4.8.1 row action buttons are UI affordances only: they must send fixed natural-language messages such as `修改第 1 个计划` back through `/api/command/chat` and must not directly call Calendar or Notes APIs.
- `refine_current_plan` is a command intent handled through `/api/command/chat`. It updates the current `command_drafts.payload_json.refinements`, emits inline refinement result cards, and does not write Calendar unless the user separately commands a Calendar write.
- `query_plan` and `patch_calendar_plan` are command intents handled through `/api/command/chat`. They emit `plan_search_results`, `plan_patch_preview`, and `plan_patch_result` cards, and replay history must tolerate those card kinds.
- Phase 3.10 task refinement must keep all new fields optional on `RefineTaskRequest` and `RefinedTask`; no database migration is required because existing `refined_task_json` storage is reused.
- `planContext` for refinement must be compact: plan title/summary, duration, quality status, daily budget, current milestone/task, previous/next task, same-milestone task titles, and top source summaries only. Do not pass a full 24+ task plan into every refine prompt.
- Refinement time budget precedence is current task `estimatedMinutes`, then `availableMinutes`, then `planContext.dailyLearningMinutes`, then user-mentioned time, then 60 minutes.
- Every refined `timeBlock.durationMinutes` must be <= 30. Backend normalization must split longer model blocks such as 40, 45, 60, 90, or 120 minutes into <=30 minute blocks.
- Learning resources may expose URLs only for official/authoritative allowlisted domains such as `docs.python.org`, `pypi.org`, `flask.palletsprojects.com`, `pandas.pydata.org`, `numpy.org`, `requests.readthedocs.io`, `beautiful-soup-4.readthedocs.io`, `fastapi.tiangolo.com`, `sqlalchemy.org`, `sqlite.org`, and `developer.mozilla.org`. Other URLs must be removed and converted to `searchKeyword`.
- Refinement must not change the task domain. If compact plan context clearly indicates skiing, a yoga/fitness/meditation refinement is invalid and should fall back to a same-domain local refinement.
- P Mode `refine all tasks` must avoid 24+ LLM calls in one request. First version should refine the current/first milestone and cap each batch at 5 tasks, preserving partial successes.
- Do not register `/api/command/drafts` or use `command_outputs` until a later phase explicitly enables them.
- `DELETE /api/settings/memory/history` clears only Runtime summary memory (`agent_runs.output_summary`), while `DELETE /api/settings/runtime/runs` deletes `agent_events` and `agent_runs`.
- `DELETE /api/settings/planning/history` clears `planning_goals` only, which is planning history/cache.

## Commands

Frontend:

```powershell
cd Frontend
npm install
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run test
npm.cmd run build
```

Backend:

```powershell
cd Backend
..\.venv\Scripts\python.exe -m compileall backend
..\.venv\Scripts\python.exe -m pytest backend\tests
```

Phase 7.1 compatibility acceptance specifically verifies Beijing and Urumqi purpose clarification, same-thread follow-up into planning, skiing/project consistency blocking, literal-only extraction without travel meaning, `goal_understanding` stream/replay, friendly stages, and Advanced Debug Mode-only technical disclosure. Phase 7.3 supersedes the original collapsed-process default UI.

Phase 7.3 acceptance specifically verifies semantic multi-turn Go completion (`complete=true`, `nextStage="strategy"` despite optional unknowns), planning-control routing before Goal Intelligence, safe `skip_current_stage` progression from saved context without rerunning Goal Intelligence, non-skippable consistency/safety/feasibility blockers, separate business/runtime status on Strategy `auth_error`, recovery from the failed Strategy node without rerunning Goal/Reality/Evidence, no fake decision/artifact on failure, additive completion/status stream and replay, non-destructive session-column migration, and one live Planning Workspace without a per-message timeline. Targeted coverage lives in `Backend/backend/tests/test_goal_completion_hotfix.py`, `Backend/backend/tests/planning_evals/test_phase73_hotfix.py`, `Frontend/src/components/command/PlanCommandCards.test.tsx`, and `Frontend/src/stores/commandAgentStore.test.tsx`.

The formal ten-direction real-model batch must be advanced through the existing `/command` page. `scripts/live_planning_e2e.py` is a GET-only manifest auditor and must never accept API keys or POST chat. Its schema-v4 report verifies the formal Planning Session snapshot, observed providers, and the credential-free runtime-source fingerprint.

Demo reliability:

```powershell
.\scripts\verify-demo.ps1
```

Desktop packaging:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-packaging-toolchain.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\build-release.ps1 -Version 3.0.0
```

Portfolio release naming uses `release\Planix-v3.0.0-windows-x64-setup.exe` as the primary Setup.exe and `release\Planix-v3.0.0-windows-x64.msi` as the backup MSI. Each installer should have its own `.sha256` checksum. Release binaries remain ignored by Git and should be attached through release tooling rather than committed.

## Documentation Maintenance

Every completed implementation must update `README.md`, `AGENTS.md`, and `CLAUDE.md` before final reporting. Include relevant behavior, API/database changes, phase boundaries, and verification notes; do not record secrets, local API keys, or machine-specific credentials.

Update these three files whenever architecture, version, routes, UI shell, API behavior, database behavior, environment variables, AI strategy, packaging, release artifacts, screenshots, or portfolio positioning changes.

`README.md` is the portfolio-facing project homepage. Keep internal maintenance rules, implementation notes, and historical development logs in `AGENTS.md`, `CLAUDE.md`, or `docs/`, not at the top of `README.md`.

The portfolio-facing documentation version is `v3.0.0`. Do not confuse this with `package.json`, `tauri.conf.json`, `Cargo.toml`, backend health responses, or installer build versions unless a separate release-version bump task is explicitly requested.

Current Calendar behavior includes a compact month layout, a `Note` area on the Calendar panel, selected-day plan clearing, and full calendar plan clearing. The two clearing buttons should remain visible above the date grid. Clearing plans deletes Calendar `plans` and their task refinements only; it must not delete notes/materials, Goals planning history, AI settings, documents, or API keys.
