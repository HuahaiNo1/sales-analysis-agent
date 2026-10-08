"""Deterministic escaped portable report exports; no remote assets or executable content."""

import html
import json
import re
from decimal import Decimal


def md(value) -> str:
    # Neutralize markdown, raw HTML, links and fence constructs in every data cell.
    text = str(value if value is not None else "不适用").replace("\r", "")
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text).replace("\n", " ")


def cell(value, kind):
    if value is None:
        return "不适用"
    if kind == "percent":
        return f"{value}%"
    return str(value)


def svg_chart(result: dict) -> str:
    """Static signed bars of displayed group values; no client-side arithmetic."""
    query = result["query"]
    if not query["group_by"] or not result["rows"]:
        return ""
    dimension, metric = query["group_by"][0], result["metrics"][0]
    comparison = bool(result.get("comparison_totals"))
    key = f"delta_{metric}" if comparison else metric
    rows = result["rows"][:11]
    values = [Decimal(row[key]) if row.get(key) is not None else None for row in rows]
    maximum = max((abs(v) for v in values if v is not None), default=Decimal(0)) or Decimal(1)
    width, height, middle, scale = 860, 48 + 35 * len(rows), 490, Decimal(220) / maximum
    title = "展示分组变化" if comparison else "展示分组本期值"
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
        f"<title>{title}；数值详见下方聚合表</title>",
        f'<text x="12" y="22" font-size="15">{title}</text>',
        f'<line x1="{middle}" y1="30" x2="{middle}" y2="{height}" stroke="#84929b"/>',
    ]
    for index, (row, value) in enumerate(zip(rows, values)):
        y = 42 + index * 35
        pixels = abs(value) * scale if value is not None else Decimal(0)
        x = Decimal(middle) - pixels if value is not None and value < 0 else Decimal(middle)
        label = str(row[dimension])
        short = label[:34] + ("…" if len(label) > 34 else "")
        pieces += [
            f'<text x="12" y="{y + 15}" font-size="12"><title>{html.escape(label)}</title>{html.escape(short)}</text>',
            f'<rect x="{x:.2f}" y="{y}" width="{pixels:.2f}" height="21" fill="{"#c96855" if value is not None and value < 0 else "#198778"}"/>',
            f'<text x="730" y="{y + 15}" font-size="11">{html.escape(str(value)) if value is not None else "不适用"}</text>',
        ]
    return "".join(pieces) + "</svg>"


