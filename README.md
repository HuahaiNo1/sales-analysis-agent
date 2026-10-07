# Contoso 销售数据分析助手

一个适合 AI Agent 实习作品集的小型完整项目：Vue 3 + TypeScript + FastAPI + PostgreSQL + 真正的 DeepAgents 主 Agent / 唯一分析子 Agent。

只分析 SQLBI Contoso V2 模拟订单数据。默认完全离线的 mock 模式仍执行真实 Agent 图、受控工具、PostgreSQL 查询和分析子 Agent；只把外部语言模型换成本地确定性演练模型。mock 不是通用自然语言模型，建议从页面示例开始。

## 已有功能

- 五个固定指标：折扣后销售额、去重订单数、销售数量、平均订单金额、商品毛利
- 按月/类别/商品/门店/客户国家/门店国家聚合，明确期间比较与贡献拆解
- KPI、折线/柱形/瀑布图、表格、安全聚合 CSV
- 会话追问、刷新恢复、取消、请求幂等、清晰失败/澄清状态
- 纯展示聊天追问与图表按钮复用同一冻结结果，未重新聚合或调用模型；过期/权限/版本变化不复用
- 两个演示账号展示全门店 / 受限门店的服务端范围隔离
- 固定 QuerySpec 与 SQL 模板，PostgreSQL 只读角色和 RLS；模型不做算术
- DeepSeek 全主/子调用共享的持久化人民币 4.8 元保守预算上限

不支持退款、付款事件、企业净利润、推断渠道、自由 SQL、任意代码执行或向量数据库。

## 数据

官方生成器目标约 100,000 单，实际生成 **97,215 单 / 232,928 订单行**。全部销售日期覆盖 2023-01-01 至 2025-12-31，币种 USD、汇率 1。不是实际经营数据，年份增长与季节性是模拟配置。

事实仅有 `fact_sales`；四个维表为 `dim_product / dim_store / dim_customer_geo / dim_date`。未重复导入 Orders/OrderRows。价格精度 NUMERIC(18,6)，中间计算不提前四舍五入。商品/类别筛选或分组不支持平均订单金额，避免把部分订单金额误称客单价。

完整来源、MIT 许可、生成配置、实际订单数原因及验证结果见 [数据来源说明](docs/data-provenance.md)。处理后 CSV 在当前工作目录的 `data/processed/`（不进入源码 Git，也不包含在本次源码恢复 ZIP 中）；源码包保留官方生成输入配置及许可证，可按下面的脚本重新生成。原始缓存和运行文件不应打包。

## 快速启动（当前 Debian 13 云端开发环境）

需要 Python 3.12、uv，推荐 Node.js 24.19.0（见 `.node-version`）。当前锁定依赖要求 Node `^22.22.2 || ^24.15.0 || >=26.0.0`，不是任意 20+。依赖版本已由 uv.lock 与 frontend/package-lock.json 锁定。

```bash
uv sync --frozen
npm --prefix frontend ci
bash scripts/install_postgres.sh
# 若没有 data/processed，先执行下面的官方数据生成命令
bash scripts/dev.sh
```

- 前端：http://127.0.0.1:5173
- API：http://127.0.0.1:8000（OpenAPI 在 /docs）
- 全门店：`admin / demo123`
- 受限门店：`analyst / demo123`

这些是本地 URL，并非公开部署。`scripts/dev.sh` 在同一个生命周期中启动本地 PostgreSQL、导入已验证数据、启动 API 与前端；保持此命令运行，Ctrl+C 关闭服务。数据已有时不会重新生成或覆盖。在按命令隔离网络的执行器中，应把依赖进程和测试放在同一启动脚本中，不能假定另一命令可连到前一次的 localhost。

上述 PostgreSQL 安装脚本只向 runtime/ 解包官方 Debian 包，不需要 root，不修改系统数据库。它是本地演示开发方案；演示数据库只监听本机，使用本地 trust 认证。请勿把此认证配置开放到外网。

### Windows 原生路径

Windows 使用已有的官方 PostgreSQL 17 安装和 Python 启动器，不运行 Debian 安装脚本。完整前置工具、固定 .NET SDK、生成和验证命令见 [Windows 指南](docs/windows.md)。当前仅在云端检查过代码和隔离单测，尚未完成 Windows 实机验收。

```powershell
uv sync --frozen --python 3.12
npm --prefix frontend ci
# 没有 data/processed 时，先按 Windows 指南安装官方 .NET SDK 8.0.425
.\.venv\Scripts\python.exe scripts\contoso_generate.py
.\.venv\Scripts\python.exe scripts\dev_windows.py
```

