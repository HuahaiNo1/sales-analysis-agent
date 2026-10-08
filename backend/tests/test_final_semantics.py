"""Narrow pure/scripted regressions for final semantic gaps; no database or provider network."""

import asyncio
import threading
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal

import pytest
from app import agent, main, query
from app.budget import shared_budget
from app.db import Identity
from app.report_export import export_report, svg_chart
from app.reports import frozen_facts
from app.review import ReviewSession
from app.schemas import QuerySpec, ReviewRequest
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

IDENTITY = Identity("admin", "fixture", (1,), "fixture")
REQUEST = {
    "period": {"start": "2025-09-01", "end": "2025-10-01"},
    "comparison": {"start": "2025-08-01", "end": "2025-09-01"},
    "metric": "gross_profit",
    "drill_down": False,
}
METADATA = {
    "coverage": {"start": "2023-01-01", "end": "2026-01-01"},
    "dataset_version": "fixture-v1",
    "metric_version": "metrics_v1",
    "currency": "USD",
}


class FixtureConnection:
    def __init__(self, values, after_execute=None):
        self.values = deepcopy(values)
        self.calls = []
        self.after_execute = after_execute

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.value = self.values[len(self.calls) - 1]
        if self.after_execute:
            self.after_execute()
        return self

    def fetchall(self):
        return deepcopy(self.value)

    def fetchone(self):
        return deepcopy(self.value)


def fixture_query(
    monkeypatch,
    *,
    dimension="category",
    limit=10,
    sort=None,
    period=None,
    comparison=None,
    after_execute=None,
):
    current = [
        {dimension: "A", "gross_profit": Decimal(100), "_line_count": 1},
        {dimension: "B", "gross_profit": Decimal(50), "_line_count": 1},
        {dimension: "C", "gross_profit": Decimal(0), "_line_count": 2},
    ]
    prior = [
        {dimension: "A", "gross_profit": Decimal(95), "_line_count": 1},
        {dimension: "C", "gross_profit": Decimal(0), "_line_count": 1},
        {dimension: "D", "gross_profit": Decimal(20), "_line_count": 1},
    ]
    if dimension in {"month", "day"}:
        current = [
            {
                dimension: "2025-09" if dimension == "month" else "2025-09-01",
                "gross_profit": Decimal(150),
                "_line_count": 4,
            }
        ]
        prior = [
            {
                dimension: "2025-08" if dimension == "month" else "2025-08-01",
                "gross_profit": Decimal(115),
                "_line_count": 3,
            }
        ]
    conn = FixtureConnection(
        [
            current,
            {"gross_profit": Decimal(150), "_line_count": 4},
            prior,
            {"gross_profit": Decimal(115), "_line_count": 3},
        ],
        after_execute,
    )

    @contextmanager
    def analytics(_):
        yield conn

    monkeypatch.setattr(query, "analytics", analytics)
    monkeypatch.setattr(query, "dataset_metadata", lambda: deepcopy(METADATA))
    spec = QuerySpec(
        metrics=["gross_profit"],
        period=period or REQUEST["period"],
        comparison=comparison or REQUEST["comparison"],
        group_by=[dimension],
        analysis="compare" if dimension in {"day", "month"} else "contribution",
        chart="table",
        limit=limit,
        sort=sort,
    ).model_dump(mode="json")
    return conn, spec


def test_missing_records_are_distinct_from_true_zero_and_flags_are_evidence_bound(monkeypatch):
    _, spec = fixture_query(monkeypatch)
    result = query.query_metrics(spec, IDENTITY)
    pairs = {
        row["category"]: (row, presence) for row, presence in zip(result["rows"], result["row_presence"])
    }
    assert pairs["B"][1]["state"] == "current_only"
    assert pairs["B"][1]["comparison_source_line_count"] == 0
    assert pairs["D"][1]["state"] == "comparison_only"
    assert pairs["C"][0]["gross_profit"] == "0" and pairs["C"][1]["state"] == "both"
    assert pairs["C"][1]["current_source_line_count"] == 2
    assert sum(Decimal(row["delta_gross_profit"]) for row in result["rows"]) == Decimal(35)
    session = ReviewSession(ReviewRequest.model_validate(REQUEST), [])
    session.evidence = [
        {"evidence_id": "E1", "role": "overall", "title": "overall", "result": result},
        {"evidence_id": "E2", "role": "category", "title": "category", "result": result},
    ]
    session._stop("drill_disabled")
    flags = {flag["rule_id"]: flag for flag in session.result()["flags"]}
    assert "B：对比期无记录/本期出现" in flags["current_only_category"]["text"]
    assert "D：本期无记录" in flags["comparison_only_category"]["text"]
    assert flags["current_only_category"]["evidence_ids"] == ["E2"]
    assert "非指标是否为零" in flags["current_only_category"]["text"]
    assert not any("C：" in flag["text"] for flag in flags.values())


