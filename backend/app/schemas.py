from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Metric(StrEnum):
    sales_amount = "sales_amount"
    order_count = "order_count"
    units_sold = "units_sold"
    avg_order_value = "avg_order_value"
    gross_profit = "gross_profit"


Dimension = Literal[
    "day", "week", "month", "product", "category", "store", "customer_country", "store_country"
]
FilterDimension = Literal["product", "category", "store", "customer_country", "store_country"]


class Period(StrictModel):
    start: date
    end: date
    date_field: Literal["order_date"] = "order_date"

    @model_validator(mode="after")
    def valid_period(self):
        if not 0 < (self.end - self.start).days <= 366:
            raise ValueError("期间必须左闭右开，且长度在 1 至 366 天内")
        return self


class DimensionFilter(StrictModel):
    dimension: FilterDimension
    op: Literal["eq", "in"] = "in"
    values: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_values(self):
        if self.op == "eq" and len(self.values) != 1:
            raise ValueError("eq 筛选必须只有一个值")
        if any(len(v) > 100 for v in self.values):
            raise ValueError("筛选键过长")
        return self


class Sort(StrictModel):
    field: str = Field(max_length=60)
    direction: Literal["asc", "desc"] = "desc"


class QuerySpec(StrictModel):
    schema_version: Literal["queryspec_v1"] = "queryspec_v1"
    dataset_id: Literal["contoso_v2"] = "contoso_v2"
    metric_version: Literal["metrics_v1"] = "metrics_v1"
    metrics: list[Metric] = Field(min_length=1, max_length=2)
    period: Period
    comparison: Period | None = None
    group_by: list[Dimension] = Field(default_factory=list, max_length=2)
    filters: list[DimensionFilter] = Field(default_factory=list, max_length=8)
    analysis: Literal["summary", "compare", "contribution"] = Field(
        default="summary",
        description="瀑布图必须使用 contribution，且明确两个期间、非时间分组和单一可加总指标",
    )
    sort: Sort | None = None
    limit: int = Field(default=20, ge=1, le=100)
    chart: Literal["table", "line", "bar", "waterfall", "none"] = Field(
        default="bar", description="waterfall 仅适用于已对账的 contribution，不能直接展示普通 compare"
    )
    base_result_id: str | None = None

    @model_validator(mode="after")
    def compatibility(self):
        if len(set(self.metrics)) != len(self.metrics) or len(set(self.group_by)) != len(self.group_by):
            raise ValueError("指标和分组维度不能重复")
        if len(set(self.group_by) & {"day", "week", "month"}) > 1:
            raise ValueError("一次只能使用一种时间粒度")
        product_scope = set(self.group_by) | {f.dimension for f in self.filters}
        if Metric.avg_order_value in self.metrics and product_scope & {"product", "category"}:
            raise ValueError("平均订单金额不支持商品或类别筛选/分组，请改看销售额和去重订单数")
        if self.analysis in {"compare", "contribution"} and not self.comparison:
            raise ValueError("比较与贡献分析需要明确的对比期间")
        if self.analysis == "contribution":
            if not self.group_by or set(self.group_by) & {"day", "week", "month"}:
                raise ValueError("贡献拆解需要一个非时间分组维度")
            if any(m in {Metric.order_count, Metric.avg_order_value} for m in self.metrics):
                raise ValueError("订单数和平均订单金额不能做加总贡献拆解")
        if self.chart == "waterfall" and (self.analysis != "contribution" or len(self.metrics) != 1):
            raise ValueError("瀑布图需要单一可加总指标的贡献拆解")
        if self.chart == "line" and not set(self.group_by) & {"day", "week", "month"}:
            raise ValueError("折线图需要时间维度")
        output = set(self.group_by) | {str(m) for m in self.metrics}
        if self.comparison:
            output |= {
                f"{prefix}_{m}" for m in self.metrics for prefix in ["comparison", "delta", "change_pct"]
            }
        if self.sort and self.sort.field not in output:
            raise ValueError("排序字段必须存在于结果中")
        return self


def normalize_query_intent(raw: dict) -> dict:
    """Repair only a complete waterfall comparison's internal analysis label.

    Used at the shared mock/live tool boundary, before the strict gateway. Never
    infer periods, metrics or groups, and never modify an existing frozen result.
    Full validation of the candidate keeps every schema safety check intact.
    """
    if raw.get("chart") != "waterfall" or raw.get("analysis") != "compare":
        return raw
    candidate = {**raw, "analysis": "contribution"}
    try:
        QuerySpec.model_validate(candidate)
    except ValidationError:
        return raw
    return candidate


class RunRequest(StrictModel):
    review: "ReviewRequest | None" = None
    message: str = Field(min_length=1, max_length=2000)
    client_request_id: str = Field(min_length=8, max_length=80)
    expected_state_version: int = Field(ge=0)


class LoginRequest(StrictModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=100)


class ReviewRequest(StrictModel):
    period: Period
    comparison: Period
    metric: Literal["sales_amount", "units_sold", "gross_profit"] = "sales_amount"
    filters: list[DimensionFilter] = Field(default_factory=list, max_length=7)
    drill_down: bool = False

    @model_validator(mode="after")
    def distinct_periods(self):
        if self.period == self.comparison:
            raise ValueError("复盘的本期与对比期不能完全相同")
        return self


class ReportCreate(StrictModel):
    run_id: UUID
    title: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=4000)
    suggestions: str = Field(default="", max_length=4000)
    client_request_id: str = Field(min_length=8, max_length=80)

    @field_validator("title", "comment", "suggestions")
    @classmethod
    def no_control_characters(cls, value):
        if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
            raise ValueError("报告字段含不支持的控制字符")
        return value

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("报告标题不能为空")
        return value.strip()


class ReportUpdate(StrictModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=4000)
    suggestions: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set - {"expected_revision"}:
            raise ValueError("请提供至少一个可编辑字段")
        for key in self.model_fields_set - {"expected_revision"}:
            value = getattr(self, key)
            if value is None:
                raise ValueError("可编辑字段不能为 null")
            if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
                raise ValueError("报告字段含不支持的控制字符")
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("报告标题不能为空")
        return self


class ReportRevision(StrictModel):
    expected_revision: int = Field(ge=1)


RunRequest.model_rebuild()
