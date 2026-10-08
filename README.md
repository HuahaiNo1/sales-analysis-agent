# Contoso 销售数据分析助手

面向销售运营与业务分析人员、单组织少量用户、本地运行的销售分析应用：Vue 3 + TypeScript + FastAPI + PostgreSQL + 真正的 DeepAgents 主 Agent / 唯一 analysis 子 Agent。

[最终交付规格](docs/final-delivery-spec.md) 是范围与验收合同；[当前状态](STATUS.md) 区分实现、实测和待验；[本地交接与人工验收](docs/final-handoff.md) 给出升级、使用和后续本地 Codex 交接步骤。2026-10-08 最新本地验收报告的实际被测版本为 `3350a9a8c228c8f760d103c54d66441accc0c4b4`；报告结束前重新 fetch，远端未变。本次提交仅同步文档，没有重跑应用测试、调用模型或部署；新文档提交本身不冒充重新测试的代码基线。 两组原失败配置的真实模型复盘及新报告闭环已通过，桌面版可交用户人工验收；用户尚未明确接受，部署须待验收后另行讨论。

只分析 SQLBI Contoso V2 模拟订单数据。默认完全离线的 mock 模式仍执行真实 Agent 图、受控工具、PostgreSQL 查询和分析子 Agent；只把外部语言模型换成本地确定性演练模型。mock 不是通用自然语言模型，建议从页面示例开始。

## 已有功能

- 五个固定指标：折扣后销售额、去重订单数、销售数量、平均订单金额、商品毛利
- 按月/类别/商品/门店/客户国家/门店国家聚合，明确期间比较与贡献拆解
- KPI、折线/柱形/瀑布图、表格、安全聚合 CSV
- 会话追问、刷新恢复、取消、请求幂等、清晰失败/澄清状态
- 纯展示聊天追问与图表按钮复用同一冻结结果，未重新聚合或调用模型；过期/权限/版本变化不复用
- 固定销售复盘：单一可加总指标的整体、类别、门店及可选一次类别→商品下钻，最多 4 个 QuerySpec
- 独立报告库：显式保存、列表、重开、编辑标题/人工备注/建议、修订冲突提示、回收站与恢复
- 报告事实与证据冻结持久化，不随聊天结果 24 小时 TTL 过期；重开和 Markdown / 自包含 HTML 导出均无模型调用
- 两个演示账号展示全门店 / 受限门店隔离；报告逐次验证本人归属和当前范围是否完整覆盖原快照
- 固定 QuerySpec 与 SQL 模板，PostgreSQL 只读角色和 RLS；模型不做算术
- DeepSeek 全主/子调用共享的持久化人民币 4.8 元保守预算上限

本轮只用 Contoso。不建设任意数据导入、通用管理后台、额外 Agent、预测/因果推断、定时报表、公开分享或部署平台；不支持退款、付款事件、企业净利润、推断渠道、自由 SQL、任意代码执行或向量数据库。

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

Windows 使用已有的官方 PostgreSQL 17 安装和 Python 启动器，不运行 Debian 安装脚本。完整前置工具、固定 .NET SDK、生成和验证命令见 [Windows 指南](docs/windows.md)。最新 2026-10-08 本地报告记录 `3350a9a` 的 Windows 升级/启动/重启、后端 143 项、前端 59 项与构建、两组 DeepSeek 复盘、新 live 报告闭环及 7 组/51 行/153 个金额字段的独立 SQL 核对通过。E2E 首轮 2 通过、1 手机宽度失败；同代码同测试复跑 3 通过。证据来自用户提供的报告，不等于用户最终人工接受。

```powershell
uv sync --frozen --python 3.12
npm --prefix frontend ci
# 没有 data/processed 时，先按 Windows 指南安装官方 .NET SDK 8.0.425
.\.venv\Scripts\python.exe scripts\contoso_generate.py

# 本轮本地验收保持 mock；不需要模型密钥
$env:AGENT_MODE = 'mock'
.\.venv\Scripts\python.exe scripts\dev_windows.py
```

已有生成数据时跳过生成命令。脚本只使用本机独立开发数据库；启动会重复安全地应用 scripts/schema.sql 的新增字段/报告表，再复用已有销售数据。不会通过删库迁移，也不会重置独立预算账本。不安装 PostgreSQL、不改系统服务或防火墙。升级前的备份与恢复注意事项见 [本地交接](docs/final-handoff.md)。

### 从官方源重新生成数据（已有数据无需执行）

```bash
bash scripts/contoso_generate.sh
```

首次需下载官方 .NET 8 和生成输入资源；缓存约 1 GB。固定配置与源提交写在 data/provenance，标准化与门禁校验由 scripts/contoso_normalize.py 执行。程序启动不会重新随机生成数据。

## 查询、复盘与报告

1. 登录后先核对模拟数据、mock/live 和当前门店范围；日常查询可从页面示例开始
2. 在“销售复盘”选择本期、对比期和一个指标：销售额、销量或商品毛利；按需选择门店和一次商品下钻
3. 表单结束日期包含当日，发送 API 时转换成左闭右开区间。查看事实、规则、证据明细、局限和停止原因；规则命中不是因果结论
4. 点击“保存报告”，填写标题及可选的人工备注/建议；保存前源查询仍须有效
5. 在“报告”重开、编辑批注、导出 Markdown/HTML，或放入回收站后恢复。事实与证据不能编辑；重新计算会产生新运行和新报告

