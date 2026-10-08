"""Critical final-scope regressions: real graph/PG with mock provider and a temporary ledger."""

import threading
import time
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest
from app.config import settings
from app.db import Identity, connect, identity_for
from app.main import app, user
from app.query import QueryError
from app.review import ReviewSession
from app.schemas import ReportCreate, ReportUpdate, ReviewRequest
from fastapi.testclient import TestClient
from pydantic import ValidationError

settings.agent_mode = "mock"


def review_request(**changes):
    return {
        "period": {"start": "2025-09-01", "end": "2025-10-01"},
        "comparison": {"start": "2025-08-01", "end": "2025-09-01"},
        "metric": "sales_amount",
        "filters": [],
        "drill_down": True,
        **changes,
    }


def login(client, username="admin"):
    assert (
        client.post("/api/auth/login", json={"username": username, "password": "demo123"}).status_code == 200
    )


def start_review(client, **changes):
    cid = client.post("/api/conversations").json()["id"]
    body = {
        "message": "固定销售期间复盘",
        "client_request_id": str(uuid4()),
        "expected_state_version": 0,
        "review": review_request(**changes),
    }
    response = client.post(f"/api/conversations/{cid}/runs", json=body)
    assert response.status_code == 202, response.text
    return cid, body, response.json()["run_id"]


def wait_run(client, run_id):
    for _ in range(200):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {
            "succeeded",
            "failed",
            "cancelled",
            "no_data",
            "needs_clarification",
            "unsupported",
            "expired",
        }:
            return payload
        time.sleep(0.03)
    raise AssertionError("Mock review timed out")


def save_report(client, run_id, **updates):
    body = {"run_id": run_id, "title": "九月销售复盘", "client_request_id": str(uuid4()), **updates}
    response = client.post("/api/reports", json=body)
    assert response.status_code == 201, response.text
    return body, response.json()


def test_review_contract_whitelist_and_exact_plan():
    for changes in [
        {"metric": "order_count"},
        {"metric": "avg_order_value"},
        {"max_queries": 20},
        {"comparison": review_request()["period"]},
        {"filters": [{"dimension": "store", "values": ["1"]}] * 8},
    ]:
        with pytest.raises(ValidationError):
            ReviewRequest.model_validate(review_request(**changes))
    session = ReviewSession(ReviewRequest.model_validate(review_request()), [])
    modified = deepcopy(session.next_spec)
    modified["limit"] = 99
    with pytest.raises(QueryError, match="固定模板"):
        session.validate_next(modified)
    with pytest.raises(ValidationError):
        ReportUpdate.model_validate({"expected_revision": 1, "facts": {}})
    for body in [
        {"expected_revision": 1},
        {"expected_revision": 1, "title": "   "},
        {"expected_revision": 1, "comment": None},
    ]:
        with pytest.raises(ValidationError):
            ReportUpdate.model_validate(body)
    with pytest.raises(ValidationError):
        ReportCreate.model_validate(
            {"run_id": str(uuid4()), "title": "   ", "client_request_id": "request-1"}
        )


def test_bounded_review_real_graph_reconciles_and_idempotency():
    from app.budget import shared_budget

    before = shared_budget().snapshot()
    with TestClient(app) as client:
        login(client)
        cid, body, rid = start_review(client)
        same = client.post(f"/api/conversations/{cid}/runs", json=body)
        assert same.json()["run_id"] == rid
        changed = deepcopy(body)
        changed["review"]["drill_down"] = False
        assert client.post(f"/api/conversations/{cid}/runs", json=changed).status_code == 409
        outcome = wait_run(client, rid)
        assert outcome["status"] == "succeeded", outcome
        assert outcome["kind"] == "review"
        assert outcome["runtime"]["subagents"] == ["analysis"]
        assert outcome["runtime"]["query_specs"] == 4 and outcome["runtime"]["model_calls"] == 9
        review = outcome["review"]
        assert review["query_count"] == review["max_queries"] == 4
        assert review["stop_reason"] == "query_limit_reached"
        assert [e["role"] for e in review["evidence"]] == ["overall", "category", "store", "drill"]
        assert outcome["result"] == review["evidence"][0]["result"]
        assert review["evidence"][3]["result"]["query"]["filters"][-1]["dimension"] == "category"
        for evidence in review["evidence"][1:]:
            result = evidence["result"]
            assert sum(Decimal(r["delta_sales_amount"]) for r in result["rows"]) == Decimal(
                result["deltas"]["sales_amount"]
            )
        assert all(
            set(item["evidence_ids"]) <= {"E1", "E2", "E3", "E4"}
            for item in review["findings"] + review["flags"]
        )
        assert client.post(f"/api/runs/{rid}/cancel").json()["status"] == "succeeded"
    assert shared_budget().snapshot() == before


