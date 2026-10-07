from __future__ import annotations

import asyncio
import csv
import hmac
import io
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .agent import AgentToolError, run_agent, runtime_smoke_check
from .budget import shared_budget
from .catalog import DIMENSIONS, METRICS
from .config import settings
from .db import Identity, connect, dataset_metadata, identity_for
from .presentation import CHART_LABELS, presentation_request
from .query import QueryError, dashboard, query_metrics
from .schemas import LoginRequest, QuerySpec, RunRequest

TERMINAL = {"succeeded", "no_data", "needs_clarification", "unsupported", "failed", "cancelled", "expired"}
TASKS: dict[str, asyncio.Task] = {}
CANCELLATIONS: dict[str, threading.Event] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = runtime_smoke_check()
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status='failed',progress_stage='failed',error_code='SERVER_RESTARTED',answer='服务已重启，请重新提交未完成的问题',updated_at=now() WHERE status NOT IN ('succeeded','no_data','needs_clarification','unsupported','failed','cancelled','expired')"
        )
    yield
    for event in CANCELLATIONS.values():
        event.set()
    if TASKS:
        await asyncio.wait(list(TASKS.values()), timeout=3)


app = FastAPI(title="Contoso Sales Analysis Agent", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def user(request: Request) -> Identity:
    token = request.cookies.get("sales_session")
    if not token:
        raise HTTPException(401, "请先登录演示账号")
    with connect() as conn:
        row = conn.execute(
            "SELECT subject FROM sessions WHERE token_hash=%s AND expires_at>now()",
            [sha256(token.encode()).hexdigest()],
        ).fetchone()
    if not row:
        raise HTTPException(401, "会话已过期，请重新登录")
    return identity_for(row["subject"])


def user_view(identity):
    return {
        "username": identity.subject,
        "display_name": identity.display_name,
        "scope_label": identity.scope_label,
        "allowed_store_ids": list(identity.allowed_store_ids),
        "scope_version": "scope-v1",
    }


def catalog_for(identity):
    metadata = dataset_metadata()
    with connect() as conn:
        stores = conn.execute(
            "SELECT store_key AS key,store_name AS label,store_country AS country FROM dim_store WHERE store_key=ANY(%s) ORDER BY store_key",
            [list(identity.allowed_store_ids)],
        ).fetchall()
        categories = conn.execute(
            "SELECT DISTINCT category_key AS key,category_name AS label FROM dim_product ORDER BY category_key"
        ).fetchall()
        countries = conn.execute(
            "SELECT DISTINCT customer_country AS key,customer_country AS label FROM dim_customer_geo ORDER BY customer_country"
        ).fetchall()
    return {
        **metadata,
        "metrics": METRICS,
        "dimensions": DIMENSIONS,
        "stores": stores,
        "categories": categories,
        "countries": countries,
        "model_mode": settings.agent_mode,
        "budget": shared_budget().snapshot(),
        "scope": user_view(identity),
        "business_date": datetime.now(timezone.utc).date().isoformat(),
        "supported_examples": [
            "2025年销售额和订单数按月看",
            "2025年商品毛利按类别看前十",
            "2025年9月比8月销售额变化来自哪些类别，用瀑布图",
            "2025年平均订单金额按门店看",
        ],
        "mock_notice": "离线演练：真实 DeepAgents 图、工具和数据库，使用本地确定性模型；自然语言范围限示例问法",
    }


@app.get("/api/health")
def health():
    try:
        metadata = dataset_metadata()
        return {
            "status": "ok",
            "dataset_ready": True,
            "model_mode": settings.agent_mode,
            "dataset_version": metadata["dataset_version"],
            "framework": "deepagents",
            "live_calls": shared_budget().snapshot()["calls_reserved"],
        }
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "error_code": "DATASET_NOT_READY"}
        )


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response):
    if body.username not in {"admin", "analyst"} or not hmac.compare_digest(body.password, "demo123"):
        raise HTTPException(401, "演示账号或密码不正确")
    identity = identity_for(body.username)
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO sessions VALUES (%s,%s,%s)",
            [
                sha256(token.encode()).hexdigest(),
                identity.subject,
                datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
            ],
        )
    response.set_cookie(
        "sales_session", token, httponly=True, samesite="strict", max_age=settings.session_hours * 3600
    )
    return user_view(identity)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("sales_session", "")
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=%s", [sha256(token.encode()).hexdigest()])
    response.delete_cookie("sales_session")
    return {"ok": True}


