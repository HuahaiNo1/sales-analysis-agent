"""Focused PostgreSQL/API regressions; run with scripts/test.sh, never calls a live model."""

import time
from decimal import Decimal
from uuid import uuid4

import pytest
from app.config import settings
from app.db import Identity, analytics, identity_for
from app.main import app, csv_cell
from app.query import METRIC_SQL, QueryError, compile_query, query_metrics
from app.schemas import QuerySpec
from fastapi.testclient import TestClient
from pydantic import ValidationError

settings.agent_mode = "mock"


def spec(**updates):
    base = {
        "metrics": ["sales_amount"],
        "period": {"start": "2025-01-01", "end": "2026-01-01"},
        "chart": "table",
    }
    return base | updates


def test_spec_denies_unsafe_and_ambiguous_inputs():
    for extra in (
        {"sql": "SELECT * FROM sessions"},
        {"allowed_store_ids": [999999]},
        {"metrics": ["avg_order_value"], "group_by": ["category"]},
        {"metrics": ["avg_order_value"], "filters": [{"dimension": "product", "values": ["1"]}]},
        {
            "analysis": "contribution",
            "metrics": ["order_count"],
            "comparison": {"start": "2024-01-01", "end": "2025-01-01"},
            "group_by": ["category"],
        },
        {"sort": {"field": "pg_sleep(5)", "direction": "desc"}},
    ):
        with pytest.raises(ValidationError):
            QuerySpec.model_validate(spec(**extra))


def test_postgres_exact_five_metric_hand_fixture():
    # These explicitly marked fixtures are independent of the official dataset.
    fixture = """WITH fact_sales(order_key,line_number,order_date,product_key,store_key,customer_key,
      quantity,net_price,unit_cost,currency_code,exchange_rate,dataset_version) AS (VALUES
      (1,0,DATE '2025-01-01',1,1,1,2,50::numeric,30::numeric,'USD',1,'contoso-v2-2023-2025-692e0347d360'),
      (1,1,DATE '2025-01-01',2,1,1,1,40::numeric,20::numeric,'USD',1,'contoso-v2-2023-2025-692e0347d360'),
      (2,0,DATE '2025-01-02',1,1,1,1,60::numeric,35::numeric,'USD',1,'contoso-v2-2023-2025-692e0347d360')),
      dim_product(product_key) AS (VALUES(1),(2)),dim_store(store_key) AS (VALUES(1)),
      dim_customer_geo(customer_key) AS (VALUES(1)),dim_date(date) AS (VALUES(DATE '2025-01-01'),(DATE '2025-01-02')) """
    identity = Identity("fixture", "fixture", (1,), "fixture")
    s = QuerySpec.model_validate(spec())
    sql, params = compile_query(s, identity, s.period, grouped=False, metrics=list(METRIC_SQL))
    with analytics(identity) as conn:
        row = conn.execute(fixture + sql, params).fetchone()
    assert row == {
        "sales_amount": Decimal(200),
        "order_count": 2,
        "units_sold": 4,
        "avg_order_value": Decimal(100),
        "gross_profit": Decimal(85),
        "_line_count": 3,
    }


def test_database_read_only_and_rls():
    restricted = identity_for("analyst")
    with analytics(restricted) as conn:
        visible = conn.execute("SELECT array_agg(DISTINCT store_key) AS ids FROM fact_sales").fetchone()[
            "ids"
        ]
        assert visible and set(visible) <= set(restricted.allowed_store_ids)
        assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        with pytest.raises(Exception):
            conn.execute("DELETE FROM fact_sales WHERE false")


def test_authorized_totals_and_cross_scope_rejected():
    admin = identity_for("admin")
    analyst = identity_for("analyst")
    a = query_metrics(spec(), admin)
    b = query_metrics(spec(), analyst)
    assert Decimal(a["totals"]["sales_amount"]) > Decimal(b["totals"]["sales_amount"]) > 0
    with pytest.raises(QueryError, match="授权范围"):
        query_metrics(spec(filters=[{"dimension": "store", "values": ["999999"]}]), analyst)
    with pytest.raises(QueryError, match="覆盖"):
        query_metrics(spec(period={"start": "2026-01-01", "end": "2027-01-01"}), admin)


def test_contribution_reconciles_including_other():
    result = query_metrics(
        spec(
            period={"start": "2025-09-01", "end": "2025-10-01"},
            comparison={"start": "2025-08-01", "end": "2025-09-01"},
            group_by=["category"],
            analysis="contribution",
            chart="waterfall",
            limit=3,
        ),
        identity_for("admin"),
    )
    assert result["truncated"] is True and len(result["rows"]) == 4
    assert sum(Decimal(r["delta_sales_amount"]) for r in result["rows"]) == Decimal(
        result["deltas"]["sales_amount"]
    )
    waterfall = result["chart"]["waterfall"]
    assert Decimal(waterfall[0]["value"]) + sum(Decimal(x["value"]) for x in waterfall[1:-1]) == Decimal(
        waterfall[-1]["value"]
    )


