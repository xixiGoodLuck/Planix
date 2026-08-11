# Planix Learning

Planix Learning 根据用户的技术学习目标建立知识路线，搜索并分析真实视频内容，使用可验证的字幕/Transcript Evidence，筛选需要观看的具体片段，并解释知识、视频内容、推荐原因和推荐观看时长。

## 产品能力

- 将学习目标拆解为成果、能力与知识图谱。
- 从真实 Provider 获取视频元数据，禁止模型编造 URL、视频 ID 或时长。
- 只从已验证字幕生成时间范围和内容证据。
- 分析知识覆盖、缺口、重复与版本冲突，并执行有限轮次的证据补全。
- 输出可追溯的 `LearningContentPlan` 与代码判定的 `LearningQualityReport`。
- 使用 PostgreSQL 17 持久化 Learning Run、Artifact、Checkpoint、Resume Event 和 Transcript Evidence。

## 页面与 API

正式页面只有：

- `#/learning`（默认）
- `#/settings`

正式业务 API：

- `/api/learning/*`
- `/api/ai/*`
- `/health`

## 本地运行

```powershell
docker compose up -d postgres
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm run dev -- --port 5176
```

访问 `http://127.0.0.1:5176/#/learning`。架构说明见 [docs/architecture.md](docs/architecture.md)。
