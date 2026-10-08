"""One code-owned review template; all facts/flags use immutable Decimal aggregates."""

from copy import deepcopy
from decimal import Decimal

from .catalog import METRIC_LABELS
from .query import QueryError, money
from .schemas import QuerySpec, ReviewRequest

MAX_REVIEW_QUERIES = 4
REVIEW_TIMEOUT_SECONDS = 180
RULE_CONFIG = {
    "version": "review_rules_v1",
    "relative_change_threshold_pct": "10",
    "concentration_threshold_pct": "50",
}
ROLE_TITLES = {
    "overall": "整体期间比较",
    "category": "类别变化贡献",
    "store": "门店变化贡献",
    "drill": "主要类别的商品下钻",
}


class ReviewSession:
    """The agent may request only the exact next query, never extend the template."""

    def __init__(self, request: ReviewRequest, categories: list[dict]):
        self.request = request
        self.categories = categories
        self.evidence: list[dict] = []
        self.stop_reason = ""
        self.role = "overall"
        self.next_spec: dict | None = self._spec([], "compare", "table")

    def _spec(self, groups, analysis, chart, filters=None):
        return QuerySpec(
            metrics=[self.request.metric],
            period=self.request.period,
            comparison=self.request.comparison,
            group_by=groups,
            filters=filters if filters is not None else self.request.filters,
            analysis=analysis,
            chart=chart,
            limit=10,
        ).model_dump(mode="json")

    def validate_next(self, raw: dict):
        spec = QuerySpec.model_validate(raw).model_dump(mode="json")
        if self.next_spec is None or len(self.evidence) >= MAX_REVIEW_QUERIES:
            raise QueryError("REVIEW_QUERY_LIMIT", "复盘已经结束，不允许继续查询")
        if spec != self.next_spec:
            raise QueryError("REVIEW_PLAN_VIOLATION", "复盘查询不符合固定模板，未发布结果")

    def accept(self, result: dict):
        if self.evidence and any(
            result["metadata"].get(key) != self.evidence[0]["result"]["metadata"].get(key)
            for key in ("dataset_version", "metric_version")
        ):
            raise QueryError(
                "REVIEW_VERSION_CHANGED", "复盘期间数据或指标版本发生变化，未发布结果；请重新运行"
            )
        self.evidence.append(
            {
                "evidence_id": f"E{len(self.evidence) + 1}",
                "role": self.role,
                "title": ROLE_TITLES[self.role],
                "result": result,
            }
        )
        if self.role == "overall":
            if result["no_data"]:
                return self._stop("no_data")
            if Decimal(result["deltas"][self.request.metric]) == 0:
                return self._stop("no_change")
            self.role = "category"
            self.next_spec = self._spec(["category"], "contribution", "waterfall")
        elif self.role == "category":
            self.role = "store"
            self.next_spec = self._spec(["store"], "contribution", "waterfall")
        elif self.role == "store":
            if not self.request.drill_down:
                return self._stop("drill_disabled")
            if any(f.dimension in {"category", "product"} for f in self.request.filters):
                return self._stop("already_product_scoped")
            rows = self.evidence[1]["result"]["rows"]
            candidates = [r for r in rows if r["category"] != "其他分组（合计）"]
            if not candidates:
                return self._stop("no_drill_candidate")
            lead = max(candidates, key=lambda r: abs(Decimal(r[f"delta_{self.request.metric}"])))
            if Decimal(lead[f"delta_{self.request.metric}"]) == 0:
                return self._stop("no_drill_candidate")
            matches = [c for c in self.categories if c["label"] == lead["category"]]
            if len(matches) != 1:
                return self._stop("ambiguous_drill_category")
            filters = [f.model_dump(mode="json") for f in self.request.filters]
            filters.append({"dimension": "category", "op": "eq", "values": [str(matches[0]["key"])]})
            self.role = "drill"
            self.next_spec = self._spec(["product"], "contribution", "waterfall", filters)
        else:
            self._stop("query_limit_reached")

    def _stop(self, reason):
        self.stop_reason = reason
        self.next_spec = None

    def result(self) -> dict:
        if self.next_spec is not None or not self.evidence:
            raise QueryError("REVIEW_INCOMPLETE", "复盘证据未完成，未发布结果")
        metric = self.request.metric
        overall = self.evidence[0]["result"]
        findings = [
            {"text": text, "evidence_ids": [evidence["evidence_id"]]}
            for evidence in self.evidence
            for text in evidence["result"]["observations"]
        ]
        flags = []
        change = overall["change_pct"].get(metric)
        if change is None and not overall["no_data"]:
            flags.append(
                {
                    "rule_id": "zero_baseline",
                    "label": "对比基数为零",
                    "severity": "info",
                    "text": "对比期基数为零，变化率不适用；请核对绝对变化",
                    "evidence_ids": ["E1"],
                }
            )
        elif change is not None and abs(Decimal(change)) >= Decimal(
            RULE_CONFIG["relative_change_threshold_pct"]
        ):
            flags.append(
                {
                    "rule_id": "large_relative_change",
                    "label": "期间变化达到阈值",
                    "severity": "attention",
                    "text": f"{METRIC_LABELS[metric]}变化率为 {money(change)}%，绝对值达到固定 10% 提示阈值；不代表统计异常或因果",
                    "evidence_ids": ["E1"],
                }
            )
        for evidence in self.evidence:
            if evidence["role"] in {"category", "store", "drill"}:
                payload = evidence["result"]
                for state, label in [
                    ("current_only", "对比期无记录/本期出现"),
                    ("comparison_only", "本期无记录"),
                ]:
                    names = [
                        " / ".join(str(row[d]) for d in payload["query"]["group_by"])
                        for row, presence in zip(payload["rows"], payload.get("row_presence", []))
                        if presence["state"] == state and not presence["is_other"]
                    ]
                    if names:
                        flags.append(
                            {
                                "rule_id": f"{state}_{evidence['role']}",
                                "label": label,
                                "severity": "info",
                                "text": f"{'、'.join(names)}：{label}。依据为对应期间源记录数是否为零，非指标是否为零；仅覆盖已展示独立分组，不含其他合计内部，不代表新品、停售或业务流失",
                                "evidence_ids": [evidence["evidence_id"]],
                            }
                        )
            if evidence["role"] not in {"category", "store"}:
                continue
            rows = evidence["result"]["rows"]
            magnitudes = [abs(Decimal(r[f"delta_{metric}"])) for r in rows]
            total = sum(magnitudes, Decimal(0))
            if total and magnitudes:
                index = max(range(len(magnitudes)), key=magnitudes.__getitem__)
                share = magnitudes[index] / total * 100
                label = rows[index][evidence["role"]]
                if share >= Decimal(RULE_CONFIG["concentration_threshold_pct"]):
                    flags.append(
                        {
                            "rule_id": f"concentrated_{evidence['role']}_change",
                            "label": "展示分组变化集中",
                            "severity": "info",
                            "text": f"{label}占展示分组（含其他合计）绝对变化之和的 {money(share)}%；固定提示阈值 50%，不是对总净变化的贡献占比",
                            "evidence_ids": [evidence["evidence_id"]],
                        }
                    )
        assumptions = [
            "仅分析 Contoso 2023–2025 模拟数据，金额为 USD，按订单日期计算；不能据此确认真实经营原因",
            "期间采用左闭右开边界；商品毛利未扣税费、物流及经营费用，不是净利润",
            "贡献只用于可加总指标；去重订单数与平均订单金额不做贡献拆解",
            "提示使用固定规则而非预测或统计异常检测；其他分组合计可能抵消内部正负变化",
        ]
        if (self.request.period.end - self.request.period.start) != (
            self.request.comparison.end - self.request.comparison.start
        ):
            assumptions.append("两个期间天数不同；本报告比较原始总量，未按日均标准化")
        if max(self.request.period.start, self.request.comparison.start) < min(
            self.request.period.end, self.request.comparison.end
        ):
            assumptions.append("两个期间存在重叠，部分记录会同时计入两期；不能视为独立期间比较")
        suggestions = (
            []
            if overall["no_data"]
            else [
                "结合类别与门店证据核对业务记录，再判断是否需要进一步调查；当前聚合数据不证明因果",
            ]
        )
        return deepcopy(
            {
                "template_id": "sales_review_v1",
                "template_version": "1",
                "request": self.request.model_dump(mode="json"),
                "evidence": self.evidence,
                "findings": findings,
                "flags": flags,
                "assumptions": assumptions,
                "suggestions": suggestions,
                "rule_config": RULE_CONFIG,
                "query_count": len(self.evidence),
                "max_queries": MAX_REVIEW_QUERIES,
                "timeout_seconds": REVIEW_TIMEOUT_SECONDS,
                "stop_reason": self.stop_reason,
            }
        )