def test_no_data_stops_and_report_no_data_is_explicit():
    with TestClient(app) as client:
        login(client)
        _, _, rid = start_review(
            client, filters=[{"dimension": "customer_country", "values": ["no-such-country"]}]
        )
        outcome = wait_run(client, rid)
        assert outcome["status"] == "no_data", outcome
        assert outcome["review"]["stop_reason"] == "no_data"
        assert outcome["review"]["query_count"] == 1
        assert outcome["review"]["flags"] == []
        _, report = save_report(client, rid)
        assert report["facts"]["result"]["no_data"]


def test_reports_frozen_restart_expiry_revision_trash_and_escaped_exports():
    with TestClient(app) as client:
        login(client)
        _, _, rid = start_review(client, drill_down=False)
        outcome = wait_run(client, rid)
        assert outcome["status"] == "succeeded", outcome
        body, report = save_report(
            client,
            rid,
            title='<script>alert("title")</script>',
            comment="<img src=x onerror=alert(1)>",
            suggestions="[click](javascript:alert(1))",
        )
        pid, facts = report["id"], report["facts"]
        same = client.post("/api/reports", json=body)
        assert same.status_code == 201 and same.json()["id"] == pid
        assert client.post("/api/reports", json={**body, "title": "different"}).status_code == 409
        html_export = client.get(f"/api/reports/{pid}/export?format=html")
        md_export = client.get(f"/api/reports/{pid}/export?format=markdown")
        assert html_export.status_code == md_export.status_code == 200
        assert "<script>" not in html_export.text and "<img src=x" not in html_export.text
        assert "&lt;script&gt;" in html_export.text and "<svg" in html_export.text
        assert "<script>" not in md_export.text and "\\[click\\]\\(javascript:" in md_export.text
        assert "default-src 'none'" in html_export.headers["content-security-policy"]
        assert client.get(f"/api/reports/{pid}/export?format=pdf").status_code == 422
        patched = client.patch(
            f"/api/reports/{pid}", json={"expected_revision": 1, "title": "更新标题", "comment": "待业务核对"}
        )
        assert patched.status_code == 200 and patched.json()["revision"] == 2
        assert patched.json()["facts"] == facts
        assert (
            client.patch(f"/api/reports/{pid}", json={"expected_revision": 1, "comment": "stale"}).status_code
            == 409
        )
        assert (
            client.patch(f"/api/reports/{pid}", json={"expected_revision": 2, "facts": {}}).status_code == 422
        )
        with connect() as conn:
            conn.execute(
                "UPDATE results SET expires_at=now()-interval '1 minute' WHERE id=%s", [outcome["result_id"]]
            )
        assert client.get(f"/api/results/{outcome['result_id']}").status_code == 410
        assert client.get(f"/api/reports/{pid}").json()["facts"] == facts
        assert (
            client.post("/api/reports", json={**body, "client_request_id": str(uuid4())}).status_code == 410
        )
    # New application lifespan and fresh login simulate restart, not an in-memory cache hit.
    with TestClient(app) as client:
        login(client)
        restored = client.get(f"/api/reports/{pid}").json()
        assert restored["facts"] == facts and restored["revision"] == 2
        before_delete = client.get(f"/api/reports/{pid}/export?format=html").text
        assert before_delete == client.get(f"/api/reports/{pid}/export?format=html").text
        assert any(row["id"] == pid for row in client.get("/api/reports").json()["items"])
        deleted = client.delete(f"/api/reports/{pid}?expected_revision=2")
        assert deleted.status_code == 200 and deleted.json()["revision"] == 3
        assert deleted.json()["deleted_at"]
        assert not any(row["id"] == pid for row in client.get("/api/reports").json()["items"])
        assert any(row["id"] == pid for row in client.get("/api/reports?deleted=true").json()["items"])
        assert client.get(f"/api/reports/{pid}/export").status_code == 409
        assert (
            client.patch(f"/api/reports/{pid}", json={"expected_revision": 3, "comment": "no"}).status_code
            == 409
        )
        assert client.post(f"/api/reports/{pid}/restore", json={"expected_revision": 2}).status_code == 409
        restored = client.post(f"/api/reports/{pid}/restore", json={"expected_revision": 3})
        assert restored.json()["revision"] == 4 and restored.json()["deleted_at"] is None
        assert restored.json()["facts"] == facts
        # A new report is independent even if its source run is later removed.
        with connect() as conn:
            conn.execute("DELETE FROM results WHERE id=%s", [outcome["result_id"]])
            conn.execute("DELETE FROM runs WHERE id=%s", [rid])
        assert client.get(f"/api/reports/{pid}").json()["facts"] == facts
        assert client.get(f"/api/reports/{pid}/export").status_code == 200