@app.get("/api/me")
def me(identity: Identity = Depends(user)):
    return user_view(identity)


@app.get("/api/catalog")
def catalog(identity: Identity = Depends(user)):
    return catalog_for(identity)


@app.get("/api/dashboard")
def get_dashboard(
    start: date = date(2025, 1, 1), end: date = date(2026, 1, 1), identity: Identity = Depends(user)
):
    try:
        return dashboard(identity, start, end)
    except (QueryError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from None


@app.post("/api/conversations", status_code=201)
def create_conversation(identity: Identity = Depends(user)):
    cid = uuid4()
    with connect() as conn:
        conn.execute("INSERT INTO conversations(id,subject) VALUES (%s,%s)", [cid, identity.subject])
    return {"id": str(cid), "conversation_id": str(cid), "state_version": 0, "latest_run_id": None}


def owned_conversation(conn, cid, identity, lock=False):
    row = conn.execute(
        "SELECT * FROM conversations WHERE id=%s AND subject=%s" + (" FOR UPDATE" if lock else ""),
        [cid, identity.subject],
    ).fetchone()
    if not row:
        raise HTTPException(404, "会话不存在")
    return row


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, identity: Identity = Depends(user)):
    with connect() as conn:
        conversation = owned_conversation(conn, conversation_id, identity)
        runs = conn.execute(
            "SELECT id,status,message,answer,result_id,created_at FROM runs WHERE conversation_id=%s AND subject=%s ORDER BY created_at",
            [conversation_id, identity.subject],
        ).fetchall()
    history = []
    for run in runs:
        history.append(
            {
                "role": "user",
                "content": run["message"],
                "run_id": str(run["id"]),
                "created_at": run["created_at"].isoformat(),
            }
        )
        if run["answer"]:
            history.append(
                {
                    "role": "assistant",
                    "content": run["answer"],
                    "run_id": str(run["id"]),
                    "result_id": str(run["result_id"]) if run["result_id"] else None,
                    "status": run["status"],
                    "created_at": run["created_at"].isoformat(),
                }
            )
    return {
        "id": str(conversation["id"]),
        "conversation_id": str(conversation["id"]),
        "state_version": conversation["state_version"],
        "latest_run_id": str(conversation["latest_run_id"]) if conversation["latest_run_id"] else None,
        "current_query": conversation["current_query"],
        "history": history,
    }


