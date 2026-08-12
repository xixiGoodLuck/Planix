# Planix Learning Release Demo

## Controlled Demo

受控演示使用生产契约、PostgreSQL、正式 API 与正式 UI，但使用受控 Metadata Adapter 和合法 SRT/VTT fixture，使结果可重复且不产生付费模型调用。

- 窄目标：`理解 FastAPI Routing、GET、POST 和 Swagger。`
- 资源：受控的 Bilibili 规范 URL 与明确标记的匹配字幕。
- 预期：Scope Review → 手动继续 → 实时阶段 → completed → Quality 100/PASS → Required Coverage 100% → Transcript-backed timestamp。
- 宽目标：`完成 FastAPI CRUD API。`，只提供 Routing 字幕。
- 预期：`waiting_evidence`、`error = null`、显示 persistence 等必需缺口、不生成 Final Plan、不生成无证据时间戳。
- Resume：注册匹配缺口字幕后继续同一个 run；KnowledgeGraph 引用不变，最终 Quality PASS。
- 非法模型：受控 Adapter 返回违反契约的结果；预期 `failed` 安全错误，不能 evidence resume。

受控演示不是在线字幕真实性声明。Fixture 不得标记为在线抓取结果。

## Live Source

实时演示必须同时满足：真实 Bilibili 元数据、真正匹配且有权使用的字幕、真实 Provider 健康、无 Mock/Template fallback。用户可以上传自己有权使用的字幕。

**Live Matching Transcript = NOT VERIFIED**

在完成逐一版权与内容匹配核验前，发布材料必须保留以上标记。真实 DeepSeek 黄金流程只在人工发布验收中运行；常规 CI 不调用付费 Provider。`workflow_dispatch` 中的 secrets-enabled 任务只做 Provider 连接预检，不能替代完整浏览器 Golden Flow。

## Screenshot set

发布截图存放在 `docs/assets/screenshots/`，目标集合依次覆盖自然语言输入、Scope Review、资源/字幕注册、实时进度、waiting evidence、同 Run Resume、最终计划、质量报告和 Settings/Runtime Health。截图不得包含密钥、数据库密码、字幕正文、Prompt、堆栈或用户私人路径。
