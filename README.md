# Planix

简体中文 | [English](README.en.md)

Planix 是一个本地优先的 AI 规划工作台，把模糊目标转换为经过证据、独立审查和用户批准的可执行计划。

它不是单轮 Prompt 演示。Planix 使用持久化 Agent Runtime 管理规划阶段、Artifact 版本、失败恢复和审批状态，并在任何 Calendar 变更前保留最终决定权给用户。

## Planix 是什么

Planix 面向需要长期推进真实目标的个人用户。它将目标澄清、现实约束、资料证据、策略选择、任务设计、独立审查和日历执行放在同一个可恢复工作区中。

典型场景包括：

- 学习新技能、准备考试或制定训练计划。
- 求职、项目交付、作品集和长期职业目标。
- 旅行、活动或其他受时间与资源约束的复杂计划。
- 使用本地笔记、资料、历史计划和日历上下文辅助决策。

默认入口是 **P Mode**。Dashboard、Calendar、Notes、Goals、Materials、Settings 和 Advanced Debug Trace 提供执行、管理和审计能力。

## 核心能力

- **认知规划**：Goal、Reality、Evidence、Strategy、Execution 和 Critic 由独立阶段负责。
- **持续会话**：保存目标、已知事实、重要未知项、Artifact 和等待状态，可在退出后恢复。
- **单页双通道**：P Mode 可让两个独立 Thread 在后台同时规划；同一 Thread 始终串行，限流时自动降为单通道。
- **本地资料 RAG**：对 TXT 和 Markdown 资料使用 SQLite FTS5/BM25 风格检索。
- **安全日历执行**：创建、更新和删除 Calendar 内容都需要显式用户确认。
- **长期用户模型**：自动提炼的长期规律必须经过独立 Memory Evaluation 才能保存。
- **多模型路由**：支持按任务类型选择主模型、备用模型和自定义兼容端点。
- **可观测运行时**：保存调度、Agent 调用、Artifact 变化、模型路由、恢复和策略决策。
- **桌面交付**：React/Vite 前端、FastAPI sidecar、SQLite 和 Tauri 2 组成 Windows 优先的桌面应用。

## 认知规划流程

```text
用户目标
  -> Goal Understanding
  -> Goal Intelligence
  -> Goal Completion
  -> Reality Assessment
  -> Evidence Synthesis
  -> Strategy Proposal
  -> 用户确认 Strategy
  -> Execution Blueprint
  -> Independent Critic
  -> 用户确认 Execution
  -> Calendar Preview
  -> 用户确认 Calendar
```

Goal Completion 只阻塞仍会显著改变方案的信息。普通缺失信息可以通过“跳过这一步”转为最佳努力假设；目标冲突、安全问题和硬可行性风险不能跳过。跳过目标补充不会批准 Strategy、Execution 或 Calendar。

Critic 可以把问题定向返回责任阶段修复。每个新的 Execution 版本都必须获得同版本的独立 Critique，修复后的旧审批自动失效。

Critic 还受确定性语义策略约束：半时间演练不能凭空变成用户的硬性周额度，缺少已验证 `sourceRef` 也不能强制要求 URL 或服务商。违反这些规则的审查会针对同一 Execution 自动复审一次；再次违规则失败关闭并停在 Critic，不消耗 Execution 修复轮次。

Execution 首版默认通过一次结构化模型调用生成包含 Narrative 的完整 Blueprint；Critic 修复则使用 Narrative → Blueprint 双调用并携带累计审查历史，避免修复振荡。截断或无效结构化输出也可以回退到双调用；候选在持久化前还必须通过依赖顺序、预算和可计算工时的确定性预检，之后才会交给独立 Critic。

## Agent Runtime Harness

`Backend/backend/app/harness` 是 Cognitive Agents 的控制面。LangGraph 执行调度结果，但不决定产品策略。

