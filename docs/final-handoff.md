# 本地交接与人工验收

适用：2026-10-07 的销售分析最终候选；候选源码与已交付 ZIP 逐字节核对后，经用户授权随本提交发布到原 GitHub 仓库 main；发布状态文案和清单作对应更新。仍未部署，也未由用户验收。

先在本地完成下面的少量操作，再决定是否接受。部署方式、长期主机、域名和公网访问，均在用户本地验收之后另行讨论，不是启动本候选的前提。

## 1. 交接边界与证据

- [最终交付规格](final-delivery-spec.md) 是范围合同；[STATUS](../STATUS.md) 和 [最终验证记录](final-verification.md) 是当前实际证据。后续定向复验或稳定性观察的结果只以这两份记录更新后的内容为准，本页不复制易过时的测试数量。
- 已有自动与真实 HTTP/PostgreSQL 证据覆盖复盘、报告、权限、修订、源结果到期和 API 重启；它们不等于当前候选的 Windows 实机、浏览器或人工验收。
- 用户反馈旧版 Windows/E2E 已通过；新增复盘和报告仍需本候选的本地检查。云端 Chromium 有已知启动权限阻塞，不能把场景发现、jsdom 或构建成功写成浏览器通过。
- 本页全部演示与验收保持 mock，不需要模型密钥，不新增付费调用。少量 live 验证仍受原预算对账与用户手动配置约束，不默认通过。
- 固定数据是 SQLBI Contoso 模拟订单，97,215 单、232,928 行，2023–2025、USD/汇率 1。商品毛利不是企业净利润，变化贡献不是因果推断。

## 2. 已有本地版本怎么升级

### 2.1 先保留现场

1. 记下当前项目路径、已保存报告的标题/ID、使用的 PostgreSQL 17 路径以及是否自定义了 `LLM_BUDGET_PATH`。不要打印或发送密钥。
2. 关闭项目启动终端中的服务，按第 5 节确认项目 PostgreSQL 已停，再做私有备份。保存重要报告的 Markdown/HTML 便于离线查看。
3. 若本地是 Git 工作区，先查看 `git status --short`，逐项保留本地修改和未跟踪文件；不能用 `reset --hard`、`clean` 或覆盖解压替代合并。
4. 将候选源码解压到旁边的新目录，对比后合并回原项目。用户改过的文件需要保留其改动，冲突逐项处理。没有得到实际远端提交证明时，不把 `git pull` 当作交付入口。

优先保持原项目路径和原运行数据目录。候选更新的主要内容是 `backend/`、`frontend/`、`scripts/`、锁文件、公开配置和文档。不要把包内模板覆盖成用户 `.env`，不要删除原 `runtime/`、原 `data/processed/` 或独立预算账本。不要从云端复制 `.venv`、`node_modules`、Linux `pgdata` 到 Windows。

源码包与冻结测试数据包用途不同：源码包提供程序、锁文件、脚本及公开来源配置/工作簿/许可证；如随交付另附测试数据包，它提供本候选验证使用的冻结 CSV 与 manifest，供缺少数据的本地环境使用，不能覆盖已有数据，亦不包含用户报告数据库或真实预算。实际文件名和附件身份以最终交付清单为准。

源码交付不包含生成交易 CSV、原始缓存、数据库、预算账本、依赖目录、凭据或 Git 元数据。已有 `data/processed/manifest.json` 和对应 CSV 必须一同保留；导入器即使发现数据库已有数据，也会先校验这些文件。不要只搬数据库而漏掉启动必需的已验证输入。

如确需换项目路径，先完成私有备份，再按第 5 节迁移同平台兼容数据库、数据文件和同次预算。新目录的空数据库/空账本不是旧报告或原预算的延续。

### 2.2 启动会怎样迁移

启动脚本先应用 `scripts/schema.sql`，为旧 `runs` 增加 `kind/request_payload/review_payload`，并创建独立 `reports` 表、索引和权限。迁移采用存在性检查，不删除旧会话、结果或销售事实；不需要另装迁移框架。