@app.get("/api/conversations")
def list_conversations(identity: Identity = Depends(user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,state_version,latest_run_id,created_at FROM conversations WHERE subject=%s ORDER BY created_at DESC LIMIT 30",
            [identity.subject],
        ).fetchall()
    return rows


def update_run(run_id, **fields):
    allowed = {"status", "progress_stage", "answer", "error_code", "result_id", "agent_runtime"}
    if not set(fields) <= allowed:
        raise RuntimeError("Invalid internal run update")
    with connect() as conn:
        clause = ",".join(f"{key}=%s" for key in fields)
        conn.execute(f"UPDATE runs SET {clause},updated_at=now() WHERE id=%s", [*fields.values(), run_id])


async def process_run(run_id: str, identity: Identity, message: str, previous: dict | None):
    event = CANCELLATIONS[run_id]
    generated = {}
    try:
        update_run(run_id, status="querying", progress_stage="querying")

        def query_callback(spec):
            if event.is_set():
                raise QueryError("CANCELLED", "运行已取消")
            result = query_metrics(spec, identity, run_id)
            generated[result["id"]] = result
            update_run(run_id, status="analyzing", progress_stage="analyzing")
            return {
                "result_id": result["id"],
                "row_count": result["row_count"],
                "status": "NO_DATA" if result["no_data"] else "SUCCEEDED",
            }

        def analysis_callback(result_id):
            if result_id not in generated:
                raise QueryError("FORBIDDEN_RESULT", "无权访问结果")
            payload = generated[result_id]
            return {
                k: payload[k]
                for k in [
                    "totals",
                    "comparison_totals",
                    "deltas",
                    "change_pct",
                    "observations",
                    "chart",
                    "no_data",
                    "row_count",
                ]
            }

        outcome = await run_agent(
            message,
            previous,
            catalog_for(identity),
            query_callback,
            analysis_callback,
            event.is_set,
            mode=settings.agent_mode,
            api_key=settings.deepseek_api_key.get_secret_value() if settings.agent_mode == "live" else None,
        )
        if event.is_set():
            update_run(
                run_id, status="cancelled", progress_stage="cancelled", answer="运行已取消，未发布新的结果"
            )
            return
        if outcome["status"] == "SUCCEEDED":
            payload = generated[outcome["result"]["result_id"]]
            status = "no_data" if payload["no_data"] else "succeeded"
            # Trusted deterministic observations are the numeric answer. Model prose cannot replace them.
            answer = ("没有匹配的数据。" if payload["no_data"] else "") + "；".join(payload["observations"])
            with connect() as conn:
                locked = conn.execute(
                    "SELECT cancel_requested FROM runs WHERE id=%s FOR UPDATE", [run_id]
                ).fetchone()
                if locked["cancel_requested"]:
                    conn.execute(
                        "UPDATE runs SET status='cancelled',progress_stage='cancelled',answer='运行已取消，未发布新的结果',updated_at=now() WHERE id=%s",
                        [run_id],
                    )
                    return
                conn.execute(
                    "INSERT INTO results(id,run_id,subject,scope_hash,payload,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
                    [
                        payload["id"],
                        run_id,
                        identity.subject,
                        identity.scope_hash,
                        Jsonb(payload),
                        payload["expires_at"],
                    ],
                )
                conn.execute(
                    "UPDATE runs SET status=%s,progress_stage=%s,answer=%s,result_id=%s,agent_runtime=%s,updated_at=now() WHERE id=%s",
                    [status, status, answer, payload["id"], Jsonb(outcome["runtime"]), run_id],
                )
                conn.execute(
                    "UPDATE conversations SET current_query=%s WHERE latest_run_id=%s",
                    [Jsonb(payload["query"]), run_id],
                )
        else:
            status = "unsupported" if outcome["status"] == "UNSUPPORTED_QUERY" else "needs_clarification"
            update_run(
                run_id,
                status=status,
                progress_stage=status,
                answer=outcome["answer"],
                agent_runtime=Jsonb(outcome["runtime"]),
            )
    except asyncio.CancelledError:
        update_run(run_id, status="cancelled", progress_stage="cancelled", answer="运行已取消")
        raise
    except Exception as exc:
        if event.is_set():
            update_run(
                run_id, status="cancelled", progress_stage="cancelled", answer="运行已取消，未发布新的结果"
            )
        else:
            code = getattr(
                exc, "code", "QUERY_VALIDATION_FAILED" if isinstance(exc, ValidationError) else "RUN_FAILED"
            )
            if isinstance(exc, ValidationError):
                answer = "；".join(e["msg"] for e in exc.errors())
            elif isinstance(exc, (QueryError, AgentToolError)) or code.startswith("BUDGET"):
                answer = str(exc)
            else:
                answer = "本次分析未完成，请缩小问题范围或稍后重试；未发布新结果"
            update_run(run_id, status="failed", progress_stage="failed", error_code=code, answer=answer)
    finally:
        CANCELLATIONS.pop(run_id, None)
        TASKS.pop(run_id, None)


def presentation_outcome(chart_type: str, previous_run: dict | None, identity: Identity) -> dict:
    """Reauthorize the same frozen result; read control records, never aggregate or call a model."""
    runtime = {
        "kind": "presentation_reuse",
        "model_mode": settings.agent_mode,
        "model_calls": 0,
        "aggregate_queries": 0,
    }
    outcome = {
        "status": "needs_clarification",
        "result_id": None,
        "error_code": None,
        "runtime": runtime,
        "answer": "请选择表格、柱状图、折线图或瀑布图",
    }
    source_id = None
    if previous_run:
        if previous_run["status"] in {"succeeded", "no_data"}:
            source_id = previous_run["result_id"]
        elif previous_run["status"] == "needs_clarification":
            previous_runtime = previous_run.get("agent_runtime") or {}
            if previous_runtime.get("kind") == "presentation_reuse":
                source_id = previous_runtime.get("pending_result_id")
    if not source_id:
        outcome.update(
            error_code="NO_RESULT_TO_REUSE",
            answer="当前会话没有可复用的有效结果，请先查询指标，再选择展示方式",
        )
        return outcome
    try:
        payload = result_for(source_id, identity)
    except HTTPException as exc:
        outcome.update(
            status="expired" if exc.status_code == 410 else "failed",
            error_code="RESULT_EXPIRED" if exc.status_code == 410 else "RESULT_ACCESS_CHANGED",
            answer="上次结果已过期，请重新查询"
            if exc.status_code == 410
            else "上次结果已不可访问，请在当前授权范围重新查询",
        )
        return outcome
    published = dataset_metadata()
    if any(
        payload["metadata"].get(key) != published.get(key) for key in ("dataset_version", "metric_version")
    ):
        outcome.update(
            status="expired",
            error_code="DATASET_VERSION_CHANGED",
            answer="数据或指标版本已变化，请重新查询后再切换展示方式",
        )
        return outcome
    if chart_type == "unspecified":
        runtime["pending_result_id"] = payload["id"]
        return outcome
    try:
        QuerySpec.model_validate({**payload["query"], "chart": chart_type})
    except ValidationError as exc:
        runtime["pending_result_id"] = payload["id"]
        outcome.update(error_code="CHART_INCOMPATIBLE", answer="；".join(e["msg"] for e in exc.errors()))
        return outcome
    runtime["presentation"] = {"chart_type": chart_type, "reused_result_id": payload["id"]}
    outcome.update(
        status="no_data" if payload["no_data"] else "succeeded",
        result_id=payload["id"],
        answer=f"已改为{CHART_LABELS[chart_type]}，复用原结果；期间、数据版本和数值不变，未重新查询或调用模型",
    )
    return outcome


@app.post("/api/conversations/{conversation_id}/runs", status_code=202)
async def create_run(conversation_id: UUID, body: RunRequest, identity: Identity = Depends(user)):
    with connect() as conn:
        conversation = owned_conversation(conn, conversation_id, identity, lock=True)
        previous = conn.execute(
            "SELECT id,message,status FROM runs WHERE conversation_id=%s AND client_request_id=%s",
            [conversation_id, body.client_request_id],
        ).fetchone()
        if previous:
            if previous["message"] != body.message:
                raise HTTPException(409, "同一请求 ID 不能用于不同问题")
            return {
                "run_id": str(previous["id"]),
                "status": previous["status"],
                "state_version": conversation["state_version"],
                "idempotent": True,
            }
        if conversation["state_version"] != body.expected_state_version:
            raise HTTPException(409, "会话已更新，请刷新后重试")
        active = None
        if conversation["latest_run_id"]:
            active = conn.execute(
                "SELECT status,result_id,agent_runtime FROM runs WHERE id=%s AND subject=%s",
                [conversation["latest_run_id"], identity.subject],
            ).fetchone()
            if active and active["status"] not in TERMINAL:
                raise HTTPException(409, "当前会话仍有运行中的问题，请等待或取消")
        run_id = str(uuid4())
        conn.execute(
            "INSERT INTO runs(id,conversation_id,subject,client_request_id,status,progress_stage,message) VALUES(%s,%s,%s,%s,'queued','queued',%s)",
            [run_id, conversation_id, identity.subject, body.client_request_id, body.message],
        )
        conn.execute(
            "UPDATE conversations SET latest_run_id=%s,state_version=state_version+1 WHERE id=%s",
            [run_id, conversation_id],
        )
        chart_type = presentation_request(body.message)
        if chart_type is not None:
            outcome = presentation_outcome(chart_type, active, identity)
            conn.execute(
                "UPDATE runs SET status=%s,progress_stage=%s,answer=%s,error_code=%s,result_id=%s,agent_runtime=%s,updated_at=now() WHERE id=%s",
                [
                    outcome["status"],
                    outcome["status"],
                    outcome["answer"],
                    outcome["error_code"],
                    outcome["result_id"],
                    Jsonb(outcome["runtime"]),
                    run_id,
                ],
            )
            return {
                "run_id": run_id,
                "status": outcome["status"],
                "state_version": conversation["state_version"] + 1,
            }
    CANCELLATIONS[run_id] = threading.Event()
    TASKS[run_id] = asyncio.create_task(
        process_run(run_id, identity, body.message, conversation["current_query"])
    )
    return {"run_id": run_id, "status": "queued", "state_version": conversation["state_version"] + 1}


def result_for(result_id, identity):
    with connect() as conn:
        row = conn.execute(
            "SELECT payload,scope_hash,expires_at FROM results WHERE id=%s AND subject=%s",
            [result_id, identity.subject],
        ).fetchone()
    if not row:
        raise HTTPException(404, "结果不存在")
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "结果已过期，请重新查询")
    if not hmac.compare_digest(row["scope_hash"], identity.scope_hash):
        raise HTTPException(403, "授权范围已变化，请重新查询")
    return row["payload"]


