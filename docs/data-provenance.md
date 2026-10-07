# Contoso 数据来源与验收记录

本项目使用 SQLBI Contoso Data Generator V2 官方程序实际生成的模拟零售数据。不是 UCI 数据，不是真实企业交易，也不是本项目自行编造的销售记录。生成、规范化和校验均已执行。

## 已生成的数据

- 数据版本：`contoso-v2-2023-2025-692e0347d360`
- 生成目标：100,000 张订单；官方生成结果：**97,215 张订单、232,928 条订单行**
- 订单日期：**2023-01-01 至 2025-12-31**；36 个月、1,096 个自然日均有销售
- 只输出 `SALES`；未导入同批交易的 `Orders` / `OrderRows`
- 8 个国家的生成币种配置均设为 USD；所有销售行均验证 `CurrencyCode=USD`、`ExchangeRate=1`
- 目标订单数不是精确承诺：源码诊断复算确认，日权重取整减少 559 单，逐日随机扰动净减少 1,094 次尝试，所选地理区域/客户分组未找到活跃客户再跳过 1,132 次，最终 100,000 − 559 − 1,094 − 1,132 = 97,215；没有补造记录，也没有用过滤把多币种数据裁成 USD。细节见 `data/provenance/order-count-explanation.json`

| 表 | 行数 | 粒度 |
| --- | ---: | --- |
| `fact_sales` | 232,928 | 一张订单的一行，联合键为 `order_key,line_number` |
| `dim_product` | 2,517 | 商品 |
| `dim_store` | 74 | 门店，包括官方在线门店 |
| `dim_customer_geo` | 41,996 | 合成客户的地理属性，仅保留客户键、国家和州 |
| `dim_date` | 1,461 | 自然日；涵盖 2023–2026，以容纳跨年交付日期 |

`line_number` 沿用官方输出，从 0 开始，允许中间不连续。在线门店的官方键为 `999999`，门店国家为 `Online`、代码为 `--`；它不是客户所在国家，也不应被转换为某个真实国家。销售日期范围独立于日期维表范围。

年度订单数分别为 2023 年 13,569、2024 年 31,468、2025 年 52,178。这些变化来自模拟权重，不能解读为真实市场趋势。

## 可追溯的官方输入