随后 `scripts/import_data.py` 检查冻结数据 manifest、版本和 CSV 哈希。已有销售行时复用原快照，不重新随机生成。报告事实保存在 PostgreSQL 的 JSONB 中，不存于浏览器缓存；费用账本是独立 SQLite 文件，不在这份 schema 里。

如果数据版本、哈希、数据库主版本或 schema 报错，停止并保留现场。不要改哈希、删库、清空 runtime 或清空预算来“修好”启动。

## 3. Windows mock 启动与可选自动检查

在项目根目录的普通 PowerShell 执行。前置工具是 Python 3.12、uv、锁文件支持的 Node.js（推荐 24.19.0）和完整 PostgreSQL 17 安装；具体要求见 [Windows 指南](windows.md)。

```powershell
uv sync --frozen --python 3.12
npm --prefix frontend ci

# 仅安装位置不同于默认目录时设置为实际路径
$env:POSTGRES_BIN = 'C:\Program Files\PostgreSQL\17\bin'
$env:AGENT_MODE = 'mock'
.\.venv\Scripts\python.exe scripts\dev_windows.py
```

已有验证数据时不要重跑生成。若首次安装确实没有 `data/processed/`，先按 Windows 指南安装 .NET SDK 8.0.425，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\contoso_generate.py --verify-inputs
.\.venv\Scripts\python.exe scripts\contoso_generate.py
```

首次下载需要网络和额外磁盘空间；源码包应包含公开生成配置、输入工作簿及许可证。生成输出仍须通过现有版本/哈希门禁，不承诺跨平台随机输出逐字节相同。

启动后打开 <http://127.0.0.1:5173>；API 是 <http://127.0.0.1:8000>。全门店账号 `admin / demo123`；受限账号 `analyst / demo123`。先确认页面显示 `MOCK`。保持终端运行，Ctrl+C 关闭本次启动的服务。脚本不会关闭它启动前已经运行的项目 PostgreSQL。

Windows 数据目录为 `runtime/pgdata-windows`。脚本只监听本机，使用 55432、8000、5173，不修改系统服务、防火墙或系统 5432 数据库。演示 trust 认证只适用于本机，不能开放到外网。

如要执行浏览器自动检查，先退出普通开发服务，再执行：

```powershell
# 首次 E2E 才需安装与锁文件匹配的浏览器
Push-Location frontend
npx playwright install chromium
Pop-Location
.\.venv\Scripts\python.exe scripts\dev_windows.py --test-e2e
```

这会执行现有 3 条场景，并强制 mock、禁用 `.env` 加载、使用临时测试账本；不会重置原预算。测试仍使用项目数据库，会产生测试会话和报告，不应当作完全无痕检查。运行结果与截图/trace 位于 `frontend/test-results/`，分享前检查是否含用户数据。

开发者可额外执行后端和前端检查；用户人工验收不要求亲自重复全部测试：

```powershell
.\.venv\Scripts\python.exe scripts\dev_windows.py --test-backend
npm --prefix frontend test
npm --prefix frontend run build
```

Debian 13 路径按 [README](../README.md) 安装依赖和本地 PostgreSQL，正常 mock 启动用 `AGENT_MODE=mock bash scripts/dev.sh`，浏览器测试用 `bash scripts/dev.sh --test-e2e`。这不是 Windows 命令，也不是通用 Linux 发行版安装保证。

## 4. 六组手动验收

只用下面的固定输入即可，无需试遍自然语言。mock 是有限演示模型，不能用任意问法的表现代替 live 验证。每组记录“通过 / 失败 / 未运行”，失败时留具体步骤、错误和脱敏截图。

### M1：登录、查询、比较

1. 用 admin 登录，核对模拟声明、MOCK、全部门店范围。
2. 提问：`2025年销售额和订单数按月看`。切换“表格”，应有 12 个月，且指标、单位、期间明确；导出 CSV 应是聚合数据。
3. 追问：`换成表格`。应继续展示同一冻结结果，不变成新业务查询；实现/自动断言检查零模型、零重新聚合，单凭肉眼不能证明调用数。
4. 提问：`2025年9月比8月销售额变化来自哪些类别，用瀑布图`。应可查看比较、贡献及瀑布/表格；差额对账使用完整精度，显示舍入不能当业务误差。

### M2：完整复盘和显式保存

1. 点击“销售复盘”→“2025 年 Q3 vs Q2”，主指标选折扣后销售额，门店用当前全部授权门店，勾选“允许一次重点类别的商品下钻”。
2. 开始复盘。表单本期应是 2025-07-01 至 2025-09-30，对比期 2025-04-01 至 2025-06-30；页面结束日期含当日，API 分别使用排他的 2025-10-01 和 2025-07-01。
3. 查看整体、类别、门店和有条件的一次商品证据，以及规则、局限、停止原因。查询数最多 4；若提前结束，应有具体原因，不能为了凑数继续下钻。
4. 点击“保存报告”，标题填写 `本地验收 Q3 复盘`，备注填写 `人工核对第一版`，点击“确认保存报告”。应进入报告详情，地址包含 `#reports/报告ID`。保存这个地址供后面检查。

