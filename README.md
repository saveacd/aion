<p align="center">
  <img src="assets/aion-icon.png" alt="AION Logo" width="220">
</p>

# AION｜加密合约AI量化交易系统
#系统架构以及设计理念超越同赛道项目90%
> **当前项目版本：v2.12**  
> AION（Autonomous Intelligence & Optimization Network）是一个以 AI 自主量化决策为主线、以持续因子研究和策略进化为学习层、以确定性量化验证和独立风险引擎为约束的AI 自主决策、因子/策略持续研究与 PAPER 模拟交易系统。项目包含 Python/FastAPI 后端、Web 控制台、原生 Kotlin/Jetpack Compose Android 客户端，以及 Docker Compose 部署配置。

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.165-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Kotlin](https://img.shields.io/badge/Kotlin-Jetpack%20Compose-7F52FF?logo=kotlin&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-blue)

**默认运行模式为 PAPER 模拟盘。** 研究模块不会直接创建交易所订单；任何真实交易仍受原有交易权限、独立风控和主网保护开关约束。本文档区分已实现能力与尚未完成的部分，不将规划功能描述为现成功能。

---

## 1. 项目定位

AION 希望解决一个常见问题：让不同的 AI 分工负责交易想法、判断想法是否正确并决定是否执行，容易形成缺少独立验证的决策链。

因此，AION 将系统拆分为三个相互制约的部分：

1. **多智能体研究与决策**：从不同专业角度分析市场、提出策略假设并相互审查。
2. **确定性量化验证与策略研究**：用可复现的量化程序评估因子和策略，而不是让语言模型自行宣布研究成功。
3. **独立风险与执行边界**：在交易执行路径上检查风险限制；研究模块与模拟账本不拥有绕过风控的权限。

AION 是软件工程和量化研究项目，不保证收益，也不构成投资建议。

## 2. 核心能力

AION 的运行主线是“行情感知 → AI 分析与决策 → 独立风控 → PAPER 执行与持仓管理 → 结果复盘 → 因子/策略研究进化 → 下一轮决策”。Autopilot 负责交易巡航，研究 worker 负责持续研究；两者协作但权限隔离。

### 2.1 六个 AI 智能体协作

| 智能体 | 主要职责 |
|---|---|
| `market_regime` | 识别趋势、震荡、高波动等市场环境 |
| `technical_analysis` | 分析技术结构、价格行为、量价与关键价位 |
| `derivatives` | 分析资金费率、持仓量、多空比等衍生品信息 |
| `strategy_research` | 结合研究结果提出因子假设与策略候选 |
| `chief_decision` | 汇总证据并形成交易意图 |
| `adversarial_review` | 反向检查证据、逻辑矛盾和潜在风险 |

对抗审查负责提出否决意见；最终执行还必须通过独立风险引擎，AI 结论不能覆盖硬性风控规则。

### 2.2 因子研究与验证

- 研究员从受控的公式目录提出候选因子，不执行模型生成的任意 Python、SQL 或 Shell 代码。
- 量化评估流程包含 IC、Rank IC、ICIR、命中率、覆盖率及分段稳定性等指标。
- 因子研究任务可持久化，支持任务状态、过程日志、幂等请求和失败重试。
- 因子生命周期包含候选、验证、观察、启用及淘汰等状态；状态变化和历史版本保留在数据库中。
- 因子组合可结合市场状态、历史相关性和方向冲突信息调整信号权重。
- ACTIVE 因子信号使用已收盘 K 线计算，并作为决策分析证据；不会直接绕过风险引擎下单。

**研究边界：** 当前公式能力以白名单公式为基础，不等同于任意表达式自动发现。动态权重属于受约束的研究机制，仍需持续进行滚动样本外评估。

### 2.3 策略研究与 PAPER 观察

- 系统可生成趋势确认、区间反转和高波动防御等研究型策略候选。
- 候选策略可执行样本内、样本外及 Walk-forward 评估，并考虑手续费、滑点、资金费率数据、成交延迟和量能参与率等约束。
- 研究通过门槛的候选可进入独立 PAPER 会话，使用各自的虚拟账本记录模拟交易。
- PAPER 观察器基于已收盘 K 线推进模拟会话，并记录权益、盈亏和回撤等指标。
- Web 研究实验室可查看近期 PAPER 会话和模拟交易明细；Android 研究实验室可查看研究任务、候选与 PAPER 会话。
- 策略版本支持审批记录和已审批版本回滚；审批操作不等于允许绕过交易风控。

**重要限制：** 当前 PAPER 成交是基于 K 线、量能参与率、手续费和滑点的估算，不是订单簿排队撮合，也不代表真实交易所成交。策略自动淘汰和比较能力仍有进一步完善空间。

### 2.4 AI 自动驾驶与交易闭环（原 v2.11 主链路保留）

v2.11 已有的 Autopilot 后台巡航模块在 v2.12 中继续保留，入口为 `backend/aion/services/autopilot.py`，配置 API 为 `/api/v1/autopilot`。它与自主研究实验室是两条协作但权限隔离的工作流：

1. **持续巡航**：后台 worker 周期性读取已启用的用户配置，按交易对运行六智能体分析；默认巡航周期为 600 秒，worker tick 默认 10 秒。
2. **因子参与决策**：分析上下文读取该用户、交易对和周期下的 ACTIVE 因子，以已收盘 K 线计算实时因子信号，并将因子组合、市场状态、策略研究候选和历史交易学习上下文提供给 AI。
3. **形成交易意图**：六智能体依次完成市场状态、技术分析、衍生品分析、策略研究、首席决策和对抗审查；对抗审查否决或模型链路失败时，结果降级为不交易。
4. **独立风控**：AI 只提出交易意图，订单必须经过确定性风控引擎；风险拒绝、急停、暂停、陈旧行情或对账异常不能被 AI 覆盖。
5. **模拟执行与持仓管理**：PAPER 自动执行分支将通过风控的意图提交给模拟 broker；持仓止损、止盈、超时平仓和结果记录由模拟交易/学习服务负责。
6. **反馈进化**：交易结果进入学习上下文；独立研究 worker 持续研究因子、验证生命周期并评估策略候选。更新后的 ACTIVE 因子会在后续 AI 巡航周期进入决策上下文。

**默认配置是关闭自动巡航和自动执行**（`enabled=false`、`auto_execute=false`），账户模式默认为 `PAPER`。因此“代码保留自动驾驶能力”不等于某个账户已经启动 24 小时运行。运行还依赖后端 worker、数据库、Redis、行情源和模型 Provider 正常工作。PAPER 模拟交易是本项目可用于持续验证的安全运行路径。

**职责边界必须保持清楚：** 因子研究 worker 只负责研究和候选评估，不直接创建订单；交易巡航读取已验证因子信号作为 AI 决策证据；策略候选的研究/PAPER 评估不等于自动发布正式策略版本。策略发布审批、独立风险引擎和紧急停止不能被移除。

### 2.5 独立风险控制

交易执行路径设有独立风险引擎，可检查杠杆、名义价值、单笔预期亏损、总敞口、持仓数量、日亏损、最大回撤、连续亏损、保证金率、行情新鲜度、点差和止损要求等条件。

当硬性规则触发、行情过期、对账异常或风险引擎不可用时，系统应拒绝或阻断交易，而不是让 AI 自行放行。

### 2.6 多端应用

- **Web 控制台**：通过后端 API 查看系统信息、因子研究、策略候选和 PAPER 观察数据。
- **Android 客户端**：原生 Kotlin + Jetpack Compose，不使用 WebView 套壳；与后端共用 API。
- **后端服务**：统一提供认证、行情、研究、风险、策略、模拟账本及系统接口。

---

## 3. 系统架构

```text
Android App ───────┐
Web 控制台 ────────┼── FastAPI / REST API / WebSocket
官网静态页面 ──────┘                 │
                                    ├── 六智能体编排与模型网关
                                    ├── Autopilot：六 AI 巡航、交易意图与 PAPER 执行
                                    ├── 因子研究 worker：挖掘、验证与生命周期
                                    ├── 策略研究、回测与 PAPER 观察
                                    ├── 独立风险引擎
                                    └── 交易执行、状态同步与对账
                                             │
                              ┌──────────────┴──────────────┐
                              │                             │
                         PostgreSQL                       Redis
                    业务数据、研究历史、账本          缓存、锁、任务协调
                              │
                         Binance USDⓈ-M
                       行情与受控交易所适配
```

### 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2、Pydantic、Alembic |
| 数据库 | PostgreSQL 16 |
| 缓存与协调 | Redis 7 |
| 量化计算 | NumPy、Pandas |
| 交易所 | Binance USDⓈ-M Futures REST，WebSocket 按配置使用 |
| Web | 原生 HTML、CSS、JavaScript 单页控制台与静态官网 |
| Android | Kotlin、Jetpack Compose、Material 3 |
| 部署 | Docker Compose、Caddy |
| 测试 | pytest、ruff（按环境安装情况执行） |

## 4. 目录结构

```text
aion/
├── backend/
│   ├── aion/
│   │   ├── agents/       # 六个智能体与编排
│   │   ├── api/          # REST API 路由
│   │   ├── core/         # 配置、安全、数据库与 Redis
│   │   ├── exchange/     # 交易所适配
│   │   ├── execution/    # 执行、模拟与对账
│   │   ├── models/       # SQLAlchemy 数据模型
│   │   ├── quant/        # 因子、指标、回测和策略研究
│   │   ├── risk/         # 独立风控引擎
│   │   ├── schemas/      # API 数据结构
│   │   ├── services/     # 行情、研究 worker、PAPER 观察等
│   │   └── web/          # Web 控制台与静态页面
│   ├── alembic/          # 数据库迁移
│   ├── tests/            # 自动化测试
│   └── .env.example      # 环境变量模板
├── android/              # 原生 Android 客户端
├── deploy/               # 部署脚本与 Caddy 配置
├── scripts/              # 数据备份与恢复脚本
├── compose.yaml
└── README.md
```

## 5. 快速部署

### 5.1 Docker Compose 一键部署

运行环境需安装 Docker Engine 和 Docker Compose 插件。

```bash
git clone https://github.com/saveacd/aion.git
cd aion

# 本机访问，默认映射 8080 端口
./deploy/deploy.sh

# 使用自己的域名，由 Caddy 配置 HTTPS
./deploy/deploy.sh trade.example.com
```

部署脚本会准备服务配置、启动数据库和 Redis、执行数据库迁移，并创建管理员账号。请妥善保存脚本生成或配置的管理员凭据。

访问地址（将 `<host>` 替换为服务器地址）：

- Web 控制台：`http://<host>:8080/ui/`
- 官网：`http://<host>:8080/site/`
- API 文档：`http://<host>:8080/docs`
- 健康检查：`http://<host>:8080/health`

域名部署时使用对应的 HTTPS 域名。

### 5.2 本地运行后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，设置密钥、PostgreSQL、Redis 和模型服务配置

alembic upgrade head
python bootstrap_admin.py
uvicorn aion.main:app --reload --host 0.0.0.0 --port 8080
```

需要先准备可访问的 PostgreSQL 和 Redis 实例。生产环境不要使用示例密钥或弱密码。

### 5.3 Android 客户端

使用 Android Studio 打开 `android/` 目录，等待 Gradle Sync 完成后运行。

命令行构建：

```bash
cd android
gradle assembleDebug
```

环境要求：JDK 17、Android SDK 35、minSdk 26。项目未内置 Gradle Wrapper；需要在本机安装兼容的 Gradle 或使用 Android Studio。

后端地址在 `android/app/build.gradle.kts` 的 `AION_BASE_URL` 中配置。默认 `10.0.2.2` 仅适用于 Android 模拟器访问开发机服务。真机或部署环境应改为实际 HTTPS API 地址。

## 6. 环境变量与配置

主要配置项如下，通用模板见 [`backend/.env.example`](backend/.env.example)，PAPER 模拟运行模板见 [`backend/.env.paper.example`](backend/.env.paper.example)。

| 变量 | 用途 |
|---|---|
| `AION_SECRET_KEY` | 应用签名密钥，必须设置为随机长密钥 |
| `AION_DATABASE_URL` | PostgreSQL 异步连接串 |
| `AION_REDIS_URL` | Redis 连接串 |
| `AION_TRADING_MODE` | 运行模式，默认 `PAPER` |
| `AION_MAINNET_ENABLED` | 主网总开关，默认关闭 |
| `AION_DEFAULT_SYMBOLS` | 默认研究/行情交易对 |
| `AION_BINANCE_*_API_KEY/SECRET` | 服务端交易所凭据，不得放入 APK 或前端 |
| `AION_ANTHROPIC_API_KEY` 等 | 可选模型供应商密钥；未配置时按代码支持情况降级 |
| `AION_RESEARCH_WORKER_ENABLED` | 是否启动后台研究 worker |
| `AION_RESEARCH_AUTOSCHEDULE_ENABLED` | 是否启用周期性研究调度 |
| `AION_REGISTRATION_ENABLED` | 是否开放用户自助注册 |

后台研究调度依赖 Redis 协调。启用自动研究前，应先确认模型调用预算、服务资源和日志监控配置。

## 7. 数据库迁移与主要 API

### 数据库

数据库结构由 Alembic 管理。升级前请先备份 PostgreSQL，再执行：

```bash
cd backend
alembic upgrade head
alembic heads
```

研究实验室相关数据模型覆盖以下领域（实际表名以迁移文件为准）：

- 因子研究任务与任务日志；
- 因子验证结果、因子版本和生命周期事件；
- 因子组合快照；
- 策略候选与策略回测；
- PAPER 模拟会话与模拟交易记录；
- 策略审批记录。

当前迁移链的最新 revision 为 `ab73c8d2e410`。不要在未备份、未验证的生产数据库上随意执行 downgrade。

### 主要 API 路径

所有业务接口位于 `/api/v1` 下，并遵循项目现有认证与用户隔离机制。常用路径包括：

| 领域 | 路径示例 | 用途 |
|---|---|---|
| 因子研究 | `POST /factors/research/tasks` | 创建后台研究任务 |
| 研究任务 | `GET /factors/research/tasks` | 查询当前用户任务 |
| 任务日志 | `GET /factors/research/tasks/{task_id}/logs` | 查看任务过程记录 |
| 因子验证 | `GET /factors/library/{factor_id}/validations` | 查询验证历史 |
| 因子版本 | `GET /factors/library/{factor_id}/history` | 查询版本与生命周期记录 |
| 策略候选 | `/research-lab/candidates` | 查询或创建研究候选 |
| PAPER 会话 | `/research-lab/paper/sessions` | 查询或创建模拟会话 |
| PAPER 成交 | `/research-lab/paper/sessions/{session_id}/trades` | 查询模拟交易记录 |
| 策略比较 | `GET /research-lab/compare` | 查看候选策略比较摘要 |
| 系统状态 | `/health`、`/ready` | 健康与就绪检查 |

交互式接口说明可在服务启动后访问 `/docs`。具体请求字段和权限以当前 API schema 与路由实现为准。

## 8. 运行与测试

后端基础检查：

```bash
cd backend
python -m compileall -q aion alembic/versions
pytest -q
ruff check aion
```

完整测试需要安装项目开发依赖，并准备测试所需的 PostgreSQL、Redis 等服务。若环境缺少依赖或外部服务，相关集成测试不能视为通过。

Android 构建：

```bash
cd android
gradle assembleDebug
```

部署验收至少应检查 `/health`、`/ready`、数据库迁移、Redis 连接、认证、研究任务创建与恢复、PAPER 账本隔离和交易安全边界。外部交易所环境不属于本 PAPER 运行配置的验收范围。

## 9. 当前实现边界与已知未完成项

AION v2.12 已包含因子研究任务持久化、验证历史和生命周期记录、因子组合诊断、研究型策略候选、PAPER 模拟账本与观察 worker、策略审批记录/回滚，以及 Web 和 Android 研究实验室入口等能力。

以下能力仍需继续开发或在完整运行环境中验收，**不能视为已经完成**：

- 真实订单簿级流动性、排队和成交撮合模型；
- 完整多策略并行回测调度，以及所有交易环境约束的充分校准；
- 可配置审批人组、多人会签和更细粒度 RBAC；
- 基于长期样本外证据的自动策略冠军选择、完整发布/运行监控和自动淘汰闭环；
- Web 与 Android 所有任务书模块的完整页面覆盖和端到端联调；
- PostgreSQL/Redis 集成、Docker 部署、Android Gradle APK 构建和 Binance Testnet 的完整验收；
- 生产级指标、告警、成本统计和长期稳定性验证。

当前 PAPER 结果是研究用途的模拟估算，不代表真实成交表现。未完成独立测试与安全审查前，不应将系统用于真实资金自动交易。

## 10. 安全原则

- 默认使用 `PAPER` 模式。
- 主网开关默认关闭；不得由 AI 自行开启主网交易。
- 交易所 API 密钥只保存在服务端，不提交到仓库，不写入 APK。
- 研究模块不得直接创建交易所订单，也不得绕过统一风险引擎。
- AI 不得自行关闭风控或修改实盘风险限制。
- 策略正式变更必须经过人工审批；保留版本与回滚记录。
- 保留急停和对账异常阻断机制。
- 生产升级前备份数据库，并先在隔离环境完成迁移和回归测试。

## 11. 参与开发

欢迎通过 Issue 反馈问题或提交 Pull Request。涉及风险引擎、交易执行、权限和数据库迁移的修改，应附带测试并说明安全影响。

## 12. 许可证与免责声明

本项目采用 MIT License，详见 [`LICENSE`](LICENSE)。

AION 仅用于软件工程、量化研究和模拟环境学习，不构成投资建议、交易建议或收益承诺。加密资产及合约交易具有极高风险，可能造成重大损失。使用者应自行理解代码、验证运行环境并承担使用责任。

## 13. PAPER 全天候运行配置

项目提供 `backend/.env.paper.example` 作为模拟环境模板。它会启用行情采集、研究 worker 和 Autopilot 后台循环，并保持交易所账户同步、私人账户数据流关闭。该模板不包含交易所 API 凭据。

```bash
cd backend
cp .env.paper.example .env
# 编辑 .env：设置随机 AION_SECRET_KEY，并填写可访问的 PostgreSQL、Redis 地址
# 如需 AI 决策，使用服务端密钥管理方式配置模型供应商密钥
```

启动前确认 PostgreSQL、Redis 和行情服务可用，并完成数据库迁移。后台循环由应用生命周期启动，主要开关如下：

| 配置 | 示例值 | 作用 |
|---|---:|---|
| `AION_AUTOPILOT_ENABLED` | `true` | 启动 Autopilot 后台巡航 worker |
| `AION_AUTOPILOT_TICK_SECONDS` | `10` | worker 检查用户配置的间隔 |
| `AION_RESEARCH_WORKER_ENABLED` | `true` | 启动因子/策略研究与 PAPER 观察 worker |
| `AION_INGEST_ENABLED` | `true` | 启动行情采集 |
| `AION_INGEST_INTERVAL_SECONDS` | `60` | 行情采集间隔 |

**全局 worker 启动不等于某个用户已启用自动驾驶。** 每个用户还必须在 Autopilot 配置中启用 `enabled`；若要让系统在 PAPER 模式自动执行模拟交易，还需明确启用 `auto_execute`。配置接口为 `GET/PUT /api/v1/autopilot`。PAPER 自动执行的请求示例：

```json
{
  "enabled": true,
  "auto_execute": true,
  "confirm_auto_execute": true,
  "execution_mode": "PAPER"
}
```

以上仅用于 PAPER 虚拟账本。当前版本的自动驾驶持续运行依赖宿主机进程、PostgreSQL、Redis、行情源和模型服务；建议使用进程监管与健康检查，并验证重启恢复、重复任务防护、异常告警和长时间运行表现。**配置为 24 小时运行不等于已经通过 24 小时稳定性验收。**

## 14. 文档维护

根目录 README 是项目定位、架构、部署、配置和验收状态的主文档。功能说明直接维护在 README 或对应模块文档中；临时审计记录、阶段性变更日志不长期堆放在仓库根目录。Android 专项说明保留在 `android/README.md`。
