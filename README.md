# Planix

简体中文 | [English](README.en.md)

Planix 是一个本地优先的 AI 规划工作台：把模糊目标转成经过验证的 Plan、可行的 Schedule，并且只在用户明确批准后写入真实 Calendar。

作品集文档版本：**v3.0.0**。

## 核心能力

- 单一、原生的正式 Planning Runtime 和 LangGraph。
- 动态目标理解与用户显式确认。
- 类型化约束、带来源的上下文、直接 Plan 生成、确定性验证、语义审查和有界的定向修复。
- Schedule 与 Plan 语义分离，分别验证和修复。
- 版本绑定的 Final Review 与显式 Calendar 权限。
- 执行反馈、局部重规划，以及经过独立评估的长期学习。
- 纯聊天、Dashboard Runtime、手动 Workbench、Goals、Calendar、Materials/RAG、Memory、Settings 和 Windows 桌面打包保持为独立产品能力。

## 正式规划流程

```text
用户输入
→ UnderstandingSnapshot
→ Understanding readiness
→ 用户确认
→ ConstraintSet
→ ContextPack
→ PlanBlueprint
→ Hard validation
→ Semantic review
→ 定向修复（仅在需要时，最多两轮）
→ ScheduleBlueprint
→ Schedule validation/repair
→ CalendarProposal
→ Final Review
→ FinalApprovalBundle
→ Calendar permission
→ Calendar write
→ Execution feedback and learning
```

项目中不存在第二套正式 Runtime、备用 Planning Graph、兼容投影、模板兜底或数字分数审批门槛。`QualityReport` 只有在全部硬规则通过且没有 blocker/major 问题时才通过；分数只用于诊断。

## 安全与数据边界

- 模型失败时保留当前有效 Artifact，并阻塞在失败的原生节点；不会生成假计划。
- Planning 内容只保存到不可变、带版本的 `planning_artifacts`。新 Session 的 `planning_sessions` 只保存生命周期和请求状态。
- 不破坏已有数据库。退役 Session 迁移默认 dry-run，执行前备份，只归档、不删除。
- Final Approval 绑定当前 Understanding、约束、上下文、Plan、质量报告、Schedule、Calendar Proposal、Calendar Snapshot 和 Checkpoint 版本。
- Calendar 写入还必须通过 Command action/approval、Harness policy、PermissionGate、当前版本检查和幂等 source key。
- 原始反馈不能直接成为长期记忆，必须先经过独立 Memory Evaluation。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite |
| 后端 | Python、FastAPI、Pydantic 2 |
| Planning Runtime | LangGraph、Planix Harness |
| 存储 | SQLite、FTS5、本地文件 |
| 桌面端 | Tauri 2、FastAPI sidecar |
| 测试 | Pytest、Vitest、Testing Library、ESLint、TypeScript |

## 本地开发

Backend：

```powershell
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

Frontend：

```powershell
cd Frontend
npm run dev -- --port 5176
```

访问地址：

- Frontend：`http://localhost:5176`
- Backend：`http://127.0.0.1:8003`
- API 文档：`http://127.0.0.1:8003/docs`

## 目录

```text
Planix/
├─ Frontend/
├─ Backend/
│  └─ backend/app/
│     ├─ cognitive_planning/  # 唯一正式 Planning Runtime
│     ├─ harness/             # Policy、Recovery、Approval、Observability
│     └─ services/            # 独立产品服务
├─ apps/desktop/
├─ docs/
├─ scripts/
└─ tools/migrations/
```

更多内容见 [架构说明](docs/architecture.md) 和 [正式 Runtime 验收标准](docs/cognitive-planning-acceptance.md)。