def test_report_every_route_owner_and_shrunken_scope_denial():
    with TestClient(app) as client:
        login(client)
        _, _, rid = start_review(client, drill_down=False)
        assert wait_run(client, rid)["status"] == "succeeded"
        _, report = save_report(client, rid)
        pid = report["id"]
        login(client, "analyst")
        assert client.get(f"/api/reports/{pid}").status_code == 404
        assert not any(row["id"] == pid for row in client.get("/api/reports").json()["items"])
        login(client)
        original = identity_for("admin")
        app.dependency_overrides[user] = lambda: Identity(
            original.subject, original.display_name, original.allowed_store_ids[:1], "shrunk"
        )
        try:
            assert not any(row["id"] == pid for row in client.get("/api/reports").json()["items"])
            assert client.get(f"/api/reports/{pid}").status_code == 404
            assert client.get(f"/api/reports/{pid}/export").status_code == 404
            assert client.get(f"/api/reports/{pid}/export?format=html").status_code == 404
            assert (
                client.patch(
                    f"/api/reports/{pid}", json={"expected_revision": 1, "title": "denied"}
                ).status_code
                == 404
            )
            assert client.delete(f"/api/reports/{pid}?expected_revision=1").status_code == 404
            assert (
                client.post(f"/api/reports/{pid}/restore", json={"expected_revision": 1}).status_code == 404
            )
        finally:
            app.dependency_overrides.pop(user, None)
        assert client.get(f"/api/reports/{pid}").status_code == 200


def test_review_cancel_is_idempotent_and_never_publishes_partial_evidence(monkeypatch):
    from app import main

    entered, release = threading.Event(), threading.Event()
    real_query = main.query_metrics

    def blocked_query(*args):
        entered.set()
        assert release.wait(5)
        return real_query(*args)

    monkeypatch.setattr(main, "query_metrics", blocked_query)
    with TestClient(app) as client:
        login(client)
        _, _, rid = start_review(client)
        assert entered.wait(3)
        try:
            assert client.post(f"/api/runs/{rid}/cancel").json()["status"] == "cancelling"
            assert client.post(f"/api/runs/{rid}/cancel").json()["status"] == "cancelling"
        finally:
            release.set()
        outcome = wait_run(client, rid)
        assert outcome["status"] == "cancelled" and outcome["result"] is None and outcome["review"] is None
        assert client.post(f"/api/runs/{rid}/cancel").json()["status"] == "cancelled"
        assert (
            client.post(
                "/api/reports", json={"run_id": rid, "title": "cancelled", "client_request_id": str(uuid4())}
            ).status_code
            == 409
        )
        with connect() as conn:
            assert (
                conn.execute("SELECT count(*) AS n FROM results WHERE run_id=%s", [rid]).fetchone()["n"] == 0
            )


