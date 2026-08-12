# Planix Learning 用户指南

1. 启动 PostgreSQL 17：运行 `docker compose up -d postgres`，不要使用 SQLite 替代。
2. 迁移数据库：在 `Backend` 目录执行 `..\.venv\Scripts\python.exe -m alembic upgrade head`，不要手工建表。
3. 启动 Backend：在 `Backend` 目录执行 `..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --env-file ..\.env --port 8003`。
4. 启动 Frontend：在 `Frontend` 目录先执行 `npm ci`，再执行 `npm run dev -- --port 5176`。
5. 打开 `#/settings`，配置真实模型 Provider，并确认 Model、Video、Transcript 和 Artifact Store 健康。密钥不会进入 PostgreSQL 或前端持久化存储。
6. 回到 `#/learning`，用自然语言输入学习目标。Planix 会先展示 Scope Review；可以一次补充基础、目标、预算和语言，也可以手动继续。
7. 如需指定视频，选择“我有指定视频”并填写真实 B 站 URL。只提供视频时可以分析元数据，但不能生成精确时间戳。
8. 上传或粘贴自己有权使用、且与该视频匹配的 SRT/VTT。原始字幕只用于注册请求，不会写入浏览器持久化状态、普通日志或 API 响应。
9. 若出现 `waiting_evidence`，查看每个 MISSING/PARTIAL 缺口、已检查资源、缺字幕资源和下一步。此状态不是失败，也不会产生无证据的最终计划。
10. 补充匹配字幕后，在同一页面点击“重新检查证据”。`resume-evidence` 会继续同一个 run，不重新生成 KnowledgeGraph；刷新页面或重启 Backend 后仍可恢复。

运行中会显示当前业务阶段、开始时间、持续时间、最近事件、Provider 状态和连接状态。SSE 临时断开后会由有限退避的状态查询接管；如阶段耗时较长，可手动刷新状态或返回 Scope Review，长耗时本身不会被误判为失败。
