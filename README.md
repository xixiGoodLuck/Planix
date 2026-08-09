# Planix

简体中文 | [English](README.en.md)

Planix 是本地优先的 AI 规划应用。作品集文档版本为 **v3.0.0**。

## Pure Planix V2

正式 P Mode 只有一套原生规划运行时：用户目标经过 Understanding、约束与上下文编译、Plan 生成与验证、Schedule 生成与验证、Final Review，最后在显式批准和权限检查后写入 Calendar。模型失败会保留有效 Artifact 并停在失败节点，不使用模板、Mock 或旧运行时兜底。

模型路由仅包含：

- `planning_understanding`
- `planning_plan`
- `planning_review`
- `planning_learning`

Calendar、Settings 和桌面打包是独立产品能力，不是正式规划的替代路径。V2 Context 仅使用可追溯的请求上下文、日历快照和正式学习记忆。

## 启动

Planix requires PostgreSQL. Start the pinned local service and apply the Alembic schema first:

```powershell
docker compose up -d postgres
$env:DATABASE_URL="postgresql://planix:planix@127.0.0.1:5432/planix"
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
```

```powershell
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm run dev -- --port 5176
```

- Frontend: `http://localhost:5176`
- Backend: `http://127.0.0.1:8003`
- API 文档: `http://127.0.0.1:8003/docs`

详见 [架构说明](docs/architecture.md) 和 [V2 验收标准](docs/cognitive-planning-acceptance.md)。