已有生成数据时跳过生成命令。脚本只使用本机独立开发数据库，并保留已有数据和预算账本；不安装 PostgreSQL、不改系统服务或防火墙。

### 从官方源重新生成数据（已有数据无需执行）

```bash
bash scripts/contoso_generate.sh
```

首次需下载官方 .NET 8 和生成输入资源；缓存约 1 GB。固定配置与源提交写在 data/provenance，标准化与门禁校验由 scripts/contoso_normalize.py 执行。程序启动不会重新随机生成数据。

## 手动配置真实模型

默认仍为 mock。当前已完成一次经人工配置与授权的真实 API 验收，详情见 [真实 API 验证记录](docs/live-api-validation.md)；它不等于浏览器验收。需要在恢复环境启用真实模型时，由你手动复制 `.env.example` 为 `.env` 并填写 `DEEPSEEK_API_KEY`；不要在聊天或 Git 中粘贴密钥。`.env` 应设为 0600，所有 `.env*` 除示例外已忽略。

准备好并确认启用后，手动将 `AGENT_MODE=live`，重启服务。程序固定调用官方 `https://api.deepseek.com` 的 `deepseek-flash`，`thinking.type=disabled`、`max_tokens=2048`、`max_retries=0`，LangSmith 追踪关闭。若需要让助手执行真实模型验证，应先确认手动配置已完成。

`runtime/llm-budget.sqlite3` 是全调用共享账本。每次调用先按官方完整上下文上限及 2048 输出上限原子预留 2.113536 元；只有成功且 usage 严格合法才按峰价用量结算并释放差额。错误、超时或缺 usage 保留整额，两个未知调用后无法再发起第三次请求。已结算金额加未决预留不得超过 4.8 元，给用户 5 元总限额留余量。界面分别显示已结算估值、未决预留、占用合计及余额，都不是供应商账单。不要删除账本来重置开发预算；未来启用真实调用前应重新核对官方价格。价格基线及具体边界见 [Agent 运行说明](docs/agent-runtime.md)。

当前累计占用 2.151512 元（6 个成功调用的峰价估值 0.037976 元，加 1 个未知调用预留 2.113536 元），门禁剩余 2.648488 元；不是供应商账单。源码包不带私有运行账本。若在新环境继续同一次开发预算，必须人工带入已占用金额或受控迁移原账本，不得因新机器创建空账本就重置预算。

一次性手动配置辅助脚本 `scripts/manual_env_setup.py` 仅用于有正式本机浏览器访问路径时的人工作业；它不会自动启用 live。某些云浏览器会禁止本地页面，禁止时直接保留 mock，不绕过限制。

## 验证

```bash
bash scripts/test.sh                 # PostgreSQL + 关键后端/Agent回归；强制mock
npm --prefix frontend run build      # TypeScript + production build
npm --prefix frontend test           # 前端关键格式/图表单测
# 首次 E2E 前：在 frontend 目录执行 npx playwright install chromium
bash scripts/dev.sh --test-e2e       # 同生命周期启动全栈 + 本地Chromium浏览器测试；强制mock
```

测试采用小而针对性的集合：五指标手算夹具、只读/RLS、跨账号结果隔离、AOV 禁用范围、比较/贡献对账、CSV 安全、真实 DeepAgents 工具边界、预算并发上限与失败保守记账、主要 UI 交互。已有一条真实模型 API 场景通过，其他问法不作扩大推断；浏览器 E2E 仍被环境阻塞，不能把 mock 或 API 成功当成页面验收通过。

Playwright 默认使用自己安装的对应版本 Chromium。已有 Linux Chromium 可显式设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium`；不再把该 Linux 路径作为所有平台的默认值。2026-10-07 修复和验证范围见 [本轮修复记录](docs/acceptance-fixes-2026-10-07.md)。

## 代码地图

- backend/app/schemas.py：受控 QuerySpec
- backend/app/query.py：固定 SQL、Decimal 汇总、比较/贡献与图表数据
- backend/app/agent.py：主 Agent、唯一子 Agent、工具能力检查与 mock/live 模型
- backend/app/budget.py：共享预算账本
- backend/app/main.py：认证、会话、运行、结果与 CSV
- frontend/src：Vue 工作台
- scripts/：官方数据、PostgreSQL、导入、统一启动与测试
- [API 合同](docs/api.md)

演示登录仅供 localhost 本地演示，不是生产身份系统；本仓库仅发布源码，项目没有部署、创建 PR 或购买服务；已执行一次经授权的真实模型 API 验收。公开部署前需自行审查认证、HTTPS、持久化部署及数据包分发方案。
