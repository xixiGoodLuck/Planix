# Phase 7 Cognitive Planning OS Acceptance

This document maps the Phase 7 product requirements to code and automated evidence.

## Canonical Runtime

- Entry point: `Backend/backend/app/cognitive_planning/runtime.py`
- Graph: `Backend/backend/app/cognitive_planning/graph/planning_graph.py`
- Contracts: `Backend/backend/app/cognitive_planning/contracts/artifacts.py`
- Agents: `Backend/backend/app/cognitive_planning/agents`
- User Model Memory: `Backend/backend/app/cognitive_planning/memory/user_model.py`
- Shared execution and Calendar invariants: `Backend/backend/app/services/cognitive_planning/evaluation/deterministic_guards.py`
- P Mode adapter: `Backend/backend/app/services/command_agent.py`

The canonical planning runtime is always selected; there is no planning feature flag or executable legacy fallback. Existing generic Harness, checkpoint, artifact, memory, and Calendar infrastructure remains shared.

## Behavioral Gates

| Requirement | Enforcement |
| --- | --- |
| No domain template or fixed question bank drives planning | Goal and Reality agents receive conversation, memory, and request context through model-backed typed contracts. |
| Reality is independent | `RealityAgent` owns `RealityAssessment` and can return the session to clarification before Evidence. |
| Strategy requires grounded understanding | Graph routing stops when Goal or Reality reports a blocking unknown. |
| Execution requires user-approved strategy | Existing strategy approval artifact gate remains enforced by the runtime. |
| Critic can veto and repair | Independent Critic issues targeted repair requests; graph caps repairs at two rounds. |
| Calendar requires human and critic approval | Canonical critic rules plus existing action preview and PermissionGate protect Calendar writes. |
| Model failure never becomes a fake plan | Runtime sets exact status `MODEL_UNAVAILABLE`, clears strategy/execution/critique artifacts, and emits no Calendar preview. |
| Learning is evidence-backed | `user_model_memories` stores evidence, contradictions, observation count, confidence, status, domain scope, and expiry. |
| P Mode is not a technical trace | Cognitive sessions emit user-facing artifact events; raw Agent decisions and messages remain persisted but are filtered from the main workspace. |

## Golden Scenarios

Automated coverage lives in `Backend/backend/tests/planning_evals/test_cognitive_kernel.py`.

1. **Swimming**: the model discovers safety, training environment, target standard, and time horizon; project, internship, and README templates are forbidden.
2. **Xinjiang travel**: Reality and Evidence address season and long-distance transport without asking software-career questions or using a static resource catalog as the decision source.
3. **Go learning**: a vague goal asks only high-impact questions about desired result, current transferable skills, and available time; no plan is generated early.
4. **Model unavailable**: exact `MODEL_UNAVAILABLE`, no strategy, no execution, no technical Agent log, and no Calendar action.
5. **Critic repair**: at most two repair rounds before approval or a blocked outcome.

## Validation

```powershell
cd Backend
..\.venv\Scripts\python.exe -m compileall backend
..\.venv\Scripts\python.exe -m pytest backend\tests
cd ..\Frontend
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run test
npm.cmd run build
git diff --check
```