def export_report(report: dict, format: str) -> str:
    facts, review = report["facts"], report["facts"]["review"]
    evidence = (
        review["evidence"]
        if review
        else [{"evidence_id": "E1", "role": "query", "title": "查询证据", "result": facts["result"]}]
    )
    assumptions = (
        review["assumptions"]
        if review
        else [facts["result"]["metadata"]["note"], "报告保留保存当时的聚合事实；贡献拆解不证明因果"]
    )
    findings = (
        review["findings"]
        if review
        else [{"text": text, "evidence_ids": ["E1"]} for text in facts["result"]["observations"]]
    )
    sections = [
        ("冻结事实", [(item["text"] + " [" + ", ".join(item["evidence_ids"]) + "]") for item in findings]),
        (
            "规则提示（非因果判断）",
            [
                (item["text"] + " [" + ", ".join(item["evidence_ids"]) + "]")
                for item in (review["flags"] if review else [])
            ],
        ),
        ("口径、假设与限制", assumptions),
        ("待验证的后续建议", review["suggestions"] if review else []),
        ("用户评论（可编辑，不改变事实）", [report["comment"] or "未填写"]),
        ("用户建议（可编辑，尚未验证）", [report["suggestions"] or "未填写"]),
    ]
    definitions = [
        f"{m['label']}：{m['formula']}；单位 {m['unit']}；{m['description']}"
        for m in facts["metric_definitions"]
    ]
    metadata = [
        f"报告 ID：{report['id']}；修订：{report['revision']}",
        f"生成：{facts['generated_at']}；保存：{report['created_at']}",
        f"数据版本：{facts['dataset_version']}；指标版本：{facts['metric_version']}",
        f"权限范围：{facts['scope']['scope_label']}；门店键：{', '.join(map(str, facts['scope']['allowed_store_ids']))}",
        "仅供 Contoso 模拟销售复盘；金额 USD；报告本身无结果缓存到期限制",
    ]
    if facts["result"].get("no_data"):
        metadata.append(
            "无数据：所选期间和筛选范围没有匹配的源记录；零值仅为空集合汇总，不代表实际经营为零，不能形成经营结论"
        )
    if review:
        metadata += [
            f"模板：{review['template_id']} v{review['template_version']}；查询：{review['query_count']}/{review['max_queries']}；停止：{review['stop_reason']}",
            "规则参数：" + json.dumps(review["rule_config"], ensure_ascii=False, sort_keys=True),
        ]
    if format == "markdown":
        out = [
            "# " + md(report["title"]),
            "",
            *[md(line) for line in metadata],
            "",
            "## 指标口径",
            *["- " + md(line) for line in definitions],
        ]
        for title, lines in sections:
            out += ["", "## " + title, *["- " + md(line) for line in (lines or ["无"])]]
        for item in evidence:
            result = item["result"]
            out += [
                "",
                f"## {md(item['evidence_id'])} · {md(item['title'])}",
                md(f"本期 {result['period']['start']} 至 {result['period']['end']}（不含结束日）"),
                md(f"对比期：{json.dumps(result['comparison'], ensure_ascii=False)}"),
                md(f"证据结果ID：{result['id']}；SHA256：{result['sha256']}"),
                md(
                    f"显示 {result['displayed_row_count']} 行；原分组 {result['row_count']}；其他合计={result['truncated']}"
                ),
                *["- " + md(warning) for warning in result.get("warnings", [])],
                "| " + " | ".join(md(c["label"]) for c in result["columns"]) + " |",
                "| " + " | ".join("---" for _ in result["columns"]) + " |",
            ]
            out += [
                "| " + " | ".join(md(cell(row.get(c["key"]), c["kind"])) for c in result["columns"]) + " |"
                for row in result["rows"]
            ]
            out += [
                "",
                "QuerySpec：" + md(json.dumps(result["query"], ensure_ascii=False, sort_keys=True)),
                "全范围汇总："
                + md(
                    json.dumps(
                        {key: result[key] for key in ["totals", "comparison_totals", "deltas", "change_pct"]},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                ),
            ]
        return "\n".join(out) + "\n"

    def esc(value):
        return html.escape(str(value), quote=True)

    out = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; sandbox\">",
        f"<title>{esc(report['title'])}</title>",
        "<style>body{font:15px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#183733}h1,h2{line-height:1.3}h2{margin-top:32px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d9e3df;text-align:left;padding:7px;overflow-wrap:anywhere}th{background:#eef5f2}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f5;padding:12px}svg{width:100%;height:auto}li{white-space:pre-wrap}@media print{body{margin:0}section{break-inside:avoid}}</style></head><body>",
        f"<h1>{esc(report['title'])}</h1>",
        *[f"<p>{esc(line)}</p>" for line in metadata],
        "<h2>指标口径</h2><ul>",
        *[f"<li>{esc(line)}</li>" for line in definitions],
        "</ul>",
    ]
    for title, lines in sections:
        out += [
            f"<section><h2>{esc(title)}</h2><ul>",
            *[f"<li>{esc(line)}</li>" for line in (lines or ["无"])],
            "</ul></section>",
        ]
    for item in evidence:
        result = item["result"]
        out += [
            f"<h2>{esc(item['evidence_id'])} · {esc(item['title'])}</h2>",
            f"<p>本期：{esc(result['period']['start'])} 至 {esc(result['period']['end'])}（不含结束日）；对比期：{esc(json.dumps(result['comparison'], ensure_ascii=False))}</p>",
            f"<p>证据结果 ID：{esc(result['id'])}；SHA256：{esc(result['sha256'])}</p>",
            f"<p>显示 {result['displayed_row_count']} 行；原分组 {result['row_count']}；其他合计={result['truncated']}</p>",
            *[f"<p>{esc(warning)}</p>" for warning in result.get("warnings", [])],
            svg_chart(result),
            "<table><thead><tr>",
            *[f"<th>{esc(c['label'])}</th>" for c in result["columns"]],
            "</tr></thead><tbody>",
        ]
        for row in result["rows"]:
            out += [
                "<tr>",
                *[f"<td>{esc(cell(row.get(c['key']), c['kind']))}</td>" for c in result["columns"]],
                "</tr>",
            ]
        out += [
            "</tbody></table><h3>冻结 QuerySpec</h3>",
            f"<pre>{esc(json.dumps(result['query'], ensure_ascii=False, sort_keys=True, indent=2))}</pre>",
            "<h3>全范围汇总</h3>",
            f"<pre>{esc(json.dumps({key: result[key] for key in ['totals', 'comparison_totals', 'deltas', 'change_pct']}, ensure_ascii=False, sort_keys=True, indent=2))}</pre>",
        ]
    return "\n".join(out) + "</body></html>\n"