@pytest.mark.parametrize("dimension", ["day", "month"])
def test_non_aligned_time_comparison_preserves_gaps_and_independent_totals(monkeypatch, dimension):
    _, spec = fixture_query(monkeypatch, dimension=dimension)
    result = query.query_metrics(spec, IDENTITY)
    for row, presence in zip(result["rows"], result["row_presence"]):
        assert row["delta_gross_profit"] is None and row["change_pct_gross_profit"] is None
        if presence["state"] == "current_only":
            assert row["gross_profit"] == "150" and row["comparison_gross_profit"] is None
        else:
            assert row["gross_profit"] is None and row["comparison_gross_profit"] == "115"
    assert result["totals"]["gross_profit"] == "150" and result["comparison_totals"]["gross_profit"] == "115"
    assert result["deltas"]["gross_profit"] == "35"
    assert any("未进行跨期日历对齐" in warning for warning in result["warnings"])


def test_explicit_sort_does_not_change_true_contribution_leader(monkeypatch):
    _, spec = fixture_query(monkeypatch, limit=1, sort={"field": "gross_profit", "direction": "desc"})
    result = query.query_metrics(spec, IDENTITY)
    assert result["rows"][0]["category"] == "A"  # User sort is unchanged.
    assert "最大的分组是 B，变化 50.00" in result["observations"][-1]
    assert result["row_presence"][-1]["is_other"] is True
    assert result["row_presence"][-1]["current_source_line_count"] == 3
    assert sum(Decimal(row["delta_gross_profit"]) for row in result["rows"]) == Decimal(35)


def test_equal_length_overlapping_periods_are_explicit(monkeypatch):
    period = {"start": "2025-09-02", "end": "2025-10-02"}
    comparison = {"start": "2025-09-01", "end": "2025-10-01"}
    _, spec = fixture_query(monkeypatch, period=period, comparison=comparison)
    result = query.query_metrics(spec, IDENTITY)
    assert any("存在重叠" in warning for warning in result["warnings"])
    assert not any("天数不同" in warning for warning in result["warnings"])
    session = ReviewSession(
        ReviewRequest.model_validate({**REQUEST, "period": period, "comparison": comparison}), []
    )
    session.evidence = [{"evidence_id": "E1", "role": "overall", "title": "overall", "result": result}]
    session._stop("drill_disabled")
    assert any("存在重叠" in item for item in session.result()["assumptions"])


def test_cancel_between_statements_prevents_hidden_remaining_sql(monkeypatch):
    stopped = threading.Event()
    conn, spec = fixture_query(monkeypatch, after_execute=stopped.set)
    with pytest.raises(query.QueryError, match="不再执行后续 SQL"):
        query.query_metrics(spec, IDENTITY, cancelled=stopped.is_set)
    assert len(conn.calls) == 1


def test_time_comparison_export_keeps_null_values_and_discloses_gaps(monkeypatch):
    _, spec = fixture_query(monkeypatch, dimension="month")
    result = query.query_metrics(spec, IDENTITY)
    chart = svg_chart(result)
    assert chart.count("不适用") == 2
    assert "None" not in chart and "NaN" not in chart
    report = {
        "id": "fixture-report",
        "revision": 1,
        "title": "趋势证据",
        "comment": "",
        "suggestions": "",
        "created_at": result["generated_at"],
        "facts": frozen_facts({"kind": "query"}, result, IDENTITY),
    }
    for format in ("html", "markdown"):
        exported = export_report(report, format)
        assert "未进行跨期日历对齐" in exported
        assert "不适用" in exported


@pytest.mark.parametrize("format", ["html", "markdown"])
def test_empty_snapshot_export_explicitly_disclaims_business_conclusions(monkeypatch, format):
    conn, spec = fixture_query(monkeypatch)
    conn.values = [
        [],
        {"gross_profit": Decimal(0), "_line_count": 0},
        [],
        {"gross_profit": Decimal(0), "_line_count": 0},
    ]
    result = query.query_metrics(spec, IDENTITY)
    assert result["no_data"]
    report = {
        "id": "empty-report",
        "revision": 1,
        "title": "空数据事实快照",
        "comment": "",
        "suggestions": "",
        "created_at": result["generated_at"],
        "facts": frozen_facts({"kind": "query"}, result, IDENTITY),
    }
    exported = export_report(report, format)
    assert "无数据：所选期间和筛选范围没有匹配的源记录" in exported
    assert "不能形成经营结论" in exported


