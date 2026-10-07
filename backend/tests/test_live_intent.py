"""Exercise the live graph with a local scripted provider; never contact an API."""

import copy
import json
import time
from decimal import Decimal
from uuid import uuid4

import app.agent as agent
import pytest
from app.schemas import QuerySpec, normalize_query_intent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, SecretStr, ValidationError

CATALOG = {"coverage": {"start": "2023-01-01", "end": "2026-01-01"}}
WATERFALL = {
    "metrics": ["sales_amount"],
    "period": {"start": "2025-09-01", "end": "2025-10-01"},
    "comparison": {"start": "2025-08-01", "end": "2025-09-01"},
    "group_by": ["category"],
    "analysis": "compare",
    "chart": "waterfall",
}
MONTHLY = {
    "metrics": ["sales_amount"],
    "period": {"start": "2025-01-01", "end": "2026-01-01"},
    "group_by": ["month"],
    "analysis": "summary",
    "chart": "line",
}


class ScriptedProvider(BaseChatModel):
    """Inject model-produced arguments without using the offline demo parser."""

    spec: dict = Field(default_factory=dict)
    query_args: dict | None = None
    clarification: str | None = None

    @property
    def _llm_type(self):
        return "local-scripted-provider"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        def call(name, args):
            return AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"{name}-{len(messages)}"}],
            )

        question = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if question.startswith('{"result_id":'):
            if agent._last_tool(messages, "analyze_result") is None:
                reply = call("analyze_result", {"result_id": json.loads(question)["result_id"]})
            else:
                reply = AIMessage(content="已核对授权分析结果，模拟数据不能推断因果")
        elif agent._last_tool(messages, "get_metric_catalog") is None:
            reply = call("get_metric_catalog", {})
        elif self.clarification:
            reply = AIMessage(content=self.clarification)
        elif agent._last_tool(messages, "query_metrics") is None:
            reply = call("query_metrics", self.query_args if self.query_args is not None else {"spec": self.spec})
        elif any(isinstance(m, ToolMessage) and m.status == "error" for m in messages):
            # Reproduce an unhelpful model response after a tool validation error.
            reply = AIMessage(content="NEEDS_CLARIFICATION: 工具出错，请授权我调整查询")
        elif agent._last_tool(messages, "task") is None:
            result = agent._last_tool(messages, "query_metrics")
            reply = call(
                "task",
                {
                    "subagent_type": "analysis",
                    "description": json.dumps({"result_id": result["result_id"], "question": question}),
                },
            )
        else:
            reply = AIMessage(content="已完成贡献分析")
        reply.usage_metadata = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.fixture(autouse=True)
