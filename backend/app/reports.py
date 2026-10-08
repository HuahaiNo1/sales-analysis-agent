"""Owner-only durable reports. Source runs/results are provenance, never storage dependencies."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.types.json import Jsonb

from .catalog import METRICS
from .db import Identity, connect
from .report_export import export_report
from .schemas import ReportCreate, ReportRevision, ReportUpdate

MAX_SNAPSHOT_BYTES = 1024 * 1024


def summary(row: dict) -> dict:
    return {
        key: str(row[key])
        if key in {"id", "source_run_id"}
        else row[key].isoformat()
        if key in {"created_at", "updated_at", "deleted_at"} and row[key]
        else row[key]
        for key in (
            "id",
            "title",
            "kind",
            "revision",
            "created_at",
            "updated_at",
            "deleted_at",
            "source_run_id",
            "scope_label",
        )
    }


def detail(row: dict) -> dict:
    return {
        **summary(row),
        "comment": row["comment"],
        "suggestions": row["suggestions"],
        "facts": row["facts"],
    }


def authorized_report(conn, report_id, identity: Identity, lock=False):
    row = conn.execute(
        "SELECT * FROM reports WHERE id=%s AND subject=%s AND scope_store_ids <@ %s::integer[]"
        + (" FOR UPDATE" if lock else ""),
        [report_id, identity.subject, list(identity.allowed_store_ids)],
    ).fetchone()
    if not row:
        # Neither existence nor title/old aggregates leak after a scope reduction.
        raise HTTPException(404, "报告不存在或不在当前授权范围内")
    return row


def check_revision(row, expected):
    if row["revision"] != expected:
        raise HTTPException(409, "报告已被更新，请重新打开后再保存")


def frozen_facts(run: dict, result: dict, identity: Identity) -> dict:
    review = run.get("review_payload")
    if run["kind"] == "review" and not review:
        raise HTTPException(409, "复盘证据尚未完成")
    evidence = review["evidence"] if review else [{"result": result}]
    if len(evidence) > 4 or any(len(e["result"]["rows"]) > 101 for e in evidence):
        raise HTTPException(413, "报告聚合证据超出保存上限")
    metrics = set()
    for item in evidence:
        payload = item["result"]
        if not payload.get("complete"):
            raise HTTPException(409, "不能保存不完整证据")
        if any(
            payload["metadata"].get(key) != result["metadata"].get(key)
            for key in ("dataset_version", "metric_version")
        ):
            raise HTTPException(409, "证据版本不一致，请重新运行复盘")
        metrics.update(payload["metrics"])
    facts = deepcopy(
        {
            "schema_version": "report_v1",
            "kind": run["kind"],
            "generated_at": result["generated_at"],
            "dataset_version": result["metadata"]["dataset_version"],
            "metric_version": result["metadata"]["metric_version"],
            "scope": {
                "allowed_store_ids": list(identity.allowed_store_ids),
                "scope_label": identity.scope_label,
                "scope_version": "scope-v1",
            },
            "metric_definitions": [metric for metric in METRICS if metric["id"] in metrics],
            "result": result,
            "review": review,
        }
    )
    if len(json.dumps(facts, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, "报告快照超过 1 MiB 保存上限，请缩小范围")
    return facts


def reports_router(user: Callable, result_for: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/reports", tags=["reports"])

    @router.post("", status_code=201)
    def create_report(body: ReportCreate, identity: Identity = Depends(user)):
        request = body.model_dump(mode="json")
        with connect() as conn:
            existing = conn.execute(
                "SELECT id,create_request FROM reports WHERE subject=%s AND client_request_id=%s",
                [identity.subject, body.client_request_id],
            ).fetchone()
            if existing:
                row = authorized_report(conn, existing["id"], identity)
                if existing["create_request"] != request:
                    raise HTTPException(409, "同一请求 ID 不能保存不同报告")
                return detail(row)
            run = conn.execute(
                "SELECT * FROM runs WHERE id=%s AND subject=%s", [body.run_id, identity.subject]
            ).fetchone()
            if not run:
                raise HTTPException(404, "运行不存在")
            if run["status"] not in {"succeeded", "no_data"} or not run["result_id"]:
                raise HTTPException(409, "只能保存已完成的分析；失败、取消或运行中结果不可保存")
            result = result_for(run["result_id"], identity)
            facts = frozen_facts(run, result, identity)
            report_id = uuid4()
            inserted = conn.execute(
                """INSERT INTO reports(id,subject,source_run_id,client_request_id,create_request,title,comment,suggestions,kind,scope_store_ids,scope_label,facts)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(subject,client_request_id) DO NOTHING RETURNING *""",
                [
                    report_id,
                    identity.subject,
                    body.run_id,
                    body.client_request_id,
                    Jsonb(request),
                    body.title,
                    body.comment,
                    body.suggestions,
                    run["kind"],
                    list(identity.allowed_store_ids),
                    identity.scope_label,
                    Jsonb(facts),
                ],
            ).fetchone()
            if inserted:
                return detail(inserted)
            existing = conn.execute(
                "SELECT id,create_request FROM reports WHERE subject=%s AND client_request_id=%s",
                [identity.subject, body.client_request_id],
            ).fetchone()
            row = authorized_report(conn, existing["id"], identity)
            if existing["create_request"] != request:
                raise HTTPException(409, "同一请求 ID 不能保存不同报告")
            return detail(row)

    @router.get("")
    def list_reports(
        deleted: bool = False,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
        identity: Identity = Depends(user),
    ):
        with connect() as conn:
            rows = conn.execute(
                """SELECT id,title,kind,revision,created_at,updated_at,deleted_at,source_run_id,scope_label
                FROM reports WHERE subject=%s AND scope_store_ids <@ %s::integer[]
                AND (deleted_at IS NOT NULL)=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""",
                [identity.subject, list(identity.allowed_store_ids), deleted, limit, offset],
            ).fetchall()
        return {"items": [summary(row) for row in rows], "limit": limit, "offset": offset}

    @router.get("/{report_id}")
    def get_report(report_id: UUID, identity: Identity = Depends(user)):
        with connect() as conn:
            return detail(authorized_report(conn, report_id, identity))

    @router.patch("/{report_id}")
    def update_report(report_id: UUID, body: ReportUpdate, identity: Identity = Depends(user)):
        with connect() as conn:
            row = authorized_report(conn, report_id, identity, lock=True)
            check_revision(row, body.expected_revision)
            if row["deleted_at"]:
                raise HTTPException(409, "请先恢复报告再编辑")
            updates = {
                key: value
                for key, value in body.model_dump(exclude_unset=True).items()
                if key != "expected_revision" and value != row[key]
            }
            if updates:
                clause = ",".join(
                    f"{key}=%s" for key in updates
                )  # Pydantic-forbidden extras: only the three annotation names.
                row = conn.execute(
                    f"UPDATE reports SET {clause},revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                    [*updates.values(), report_id],
                ).fetchone()
            return detail(row)

    def transition(report_id, expected, identity, deleting):
        with connect() as conn:
            row = authorized_report(conn, report_id, identity, lock=True)
            check_revision(row, expected)
            if bool(row["deleted_at"]) != deleting:
                row = conn.execute(
                    "UPDATE reports SET deleted_at=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                    [datetime.now(timezone.utc) if deleting else None, report_id],
                ).fetchone()
            return detail(row)

    @router.delete("/{report_id}")
    def delete_report(
        report_id: UUID, expected_revision: int = Query(..., ge=1), identity: Identity = Depends(user)
    ):
        return transition(report_id, expected_revision, identity, True)

    @router.post("/{report_id}/restore")
    def restore_report(report_id: UUID, body: ReportRevision, identity: Identity = Depends(user)):
        return transition(report_id, body.expected_revision, identity, False)

    @router.get("/{report_id}/export")
    def export(
        report_id: UUID, format: Literal["markdown", "html"] = "markdown", identity: Identity = Depends(user)
    ):
        with connect() as conn:
            row = authorized_report(conn, report_id, identity)
        if row["deleted_at"]:
            raise HTTPException(409, "请先恢复报告再导出")
        content = export_report(detail(row), format)
        extension, media_type = ("md", "text/markdown") if format == "markdown" else ("html", "text/html")
        return Response(
            content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="sales-report-{report_id}.{extension}"',
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "X-Export-Scope": "owner-authorized-frozen-report",
            },
        )

    return router
