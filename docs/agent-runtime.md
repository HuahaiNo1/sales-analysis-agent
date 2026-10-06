# DeepAgents 运行边界与预算

## 运行方式

本项目实际使用 `deepagents==0.7.22` 的 `create_deep_agent` 创建主 Agent 和一个预编译分析子 Agent。不是把普通条件判断改名为 DeepAgents。默认 `AGENT_MODE=mock`：仅把聊天模型替换为本地 `BaseChatModel`，仍执行同一张图、同样的工具和查询网关，不创建外部模型客户端，不产生 LLM 费用。

典型一次成功运行：

1. 主模型调用 `get_metric_catalog`
2. 主模型调用 `query_metrics` 提交结构化 QuerySpec
3. 网关进行身份授权、严格校验、只读模板查询，冻结结果并返回不透明句柄
4. 主模型调用 DeepAgents 原生 `task`，指定 `subagent_type=analysis`
5. 分析子模型调用 `analyze_result`；闭包检查句柄是否属于本次运行，网关再次验证授权并计算确定性统计
6. 子模型整理观察，主模型返回答复

普通成功运行包含 4 次主模型调用、2 次分析模型调用。两者共用一次运行最多 12 次模型调用限制、同一个持久预算账本。每次运行最多提交一次聚合查询，错误立即交还后端处理。

后端入口：

```python
await run_agent(
    message, previous_query, catalog,
    query_callback, analysis_callback, cancelled,
    mode=settings.agent_mode,
    api_key=settings.deepseek_api_key if settings.agent_mode == "live" else None,
)
```

回调可以是同步或异步函数。同步查询在工作线程执行，主事件循环可继续处理取消和状态查询。查询回调须返回至少 `result_id`；完整聚合结果由后端持久保存。分析回调只接受句柄，返回由普通程序计算的统计。真实运行状态、数据是否为空、完整性、过期和取消均由后端决定，不能被模型文本覆盖。

## 离线模式的明确范围

离线模式是演示问法转换器，并非通用自然语言理解。示例：

- `2025年销售额按月看`
- `2025年销售额按类别看`
- `2025年订单数按门店看`
- `2025年销量按类别看`
- `2025年商品毛利`
- `2025年9月比8月销售额下降多少，主要来自哪些类别？`
- `2025年9月比8月销售额变化来自哪些类别，用瀑布图`
- 上述查询之后：`按月看`、`只看前十`

明确出现的年份或月份会进入 QuerySpec，并由网关验证数据覆盖。未指定期间的演示查询使用数据集最后一个日历年的已覆盖区间，答复明确展示实际起止日期。日期为左闭右开。

离线模式对当前年、上月、同比等相对日期要求改为明确年月；精确到日的自然语言区间尚未支持。不支持的问题需要澄清，而非悄悄变成任意 SQL。付款、退款、取消率和企业净利润不属于此数据集。生产模式由真正的模型理解自然语言，仍必须经过完全相同的查询合同和授权网关。

## 实际工具检查

`runtime_smoke_check()` 在不创建外部客户端的情况下构建正式拓扑，可由服务启动阶段调用。每次运行构建图时也再次检查：

| 组件 | 唯一允许的工具 |
| --- | --- |
| 主 Agent | `get_metric_catalog`, `query_metrics`, `task` |
| 分析子 Agent | `analyze_result` |
| `task` 真实分发注册表 | 仅 `analysis` |

默认 general-purpose 子 Agent 关闭；SummarizationMiddleware 关闭，没有额外摘要模型调用。`HarnessProfile` 隐藏所有默认文件及 shell 工具，包括当前版本中的 `delete`。由于框架的工具排除仍会保留内部 ToolNode 注册，本项目同时检查真实 ToolNode 注册表、移除已知默认处理器并断言精确白名单；出现未知工具即构建失败。模型绑定和工具分发各有独立白名单验证。子 Agent 不持有查询工具，也不能用猜到的其他运行句柄读取数据。

此额外 ToolNode 加固依赖已固定版本的内部结构；升级 DeepAgents 必须重新运行测试及启动检查，不能因检查失败而临时放开工具。工具允许列表不是完整的数据安全边界；可信认证、QuerySpec 校验、数据库只读角色、RLS、句柄归属和不可变聚合仍由后端实施。

## Live 模式与凭据

只有用户手动配置 `.env` 中的 `DEEPSEEK_API_KEY`，并主动选择 `AGENT_MODE=live` 后，运行时才创建外部模型客户端。不得把真实密钥发到聊天、日志、仓库、浏览器静态资源或测试夹具中。常规自动检查仍使用 mock；用户人工配置与授权后，已完成一条真实 API 场景，见 [真实 API 验证记录](live-api-validation.md)。

固定配置：

