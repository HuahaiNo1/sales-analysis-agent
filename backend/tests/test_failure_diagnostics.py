"""Unpaid failure regressions; no provider, database, credentials or external I/O."""

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app import main
from app.agent import AgentBoundaryError
from app.db import Identity
from app.query import QueryError
from app.schemas import ReviewRequest
from fastapi.testclient import TestClient

SECRET = "sk-secret-DO-NOT-PERSIST raw prompt private totals"
IDENTITY = Identity("admin", "Admin", (1, 2), "Test scope")


async def failed_run(monkeypatch, failure, *, review=None):
    rid = str(uuid4())
    updates = []
    monkeypatch.setattr(main, "update_run", lambda run_id, **fields: updates.append(fields))
    monkeypatch.setattr(main, "catalog_for", lambda identity: {"categories": []})
    monkeypatch.setattr(main.settings, "agent_mode", "mock")

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(main, "run_agent", fail)
    main.CANCELLATIONS[rid] = threading.Event()
    await main.process_run(rid, IDENTITY, "test question", None, review)
    assert rid not in main.CANCELLATIONS
    return rid, updates[-1]


@pytest.mark.asyncio
async def test_failed_boundary_persists_bounded_diagnostics(monkeypatch):
    exc = AgentBoundaryError(SECRET)
    exc.failure_diagnostics = {
        "stage": "finalize",
        "boundary_reason": "analysis_missing",
        "model_calls": 9,
        "query_specs": 4,
        "tool_counts": {"query_metrics": 4, "analyze_result": 0, SECRET: 99},
        "prompt": SECRET,
        "tool_arguments": {"secret": SECRET},
    }
    _, failed = await failed_run(monkeypatch, exc)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "AGENT_BOUNDARY_ERROR"
    runtime = failed["agent_runtime"].obj
    failure = runtime["failure"]
    assert failure["stage"] == "finalize"
    assert failure["boundary_reason"] == "analysis_missing"
    assert failure["model_calls"] == 9 and failure["query_specs"] == 4
    assert failure["tool_counts"] == {"query_metrics": 4, "analyze_result": 0}
    assert SECRET not in json.dumps({**failed, "agent_runtime": runtime})
    assert failed.get("result_id") is None


@pytest.mark.asyncio
async def test_arbitrary_exception_code_text_and_metadata_cannot_leak(monkeypatch):
    exc = RuntimeError(SECRET)
    exc.code = SECRET
    exc.failure_diagnostics = {
        "stage": SECRET,
        "boundary_reason": SECRET,
        "model_calls": SECRET,
        "query_specs": -1,
        "tool_counts": {"query_metrics": True, "task": 10**100, SECRET: 1},
        "traceback": SECRET,
    }
    _, failed = await failed_run(monkeypatch, exc)
    assert failed["error_code"] == "RUN_FAILED"
    runtime = failed["agent_runtime"].obj
    assert runtime["failure"]["stage"] == "agent"
    assert runtime["failure"]["boundary_reason"] == "other_error"
    assert runtime["failure"]["model_calls"] is None
    assert runtime["failure"]["query_specs"] is None
    assert runtime["failure"]["tool_counts"] == {}
    assert SECRET not in json.dumps({**failed, "agent_runtime": runtime})


@pytest.mark.asyncio
async def test_scope_failure_no_partial_result_and_safe_reason(monkeypatch):
    monkeypatch.setattr(main, "identity_for", lambda subject: Identity(subject, "Admin", (1,), "Shrunk"))
    review = ReviewRequest.model_validate(
        {
            "period": {"start": "2025-09-01", "end": "2025-10-01"},
            "comparison": {"start": "2025-08-01", "end": "2025-09-01"},
        }
    )
    _, failed = await failed_run(monkeypatch, AssertionError("agent must not start"), review=review)
    assert failed["error_code"] == "SCOPE_CHANGED"
    assert failed.get("result_id") is None
    assert failed["agent_runtime"].obj["failure"]["stage"] == "preflight"
    assert failed["agent_runtime"].obj["failure"]["error_code"] == "SCOPE_CHANGED"


@pytest.mark.asyncio
async def test_query_error_does_not_persist_arbitrary_message(monkeypatch):
    _, failed = await failed_run(monkeypatch, QueryError("SCOPE_CHANGED", SECRET))
    assert failed["error_code"] == "SCOPE_CHANGED"
    assert SECRET not in failed["answer"]


@pytest.mark.asyncio
async def test_timeout_also_has_safe_metadata(monkeypatch):
    _, failed = await failed_run(monkeypatch, TimeoutError(SECRET))
    assert failed["error_code"] == "RUN_TIMEOUT"
    runtime = failed["agent_runtime"].obj
    assert runtime["failure"]["error_code"] == "RUN_TIMEOUT"
    assert runtime["failure"]["stage"] == "agent"
    assert SECRET not in json.dumps({**failed, "agent_runtime": runtime})


@pytest.mark.asyncio
async def test_unknown_query_error_code_is_not_persisted(monkeypatch):
    _, failed = await failed_run(monkeypatch, QueryError(SECRET, SECRET))
    assert failed["error_code"] == "RUN_FAILED"
    assert SECRET not in json.dumps({**failed, "agent_runtime": failed["agent_runtime"].obj})


def test_failed_metadata_read_keeps_existing_owner_auth(monkeypatch):
    rid, cid = uuid4(), uuid4()
    runtime = {"failure": {"stage": "finalize", "boundary_reason": "analysis_missing"}}
    row = dict(
        id=rid,
        conversation_id=cid,
        status="failed",
        progress_stage="failed",
        answer="分析失败",
        error_code="AGENT_BOUNDARY_ERROR",
        result_id=None,
        kind="review",
        review_payload=None,
        agent_runtime=runtime,
        created_at=datetime.now(timezone.utc),
        cancel_requested=False,
    )

    class Connection:
        def execute(self, sql, params):
            assert "WHERE id=%s AND subject=%s" in sql
            self.row = row if params == [rid, IDENTITY.subject] else None
            return self

        def fetchone(self):
            return self.row

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(main, "connect", connect)
    monkeypatch.setattr(main, "shared_budget", lambda: type("Budget", (), {"snapshot": lambda self: {}})())
    client = TestClient(main.app)  # No lifespan: no PostgreSQL access.
    assert client.get(f"/api/runs/{rid}").status_code == 401
    main.app.dependency_overrides[main.user] = lambda: IDENTITY
    try:
        response = client.get(f"/api/runs/{rid}")
        assert response.status_code == 200
        assert response.json()["runtime"] == runtime
        assert response.json()["result"] is None and response.json()["review"] is None
        main.app.dependency_overrides[main.user] = lambda: Identity("analyst", "Analyst", (1,), "Other")
        response = client.get(f"/api/runs/{rid}")
        assert response.status_code == 404
        assert "analysis_missing" not in response.text
    finally:
        main.app.dependency_overrides.pop(main.user, None)
        client.close()
