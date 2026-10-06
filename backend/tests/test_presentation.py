"""Only display-reuse regressions; SQL aggregation and model calls are forbidden after setup."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import app.main as main
import pytest
from app.db import connect, identity_for
from app.presentation import presentation_request
from app.query import query_metrics
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb


def test_standalone_parser_does_not_capture_business_changes():
    for text, expected in [
        ("换成表格", "table"),
        ("请把结果改成柱状图！", "bar"),
        ("用折线图展示", "line"),
        ("切换到瀑布图", "waterfall"),
        ("换张图", "unspecified"),
    ]:
        assert presentation_request(text) == expected
    for text in ["2024年换成表格", "只看门店10，换成柱状图", "换成柱状图，按月看", "销售额用表格展示"]:
        assert presentation_request(text) is None


@pytest.fixture
def existing_result(monkeypatch):
    main.settings.agent_mode = "mock"
    with TestClient(main.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "demo123"})
        cid = client.post("/api/conversations").json()["id"]
        identity = identity_for("admin")
        source_run = str(uuid4())
        payload = query_metrics(
            {
                "metrics": ["sales_amount"],
                "period": {"start": "2025-01-01", "end": "2026-01-01"},
                "group_by": ["month"],
                "chart": "line",
            },
            identity,
            source_run,
        )
        with connect() as conn:
            conn.execute(
                "INSERT INTO runs(id,conversation_id,subject,client_request_id,status,progress_stage,message,result_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    source_run,
                    cid,
                    "admin",
                    str(uuid4()),
                    "succeeded",
                    "succeeded",
                    "isolated display reuse fixture",
                    payload["id"],
                ],
            )
            conn.execute(
                "INSERT INTO results(id,run_id,subject,scope_hash,payload,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
                [
                    payload["id"],
                    source_run,
                    "admin",
                    identity.scope_hash,
                    Jsonb(payload),
                    payload["expires_at"],
                ],
            )
            conn.execute(
                "UPDATE conversations SET latest_run_id=%s,state_version=1,current_query=%s WHERE id=%s",
                [source_run, Jsonb(payload["query"]), cid],
            )
        aggregate = Mock(side_effect=AssertionError("Display reuse must not execute an aggregate"))
        model = AsyncMock(side_effect=AssertionError("Display reuse must not call a model"))
        monkeypatch.setattr(main, "query_metrics", aggregate)
        monkeypatch.setattr(main, "run_agent", model)
        yield client, cid, payload, source_run
        aggregate.assert_not_called()
        model.assert_not_called()


def submit(client, cid, text):
    version = client.get(f"/api/conversations/{cid}").json()["state_version"]
    body = {"message": text, "client_request_id": str(uuid4()), "expected_state_version": version}
    response = client.post(f"/api/conversations/{cid}/runs", json=body)
    assert response.status_code == 202, response.text
    return response.json()["run_id"], body


def test_display_reuses_identical_payload_csv_and_refresh(existing_result):
    client, cid, payload, _ = existing_result
    budget = main.shared_budget().snapshot()
    original_csv = client.get(f"/api/results/{payload['id']}/csv").content
    rid, body = submit(client, cid, "换成表格")
    run = client.get(f"/api/runs/{rid}").json()
    assert run["status"] == "succeeded" and run["result_id"] == payload["id"]
    assert run["result"] == payload  # Includes the original chart, version, expiry and SHA unchanged.
    assert run["presentation"] == {"chart_type": "table", "reused_result_id": payload["id"]}
    assert run["runtime"]["model_calls"] == run["runtime"]["aggregate_queries"] == 0
    assert client.get(f"/api/results/{payload['id']}/csv").content == original_csv
    assert client.post(f"/api/conversations/{cid}/runs", json=body).json()["run_id"] == rid
    restored = client.get(f"/api/conversations/{cid}").json()
    assert restored["latest_run_id"] == rid and restored["current_query"] == payload["query"]
    assert (
        client.get(f"/api/runs/{restored['latest_run_id']}").json()["presentation"]["chart_type"] == "table"
    )
    bar, _ = submit(client, cid, "换成柱状图")
    assert client.get(f"/api/runs/{bar}").json()["result_id"] == payload["id"]
    assert main.shared_budget().snapshot() == budget


def test_display_clarification_keeps_only_authorized_pending_handle(existing_result):
    client, cid, payload, _ = existing_result
    rid, _ = submit(client, cid, "换张图")
    run = client.get(f"/api/runs/{rid}").json()
    assert run["status"] == "needs_clarification" and run["result"] is None
    reply, _ = submit(client, cid, "柱状图")
    assert client.get(f"/api/runs/{reply}").json()["result_id"] == payload["id"]
    incompatible, _ = submit(client, cid, "换成瀑布图")
    assert client.get(f"/api/runs/{incompatible}").json()["error_code"] == "CHART_INCOMPATIBLE"
    reply, _ = submit(client, cid, "换成表格")
    assert client.get(f"/api/runs/{reply}").json()["result_id"] == payload["id"]


@pytest.mark.parametrize("reason", ["expired", "scope", "owner", "version"])
def test_display_rejects_invalid_results(existing_result, monkeypatch, reason):
    client, cid, payload, _ = existing_result
    if reason == "version":
        original = main.dataset_metadata
        monkeypatch.setattr(
            main, "dataset_metadata", lambda: original() | {"dataset_version": "a-new-version"}
        )
    else:
        with connect() as conn:
            if reason == "expired":
                conn.execute(
                    "UPDATE results SET expires_at=%s WHERE id=%s",
                    [datetime.now(timezone.utc) - timedelta(seconds=1), payload["id"]],
                )
            elif reason == "scope":
                conn.execute("UPDATE results SET scope_hash=%s WHERE id=%s", ["revoked", payload["id"]])
            else:
                conn.execute("UPDATE results SET subject=%s WHERE id=%s", ["analyst", payload["id"]])
    rid, _ = submit(client, cid, "换成表格")
    run = client.get(f"/api/runs/{rid}").json()
    assert run["status"] in {"failed", "expired"}
    assert run["result_id"] is None and run["result"] is None and run["presentation"] is None


def test_display_without_history_and_cross_account_are_rejected(existing_result):
    client, cid, payload, _ = existing_result
    empty = client.post("/api/conversations").json()["id"]
    rid, _ = submit(client, empty, "换成表格")
    assert client.get(f"/api/runs/{rid}").json()["error_code"] == "NO_RESULT_TO_REUSE"
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "analyst", "password": "demo123"})
    forbidden = client.post(
        f"/api/conversations/{cid}/runs",
        json={"message": "换成表格", "client_request_id": str(uuid4()), "expected_state_version": 1},
    )
    assert forbidden.status_code == 404
    assert client.get(f"/api/results/{payload['id']}").status_code == 404