def test_review_tool_failure_is_failed_not_clarification(monkeypatch):
    from app import main

    def broken(*args):
        raise QueryError("FIXTURE_FAILURE", "测试受控错误")

    monkeypatch.setattr(main, "query_metrics", broken)
    with TestClient(app) as client:
        login(client)
        _, _, rid = start_review(client)
        outcome = wait_run(client, rid)
        assert outcome["status"] == "failed" and outcome["error_code"] == "RUN_FAILED", outcome
        assert "测试受控错误" not in outcome["answer"]
        assert outcome["result"] is None and outcome["review"] is None


def test_history_and_expired_run_never_leak_after_scope_reduction():
    with TestClient(app) as client:
        login(client)
        cid, _, rid = start_review(client, drill_down=False)
        outcome = wait_run(client, rid)
        assert outcome["status"] == "succeeded"
        with connect() as conn:
            conn.execute(
                "UPDATE results SET expires_at=now()-interval '1 minute' WHERE id=%s", [outcome["result_id"]]
            )
        original = identity_for("admin")
        app.dependency_overrides[user] = lambda: Identity(
            original.subject, original.display_name, original.allowed_store_ids[:1], "shrunk"
        )
        try:
            assert client.get(f"/api/runs/{rid}").status_code == 403
            history = client.get(f"/api/conversations/{cid}").json()
            assert history["current_query"] is None
            answer = history["history"][-1]
            assert answer["result_id"] is None and answer["status"] == "failed"
            assert outcome["answer"] not in answer["content"]
            assert "不可访问" in answer["content"]
            failed_id = str(uuid4())
            with connect() as conn:
                conn.execute(
                    "INSERT INTO runs(id,conversation_id,subject,client_request_id,status,progress_stage,message,answer) VALUES(%s,%s,'admin',%s,'needs_clarification','needs_clarification','fixture','请说明期间')",
                    [failed_id, cid, str(uuid4())],
                )
                conn.execute("UPDATE conversations SET latest_run_id=%s WHERE id=%s", [failed_id, cid])
            assert client.get(f"/api/conversations/{cid}").json()["current_query"] is None
        finally:
            app.dependency_overrides.pop(user, None)


def test_terminal_writes_honor_database_cancellation_before_event_and_never_revive():
    from app.main import update_run

    with TestClient(app) as client:
        login(client)
        cid = client.post("/api/conversations").json()["id"]
        for attempted_status in ["failed", "needs_clarification", "unsupported"]:
            rid = str(uuid4())
            with connect() as conn:
                conn.execute(
                    "INSERT INTO runs(id,conversation_id,subject,client_request_id,status,progress_stage,message,cancel_requested) VALUES(%s,%s,'admin',%s,'cancelling','cancelling','fixture',true)",
                    [rid, cid, str(uuid4())],
                )
            update_run(
                rid,
                status=attempted_status,
                progress_stage=attempted_status,
                answer="must not win",
                error_code="FIXTURE",
            )
            update_run(rid, status="analyzing", progress_stage="analyzing")
            update_run(rid, status="failed", progress_stage="failed", answer="late failure")
            with connect() as conn:
                row = conn.execute(
                    "SELECT status,progress_stage,error_code,answer FROM runs WHERE id=%s", [rid]
                ).fetchone()
            assert row["status"] == row["progress_stage"] == "cancelled"
            assert row["error_code"] is None and "取消" in row["answer"]


def test_export_null_chart_is_not_zero_and_cross_version_evidence_fails():
    from app.report_export import svg_chart

    result = {
        "query": {"group_by": ["store"]},
        "metrics": ["avg_order_value"],
        "comparison_totals": {"avg_order_value": "10"},
        "rows": [
            {"store": "only in current", "delta_avg_order_value": None},
            {"store": "unchanged", "delta_avg_order_value": "0"},
        ],
    }
    svg = svg_chart(result)
    assert "不适用</text>" in svg and ">None<" not in svg and ">0</text>" in svg
    session = ReviewSession(ReviewRequest.model_validate(review_request()), [])
    session.accept(
        {
            "no_data": False,
            "deltas": {"sales_amount": "10"},
            "metadata": {"dataset_version": "a", "metric_version": "metrics_v1"},
        }
    )
    with pytest.raises(QueryError, match="版本"):
        session.accept({"metadata": {"dataset_version": "b", "metric_version": "metrics_v1"}})