@app.get("/api/runs/{run_id}")
def get_run(run_id: UUID, identity: Identity = Depends(user)):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE id=%s AND subject=%s", [run_id, identity.subject]
        ).fetchone()
    if not row:
        raise HTTPException(404, "运行不存在")
    payload = None
    if row["result_id"]:
        try:
            payload = result_for(row["result_id"], identity)
        except HTTPException as exc:
            if exc.status_code == 410:
                row["status"] = "expired"
                row["progress_stage"] = "expired"
            else:
                raise
    return {
        "id": str(row["id"]),
        "run_id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "status": row["status"],
        "progress_stage": row["progress_stage"],
        "message": row["answer"],
        "answer": row["answer"],
        "error_code": row["error_code"],
        "result_id": str(row["result_id"]) if row["result_id"] else None,
        "result": payload,
        "runtime": row["agent_runtime"],
        "presentation": (row["agent_runtime"] or {}).get("presentation"),
        "budget": shared_budget().snapshot(),
        "created_at": row["created_at"].isoformat(),
        "cancel_requested": row["cancel_requested"],
    }


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: UUID, identity: Identity = Depends(user)):
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM runs WHERE id=%s AND subject=%s FOR UPDATE", [run_id, identity.subject]
        ).fetchone()
        if not row:
            raise HTTPException(404, "运行不存在")
        if row["status"] in TERMINAL:
            return {"run_id": str(run_id), "status": row["status"]}
        conn.execute(
            "UPDATE runs SET cancel_requested=true,status='cancelling',progress_stage='cancelling',updated_at=now() WHERE id=%s",
            [run_id],
        )
    event = CANCELLATIONS.get(str(run_id))
    if event:
        event.set()
    return {"run_id": str(run_id), "status": "cancelling"}


@app.get("/api/results/{result_id}")
def get_result(result_id: UUID, identity: Identity = Depends(user)):
    return result_for(result_id, identity)


def csv_cell(value):
    text = "" if value is None else str(value)
    # Labels can contain spreadsheet formulas. Prefix only text cells; known numeric columns remain numeric.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


@app.get("/api/results/{result_id}/csv")
def export_csv(result_id: UUID, identity: Identity = Depends(user)):
    payload = result_for(result_id, identity)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = payload["columns"]
    writer.writerow([c["label"] for c in columns])
    for row in payload["rows"]:
        writer.writerow(
            [csv_cell(row.get(c["key"])) if c["kind"] == "dimension" else row.get(c["key"]) for c in columns]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="sales-{result_id}.csv"',
            "X-Export-Scope": "authorized-displayed-aggregate",
            "X-Source-Result-SHA256": payload["sha256"],
        },
    )
