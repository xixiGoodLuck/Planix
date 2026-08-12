# Planix Learning

Planix Learning 是本地优先的技术学习内容 Agent。它理解自然语言学习目标，建立知识路线，搜索 B 站视频，使用可验证的 Transcript 证据推荐具体观看片段，并解释推荐内容、理由与时长。证据不足时，Planix 会进入可恢复的 `waiting_evidence`，等待用户补充有权使用且与视频匹配的字幕，而不会伪造内容或时间戳。

## 产品能力

- 把学习目标拆解为学习成果、能力与知识图谱。
- 从真实 Provider 获取视频元数据；模型不能发明 URL、视频 ID 或时长。
- 只有通过验证的 SRT/VTT 字幕片段才能产生精确观看时间范围。
- 检查必需知识覆盖、缺口、冗余与版本兼容性，并执行有限的证据补全。
- 输出可追溯的 `LearningContentPlan` 与代码判定的 `LearningQualityReport`。
- 使用 PostgreSQL 17 持久化运行、Artifact、Checkpoint、恢复事件和 Transcript Evidence。
- 页面刷新、SSE 临时断线或 Backend 重启后，以 Backend 状态为准恢复运行。

## 本地运行

需要 Python 3.11+、Node.js 20+、PostgreSQL 17，并设置指向 PostgreSQL 的 `DATABASE_URL`。

```powershell
docker compose up -d postgres
cd Backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --env-file ..\.env --port 8003
```

```powershell
cd Frontend
npm ci
npm run dev -- --port 5176
```

访问 `http://127.0.0.1:5176/#/learning`。正式页面只有 `#/learning`（默认）与 `#/settings`。

详细步骤见 [用户指南](docs/user-guide.md)，架构边界见 [架构说明](docs/architecture.md)，发布演示见 [演示指南](docs/demo.md)。