def no_real_provider(tmp_path, monkeypatch):
    import langchain_openai

    monkeypatch.setenv("LLM_BUDGET_PATH", str(tmp_path / "isolated-budget.sqlite3"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("A real model client must never be created in this test")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", forbidden)
    monkeypatch.setattr(agent, "mock_plan", forbidden)


def fake_provider(monkeypatch, spec, **kwargs):
    monkeypatch.setattr(agent, "_deepseek_model", lambda key: ScriptedProvider(spec=spec, **kwargs))


async def run_fake(message, previous, query, analysis):
    return await agent.run_agent(
        message,
        previous,
        CATALOG,
        query,
        analysis,
        lambda: False,
        mode="live",
        api_key="LOCAL-TEST-PLACEHOLDER-NOT-A-CREDENTIAL",
    )


@pytest.mark.parametrize("previous", [None, MONTHLY, {**MONTHLY, "chart": "table"}])
@pytest.mark.parametrize("metric", ["sales_amount", "units_sold", "gross_profit"])
async def test_live_graph_normalizes_complete_waterfall_intent(monkeypatch, previous, metric):
    raw = copy.deepcopy({**WATERFALL, "metrics": [metric], "limit": 3})
    before = copy.deepcopy(raw)
    fake_provider(monkeypatch, raw)
    calls = []

    def query(spec):
        validated = QuerySpec.model_validate(spec)
        assert validated.analysis == "contribution"
        assert validated.chart == "waterfall"
        assert spec == {**before, "analysis": "contribution"}
        calls.append("query")
        return {"result_id": "authorized-waterfall", "row_count": 3}

    def analysis(result_id):
        assert result_id == "authorized-waterfall"
        calls.append("analysis")
        return {"observations": ["分组变化已对账；模拟数据不证明因果"]}

    result = await run_fake("2025年9月比8月销售额，按类别用瀑布图", previous, query, analysis)
    assert result["status"] == "SUCCEEDED"
    assert result["query_spec"] == {**before, "analysis": "contribution"}
    assert result["runtime"]["model_mode"] == "live"
    assert result["runtime"]["model_calls"] == 6
    assert calls == ["query", "analysis"]
    assert raw == before


@pytest.mark.parametrize(
    "updates",
    [
        {"comparison": None},
        {"group_by": []},
        {"group_by": ["month"]},
        {"metrics": ["sales_amount", "units_sold"]},
        {"metrics": ["order_count"]},
        {"metrics": ["avg_order_value"]},
        {"metrics": ["sales_amount", "sales_amount"]},
        {"period": {"start": "2025-09-01", "end": "2025-09-01"}},
        {"sort": {"field": "untrusted_sql"}},
        {"allowed_store_ids": [999]},
        {"analysis": "summary"},
    ],
)
async def test_live_invalid_spec_is_failure_not_permission_or_ambiguity(monkeypatch, updates):
    raw = {**WATERFALL, **updates}
    fake_provider(monkeypatch, raw)

    def query(spec):
        assert spec == raw  # Invalid input must not be partially rewritten.
        QuerySpec.model_validate(spec)
        raise AssertionError("Invalid spec must not reach aggregation")

    def forbidden(_):
        raise AssertionError("Invalid query cannot be analyzed")

    with pytest.raises(ValidationError):
        await run_fake("请分析瀑布图", None, query, forbidden)


async def test_live_missing_tool_argument_is_failure_not_clarification(monkeypatch):
    fake_provider(monkeypatch, WATERFALL, query_args={})

    def forbidden(_):
        raise AssertionError("Malformed tool call must not execute the callback")

    with pytest.raises(agent.AgentBoundaryError, match="工具"):
        await run_fake("2025年9月比8月销售额，按类别用瀑布图", None, forbidden, forbidden)


async def test_live_genuine_ambiguity_still_requests_missing_information(monkeypatch):
    fake_provider(monkeypatch, {}, clarification="NEEDS_CLARIFICATION: 请说明两个比较期间")

    def forbidden(_):
        raise AssertionError("Missing information must not issue a query")

    result = await run_fake("销售额瀑布图", None, forbidden, forbidden)
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["result"] is None
    assert result["tools_called"] == ["get_metric_catalog"]
    assert result["answer"] == "请说明两个比较期间"


@pytest.mark.parametrize("chart", ["bar", "table", "line", "none"])
def test_normalization_does_not_promote_other_chart_intents(chart):
    raw = {**WATERFALL, "chart": chart}
    assert normalize_query_intent(raw) == raw


def test_normalization_preserves_all_business_fields_and_strict_schema():
    raw = {
        **WATERFALL,
        "filters": [{"dimension": "store", "op": "eq", "values": ["1"]}],
        "group_by": ["category", "store"],
        "sort": {"field": "delta_sales_amount", "direction": "asc"},
        "limit": 3,
        "base_result_id": "previous-query-handle",
    }
    before = copy.deepcopy(raw)
    with pytest.raises(ValidationError, match="瀑布图"):
        QuerySpec.model_validate(raw)  # The strict gateway still rejects this mismatch.
    normalized = normalize_query_intent(raw)
    assert normalized == {**before, "analysis": "contribution"}
    assert raw == before
    QuerySpec.model_validate(normalized)


async def test_live_gateway_error_code_survives_tool_error_message(monkeypatch):
    fake_provider(monkeypatch, WATERFALL)

    class ReconciliationFailure(ValueError):
        code = "RECONCILIATION_FAILED"

    failure = ReconciliationFailure("分组变化与总变化无法对账，未发布结果")

    def query(spec):
        assert QuerySpec.model_validate(spec).analysis == "contribution"
        raise failure

    def forbidden(_):
        raise AssertionError("Failed reconciliation cannot be analyzed")

    with pytest.raises(ReconciliationFailure) as caught:
        await run_fake("2025年9月比8月销售额，按类别用瀑布图", None, query, forbidden)
    assert caught.value is failure
    assert caught.value.code == "RECONCILIATION_FAILED"


def submit_and_wait(client, conversation_id, message):
    version = client.get(f"/api/conversations/{conversation_id}").json()["state_version"]
    response = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"message": message, "client_request_id": str(uuid4()), "expected_state_version": version},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] not in {"queued", "querying", "analyzing"}:
            return run
        time.sleep(0.01)
    raise AssertionError("The local scripted run did not finish")


