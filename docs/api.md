# API 合同

所有接口都在 `/api` 下。前端 5173 的 `/api` 请求由 Vite 转发到 8000。除健康检查与登录外，必须带 HttpOnly、SameSite=Strict 的 `sales_session` cookie。两账号仅用于本地作品集演示：`admin / demo123` 和 `analyst / demo123`。不要把此演示认证直接部署公网。

| 接口 | 合同 |
|---|---|
| GET /health | 数据/服务准备状态，mock/live 模式 |
| POST /auth/login | `{username,password}`；设置 cookie，返回账号和授权门店 |
| POST /auth/logout | 撤销当前会话 |
| GET /me | `{username,display_name,scope_label,allowed_store_ids,scope_version}` |
| GET /catalog | 指标、维度、可选键、数据版本、coverage、mock 提示、预算余额 |
| GET /dashboard?start=2025-01-01&end=2026-01-01 | 五个 KPI、月度销售额/订单数、类别销售额；日期左闭右开 |
| POST /conversations | `{id,conversation_id,state_version:0}` |
| GET /conversations/{id} | 当前状态版本、latest_run_id、已验证 current_query、history |
| POST /conversations/{id}/runs | `{message,client_request_id,expected_state_version}`；202 返回 run_id/state_version |
| GET /runs/{id} | status/progress_stage、message/error_code、result_id/result、runtime、presentation |
| POST /runs/{id}/cancel | 幂等；取消请求进入 cancelling，工作实际结束后才 cancelled |
| GET /results/{id} | 重新检查归属、范围摘要和 24 小时有效期 |
| GET /results/{id}/csv | 当前授权结果的展示聚合行；UTF-8 BOM，安全转义文本公式 |

运行终态为 `succeeded / no_data / needs_clarification / unsupported / failed / cancelled / expired`，非终态为 `queued / querying / analyzing / cancelling`。需要澄清时，在相同会话提交下一条明确的问题即可。服务重启后，未结束的运行会明确失败，原成功结果不替代它。

每个会话只有一个活动运行。`client_request_id` 重复时返回同一运行，不能复用于不同问题；版本过旧返回 409。同一问题重新提交必须使用新 ID。刷新恢复流程：GET 会话 → GET latest_run_id → 展示其结果或继续轮询。

## 结果

金额和数量均为十进制字符串，空均值为 null。前端不能自己求 KPI、百分比或贡献。

- `id / run_id / query / period / comparison / metrics`
- `columns: [{key,label,kind}]`，kind 为 dimension/money/count/percent
- `rows: [{category, sales_amount, comparison_sales_amount, delta_sales_amount, change_pct_sales_amount}]`（实际字段由查询决定）
- `totals / comparison_totals / deltas / change_pct`
- `chart: {type,x,y,series,waterfall?}`；waterfall 是 `{name,value,kind:'total'|'delta'}` 数组
- `observations` 来自普通程序的确定性计算，模型不能覆盖数字
- `row_count / displayed_row_count / hidden_row_count / truncated / complete / no_data`
- `metadata` 包含模拟属性、官方来源、版本、scope_label、源期间、精度说明和 query_hash
- `sha256 / generated_at / expires_at`

百分比 10.3 代表 10.3%，不是 0.103。Top N 仅截断展示，不改变 totals。贡献场景有隐藏组时补“其他分组（合计）”，并验证所有 delta 之和等于总 delta。导出对应实际展示的聚合表，不包含客户明细；图表切换在前端复用当前结果，不触发查询。

## 安全边界

QuerySpec 拒绝未知字段、SQL、身份和门店权限等字段；表达式/连接/排序来自代码白名单，值全部绑定参数。只读数据库角色和每事务的门店 RLS 同时约束 SQL。导入角色、应用控制表角色、查询只读角色分离。会话、运行与结果都重新校验 owner；猜到 UUID 不会授予访问权。预算不取模型入参。

## 纯展示聊天复用

独立短句如“换成表格”“请把结果改成柱状图”直接走受控展示路径；含期间、指标或筛选修改的消息不匹配此路径。复用运行仍有新 run_id，但 result_id 保持原值，原结果内容和 SHA 不变。

GET /runs/{id} 新增 presentation，可空或为 `{chart_type, reused_result_id}`。前端只有在 reused_result_id 等于当前 result.id 时应用该展示类型，不修改原始 result.chart/query。运行 runtime.kind 为 presentation_reuse，model_calls 和 aggregate_queries 均为 0；认证与结果读取仍查询控制表。

复用前重新检查归属、当前授权范围、原到期时间、数据/指标版本，以及目标图表兼容性。无历史返回 needs_clarification；过期或版本变化返回 expired；权限/归属变化返回 failed。图表选择歧义或不兼容返回 needs_clarification，仅在服务端保留已授权待澄清句柄，下一次选择时仍重新授权。不会重置结果 TTL，也不会新建统计快照。

## 最终复盘与报告合同

已有 API 保持兼容。`POST /conversations/{id}/runs` 新增可选 `review`，采用固定 `sales_review_v1` 模板；`GET /runs/{id}` 新增 `kind` 和 `review`。整体、类别、门店及可选一次商品下钻最多执行 4 个受控 QuerySpec，复用原取消与轮询生命周期。规则提示和事实数字由确定性程序计算，模型不做算术，也不能将提示解释为因果或统计异常。

独立 `/reports` API 支持保存、列表、打开、注释修订、回收站/恢复及 Markdown/HTML 导出。报告包含完整冻结聚合快照，与会话结果 24 小时 TTL 无依赖；报告事实不可修改，重新运行须保存新报告。所有操作重新校验本人归属及当前门店范围是否包含整份原快照范围。

完整字段、状态、限制及错误约定以 [最终前后端合同](final-api-contract.md) 为准。