报告持久化在本机 PostgreSQL。当前权限必须覆盖整份原快照，否则列表隐藏且读取/导出拒绝，不会删掉部分行继续展示旧总额。已下载文件不能由应用追溯撤回。持久化不等于自动备份或云同步。

## 真实模型与原预算

日常开发与演示仍默认 mock。最新本地报告已在 `3350a9a` 原固定 DeepSeek 配置上完成 Q3 vs Q2（开启下钻、4 个 QuerySpec）及 9 月 vs 8 月（关闭下钻、3 个 QuerySpec），均 succeeded，并保存新 live 报告、导出及独立 SQL 对账。原两组配置的验收阻断已解除；不能据此声称任意自然语言、所有模型输出或供应商异常诊断分支都已稳定。详见 [最终验证记录](docs/final-verification.md)；[旧 live 记录](docs/live-api-validation.md) 仅保留作历史。

最新本地报告记录：本轮新增 16 次调用、估算 ¥0.182672；原本地账本累计 56 次有效结算、¥0.481842，原额度 ¥2.648488 不变，pending=0、blocked=false。加上历史云端占用 ¥2.151512（含未知预留 ¥2.113536），跨环境保守占用为 **¥2.633354**，不是供应商实际账单。用户总限额 ¥5、应用保守门禁 ¥4.8 不变；未知预留不得释放或清零。单次仍须完整预留 ¥2.113536，本地剩余额度 ¥2.166646 不等于可持续调用余额，可用调用余量已很紧。用户本地保留的原账本是本地活动的权威记录，云端缺少的原凭据/账本及测试空账本均不能替代；任何未来 live 调用须先按最新活动对账并核对价格与授权，不得换目录或环境重置预算。

`runtime/llm-budget.sqlite3` 是主/子调用共享的持久账本。按现有价格基线，每次调用先原子预留 2.113536 元，只有成功且 usage 严格合法才结算并释放差额；错误、超时或缺 usage 保留整额。页面的估值、预留与占用都不是供应商实际账单。任何后续 live 前还须重新核对官方价格，详见 [Agent 运行与预算](docs/agent-runtime.md)。

确需恢复真实模型时，由用户手动复制 `.env.example` 为 `.env` 并填写密钥，不在聊天或 Git 中提交。确认后显式设 `AGENT_MODE=live` 并重启；当前 PowerShell 若已设置 mock，也需由用户显式调整该会话变量。固定使用官方 DeepSeek API 的 `deepseek-flash`，关闭 thinking 和 LangSmith，输出上限 2048、自动重试 0。Linux 私有 `.env` 使用 0600 权限，Windows 限当前用户可访问。源码包不含凭据或原账本，恢复时须保留同次开发的累计占用。

## 验证

```bash
bash scripts/test.sh                 # PostgreSQL + 关键后端/Agent回归；强制mock
npm --prefix frontend run build      # TypeScript + production build
npm --prefix frontend test           # 前端关键格式/图表单测
# 首次 E2E 前：在 frontend 目录执行 npx playwright install chromium
bash scripts/dev.sh --test-e2e       # 同生命周期启动全栈 + 本地Chromium浏览器测试；强制mock
```

自动检查覆盖五指标、只读/RLS、贡献对账、预算、有限复盘、报告持久化/权限/修订与导出安全。基线云端自动结果、一次 30 分钟 mock 观察、用户 2026-10-08 本地报告及当前修复的验证边界见 [最终验证记录](docs/final-verification.md)。不同版本和来源的证据分别记录。

Playwright 共 **3 条**场景：原工作台、延迟新会话/快速动作、复盘→报告闭环。最新 `3350a9a` 本地报告首轮 2 通过、1 手机宽度断言失败；无代码或测试修改的复跑 3 通过、0 失败、0 跳过。2026-10-08 用户明确暂缓手机端页面；手机宽度 E2E 偶发失败保留为已知问题，不是当前桌面交付阻断，不删除/跳过测试或隐藏首轮失败。重复展示总体金额为 P3 后置优化，同样不阻断本次交付。 云端曾有 Chromium 启动权限阻塞，不与本地报告混同；本次文档同步未执行浏览器验证。

Playwright 默认使用与锁文件匹配的 Chromium。已有浏览器时可显式设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`；不默认使用 Linux 特定路径。Windows 的 E2E 命令为 `.\.venv\Scripts\python.exe scripts\dev_windows.py --test-e2e`。测试入口强制 mock 并使用临时测试账本，不重置原账本。

## 代码地图

- backend/app/schemas.py：受控 QuerySpec
- backend/app/query.py：固定 SQL、Decimal 汇总、比较/贡献与图表数据
- backend/app/agent.py：主 Agent、唯一子 Agent、工具能力检查与 mock/live 模型
- backend/app/budget.py：共享预算账本
- backend/app/main.py：认证、会话、运行、结果与 CSV
- backend/app/review.py：固定复盘计划、规则和证据
- backend/app/reports.py / report_export.py：独立报告、当前授权、修订和安全导出
- frontend/src：Vue 工作台
- scripts/：官方数据、PostgreSQL、导入、统一启动与测试
- [API 合同](docs/api.md)

演示登录仅供 localhost 本地使用，不是生产身份系统。2026-10-08 起后续开发和测试全部交由用户的本地 Codex 助手负责；本次仅完成获授权的仓库文档同步，原助手不再自行推进开发或测试。当前未部署或购买服务，也不承诺服务永久在线。先由用户人工接受，再另行决定是否部署及采用何种方式。