| 组件 | 职责 |
| --- | --- |
| Scheduler | 决定调用哪个 Agent、继续、等待、修复、恢复或停止 |
| Agent Contracts | 声明输入/输出 Artifact、职责、权限、失败条件和重试边界 |
| Artifact Store | 持久化类型化 Artifact，并维护 ID、版本和当前引用 |
| Policy Engine | 统一判断规划推进、用户等待、审批、Calendar 和 Memory 权限 |
| Recovery Manager | 提供模型切换、有限重试、JSON 语法修复、checkpoint resume 和只读降级 |
| Approval Controller | 将 Strategy、Execution 和 Calendar 审批绑定到精确 Artifact 版本 |
| Critic Controller | 保证 Execution 与独立 Critique 一一对应 |
| Memory Controller | 独立评估候选长期规律，并以 fail-closed 策略决定是否保存 |
| Observability | 持久化 Harness decision、Agent invocation、Artifact change 和 recovery event |

SQLite checkpoint 保存当前阶段、已完成和待执行 Agent、Artifact 版本、审批、错误及等待状态。进程恢复后从确切的 pending Agent 继续，而不是重放整条流水线。

文件数据库使用 WAL 和 5 秒 `busy_timeout`，在进程内只执行一次完整 Schema 初始化，并用相邻锁文件串行化多进程初始化，以支持两个独立规划 Session 的并发读写和 Uvicorn reload。

## 安全边界

1. **不伪造计划**：模型不可用或结构化输出不合法时，Planix 保存已完成状态并阻塞在失败阶段。
2. **独立审查**：任何 Execution 版本在获批前都必须通过对应版本的 Critic。
3. **版本化审批**：上游 Artifact 或方案变化会使受影响的旧审批失效。
4. **Calendar 必须确认**：创建、修改和删除在首次数据库写入前都会重新校验并等待用户批准。
5. **Memory 默认拒绝**：原始聊天不会自动成为长期规律；自动记忆必须有来源 Artifact、独立评估和 Policy 许可。
6. **关键风险不可跳过**：目标矛盾、安全和硬可行性问题必须由用户解决。
7. **敏感信息最小暴露**：Provider Key 保存在本地设置中，不由公共设置接口回传，日志和 Harness 事件会进行脱敏。

## 技术栈

| 层 | 技术 |
| --- | --- |
| Web | React 18、TypeScript 5.7、Vite 6 |
| Desktop | Tauri 2、Rust、WebView2 |
| Backend | Python 3.11、FastAPI、Pydantic 2 |
| Agent Runtime | LangGraph、Planix Harness、NDJSON streaming |
| Storage | SQLite、FTS5、local files |
| Testing | Pytest、Vitest、Testing Library、ESLint、TypeScript |
| Packaging | PyInstaller sidecar、NSIS、MSI |

## 项目结构

```text
Planix/
├─ apps/
│  ├─ web/                 React/Vite 用户界面
│  └─ desktop/             Tauri 桌面壳
├─ backend/
│  ├─ app/
│  │  ├─ cognitive_planning/  认知 Agent 与 LangGraph 执行图
│  │  ├─ harness/             调度、策略、恢复、审批和审计
│  │  ├─ routers/             FastAPI 路由
│  │  └─ services/            兼容服务与产品能力
│  └─ tests/               后端与规划验收测试
├─ docs/                   深层设计与验收文档
├─ scripts/                开发、验证和打包脚本
└─ data/                   本地开发数据库
```

## 快速开始

### 前置要求

- Python 3.11
- Node.js 20 与 npm
- Windows PowerShell
- 桌面开发额外需要 Rust stable、Microsoft C++ Build Tools 和 WebView2

### 获取代码

```powershell
git clone https://github.com/ab2956955606-cmyk/Planix.git
cd Planix
```

