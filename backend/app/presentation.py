"""A deliberately narrow parser for display-only changes, never business queries."""

import re

CHART_LABELS = {"table": "表格", "bar": "柱状图", "line": "折线图", "waterfall": "瀑布图"}
_CHARTS = {
    "表格": "table",
    "柱状图": "bar",
    "柱形图": "bar",
    "柱状": "bar",
    "折线图": "line",
    "折线": "line",
    "瀑布图": "waterfall",
    "瀑布": "waterfall",
}
_PATTERN = re.compile(
    r"^(?:请)?(?:把(?:它|结果|这张图))?(?:换成|改成|切换到|换为|改为|用|显示|展示)?"
    r"(表格|柱状图|柱形图|柱状|折线图|折线|瀑布图|瀑布)(?:展示|显示|呈现)?$"
)


def presentation_request(message: str) -> str | None:
    """Only an entire standalone command qualifies; dates/filters/metrics never do."""
    text = re.sub(r"\s+", "", message).strip("。！!？?，,")
    if text in {"换张图", "换个图", "换种图", "换一种图", "换成图表", "请换张图"}:
        return "unspecified"
    match = _PATTERN.fullmatch(text)
    return _CHARTS[match.group(1)] if match else None