def test_empty_aov_and_spreadsheet_formula_escape():
    result = query_metrics(
        spec(
            metrics=["avg_order_value"],
            filters=[{"dimension": "customer_country", "values": ["nonexistent"]}],
        ),
        identity_for("admin"),
    )
    assert result["no_data"] and result["totals"]["avg_order_value"] is None
    for text in ['=HYPERLINK("x")', " +cmd", "@sum(A1)", "\t=1"]:
        assert csv_cell(text).startswith("'")


def wait_result(client, rid):
    for _ in range(150):
        data = client.get(f"/api/runs/{rid}").json()
        if data["status"] in {
            "succeeded",
            "failed",
            "cancelled",
            "no_data",
            "needs_clarification",
            "unsupported",
        }:
            return data
        time.sleep(0.1)
    raise AssertionError("Run timed out")


def test_waterfall_business_followups_and_display_reuse_remain_reconciled():
    from app.budget import shared_budget

    budget = shared_budget().snapshot()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "demo123"})
        cid = client.post("/api/conversations").json()["id"]
        previous_result = None
        for version, message in enumerate(
            ["2025年9月比8月销售额，按类别用瀑布图", "按门店看", "只看前3", "换成瀑布图"]
        ):
            submitted = client.post(
                f"/api/conversations/{cid}/runs",
                json={
                    "message": message,
                    "client_request_id": str(uuid4()),
                    "expected_state_version": version,
                },
            )
            assert submitted.status_code == 202
            outcome = wait_result(client, submitted.json()["run_id"])
            assert outcome["status"] == "succeeded", outcome
            result = outcome["result"]
            assert result["query"]["analysis"] == "contribution"
            QuerySpec.model_validate({**result["query"], "chart": "waterfall"})
            assert sum(Decimal(row["delta_sales_amount"]) for row in result["rows"]) == Decimal(
                result["totals"]["sales_amount"]
            ) - Decimal(result["comparison_totals"]["sales_amount"])
            if version == 0:
                assert result["chart"]["type"] == "waterfall"
            if version == 2:
                assert result["truncated"] and len(result["rows"]) == 4
                assert result["rows"][-1]["store"] == "其他分组（合计）"
            if version == 3:
                assert result == previous_result
                assert outcome["presentation"]["chart_type"] == "waterfall"
                assert outcome["runtime"]["model_calls"] == 0
                assert outcome["runtime"]["aggregate_queries"] == 0
            previous_result = result
    assert shared_budget().snapshot() == budget


def test_end_to_end_session_agent_results_csv_history_isolation():
    with TestClient(app) as client:
        assert client.get("/api/catalog").status_code == 401
        assert (
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        )
        assert (
            client.post("/api/auth/login", json={"username": "admin", "password": "demo123"}).status_code
            == 200
        )
        cat = client.get("/api/catalog").json()
        assert cat["model_mode"] == "mock" and cat["orders"] == 97215
        dashboard = client.get("/api/dashboard").json()
        assert len(dashboard["totals"]) == 5 and len(dashboard["monthly"]) == 12
        cid = client.post("/api/conversations").json()["id"]
        body = {
            "message": "2025年9月比8月销售额变化来自哪些类别，用瀑布图",
            "client_request_id": str(uuid4()),
            "expected_state_version": 0,
        }
        submitted = client.post(f"/api/conversations/{cid}/runs", json=body)
        assert submitted.status_code == 202
        rid = submitted.json()["run_id"]
        assert client.post(f"/api/conversations/{cid}/runs", json=body).json()["run_id"] == rid
        outcome = wait_result(client, rid)
        assert outcome["status"] == "succeeded", outcome
        result = outcome["result"]
        assert outcome["runtime"]["subagents"] == ["analysis"] and outcome["runtime"]["model_calls"] == 6
        assert result["chart"]["type"] == "waterfall"
        exported = client.get(f"/api/results/{result['id']}/csv")
        assert exported.status_code == 200 and exported.text.startswith("\ufeff")
        assert client.get(f"/api/conversations/{cid}").json()["state_version"] == 1
        assert len(client.get(f"/api/conversations/{cid}").json()["history"]) == 2
        follow = {"message": "换成表格", "client_request_id": str(uuid4()), "expected_state_version": 1}
        rid2 = client.post(f"/api/conversations/{cid}/runs", json=follow).json()["run_id"]
        assert wait_result(client, rid2)["status"] == "succeeded"
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "analyst", "password": "demo123"})
        assert client.get(f"/api/conversations/{cid}").status_code == 404
        assert client.get(f"/api/runs/{rid}").status_code == 404
        assert client.get(f"/api/results/{result['id']}/csv").status_code == 404