- 兼容 OpenAI Chat Completions 的官方地址 `https://api.deepseek.com`
- 模型 `deepseek-flash`
- `thinking={"type":"disabled"}`，最大输出 2048 tokens
- SDK `max_retries=0`，请求超时 30 秒，无框架自动重试或备用付费模型
- 所有主/子模型请求通过同一个 `GuardedModel`
- 关闭 `LANGSMITH_TRACING`、`LANGCHAIN_TRACING`、`LANGCHAIN_TRACING_V2`，运行上下文也显式禁用 tracing，不传外部回调

## 人民币 4.8 元共享安全预算

用户给出的 5 元额度保留 0.2 元余量。应用预算硬上限固定为 4.8 元，不能通过传入更大的配置提高。

账本默认位于持久化 `runtime/llm-budget.sqlite3`，可用 `LLM_BUDGET_PATH` 指向部署的持久卷。所有用户、会话、主 Agent 和子 Agent 都共享同一个路径。不能为不同 worker 或容器设置独立账本；不要删除/重新创建该文件来重置额度。多个副本若不共享同一可靠文件锁文件系统，应改为数据库集中记账后再部署。

预留与结算规则（不依赖本地 tokenizer 或字节估算）：

1. 使用 2026-10-06 核对的 Flash 高峰、缓存未命中人民币单价：输入每百万 tokens 2 元，输出每百万 tokens 8 元
2. 每一次模型调用都按官方 1M context 的完整上界 **1,048,576 个输入 tokens** 加完整的 **2048 个输出 tokens** 预留，即 **2.113536 元**。即使是很短的请求也一样，不把 UTF-8 字节估算当作硬上界
3. SQLite `BEGIN IMMEDIATE` 事务原子检查 **已结算金额 + 全部未决预留 + 本次完整预留 ≤ 4.8 元**，提交后才允许发请求
4. 只有成功响应且 usage 严格合法时，按峰价实际输入/输出 tokens 结算，并原子释放预留差额。两个 token 数必须都是非负 `int`，不能是 `bool`，且输入不超过 1,048,576、输出不超过 2048；如果提供 `total_tokens`，它也必须为合法整数且等于两者之和
5. 超时、失败、取消、usage 缺失或非法时，保留整额 2.113536 元；后来的响应不能再释放已标记未知的预留。两个未知调用占用 4.227072 元，只剩 0.572928 元，因此第三次调用会在发送前被拒绝
6. 合法成功响应能及时结算，普通六次模型调用的完整分析流程可继续执行。模型调用仍无自动重试，失败不会切换付费备用模型
7. usage 超出硬上界或已结算记录出现冲突时，保留/恢复整额预留并锁定账本，阻止后续调用
8. 重启保留账本。旧版记录不因迁移而被清空或按旧字节估算释放：保留原审计字段，将每笔未知旧记录至少按 2.113536 元作为未决预留；若旧记录迁移后的总敞口已超上限，则锁定账本

预算接口字段：

| 字段 | 含义 |
| --- | --- |
| `cap_rmb` | 应用总上限，最多 4.8 元 |
| `spent_rmb` | 通过严格 usage 校验的已结算峰价估计 |
| `reserved_rmb` | 仍未决或结果未知的整额预留 |
| `exposure_rmb` | 总承诺金额，等于 spent + reserved；进度/用量展示应使用此字段 |
| `remaining_rmb` | max(0, cap − exposure) |
| `calls_reserved` | 历史获准并已预留的调用数，包含已结算调用 |
| `per_call_reserve_rmb` | 当前每次调用必须预留的 2.113536 元 |
| `context_token_ceiling` | 1,048,576 |
| `blocked` | 账本完整性锁定状态 |

这些金额是应用的峰价结算与风险预留，不是供应商账单。应用门禁基于固定模型、官方上下文上限、输出限制及已核对价格；无法覆盖该密钥在其他程序中的消费，供应商未来涨价也需重新核对。正式开放付费前必须确认官网价格仍不高于预算配置；不会自动充值、切模型或提高预算。

## 验证

```bash
.venv/bin/pytest backend/tests/test_agent.py -q
```

测试不构造真实 provider 客户端，覆盖真实 DeepAgents 工具链、仅一个子 Agent、无默认文件/shell 工具、框架工具漂移拒绝、猜测句柄拒绝、澄清/不支持问法、取消、共享账本、并发原子预留、重启不清零、完整上下文预留、严格 usage 结算、模糊失败保留整额、两个未知后停止、旧账本保守迁移、usage 超界锁定、预算不足时完全不发请求。

本次预算改动仅运行针对性检查，没有重新运行全套回归：

```bash
.venv/bin/pytest backend/tests/test_agent.py -k 'budget or ledger or provider_never' -q
```

结果：24 passed，21 deselected；未读取真实密钥、未创建真实供应商客户端、未发起付费模型请求。

## 官方参考

- [DeepAgents Harness Profiles](https://docs.langchain.com/oss/python/deepagents/profiles)
- [DeepAgents Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [DeepAgents 源码及实际工具排除行为](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/_tool_exclusion.py)
- [DeepSeek 人民币模型价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [DeepSeek Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)