- [SQLBI 官方生成器](https://github.com/sql-bi/Contoso-Data-Generator-V2)，固定 commit：`eaeb57a9eaa6ad0cdab4fb527552102685434ee0`
- 生成器版本 `2.0.1.0`，.NET SDK `8.0.425`
- [官方配套数据仓库](https://github.com/sql-bi/Contoso-Data-Generator-V2-Data)，记录时 commit：`b3d5caa022e5180eefb6915eb57e60a8290b3142`
- 输入工作簿：生成器同一 commit 下的 `scripts/publish_tool/data.xlsx`，原样归档到 `data/provenance/input-data.xlsx`
- [static-files 官方发布](https://github.com/sql-bi/Contoso-Data-Generator-V2-Data/releases/tag/static-files)：21 个客户压缩 CSV、UKPostcodes.csv、ECB_eurofxref-hist.csv；每个下载 URL、asset ID、字节数与 SHA-256 均记录于 `data/provenance/source-manifest.json`
- 两个仓库均采用 MIT；版权和许可原文保存在 `data/provenance/LICENSE-generator.txt` 与 `LICENSE-data.txt`
- [官方使用文档](https://docs.sqlbi.com/contoso-data-generator/)

生成器源码没有业务修改。唯一构建调整是仅在 Windows 上执行原有 Windows cmd 版本戳命令，见 `data/provenance/linux-build.patch`。`Engine`、客户分配、商品、折扣、数量、日期和价格生成代码均未改动。

配置从官方 `scripts/publish_tool/config.json` 复制，只修改订单数、2023–2025 日期范围、客户抽样比例 0.02、`SalesOrders=SALES` 及统一 USD 映射。完整配置保存在 `data/provenance/generator-config.json`；0.02 使用的是官方已有抽样参数，底层客户输入来自全部官方客户文件。

## 金额语义与精度

[固定版本源码：价格生成](https://github.com/sql-bi/Contoso-Data-Generator-V2/blob/eaeb57a9eaa6ad0cdab4fb527552102685434ee0/DatabaseGenerator/Engine.cs#L515-L532)：

- `UnitPrice` 是产品基础售价乘类别的时段价格因子
- `NetPrice = UnitPrice × (1 − discount / 100)`，是折扣后的**单位售价**；不能再扣一次折扣
- `UnitCost` 是产品基础单位成本乘同类别的时段价格因子
- [订单行复制到 Sales](https://github.com/sql-bi/Contoso-Data-Generator-V2/blob/eaeb57a9eaa6ad0cdab4fb527552102685434ee0/DatabaseGenerator/Engine.cs#L257-L279) 时，单价与成本直接复制；汇率字段另存，未先作用于价格
- 官方引擎在生成阶段使用 double，再转 C# decimal 输出。本项目保留其 CSV 十进制文本作为来源真值，导入时不经过 Python float，也不声称消除了上游生成阶段的浮点运算

每一行均通过折扣公式复核，折扣范围是归档配置允许的整数 0–14%。观测最大小数位分别为售价 4、折后价 6、单位成本 4、汇率 5；所有值完整装入 `NUMERIC(18,6)`，无需截断。内部加总使用精确十进制，显示时才取两位小数。

第一个实际样本：订单 1000、第 0 行，数量 7，售价 131.4，折后单价 119.574，成本 43.536；折扣为 9%，销售额 837.018，商品毛利 532.266。

全量基准值（未四舍五入）：

| 指标 | 精确值 |
| --- | ---: |
| 折后销售额 | 231599268.077812 USD |
| 商品毛利 | 129409764.342112 USD |
| 销售数量 | 731756 |
| 去重订单数 | 97215 |

商品毛利未扣税费、物流、营销等经营费用，不是企业净利润。数据没有付款、退款、取消或实收款事件。

## 发布前校验

`python scripts/contoso_normalize.py` 已通过：

- 订单行与各维表主键唯一，事实外键完整
- 同一订单的日期、交付日、客户、门店、币种、汇率一致
- 数量为正，单价和折后价为正，成本非负，折后价不高于售价
- 日期覆盖完整，交付日不早于订单日，跨年交付日期可关联日期维表
- USD/汇率 1、精确小数范围、折扣公式与配置相符
- 未出现重复表达交易的 Orders/OrderRows 输出

异常分类计数全部为 0。另用含重复订单行、EUR 币种和无效商品键的损坏副本做了负向测试：三个问题均被检出，未发布任何规范化 CSV，记录见 `data/provenance/validator-negative-test.json`。验证不静默删行、不改符号，任一检查失败都不发布新规范化表。完整计数、样本、文件 SHA-256 和全量基准值见 `data/provenance/validation-report.json`；应用使用的同份版本清单为 `data/processed/manifest.json`。

## 规范化与隐私边界

只做字段重命名、维表裁剪和日期属性推导。事实记录数保持不变，价格文本原样保留。原始客户文件虽为合成数据，但姓名、街道、生日、经纬度等仍不导入应用表；应用只保留完成国家/州聚合所需的地理字段。

CSV 文件均使用 UTF-8、英文 snake_case 字段；金额列读取为 Decimal / NUMERIC。`dim_date.week_start` 为周一日期，`year_month` 为 `YYYY-MM`。各字段名称以 CSV 表头及导入模型为准。

## 重跑和归档

日常启动直接加载归档数据，不重新生成。重新执行官方引擎可用：

```bash
bash scripts/contoso_generate.sh
```

Windows 或已安装固定 .NET SDK 8.0.425 的环境也可使用 `python scripts/contoso_generate.py`，先运行 `--verify-inputs` 可只检查配置和工作簿而不下载或生成。前置条件见 [Windows 指南](windows.md)。该路径保留相同源提交、输入哈希、生成参数和标准化门禁；额外只对 Windows 构建版本戳的输出路径加引号，不修改生成引擎。Windows 实机生成及逐字节重现尚未验证。

Git 属性将有字节哈希的 `generator-config.json` 固定为 LF，工作簿保持二进制；不会为换行差异修改已审核的来源清单哈希。

若已有原始数据，脚本会停止，防止覆盖已发布基准。要进行独立重跑：

```bash
CONTOSO_DATA_DIR="$PWD/runtime/contoso-replay" bash scripts/contoso_generate.sh
```

首次运行会从 Microsoft 官方安装脚本取得 .NET SDK（本机没有 dotnet 时），从官方仓库克隆固定源码，从 NuGet 恢复源码列出的依赖，再下载约 480 MB 官方静态输入。客户缓存还需要约 1 GB 磁盘空间；无需 SQL Server、数据库账号或模型密钥。

官方源码具有固定 `Random(0)` 和按日期派生的随机数初始化；本次第二次执行已验证六个官方 CSV 全部逐字节相同；固定源码、输入、运行时的重跑比对记录见 `data/provenance/replay-verification.json`。长期复现以已归档输出及 SHA-256 为准，不依赖远程 release asset 永远不变。

`data/processed/` 是已校验的应用输入；`data/provenance/` 是可审查的证据。原始生成 CSV 在 `data/raw/`，下载缓存和重跑临时结果在 `data/cache/`，均被 Git 忽略。发布作品集时应明确包含已冻结的 processed 数据归档及来源许可，不应把近 GB 级缓存、原始客户属性、运行环境或任何秘密一起打包。
