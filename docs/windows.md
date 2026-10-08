# Windows 本地运行

这条路径直接使用 Windows Python、Node.js 和 PostgreSQL，不需要 WSL、Bash、Debian 包或管理员方式运行项目。脚本只管理项目 `runtime/` 内的独立演示数据库，不安装软件、不注册 Windows 服务、不修改防火墙或系统 PostgreSQL 数据目录。

用户已反馈旧版本 Windows 启动与 E2E 通过；本轮新增复盘/报告候选仍须在 Windows 实机复验。云端单测、真实 HTTP 验证和构建不等于 Windows 浏览器或用户人工验收。当前证据见 [STATUS](../STATUS.md) 与 [最终验证](final-verification.md)。

## 1. 准备已有工具

- Python 3.12 与 uv
- Node.js 24.19.0（仓库 `.node-version`）；锁定依赖支持 `^22.22.2 || ^24.15.0 || >=26.0.0`
- Git
- PostgreSQL 17：[官方 Windows 下载页](https://www.postgresql.org/download/windows/) 提供 EDB 安装器和二进制 ZIP 的链接。自行安装或解压完整发行包，保留 `bin`、`lib`、`share` 等目录，不能只拷贝几个 exe
- 仅首次生成 Contoso 数据需要 .NET **SDK 8.0.425**，不是仅 Runtime：[Microsoft Windows 安装说明](https://learn.microsoft.com/en-us/dotnet/core/install/windows)。生成器会检查该 SDK 已安装，并使用项目临时目录内的 `global.json` 固定版本；不会自动安装

PostgreSQL 默认查找 `%ProgramFiles%\PostgreSQL\17\bin`。如果安装在别处，在当前 PowerShell 会话设置 `POSTGRES_BIN`。无需改系统 PATH，也不必启动安装器创建的数据库服务。脚本使用单独的 55432 端口，不接管系统 5432 服务。

## 2. 安装项目依赖与生成数据

在项目根目录打开普通 PowerShell：

```powershell
uv sync --frozen --python 3.12
npm --prefix frontend ci

# 只在 PostgreSQL 不位于默认目录时设置，替换成实际 bin 路径
$env:POSTGRES_BIN = 'C:\Program Files\PostgreSQL\17\bin'

# 下载前先检查已审核的配置和工作簿，不能更换 checksum 来掩盖变化
.\.venv\Scripts\python.exe scripts\contoso_generate.py --verify-inputs

# 仅在没有 data\processed 数据时运行；首次需下载官方输入并构建
.\.venv\Scripts\python.exe scripts\contoso_generate.py
```

生成脚本使用原有固定 SQLBI 提交、参数、工作簿和来源清单；仍是 2023–2025、USD、仅 SALES、100,000 目标订单和 0.02 客户抽样比例。现有 `data/raw/sales.csv` 会使生成停止，不覆盖冻结数据。校验仍包含官方输入哈希与完整标准化门禁，没有降低门槛或新增无意义校验和。

`.gitattributes` 把 `generator-config.json` 固定为 UTF-8/LF 文件的原有字节，防止 Git for Windows 的 `core.autocrlf` 破坏已记录哈希。如果旧工作目录已被 CRLF 转换，请在保留自己修改的前提下，从包含新属性的干净检出恢复该文件；不要更新来源清单的哈希来迁就 CRLF。

生成器额外只调整构建元数据：非 Windows 不执行 cmd 版本戳，Windows 输出版本戳的重定向路径加引号以支持含空格的目录；不改 Engine 或随机/业务参数。生成日志写入 `data/provenance/generation-console.log`。首次输入下载和缓存仍需约 1 GB 以上空间。Windows 生成输出是否逐字节复现当前快照仍待实机验证，导入器保留原有数据版本检查，不会默许不同版本。

## 3. 统一启动

```powershell
$env:AGENT_MODE = 'mock'
.\.venv\Scripts\python.exe scripts\dev_windows.py
```

- 页面：<http://127.0.0.1:5173>；API：<http://127.0.0.1:8000>
- 演示账号：`admin / demo123` 或 `analyst / demo123`
- 保持终端运行，Ctrl+C 关闭本次启动的服务；已经运行的项目 PostgreSQL 不由本次退出关闭
- 使用 `runtime/pgdata-windows`，日志在 `runtime/`；不复用 Linux 的 `runtime/pgdata`
- 55432、8000 或 5173 被其他服务占用会报错，不自动换端口或终止其他进程

此脚本明确使用本项目本机数据库连接，覆盖子进程的三个数据库 URL；不适合连接共享或生产数据库。演示数据库仅监听 127.0.0.1，采用 trust 认证，不能开放到外网。默认模型模式仍为 mock；测试参数强制 mock 并关闭追踪。请勿在排查可移植性时启用真实模型。

普通 mock 演示不需要模型密钥。需要手动配置 `.env` 时，请遵循主 README 的真实调用授权和原有预算迁移要求；Windows 应使用当前用户可访问的文件权限，不使用 Unix `chmod`。不在聊天或源码中提交密钥，不删除或清空现有预算账本。

## 4. 已有版本升级与报告迁移

先停止项目服务、备份私有运行数据并保留本地未提交改动，再更新候选源码。若收到源码包，把它与本地目录比较后合并；只有确认本轮改动已经推送到正确远端提交时才考虑拉取，不能默认 `git pull` 会取得未发布版本。

启动脚本每次都会应用 `scripts/schema.sql`：用 `ADD COLUMN IF NOT EXISTS` 增补 `runs.kind/request_payload/review_payload`，用 `CREATE TABLE IF NOT EXISTS` 新建 `reports` 与所需索引/权限；不会通过删除旧会话、旧结果或销售事实完成迁移。独立费用账本不在这份 schema 中。销售数据已存在时，导入器复用原快照，不重新随机生成。

不需要另装迁移框架或手工删库。schema 错误应先看 `runtime/dev-postgres.log`，修复具体问题后再启动；不要清空 `runtime` 作为排障办法。新机器/重建数据库不会自动拥有原报告，源码包也不携带数据库和预算。

备份时先关闭使用这些文件的全部本项目进程，再在私有位置保留 `runtime/pgdata-windows`、预算账本及其伴随文件、已生成数据和必要的用户配置；不要把运行中的单个 PostgreSQL/SQLite 文件当作有效备份。原始数据目录备份仅用于兼容的本机 PostgreSQL 17 恢复，跨平台应使用 PostgreSQL 正式逻辑备份/恢复工具，不能把 Linux `pgdata` 直接搬给 Windows。密钥和账本不提交 Git、不装入公开源码 ZIP。重要报告可另导出 Markdown/HTML，方便服务中断时查看；导出文件不是可回写数据库的备份。

更短的操作清单与可复制 Codex 交接见 [本地交接与人工验收](final-handoff.md)。

## 5. 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q scripts\tests\test_portability.py
.\.venv\Scripts\python.exe scripts\dev_windows.py --test-backend
npm --prefix frontend test
npm --prefix frontend run build

# 浏览器只在需要 E2E 时下载，与锁定的 Playwright 版本匹配
Push-Location frontend
npx playwright install chromium
Pop-Location
.\.venv\Scripts\python.exe scripts\dev_windows.py --test-e2e
```

Playwright 默认使用自己的跨平台 Chromium，不再寻找 `/usr/bin/chromium`。需要测试环境已有的浏览器时，可显式设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 为实际 exe 路径；通常保持未设置更可靠。安装与缓存行为见 [Playwright 官方浏览器说明](https://playwright.dev/docs/browsers)。

E2E 使用真实 PostgreSQL/API 与本地 mock 模型，不是浏览器网络伪造响应；不产生外部模型费用。其通过结果也不能替代真实模型浏览器验收。

本套件共 3 条 E2E（原工作台、延迟重置/快速动作、新复盘报告闭环）。先停止已有开发服务，再用测试入口统一管理服务。新增候选的实际浏览器结果未取得前，不将测试发现或旧 E2E 反馈写成通过。
