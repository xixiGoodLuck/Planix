# Formal Planning Runtime Acceptance Map

Planix has one production planning runtime and one formal planning graph. There is no runtime selector or template fallback.

| # | Requirement | Implementation evidence | Automated evidence |
|---|---|---|---|
| 1 | One runtime entry | `cognitive_planning.get_planning_orchestrator` returns `CognitiveOSRuntime` directly | planning command and runtime tests |
| 2 | Dynamic Understanding and explicit confirmation | formal Understanding contracts, readiness gate, and `wait_for_understanding` | understanding and runtime tests |
| 3 | Confirmed facts are frozen before planning | versioned `UnderstandingSnapshot` and stale-value guards | understanding tests |
| 4 | Constraint and Context compilation | formal planning services and graph nodes | plan/runtime tests |
| 5 | Plan validation, semantic review, and bounded repair | `QualityReport`, repair budget, and formal repair nodes | plan and repair tests |
| 6 | Schedule validation and repair | `ScheduleBlueprint` and schedule quality gate | schedule tests |
| 7 | Final Review binds the complete proposal | `FinalApprovalBundle` binds all upstream versions and checkpoint | runtime tests |
| 8 | Calendar remains permission-gated and idempotent | Harness policy, Calendar approval, and Command PermissionGate | harness/runtime/command tests |
| 9 | Model failure is recoverable without an alternate runtime | persisted checkpoint and blocked-model state | runtime and command tests |
| 10 | Feedback records outcomes and learning observations | execution feedback service and learning node | learning/runtime tests |
| 11 | Archived retired sessions are preserved, not executed | one-time migration tool with backup, raw metadata, and `ARCHIVED` status | migration tests |
| 12 | Frontend exposes only Understanding and Final Review decisions | Planning Workspace and formal action bar | frontend component/store tests |

## Runtime boundary

- Planning always enters the formal graph through `CognitiveOSRuntime`.
- The only user gates are Understanding confirmation, Final Review approval, and the version-bound Calendar permission gate.
- A model failure never invokes an alternate graph or template planner.
- Dashboard Runtime and Goals remain separate product capabilities; they are not planning-runtime fallbacks.
