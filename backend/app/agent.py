"""Real DeepAgents orchestration with a deliberately tiny capability surface.

Mock mode replaces only the chat model, not the graph, tools, or query gateway.
Never load .env here: the service owner controls explicit configuration loading.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import os
import re
import threading
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

# Set before framework imports; every invocation also disables tracing context.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_TRACING"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langsmith import tracing_context
from pydantic import ConfigDict, Field

from .budget import MAX_OUTPUT_TOKENS, BudgetGuard, shared_budget

MAIN_TOOLS = frozenset({"get_metric_catalog", "query_metrics", "task"})
ANALYSIS_TOOLS = frozenset({"analyze_result"})
FORBIDDEN_DEFAULT_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute", "write_todos"}
)
_PROFILE_LOCK = threading.Lock()


class AgentBoundaryError(RuntimeError):
    code = "AGENT_BOUNDARY_ERROR"


class AgentCancelled(RuntimeError):
    code = "CANCELLED"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise AgentCancelled("运行已取消")


class GuardedModel(BaseChatModel):
    """Only entry to the provider; clones keep the very same ledger and limiter."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    inner: Any = Field(exclude=True, repr=False)
    budget: Any = Field(exclude=True, repr=False)
    cancelled: Any = Field(exclude=True, repr=False)
    run_counter: Any = Field(exclude=True, repr=False)
    role: str
    live: bool = False
    allowed_tools: frozenset[str]
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str = "sales-guarded:locked"

    @property
    def _llm_type(self) -> str:
        return "sales-guarded"

    @property
    def _identifying_params(self) -> dict[str, str]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> GuardedModel:
        schemas = [convert_to_openai_tool(t) for t in tools]
        names = {t["function"]["name"] for t in schemas}
        if names != self.allowed_tools:
            raise AgentBoundaryError(f"Unexpected {self.role} model tool inventory: {sorted(names)}")
        bound = self.inner.bind_tools(tools, **kwargs)
        return self.model_copy(update={"inner": bound, "tool_schemas": schemas})

    def _before(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> str | None:
        _check_cancelled(self.cancelled)
        if kwargs:
            # Provider request overrides must never change token bounds/model/tool set.
            unexpected = set(kwargs) - {"stop"}
            if unexpected:
                raise AgentBoundaryError("Unsupported model request override")
        if any(not isinstance(m.content, str) for m in messages):
            raise AgentBoundaryError("Only text model inputs are supported")
        with self.run_counter["lock"]:
            self.run_counter["calls"] += 1
            if self.run_counter["calls"] > 12:
                raise AgentBoundaryError("本次运行模型调用次数已达上限")
        if not self.live:
            return None
        payload = {
            "model": "deepseek-flash",
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "tools": self.tool_schemas,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "thinking": {"type": "disabled"},
        }
        return self.budget.reserve(payload, self.role)

    def _after(self, reservation: str | None, message: AIMessage) -> ChatResult:
        if reservation:
            self.budget.finish(reservation, message.usage_metadata)
        _check_cancelled(self.cancelled)
        if any(call["name"] not in self.allowed_tools for call in message.tool_calls):
            raise AgentBoundaryError("Model attempted an unapproved tool")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        reservation = self._before(messages, kwargs)
        try:
            with tracing_context(enabled=False):
                response = self.inner.invoke(messages, config={"callbacks": []}, stop=stop)
        except BaseException:
            if reservation:
                self.budget.finish(reservation, None, "failed")
            raise
        return self._after(reservation, response)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        reservation = self._before(messages, kwargs)
        try:
            with tracing_context(enabled=False):
                response = await self.inner.ainvoke(messages, config={"callbacks": []}, stop=stop)
        except BaseException:
            if reservation:
                self.budget.finish(reservation, None, "failed")
            raise
        return self._after(reservation, response)


class ToolBoundary(AgentMiddleware):
    """Defense in depth: validate dispatch independently of model visibility."""

    def __init__(self, allowed: frozenset[str], cancelled: Callable[[], bool]):
        self.allowed = allowed
        self.cancelled = cancelled

    def _check(self, request: Any) -> None:
        _check_cancelled(self.cancelled)
        call = request.tool_call
        if call["name"] not in self.allowed:
            raise AgentBoundaryError("Unapproved tool dispatch")
        if call["name"] == "task" and call["args"].get("subagent_type") != "analysis":
            raise AgentBoundaryError("Only the analysis subagent is permitted")

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._check(request)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._check(request)
        return await handler(request)


def _harden_graph(graph: Any, allowed: frozenset[str]) -> list[str]:
    """Inspect real dispatch inventory, remove known defaults, reject any drift.

    DeepAgents exclusions filter model visibility but retain ToolNode handlers.
    This extra check physically removes those handlers from this pinned graph.
    Any version/API change fails construction instead of silently widening access.
    """
    node = graph.get_graph().nodes.get("tools")
    tool_node = getattr(node, "data", None)
    inventory = getattr(tool_node, "tools_by_name", None)
    if not isinstance(inventory, dict):
        raise AgentBoundaryError("Cannot inspect compiled graph tool inventory")
    if set(inventory) - allowed - FORBIDDEN_DEFAULT_TOOLS:
        raise AgentBoundaryError("Unexpected tool registered by DeepAgents")
    for name in FORBIDDEN_DEFAULT_TOOLS:
        inventory.pop(name, None)
    if set(inventory) != allowed:
        raise AgentBoundaryError("Compiled graph capability inventory mismatch")
    return sorted(inventory)


def _profile() -> None:
    with _PROFILE_LOCK:
        register_harness_profile(
            "sales-guarded:locked",
            HarnessProfile(
                excluded_tools=FORBIDDEN_DEFAULT_TOOLS,
                excluded_middleware={"SummarizationMiddleware"},
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )


def _last_tool(messages: list[BaseMessage], name: str) -> Any:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == name:
            try:
                return json.loads(message.content)
            except (ValueError, TypeError):
                return {"text": message.content}
    return None


def _month_period(year: int, month: int) -> dict[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return {"start": start.isoformat(), "end": end.isoformat(), "date_field": "order_date"}


def mock_plan(message: str, previous: dict | None, catalog: dict) -> tuple[dict | None, str]:
    """A small published demo grammar, deliberately not a pretend general LLM."""
    text = message.strip().lower()
    if any(
        word in text
        for word in ("退款", "已支付", "支付额", "净利润", "取消率", "到账", "refund", "net profit")
    ):
        return None, "UNSUPPORTED_QUERY: 数据不包含付款、退款、取消或企业净利润，无法计算该指标"
    if any(
        word in text for word in ("sql", "select ", "管理员", "忽略", "password", "api_key", ".env", "shell")
    ):
        return None, "UNSUPPORTED_QUERY: 仅支持当前可见范围内的受控销售指标查询"
    metrics = []
    for metric, words in [
        ("sales_amount", ("销售额", "销售趋势", "销售情况", "sales")),
        ("order_count", ("订单数", "订单量")),
        ("units_sold", ("销量", "销售数量")),
        ("avg_order_value", ("客单价", "平均订单金额")),
        ("gross_profit", ("毛利",)),
    ]:
        if any(word in text for word in words):
            metrics.append(metric)
    followup = previous is not None and any(
        word in text
        for word in ("按月", "按类别", "按门店", "前十", "前10", "柱状", "折线", "表格", "换成", "只看")
    )
    if not metrics and not followup:
        return (
            None,
            "NEEDS_CLARIFICATION: 请选择销售额、订单数、销量、平均订单金额或商品毛利，并说明期间；离线模式支持页面示例问法",
        )
    if len(metrics) > 2:
        return None, "NEEDS_CLARIFICATION: 一次最多选择两个指标，请选出本次要查看的指标"
    coverage = catalog.get("coverage", {"start": "2025-01-01", "end": "2026-01-01"})
    last = date.fromisoformat(coverage["end"]) - timedelta(days=1)
    default_start = max(date(last.year, 1, 1), date.fromisoformat(coverage["start"]))
    query = (
        copy.deepcopy(previous)
        if followup
        else {
            "schema_version": "queryspec_v1",
            "dataset_id": "contoso_v2",
            "metric_version": "metrics_v1",
            "metrics": metrics,
            "period": {
                "start": default_start.isoformat(),
                "end": coverage["end"],
                "date_field": "order_date",
            },
            "comparison": None,
            "group_by": [],
            "filters": [],
            "analysis": "summary",
            "sort": None,
            "limit": 20,
            "chart": "table",
        }
    )
    if metrics:
        query["metrics"] = metrics
    for name in ("base_result_id",):
        query.pop(name, None)
    # Explicit month(s) or one year; otherwise disclose the dataset-latest year.
    months = re.findall(r"(20\d{2})\s*[年\-/]\s*(\d{1,2})\s*(?:月|(?=\D|$))", text)
    if months:
        if len(months) > 2:
            return None, "NEEDS_CLARIFICATION: 请指定一个查询月份或两个比较月份"
        try:
            query["period"] = _month_period(*map(int, months[0]))
            if len(months) == 2:
                query["comparison"] = _month_period(*map(int, months[1]))
        except ValueError:
            return None, "NEEDS_CLARIFICATION: 请使用有效的年份和月份"
        if len(months) == 1:
            short_compare = re.search(r"(?:比|对比|比较|与|和|较)\s*(\d{1,2})月", text)
            if short_compare:
                try:
                    query["comparison"] = _month_period(int(months[0][0]), int(short_compare.group(1)))
                except ValueError:
                    return None, "NEEDS_CLARIFICATION: 请使用有效的比较月份"
    else:
        years = re.findall(r"(20\d{2})\s*年", text)
        if years:
            y = int(years[0])
            query["period"] = {"start": f"{y}-01-01", "end": f"{y + 1}-01-01", "date_field": "order_date"}
    if re.search(r"20\d{2}-\d{2}-\d{2}", text):
        return None, "NEEDS_CLARIFICATION: 离线模式目前支持整年或整月，请按页面示例明确年月"
    if any(word in text for word in ("今年", "上月", "最近一个月", "去年", "同比", "环比")):
        return None, "NEEDS_CLARIFICATION: 离线模式请明确写出查询或比较年月，不能把模拟数据年份当作当前年份"
    for word, dimension in [
        ("类别", "category"),
        ("商品", "product"),
        ("门店", "store"),
        ("客户国家", "customer_country"),
        ("门店国家", "store_country"),
    ]:
        if ("按" + word) in text or ("哪些" + word) in text:
            query["group_by"] = [dimension]
            query["chart"] = "bar"
    if "按月" in text or "趋势" in text:
        query["group_by"] = ["month"]
        query["chart"] = "line"
    if any(word in text for word in ("前十", "前10")):
        query["limit"] = 10
        query["sort"] = {"field": query["metrics"][0], "direction": "desc"}
    elif re.search(r"前\s*\d+", text):
        query["limit"] = int(re.search(r"前\s*(\d+)", text).group(1))
        query["sort"] = {"field": query["metrics"][0], "direction": "desc"}
    requested_chart = None
    for word, chart in [("柱状", "bar"), ("折线", "line"), ("表格", "table"), ("瀑布", "waterfall")]:
        if word in text:
            query["chart"] = chart
            requested_chart = chart
    if query.get("comparison"):
        # A limit/filter follow-up retains a reconciled contribution query. A new
        # time grouping instead asks for a period comparison, not a contribution.
        keep_contribution = (
            followup
            and query.get("analysis") == "contribution"
            and not set(query["group_by"]) & {"day", "week", "month"}
            and not set(query["metrics"]) & {"order_count", "avg_order_value"}
        )
        query["analysis"] = (
            "contribution"
            if requested_chart == "waterfall"
            or keep_contribution
            or any(w in text for w in ("贡献", "哪些", "为什么", "原因", "来自"))
            else "compare"
        )
        if query["analysis"] == "contribution" and not query["group_by"]:
            query["group_by"] = ["category"]
            query["chart"] = requested_chart or "bar"
    elif any(w in text for w in ("对比", "比较", "下降", "增长", "为什么", "原因", "贡献")):
        return None, "NEEDS_CLARIFICATION: 请明确两个比较期间，例如：2025年9月比8月销售额变化，按类别看"
    if any(w in text for w in ("换成", "只看")) and "门店" in text:
        matched = [
            s
            for s in catalog.get("stores", [])
            if str(s.get("key")) in text or s.get("label", "\x00") in text
        ]
        if len(matched) != 1:
            return None, "NEEDS_CLARIFICATION: 请从可见门店列表中明确选择一家门店"
        query["filters"] = [f for f in query.get("filters", []) if f["dimension"] != "store"] + [
            {"dimension": "store", "op": "eq", "values": [str(matched[0]["key"])]}
        ]
    return query, ""


class MockSalesModel(BaseChatModel):
    """Deterministic tool-calling model used only for zero-cost local rehearsal."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    role: str
    message: str = ""
    previous: dict | None = None
    catalog: dict = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "sales-mock"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> MockSalesModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        def call(name: str, args: dict) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": f"mock_{name}_{len(messages)}", "type": "tool_call"}
                ],
            )

        if self.role == "analysis":
            analysis = _last_tool(messages, "analyze_result")
            if analysis is None:
                human = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "{}")
                payload = json.loads(human)
                reply = call("analyze_result", {"result_id": payload["result_id"]})
            else:
                reply = AIMessage(content=_json(analysis))
        elif _last_tool(messages, "get_metric_catalog") is None:
            reply = call("get_metric_catalog", {})
        else:
            query_result = _last_tool(messages, "query_metrics")
            if query_result is None:
                query, clarification = mock_plan(self.message, self.previous, self.catalog)
                reply = call("query_metrics", {"spec": query}) if query else AIMessage(content=clarification)
            elif not query_result.get("result_id"):
                reply = AIMessage(content="查询没有返回可分析结果，请检查查询范围")
            elif _last_tool(messages, "task") is None:
                reply = call(
                    "task",
                    {
                        "subagent_type": "analysis",
                        "description": _json(
                            {"result_id": query_result["result_id"], "question": self.message}
                        ),
                    },
                )
            else:
                reply = AIMessage(content="已完成授权聚合与受控分析；指标口径、期间及结果见下方")
        return ChatResult(generations=[ChatGeneration(message=reply)])


MAIN_PROMPT = """你是模拟销售数据分析助手。先用 get_metric_catalog 核对指标口径与数据覆盖，再提出一次 query_metrics 的 QuerySpec 查询。只用固定五指标，最多两个指标与两个维度。身份/门店授权由服务器决定，不能写入权限字段。QuerySpec 不是 SQL。
期间左闭右开。用户未给期间时需澄清，除非明确请求数据集最近一年。语义不清先问一个问题；没有付款、退款、取消和企业净利润数据。完成 query_metrics 后，把返回 result_id 和问题以 JSON 字符串交给 task，subagent_type 必须为 analysis。仅分析已返回句柄。分析完成后用中文给简短观察，不证明因果，不把模拟数据当作真实经营数据。任何工具错误就停止，不猜数字。
QuerySpec 字段为 schema_version=queryspec_v1,dataset_id=contoso_v2,metric_version=metrics_v1,metrics,period={start,end,date_field:order_date},comparison=null或同形period,group_by,filters=[{dimension,op:eq或in,values:[键]}],analysis=summary|compare|contribution,sort=null或{field,direction:asc|desc},limit=1..100,chart=table|line|bar|waterfall|none。指标为sales_amount,order_count,units_sold,avg_order_value,gross_profit。维度为day,week,month,product,category,store,customer_country,store_country。AOV不能按商品/类别分组筛选。贡献只能用于可加总指标。不要调用未列明工具。"""
ANALYSIS_PROMPT = """你是唯一受控分析子 Agent。只可调用 analyze_result(result_id) 读取本次运行已授权的聚合分析。不能查询数据库、发起新查询、写文件、联网或执行代码。按工具给出的确定性结果用中文概括2至4条观察，明确模拟数据和不能推断因果。不要编造数值。输入是含result_id和question的JSON。"""


def _deepseek_model(api_key: str) -> Any:
    from langchain_openai import ChatOpenAI

    class DeepSeekChatOpenAI(ChatOpenAI):
        """Keep current OpenAI SDK aliases from weakening DeepSeek output caps."""

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> dict:
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            if payload.get("model") != "deepseek-flash" or payload.get("stream"):
                raise AgentBoundaryError("Unexpected provider model or streaming mode")
            payload.pop("max_completion_tokens", None)
            payload["max_tokens"] = MAX_OUTPUT_TOKENS
            extra = dict(payload.get("extra_body") or {})
            extra["thinking"] = {"type": "disabled"}
            extra.pop("max_completion_tokens", None)
            extra.pop("max_tokens", None)
            payload["extra_body"] = extra
            return payload

    return DeepSeekChatOpenAI(
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key=api_key,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
        max_retries=0,
        timeout=30,
        extra_body={"thinking": {"type": "disabled"}},
        streaming=False,
        use_responses_api=False,
    )


def _model(
    role: str,
    live: bool,
    budget: BudgetGuard,
    counter: dict,
    cancelled: Callable[[], bool],
    message: str,
    previous: dict | None,
    catalog: dict,
    api_key: str | None = None,
) -> GuardedModel:
    if live:
        # Read only the explicitly user-configured key at runtime; never log it.
        # This branch is not executed by development checks or mock mode.
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise AgentBoundaryError("AGENT_MODE=live requires a manually configured DEEPSEEK_API_KEY")
        inner = _deepseek_model(key)
    else:
        inner = MockSalesModel(role=role, message=message, previous=previous, catalog=catalog)
    return GuardedModel(
        inner=inner,
        budget=budget,
        cancelled=cancelled,
        run_counter=counter,
        role=role,
        live=live,
        allowed_tools=MAIN_TOOLS if role == "main" else ANALYSIS_TOOLS,
    )


async def _callback(fn: Callable, *args: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    result = await asyncio.to_thread(fn, *args)
    return await result if inspect.isawaitable(result) else result


def build_runtime(
    message: str,
    previous_query: dict | None,
    catalog: dict,
    query_callback: Callable,
    analysis_callback: Callable,
    cancelled: Callable[[], bool],
    *,
    mode: str | None = None,
    budget: BudgetGuard | None = None,
    api_key: str | None = None,
) -> tuple[Any, dict]:
    selected_mode = mode or os.environ.get("AGENT_MODE", "mock")
    if selected_mode not in {"mock", "live"}:
        raise AgentBoundaryError("AGENT_MODE must be mock or live")
    live = selected_mode == "live"
    # UI catalog may contain session identity and budget diagnostics. The model
    # receives only the business catalog; authority stays in gateway closures.
    catalog = {
        k: copy.deepcopy(v)
        for k, v in catalog.items()
        if k
        in {
            "metrics",
            "dimensions",
            "stores",
            "categories",
            "countries",
            "coverage",
            "dataset_id",
            "dataset_version",
            "metric_version",
            "business_date",
            "currency",
        }
    }
    if previous_query is not None:
        previous_query = {
            k: copy.deepcopy(v)
            for k, v in previous_query.items()
            if k
            in {
                "schema_version",
                "dataset_id",
                "metric_version",
                "metrics",
                "period",
                "comparison",
                "group_by",
                "filters",
                "analysis",
                "sort",
                "limit",
                "chart",
                "base_result_id",
            }
        }
    ledger = budget or shared_budget()
    _profile()
    state: dict[str, Any] = {
        "result": None,
        "analysis": None,
        "query_spec": None,
        "queries": 0,
        "tools_called": [],
        "model_mode": selected_mode,
    }
    counter = {"calls": 0, "lock": threading.Lock()}
    handles: set[str] = set()
    query_lock = asyncio.Lock()

    @tool
    async def get_metric_catalog() -> dict:
        """Read the current authorized metric definitions, dimensions and data coverage."""
        _check_cancelled(cancelled)
        state["tools_called"].append("get_metric_catalog")
        return copy.deepcopy(catalog)

    @tool
    async def query_metrics(spec: dict[str, Any]) -> dict:
        """Submit a QuerySpec to the validating authorization gateway; never SQL or identity fields."""
        _check_cancelled(cancelled)
        async with query_lock:
            if state["queries"] >= 1:
                raise AgentBoundaryError("每次运行只允许一次聚合查询")
            state["queries"] += 1
            result = await _callback(query_callback, spec)
            _check_cancelled(cancelled)
            if not isinstance(result, dict) or not isinstance(result.get("result_id"), str):
                raise AgentBoundaryError("Query gateway did not return an immutable result handle")
            handles.add(result["result_id"])
            state["result"] = result
            state["query_spec"] = copy.deepcopy(spec)
            state["tools_called"].append("query_metrics")
            # The main model sees only an opaque handle and small safe metadata.
            return {
                "result_id": result["result_id"],
                "status": result.get("status", "SUCCEEDED"),
                "row_count": result.get("row_count"),
                "currency": "USD",
            }

    @tool
    async def analyze_result(result_id: str) -> dict:
        """Read deterministic statistics from a result produced and authorized in this run."""
        _check_cancelled(cancelled)
        if result_id not in handles:
            raise AgentBoundaryError("Result handle is not authorized for this run")
        analysis = await _callback(analysis_callback, result_id)
        _check_cancelled(cancelled)
        state["analysis"] = analysis
        state["tools_called"].append("analyze_result")
        return analysis

    analysis_model = _model(
        "analysis", live, ledger, counter, cancelled, message, previous_query, catalog, api_key
    )
    analysis_graph = create_deep_agent(
        model=analysis_model,
        tools=[analyze_result],
        system_prompt=ANALYSIS_PROMPT,
        subagents=[],
        middleware=[ToolBoundary(ANALYSIS_TOOLS, cancelled)],
        name="analysis",
    )
    analysis_inventory = _harden_graph(analysis_graph, ANALYSIS_TOOLS)
    main_model = _model("main", live, ledger, counter, cancelled, message, previous_query, catalog, api_key)
    prompt = MAIN_PROMPT + "\n上一条已验证业务查询（不含身份授权）:" + _json(previous_query)
    graph = create_deep_agent(
        model=main_model,
        tools=[get_metric_catalog, query_metrics],
        system_prompt=prompt,
        subagents=[
            {
                "name": "analysis",
                "description": "仅解释本次已授权聚合结果，不可访问查询工具",
                "runnable": analysis_graph,
            }
        ],
        middleware=[ToolBoundary(MAIN_TOOLS, cancelled)],
        name="sales-main",
    )
    main_inventory = _harden_graph(graph, MAIN_TOOLS)
    dispatch = graph.get_graph().nodes["tools"].data.tools_by_name["task"]
    registered_subagents = inspect.getclosurevars(dispatch.coroutine).nonlocals.get("subagent_graphs")
    if not isinstance(registered_subagents, dict) or set(registered_subagents) != {"analysis"}:
        raise AgentBoundaryError("Actual subagent dispatch registry is not exactly analysis")
    for candidate in (graph, analysis_graph):
        if any("summari" in name.lower() for name in candidate.nodes):
            raise AgentBoundaryError("Unexpected summarization middleware")
    state["runtime"] = {
        "framework": "deepagents",
        "main_tools": main_inventory,
        "analysis_tools": analysis_inventory,
        "subagents": ["analysis"],
        "model_mode": selected_mode,
        "langsmith_tracing": False,
        "max_model_calls_per_run": 12,
    }
    state["_counter"] = counter
    state["_budget"] = ledger
    return graph, state


async def run_agent(
    message: str,
    previous_query: dict | None,
    catalog: dict,
    query_callback: Callable,
    analysis_callback: Callable,
    cancelled: Callable[[], bool],
    *,
    mode: str | None = None,
    api_key: str | None = None,
) -> dict:
    graph, state = build_runtime(
        message,
        previous_query,
        catalog,
        query_callback,
        analysis_callback,
        cancelled,
        mode=mode,
        api_key=api_key,
    )
    _check_cancelled(cancelled)
    with tracing_context(enabled=False):
        output = await graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"recursion_limit": 30, "callbacks": [], "max_concurrency": 1},
        )
    _check_cancelled(cancelled)
    answer = output["messages"][-1].content
    if state["model_mode"] == "mock" and state["query_spec"]:
        p = state["query_spec"]["period"]
        answer = f"已分析 {p['start']} 至 {p['end']}（不含结束日）的模拟销售数据；口径及确定性分析见结果"
    status = "SUCCEEDED" if state["result"] else "NEEDS_CLARIFICATION"
    if answer.startswith("UNSUPPORTED_QUERY:"):
        status = "UNSUPPORTED_QUERY"
    if state["result"] is not None and state["analysis"] is None:
        raise AgentBoundaryError("Analysis subagent did not complete the required authorized analysis")
    state["runtime"]["model_calls"] = state.pop("_counter")["calls"]
    state["runtime"]["budget"] = state.pop("_budget").snapshot()
    return {
        "status": status,
        "answer": answer.removeprefix("NEEDS_CLARIFICATION: ").removeprefix("UNSUPPORTED_QUERY: "),
        "query_spec": state["query_spec"],
        "result": state["result"],
        "analysis": state["analysis"],
        "runtime": state["runtime"],
        "tools_called": state["tools_called"],
    }


def runtime_smoke_check() -> dict:
    """Build the exact production topology with local models and inspect all tools."""
    _, state = build_runtime("", None, {}, lambda _: {}, lambda _: {}, lambda: False, mode="mock")
    return state["runtime"]