@pytest.fixture
def live_api_client(monkeypatch):
    # Like test_backend.py, these API checks require the local PostgreSQL fixture.
    import app.main as main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main.settings, "agent_mode", "live")
    monkeypatch.setattr(main.settings, "deepseek_api_key", SecretStr("LOCAL-TEST-PLACEHOLDER-NOT-A-CREDENTIAL"))
    with TestClient(main.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "demo123"}).status_code == 200
        conversation_id = client.post("/api/conversations").json()["id"]
        yield client, conversation_id


def test_live_api_monthly_table_then_explicit_waterfall_reconciles(live_api_client, monkeypatch):
    client, conversation_id = live_api_client
    fake_provider(monkeypatch, MONTHLY)
    monthly = submit_and_wait(client, conversation_id, "2025年销售额按月看")
    assert monthly["status"] == "succeeded"
    assert monthly["result"]["query"]["group_by"] == ["month"]
    before_display = agent.shared_budget().snapshot()
    table = submit_and_wait(client, conversation_id, "换成表格")
    assert table["status"] == "succeeded"
    assert table["result_id"] == monthly["result_id"]
    assert table["presentation"]["chart_type"] == "table"
    assert table["runtime"]["model_calls"] == table["runtime"]["aggregate_queries"] == 0
    assert agent.shared_budget().snapshot() == before_display

    fake_provider(monkeypatch, {**WATERFALL, "limit": 3})
    waterfall = submit_and_wait(client, conversation_id, "2025年9月比8月销售额，按类别用瀑布图")
    assert waterfall["status"] == "succeeded", waterfall
    assert waterfall["runtime"]["model_mode"] == "live"
    assert waterfall["runtime"]["model_calls"] == 6
    payload = waterfall["result"]
    assert payload["query"]["analysis"] == "contribution"
    assert payload["query"]["group_by"] == ["category"]
    assert payload["chart"]["type"] == "waterfall"
    assert payload["query"]["period"] == {**WATERFALL["period"], "date_field": "order_date"}
    assert payload["query"]["comparison"] == {**WATERFALL["comparison"], "date_field": "order_date"}
    assert payload["truncated"] and payload["displayed_row_count"] == 4
    delta = Decimal(payload["deltas"]["sales_amount"])
    assert sum(Decimal(row["delta_sales_amount"]) for row in payload["rows"]) == delta
    steps = payload["chart"]["waterfall"]
    assert sum(Decimal(step["value"]) for step in steps[1:-1]) == delta
    assert Decimal(steps[0]["value"]) + delta == Decimal(steps[-1]["value"])
    restored = client.get(f"/api/conversations/{conversation_id}").json()
    assert restored["current_query"] == payload["query"]


@pytest.mark.parametrize(
    "raw,kwargs,expected_code",
    [
        ({**WATERFALL, "comparison": None}, {}, "QUERY_VALIDATION_FAILED"),
        (WATERFALL, {"query_args": {}}, "AGENT_TOOL_ERROR"),
    ],
)
def test_live_api_tool_failures_do_not_publish_clarification(live_api_client, monkeypatch, raw, kwargs, expected_code):
    client, conversation_id = live_api_client
    fake_provider(monkeypatch, raw, **kwargs)
    run = submit_and_wait(client, conversation_id, "销售额瀑布图")
    assert run["status"] == "failed"
    assert run["error_code"] == expected_code
    assert run["result"] is None and run["result_id"] is None
    assert "授权我调整" not in run["answer"]
    assert client.get(f"/api/conversations/{conversation_id}").json()["current_query"] is None