### M3：刷新、重启、批注与修订冲突

1. 刷新报告页面，应恢复报告。记下报告 ID、期间、指标总量和一项证据值。
2. Ctrl+C 停止本项目，再用同一启动命令和同一数据目录重启。重新登录、从“报告”打开同一份报告，之前记录的事实应不变，不需要重跑复盘。
3. 点击“编辑批注”，修改标题/备注/建议并保存；应看到“冻结数据事实未改变”，事实数值不可直接编辑。
4. 两个标签页都打开同一报告并进入编辑。A 页先保存新备注，B 页再提交不同备注；B 应提示报告已更新，不无声覆盖 A。先复制 B 需要保留的文字，再“重新读取最新报告”后自行合并。

报告修订从 1 开始，实际批注修改、移入回收站、恢复会增加 revision；无变化不增加。旧版本提交返回 409。数据/证据、期间、筛选和指标定义冻结，重新分析和保存产生新报告。

聊天结果默认 24 小时到期，登录默认 12 小时到期。首次保存必须趁源结果仍有效；已保存报告没有这个 TTL，嵌入的源 `expires_at` 只是历史信息。开发者有到期/重启验证记录，用户无需改系统时间或手工改数据库；愿意自然等到次日时可重新登录后复查同一报告。

### M4：导出、回收站与恢复

1. 分别点击“导出 Markdown”“导出 HTML”，打开文件，检查标题、期间、范围、模拟声明、指标口径、证据和人工批注；HTML 应可断网阅读。
2. 点击“移入回收站”，先选择“保留报告”，报告应仍可用；再次移入并确认，普通列表应不再出现。
3. 从回收站打开并恢复，原报告 ID、事实和批注应完整，再次导出应可用。已删除状态不能编辑或导出，须先恢复。

本轮没有永久清空或自动清理。已下载文件不受后来撤权/软删除追溯控制；导出只是离线阅读文件，不是可导回应用的数据库备份。

### M5：账号隔离与当前范围

1. 退出 admin，确认之前报告正文清除；用 analyst 登录，应显示仅授权 3 家门店。
2. 查看报告列表，不应出现 admin 的报告标题；访问 M2 保存的完整报告地址，应拒绝展示，不能出现旧数字或证据。也不能下载 admin 报告。
3. 用 analyst 提问 `2025年销售额按门店看`，展示门店应在这 3 家范围内。退出并登录 admin 后，原报告仍可访问。

