"""All SQL identifiers and expressions are code-owned constants; model input is values only."""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from .catalog import DIMENSION_LABELS, METRIC_LABELS
from .db import Identity, analytics, dataset_metadata
from .schemas import QuerySpec

METRIC_SQL = {
    "sales_amount": "COALESCE(SUM(f.quantity * f.net_price),0)",
    "order_count": "COUNT(DISTINCT f.order_key)",
    "units_sold": "COALESCE(SUM(f.quantity),0)",
    "avg_order_value": "SUM(f.quantity * f.net_price) / NULLIF(COUNT(DISTINCT f.order_key),0)",
    "gross_profit": "COALESCE(SUM(f.quantity * (f.net_price-f.unit_cost)),0)",
}
DIMENSION_SQL = {
    "day": "to_char(f.order_date,'YYYY-MM-DD')",
    "week": "to_char(d.week_start,'YYYY-MM-DD')",
    "month": "to_char(f.order_date,'YYYY-MM')",
    "product": "p.product_name || ' #' || p.product_key::text",
    "category": "p.category_name",
    "store": "s.store_name || ' #' || s.store_key::text",
    "customer_country": "c.customer_country",
    "store_country": "s.store_country",
}
FILTER_SQL = {
    "product": "f.product_key::text",
    "category": "p.category_key::text",
    "store": "f.store_key::text",
    "customer_country": "c.customer_country",
    "store_country": "s.store_country",
}
JOINS = """ FROM fact_sales f
 JOIN dim_product p ON p.product_key=f.product_key
 JOIN dim_store s ON s.store_key=f.store_key
 JOIN dim_customer_geo c ON c.customer_key=f.customer_key
 JOIN dim_date d ON d.date=f.order_date """


class QueryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def exact(value):
    if value is None:
        return None
    if isinstance(value, (Decimal, int)):
        return str(value)
    return value


def percent(delta, base):
    if base == 0 or base is None:
        return None
    return delta / abs(base) * 100


def money(value):
    return format(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ",.2f")


def validate_query(spec: QuerySpec, identity: Identity, metadata: dict):
    start, end = (
        date.fromisoformat(metadata["coverage"]["start"]),
        date.fromisoformat(metadata["coverage"]["end"]),
    )
    for period in [spec.period, spec.comparison]:
        if period and (period.start < start or period.end > end):
            raise QueryError(
                "OUTSIDE_DATA_COVERAGE", f"数据覆盖 {start} 至 {end - timedelta(days=1)}，请指定覆盖内的期间"
            )
    for f in spec.filters:
        if f.dimension == "store" and any(
            not x.isdigit() or int(x) not in identity.allowed_store_ids for x in f.values
        ):
            raise QueryError("FORBIDDEN_SCOPE", "所选门店不在当前账号的授权范围内")


def compile_query(spec: QuerySpec, identity: Identity, period, *, grouped=True, metrics=None):
    selected = metrics or [str(m) for m in spec.metrics]
    dimensions = spec.group_by if grouped else []
    expressions = [f"{DIMENSION_SQL[d]} AS {d}" for d in dimensions]
    expressions += [f"{METRIC_SQL[m]} AS {m}" for m in selected]
    # Count source lines distinctly from displayed aggregates to distinguish an empty result.
    expressions.append("COUNT(*) AS _line_count")
    where = [
        "f.order_date >= %s",
        "f.order_date < %s",
        "f.store_key = ANY(%s)",
        "f.currency_code = 'USD'",
        "f.exchange_rate = 1",
        "f.dataset_version = %s",
    ]
    values = [period.start, period.end, list(identity.allowed_store_ids), "contoso-v2-2023-2025-692e0347d360"]
    for f in spec.filters:
        where.append(f"{FILTER_SQL[f.dimension]} = ANY(%s)")
        values.append(f.values)
    statement = "SELECT " + ",".join(expressions) + JOINS + " WHERE " + " AND ".join(where)
    if dimensions:
        statement += " GROUP BY " + ",".join(DIMENSION_SQL[d] for d in dimensions)
    # Full aggregation is retained for reconciliation, then only presentation is reduced.
    if dimensions:
        statement += " LIMIT 20001"
    return statement, values


