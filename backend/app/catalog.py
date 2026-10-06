METRICS = [
    {
        "id": "sales_amount",
        "label": "折扣后销售额",
        "formula": "SUM(Quantity × NetPrice)",
        "unit": "USD",
        "description": "折扣后的单位售价乘销量，不代表实际收款",
    },
    {
        "id": "order_count",
        "label": "订单数",
        "formula": "COUNT(DISTINCT OrderKey)",
        "unit": "orders",
        "description": "去重订单数；商品/类别分组不可直接相加",
    },
    {
        "id": "units_sold",
        "label": "销售数量",
        "formula": "SUM(Quantity)",
        "unit": "units",
        "description": "销售件数，不代表重量或体积",
    },
    {
        "id": "avg_order_value",
        "label": "平均订单金额",
        "formula": "销售额 ÷ 去重订单数",
        "unit": "USD",
        "description": "无订单时为不适用；不支持商品/类别筛选或分组",
    },
    {
        "id": "gross_profit",
        "label": "商品毛利",
        "formula": "SUM(Quantity × (NetPrice − UnitCost))",
        "unit": "USD",
        "description": "未扣税费、物流及经营费用，不是净利润",
    },
]
DIMENSIONS = [
    {"id": k, "label": v}
    for k, v in {
        "month": "月份",
        "week": "周",
        "day": "日期",
        "category": "类别",
        "product": "商品",
        "store": "门店",
        "customer_country": "客户国家",
        "store_country": "门店国家",
    }.items()
]
METRIC_LABELS = {m["id"]: m["label"] for m in METRICS}
DIMENSION_LABELS = {d["id"]: d["label"] for d in DIMENSIONS}