### 启动后端

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
cd Backend
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --env-file ..\.env --port 8003
```

API 默认运行在 `http://127.0.0.1:8003`。可通过以下命令检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8003/api/health
```

### 启动 Web

打开另一个 PowerShell：

```powershell
cd Frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5176`。Vite 会把本地 `/api` 请求代理到 FastAPI。

### 启动桌面开发环境

先从仓库根目录启动后端和 Web 服务：

```powershell
.\scripts\dev-desktop.ps1
```

再打开另一个 PowerShell：

```powershell
cd apps\desktop
npm ci
npm run dev
```

## 配置模型

`.env.example` 默认使用 `mock`，可在没有外部 Key 时启动本地开发环境。真实模型能力需要配置 Provider，或在 Settings 中保存对应 Provider 的设置。

| Provider | `AI_PROVIDER` | Key 环境变量 |
| --- | --- | --- |
| Mock | `mock` | 不需要 |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Kimi | `kimi` | `MOONSHOT_API_KEY` |
| 智谱 GLM | `zhipu_glm` | `ZHIPU_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| OpenAI-compatible | `custom` | `AI_API_KEY`，并配置 `AI_API_BASE` 与 `AI_MODEL` |

Settings 支持按任务类型设置主 Provider 和 fallback。Cognitive Planning 的正式 Artifact 始终依赖有效的模型结构化输出；本地模板不会伪装成模型生成结果。

## 数据与隐私

- 按上述快速启动加载 `.env.example` 时使用 `data/mynotes.db`；未设置 `DATABASE_URL` 时，后端代码默认使用 `data/planix.db`。
- 桌面应用默认使用 `%APPDATA%\Planix\planix.db`。
- `DATABASE_URL` 或 `PLANIX_DB_PATH` 可以覆盖数据库位置。
- 计划、资料、Memory、Harness checkpoint 和审计事件默认保存在本地 SQLite。
- 外部模型调用受所选 Provider 的网络和数据政策约束。

## 验证

### Backend

```powershell
cd Backend
..\.venv\Scripts\python.exe -m compileall backend
..\.venv\Scripts\python.exe -m pytest backend\tests
```

### Web

```powershell
cd Frontend
npx.cmd tsc -b
npm.cmd run lint
npm.cmd run test
npm.cmd run build
```

### Desktop

```powershell
.\scripts\check-desktop-config.ps1
.\scripts\check-packaging-toolchain.ps1
```

真实模型验收必须先在 `/command` 页面创建并推进 Thread；审计脚本只读取回放，不代替页面发送消息：

```powershell
python scripts/live_planning_e2e.py --print-source-fingerprint
python scripts/live_planning_e2e.py --audit-manifest data/e2e-manifest.json --required-provider deepseek
```

Manifest 使用 `{"sourceFingerprint":"<冻结前输出>","threads":{"travel":"..."}}` 结构。Schema v3 只有在 GET-only 回放、源码指纹未变化、旅游 canary 先于其余九轮完成且每轮最终 Critic 得分至少为 90 时才允许 `fullAcceptancePassed=true`；报告将浏览器请求区间与人工等待时间分开，按阶段汇总真实模型请求与耗时，并记录双通道利用率、限流、截断、自动修复和 Execution 生成模式，`skipped` 路由项不计作真实请求。

GitHub Actions 在 Ubuntu runner 上使用 Python 3.11 和 Node.js 20 验证 Backend 与 Web，并在 Windows runner 上执行桌面配置检查。打包工具链检查保留为本地发布前验证。

## 进一步文档

- [Cognitive Planning Kernel 验收边界](docs/cognitive-planning-acceptance.md)
- [Cognitive Planning OS 行为与黄金场景](docs/cognitive-os-acceptance.md)
- [Legacy Planning Template 清单](docs/planning-template-inventory.md)

## 当前边界

- 打包后的桌面应用以 Windows 为主要目标；Web 开发模式可独立运行。
- 根目录 Dockerfile 只构建 FastAPI API，不是完整的 Web 或桌面镜像。
- Planix 采用单用户、本地优先的数据模型，不是多租户云服务。
- P Mode 默认最多并发两个独立 Thread；DeepSeek 返回限流后，本页面会降为单通道。
- 真实规划质量和可用性取决于所选模型、API 配额与网络状态。