报告不是两个演示账号间的共享文档。所有列表、读、改、回收、恢复和导出都检查 owner 与当前范围对整份原快照的包含关系。当前保存范围是生成时完整授权门店集合，可能比查询显式选中的门店更保守；权限收缩后不能覆盖原集合时整份隐藏，不裁剪行后继续显示旧总额。扩大权限也不会扩展旧事实。

当前没有权限管理 UI，不要求用户为了验收修改账号代码或数据库。真正“同一账号范围缩小”的接口级检查见最终验证记录；跨账号手测不能替代这项开发证据。

### M6：错误和快速操作

1. 查询 `2025年退款金额是多少？`，应明确不支持，不把上次成功结果充当回答；再输入 M1 的有效问题，应可继续工作。
2. 发起查询/复盘时若仍在运行，尝试取消或新建会话；只有实际停止后才显示取消，不应发布半份可保存复盘。mock 可能结束很快，来不及取消时记“未观察到”，不记通过。
3. 切换报告、刷新、退出重登后，不应被迟到响应带回旧会话/旧账号内容。将浏览器缩窄到约 390px，确认主入口、表格与报告操作仍可用。

模型/SQL 超时、工具失败、预算不足等故障由开发者定向检查覆盖。不要为了手测制造真实付费失败、清空账本或破坏正式数据。

## 5. 私有备份与恢复

### 5.1 必须保留什么

- PostgreSQL 完整数据目录：Windows 是 `runtime/pgdata-windows`，当前 Debian 是 `runtime/pgdata`，包含报告、会话和销售数据。
- 原预算 SQLite 文件及存在的同名前缀伴随文件，默认 `runtime/llm-budget.sqlite3*`；若设置 `LLM_BUDGET_PATH`，应保留实际位置的同组文件。
- `data/processed/` 整目录和 `data/provenance/` 公开来源材料；原始数据/缓存按需要私下保留，启动无需重新随机生成。
- 与备份对应的源码、锁文件及用户本地改动。个人 `.env` 由用户自行在受限私有位置保留，不交给助手读取，不放进交付 ZIP 或 Git。

持久化不等于自动备份、云同步或永久托管。本地磁盘丢失、runtime 被删除或重建数据库都会影响报告。源码 ZIP 和报告导出均不包含可恢复整个数据库的副本。

### 5.2 Windows 停库冷备示例

这是用户在自己机器执行的维护流程；本轮没有替用户执行备份/恢复。先退出 API、前端、测试和所有使用预算文件的本项目进程。若项目启动前数据库已在运行，Ctrl+C 不会停它；先检查精确目录，不动系统数据库服务：

```powershell
$pg = if ($env:POSTGRES_BIN) { $env:POSTGRES_BIN } else { Join-Path $env:ProgramFiles 'PostgreSQL\17\bin' }
& "$pg\pg_ctl.exe" -D "$PWD\runtime\pgdata-windows" status
# 仅在确认上面显示的是本项目仍运行的集群时，执行以下停库命令
& "$pg\pg_ctl.exe" -D "$PWD\runtime\pgdata-windows" -m fast -w stop
```

已停止时无需再次 stop。确认 `status` 明确显示未运行后，在当前用户私有目录创建新备份；下面复制的是整个已停库目录，不是运行中的单个数据库文件：

```powershell
& "$pg\pg_ctl.exe" -D "$PWD\runtime\pgdata-windows" status
if ($LASTEXITCODE -ne 3) { throw '未确认 PostgreSQL 已停止，请先检查状态，勿复制运行中的数据目录。' }
$backup = Join-Path $env:USERPROFILE ('sales-agent-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
Copy-Item 'runtime\pgdata-windows' -Destination $backup -Recurse -ErrorAction Stop
Copy-Item 'data\processed' -Destination $backup -Recurse -ErrorAction Stop
Copy-Item 'data\provenance' -Destination $backup -Recurse -ErrorAction Stop
Get-ChildItem 'runtime' -Filter 'llm-budget.sqlite3*' -File |
  Copy-Item -Destination $backup -ErrorAction Stop
```