def query_metrics(raw: dict, identity: Identity, run_id: str | None = None) -> dict:
    spec = QuerySpec.model_validate(raw)
    metadata = dataset_metadata()
    validate_query(spec, identity, metadata)
    metrics = [str(x) for x in spec.metrics]
    with analytics(identity) as conn:
        groups = conn.execute(*compile_query(spec, identity, spec.period)).fetchall()
        totals = conn.execute(*compile_query(spec, identity, spec.period, grouped=False)).fetchone()
        compare_rows, comparison = [], None
        if spec.comparison:
            compare_rows = conn.execute(*compile_query(spec, identity, spec.comparison)).fetchall()
            comparison = conn.execute(
                *compile_query(spec, identity, spec.comparison, grouped=False)
            ).fetchone()
    if len(groups) > 20000 or len(compare_rows) > 20000:
        raise QueryError("RESULT_TOO_LARGE", "分组结果超过 20000 行，请缩小期间或分组范围")
    total_lines = totals.pop("_line_count")
    comparison_lines = comparison.pop("_line_count") if comparison else 0
    for row in groups + compare_rows:
        row.pop("_line_count", None)
    current = {tuple(row[d] for d in spec.group_by): row for row in groups}
    prior = {tuple(row[d] for d in spec.group_by): row for row in compare_rows}
    merged = []
    for key in sorted(current.keys() | prior.keys()):
        row = {d: key[i] for i, d in enumerate(spec.group_by)}
        for m in metrics:
            a = current.get(key, {}).get(m, None if m == "avg_order_value" else Decimal(0))
            row[m] = a
            if comparison is not None:
                b = prior.get(key, {}).get(m, None if m == "avg_order_value" else Decimal(0))
                row[f"comparison_{m}"] = b
                row[f"delta_{m}"] = a - b if a is not None and b is not None else None
                row[f"change_pct_{m}"] = percent(a - b, b) if a is not None and b is not None else None
        merged.append(row)
    deltas = (
        {
            m: totals[m] - comparison[m] if totals[m] is not None and comparison[m] is not None else None
            for m in metrics
        }
        if comparison
        else {}
    )
    percentages = (
        {m: percent(deltas[m], comparison[m]) if deltas[m] is not None else None for m in metrics}
        if comparison
        else {}
    )
    if spec.analysis == "contribution":
        for m in metrics:
            if sum(row[f"delta_{m}"] for row in merged) != deltas[m]:
                raise QueryError("RECONCILIATION_FAILED", "分组变化与总变化无法对账，未发布结果")
    field = (
        spec.sort.field
        if spec.sort
        else (f"delta_{metrics[0]}" if spec.analysis == "contribution" else metrics[0])
    )
    descending = spec.sort.direction == "desc" if spec.sort else True
    if spec.analysis == "contribution" and not spec.sort:
        merged.sort(key=lambda r: abs(r[field] or Decimal(0)), reverse=True)
    elif not spec.sort and set(spec.group_by) & {"day", "week", "month"}:
        merged.sort(key=lambda r: tuple(r[d] for d in spec.group_by))
    else:
        merged.sort(key=lambda r: (r[field] is not None, r[field] or Decimal(0)), reverse=descending)
    shown = merged[: spec.limit]
    hidden_count = len(merged) - len(shown)
    if spec.analysis == "contribution" and hidden_count:
        other = {d: "其他分组（合计）" for d in spec.group_by}
        for m in metrics:
            other[m] = sum(r[m] for r in merged[spec.limit :])
            other[f"comparison_{m}"] = sum(r[f"comparison_{m}"] for r in merged[spec.limit :])
            other[f"delta_{m}"] = other[m] - other[f"comparison_{m}"]
            other[f"change_pct_{m}"] = percent(other[f"delta_{m}"], other[f"comparison_{m}"])
        shown.append(other)
    observations = []
    for m in metrics:
        label = METRIC_LABELS[m]
        value = (
            "不适用"
            if totals[m] is None
            else money(totals[m])
            if m not in {"order_count", "units_sold"}
            else str(totals[m])
        )
        observations.append(
            f"本期{label}为 {value}"
            + (" USD" if m not in {"order_count", "units_sold"} and totals[m] is not None else "")
        )
        if comparison is not None and deltas[m] is not None:
            sign = "增加" if deltas[m] >= 0 else "减少"
            change = (
                "，对比期为 0，变化率不适用"
                if percentages[m] is None
                else f"，变化率 {money(percentages[m])}%"
            )
            observations.append(f"较对比期{sign} {money(abs(deltas[m]))}{change}")
    if spec.analysis == "contribution" and shown:
        lead = shown[0]
        observations.append(
            f"绝对变化最大的分组是 {' / '.join(str(lead[d]) for d in spec.group_by)}，变化 {money(lead[f'delta_{metrics[0]}'])}；贡献拆解不证明因果"
        )
    columns = [{"key": d, "label": DIMENSION_LABELS[d], "kind": "dimension"} for d in spec.group_by]
    for m in metrics:
        kind = "count" if m in {"order_count", "units_sold"} else "money"
        columns.append({"key": m, "label": METRIC_LABELS[m], "kind": kind})
        if comparison is not None:
            columns += [
                {"key": f"comparison_{m}", "label": f"对比期{METRIC_LABELS[m]}", "kind": kind},
                {"key": f"delta_{m}", "label": f"{METRIC_LABELS[m]}变化", "kind": kind},
                {"key": f"change_pct_{m}", "label": "变化率", "kind": "percent"},
            ]
    chart = {
        "type": spec.chart if spec.group_by else "table",
        "x": spec.group_by[0] if spec.group_by else None,
        "y": metrics[0],
        "series": metrics,
    }
    if spec.chart == "waterfall":
        m = metrics[0]
        chart["waterfall"] = (
            [{"name": "对比期", "value": exact(comparison[m]), "kind": "total"}]
            + [
                {
                    "name": " / ".join(r[d] for d in spec.group_by),
                    "value": exact(r[f"delta_{m}"]),
                    "kind": "delta",
                }
                for r in shown
            ]
            + [{"name": "本期", "value": exact(totals[m]), "kind": "total"}]
        )
    now = datetime.now(timezone.utc)
    query = spec.model_dump(mode="json")
    payload = {
        "id": str(uuid4()),
        "run_id": run_id,
        "query": query,
        "period": query["period"],
        "comparison": query["comparison"],
        "metrics": metrics,
        "columns": columns,
        "rows": [{k: exact(v) for k, v in row.items()} for row in shown],
        "totals": {k: exact(v) for k, v in totals.items()},
        "comparison_totals": {k: exact(v) for k, v in comparison.items()} if comparison else None,
        "deltas": {k: exact(v) for k, v in deltas.items()},
        "change_pct": {k: exact(v) for k, v in percentages.items()},
        "chart": chart,
        "observations": observations[:5],
        "row_count": len(merged),
        "displayed_row_count": len(shown),
        "truncated": hidden_count > 0,
        "hidden_row_count": hidden_count,
        "complete": True,
        "no_data": total_lines == 0 and comparison_lines == 0,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "metadata": {
            **metadata,
            "scope_label": identity.scope_label,
            "scope_version": "scope-v1",
            "query_hash": hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest(),
            "source_line_count": total_lines,
            "comparison_source_line_count": comparison_lines,
            "precision": "Decimal / PostgreSQL NUMERIC(18,6); display ROUND_HALF_UP 2dp",
            "note": "模拟数据；按订单日期计算；不包含付款、退款或可信渠道字段",
        },
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return payload


def dashboard(identity: Identity, start: date, end: date):
    spec = QuerySpec(metrics=["sales_amount"], period={"start": start, "end": end}, chart="table")
    metadata = dataset_metadata()
    validate_query(spec, identity, metadata)
    with analytics(identity) as conn:
        totals = conn.execute(
            *compile_query(spec, identity, spec.period, grouped=False, metrics=list(METRIC_SQL))
        ).fetchone()
    totals.pop("_line_count")
    monthly = query_metrics(
        {
            **spec.model_dump(mode="json"),
            "metrics": ["sales_amount", "order_count"],
            "group_by": ["month"],
            "limit": 100,
            "chart": "line",
        },
        identity,
    )
    categories = query_metrics(
        {**spec.model_dump(mode="json"), "group_by": ["category"], "limit": 10, "chart": "bar"}, identity
    )
    return {
        "totals": {k: exact(v) for k, v in totals.items()},
        "monthly": monthly["rows"],
        "categories": categories["rows"],
        "period": spec.period.model_dump(mode="json"),
        "metadata": metadata,
    }