async def test_review_deadline_cancels_provider_and_preserves_unknown_reservation(monkeypatch, tmp_path):
    entered = asyncio.Event()
    ended = asyncio.Event()
    calls = []

    class SlowProvider(BaseChatModel):
        @property
        def _llm_type(self):
            return "local-scripted-slow-provider"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError("Only async local scripted model is allowed")

        async def _agenerate(self, *args, **kwargs):
            calls.append("provider-start")
            entered.set()
            try:
                await asyncio.sleep(60)
                raise AssertionError("Deadline must cancel this local scripted request")
            finally:
                ended.set()

    monkeypatch.setenv("LLM_BUDGET_PATH", str(tmp_path / "deadline-budget.sqlite3"))
    monkeypatch.setattr(agent, "_deepseek_model", lambda _: SlowProvider())
    monkeypatch.setattr(main.settings, "agent_mode", "live")
    monkeypatch.setattr(
        main.settings, "deepseek_api_key", SecretStr("LOCAL-TEST-PLACEHOLDER-NOT-A-CREDENTIAL")
    )
    monkeypatch.setattr(main, "REVIEW_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(main, "catalog_for", lambda _: {"categories": [], **METADATA})
    monkeypatch.setattr(main, "identity_for", lambda _: IDENTITY)
    writes = []
    monkeypatch.setattr(main, "update_run", lambda rid, **fields: writes.append(fields))
    monkeypatch.setattr(
        main, "query_metrics", lambda *args: pytest.fail("No aggregate may run after the stalled model")
    )
    rid = "scripted-deadline"
    main.CANCELLATIONS[rid] = threading.Event()
    await main.process_run(rid, IDENTITY, "fixed review", None, ReviewRequest.model_validate(REQUEST))
    assert entered.is_set() and ended.is_set() and calls == ["provider-start"]
    assert writes[-1]["status"] == "failed" and writes[-1]["error_code"] == "REVIEW_TIMEOUT"
    assert not any("result_id" in update for update in writes)
    snapshot = shared_budget().snapshot()
    assert snapshot["calls_reserved"] == 1 and snapshot["spent_rmb"] == 0 and snapshot["reserved_rmb"] > 0
    await asyncio.sleep(0.05)
    assert shared_budget().snapshot() == snapshot and calls == ["provider-start"]
    assert rid not in main.CANCELLATIONS and rid not in main.TASKS


@pytest.mark.parametrize("boundary", ["query", "sql", "analysis", "model", "publication"])
async def test_review_scope_reduction_blocks_next_action_and_publication(monkeypatch, boundary):
    current_identity = [IDENTITY]
    shrunk = Identity(IDENTITY.subject, "restricted", (), "restricted")
    writes, sql_calls = [], []
    monkeypatch.setattr(main, "identity_for", lambda _: current_identity[0])
    monkeypatch.setattr(main, "catalog_for", lambda _: {"categories": [], **METADATA})
    monkeypatch.setattr(main, "update_run", lambda rid, **fields: writes.append(fields))
    conn, _ = fixture_query(monkeypatch, after_execute=(
        lambda: current_identity.__setitem__(0, shrunk)
    ) if boundary == "sql" else None)
    # Overall empty evidence ends the plan normally; all source values stay local fixtures.
    conn.values = [
        [{"gross_profit": Decimal(0), "_line_count": 0}],
        {"gross_profit": Decimal(0), "_line_count": 0},
        [{"gross_profit": Decimal(0), "_line_count": 0}],
        {"gross_profit": Decimal(0), "_line_count": 0},
    ]
    monkeypatch.setattr(main, "query_metrics", query.query_metrics)

    class PublicationConnection:
        def execute(self, sql, params):
            sql_calls.append(sql)
            assert sql.startswith("SELECT status,cancel_requested")
            current_identity[0] = shrunk
            return self

        def fetchone(self):
            return {"status": "analyzing", "cancel_requested": False}

    @contextmanager
    def publication_connection():
        yield PublicationConnection()

    monkeypatch.setattr(main, "connect", publication_connection)

    async def fake_agent(message, previous, catalog, query_callback, analysis_callback, stopped, **kwargs):
        if boundary in {"query", "model"}:
            current_identity[0] = shrunk
        if boundary == "model":
            stopped()
            pytest.fail("No model call may follow a detected scope reduction")
        handle = query_callback(kwargs["review_query"])
        if boundary == "analysis":
            current_identity[0] = shrunk
        analysis_callback(handle["result_id"])
        assert boundary == "publication"
        return {"status": "SUCCEEDED", "result": handle, "runtime": {}}

    monkeypatch.setattr(main, "run_agent", fake_agent)
    rid = "scope-boundary-" + boundary
    main.CANCELLATIONS[rid] = threading.Event()
    await main.process_run(rid, IDENTITY, "fixed review", None, ReviewRequest.model_validate(REQUEST))
    assert writes[-1]["status"] == "failed" and writes[-1]["error_code"] == "SCOPE_CHANGED"
    assert not any("result_id" in update for update in writes)
    assert len(conn.calls) == (0 if boundary in {"query", "model"} else 1 if boundary == "sql" else 4)
    assert len(sql_calls) == (1 if boundary == "publication" else 0)
    assert rid not in main.CANCELLATIONS and rid not in main.TASKS