该预算命令只覆盖默认路径；自定义路径必须单独保留，不得因默认路径没文件就认为已经备份。检查备份包含 `pgdata-windows/PG_VERSION`（17）、完整数据目录、processed manifest/CSV 和实际预算文件；检查复制过程中没有错误，并保留原件。密钥不在上述复制命令范围内。磁盘空间不足或复制失败时，不能把部分目录当有效备份。

### 5.3 恢复原则

1. 关闭全部项目进程并确认项目集群停止，核对原备份来自兼容的 Windows PostgreSQL 17；优先同一安装/版本恢复。冷备不能直接跨 Windows/Linux 或不同 PostgreSQL 主版本使用。
2. 保留当前故障目录作为单独现场，不合并两套 `pgdata`，不对现有目录覆盖复制。在原项目预期路径放回完整备份目录，让 `runtime/pgdata-windows/PG_VERSION` 正确就位；不要多套一层目录。
3. 恢复匹配的 processed 数据与来源材料、匹配源码及本地改动。独立预算保留最新累计状态：若备份后还有真实消费，不能用旧预算副本回滚占用。先核对所有环境费用和未知预留，期间保持 mock。
4. 依赖按锁文件重建，按第 3 节 mock 启动；核对旧报告 ID、事实、备注、回收站状态及导出。仅“服务启动成功”不构成恢复验收。
5. 验证完成前保留原件与备份。若跨平台迁移，采用 PostgreSQL 正式逻辑备份/恢复，并正确重建角色与权限；本仓库没有一键跨平台恢复工具，本页不把原始目录搬迁称为跨平台方案。

Debian 同理，必须在整个项目生命周期结束、项目 PostgreSQL 确认停止后复制完整 `runtime/pgdata` 与预算文件；不要仅复制 `reports` 某个底层文件，也不要把运行中的 PostgreSQL/SQLite 文件逐个拷贝当可靠备份。

## 6. 常见阻塞与反馈

- 启动失败：先读 `runtime/dev-postgres.log`、`runtime/api.log`、`runtime/frontend.log` 的具体错误。端口占用时识别实际占用者，不自动杀其他进程。
- 源结果过期而不能首次保存：按当前授权重新运行后再保存；已有报告直接在报告库打开。
- 409 修订冲突：保留当前输入，重新读取，再自行合并；不要反复提交旧 revision。
- 404 报告不可访问：核对登录账号和范围；不会为了“恢复显示”绕过权限。
- 缺少 processed 或校验失败：恢复原已验证数据或按生成指南排查，不修改清单哈希。
- live：原总预算 5 元，程序保守上限 4.8 元；最新已知占用 2.252764 元含未知预留 2.113536 元，最新本地真实消费仍需对账。这不是可花余额；不得用测试空账本、新目录或新环境重置额度。

请反馈：候选包标识、Windows/工具版本、M1–M6 结果、首次失败步骤和脱敏错误。无需提供 `.env`、密钥、数据库或预算文件。自动/浏览器通过之后，仍需用户明确确认本地体验是否接受；若接受但 live 未验，应把这个边界一并写明。

## 7. 可复制给本地 Codex 的交接指令

> 在我的现有 sales-analysis-agent 工作目录中，先阅读 README、STATUS、docs/final-delivery-spec.md、docs/final-handoff.md 与 docs/windows.md。检查并保留我的本地修改，先确认私有运行数据已有停库备份，再把收到的候选源码逐项比较合并；不要覆盖我的 .env、runtime、processed 数据或原预算账本，不打印凭据。只使用 AGENT_MODE=mock，按现有 Windows 启动和测试入口做候选验证，保留真实测试结果与阻塞说明。不要重生成已有数据、清库、清账本、启用付费模型、推送 GitHub、部署、改系统服务/防火墙或新增平台功能。整理手动验收结果，等我明确确认本地接受之后再讨论部署。若操作将影响现有数据或范围超出这些步骤，先说明并询问。
