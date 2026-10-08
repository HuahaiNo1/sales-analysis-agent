"""Scripted provider regressions for the real graph; never real API evidence."""

import json

import app.agent as agent
import pytest
from app.schemas import QuerySpec
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ReviewProvider(BaseChatModel):
    specs: list[dict] = Field(default_factory=list)
    behavior: str = "stale_handle"

    @property
    def _llm_type(self):
        return "local-review-script"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        def call(name, args):
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(len(messages))}])

        question = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if question.startswith('{"result_id":'):
            if agent._last_tool(messages, "analyze_result") is None:
                handle = json.loads(question)["result_id"]
                if self.behavior == "foreign_child_handle":
                    handle = "another-run"
                reply = call("analyze_result", {"result_id": handle})
            else:
                reply = AIMessage(content="已读取确定性分析，不能推断因果")
        elif agent._last_tool(messages, "get_metric_catalog") is None:
            reply = call("get_metric_catalog", {})
        else:
            results = [
                json.loads(m.content)
                for m in messages
                if isinstance(m, ToolMessage) and m.name == "query_metrics"
            ]
            tasks = [m for m in messages if isinstance(m, ToolMessage) and m.name == "task"]
            if tasks and self.behavior != "repeat_task":
                reply = AIMessage(content="复盘完成")
            elif not results:
                reply = call("query_metrics", {"spec": self.specs[0]})
            elif results[-1].get("next_query") is not None and self.behavior != "early_task":
                reply = call("query_metrics", {"spec": results[-1]["next_query"]})
            else:
                # Models may select the overall handle to summarize the review.
                # Descriptions are instructions, not an authorization source.
                description = json.dumps({"result_id": results[0]["result_id"], "question": question})
                if self.behavior == "prose_task":
                    description = "请综合所有复盘证据解释销售变化，不推断因果。"
                reply = call("task", {"subagent_type": "analysis", "description": description})
        reply.usage_metadata = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.fixture(autouse=True)
def no_network(tmp_path, monkeypatch):
    import langchain_openai

    monkeypatch.setenv("LLM_BUDGET_PATH", str(tmp_path / "isolated-budget.sqlite3"))

    def forbidden(*args, **kwargs):
        raise AssertionError("No real provider client is allowed")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", forbidden)
    monkeypatch.setattr(agent, "mock_plan", forbidden)


async def run_review(monkeypatch, count, behavior="stale_handle"):
    specs = [
        QuerySpec(
            metrics=["sales_amount"],
            period={"start": "2025-09-01", "end": "2025-10-01"},
            comparison={"start": "2025-08-01", "end": "2025-09-01"},
            group_by=groups,
            analysis="compare" if not groups else "contribution",
            chart="table" if not groups else "bar",
        ).model_dump(mode="json")
        for groups in [[], ["category"], ["store"], ["product"]][:count]
    ]
    monkeypatch.setattr(agent, "_deepseek_model", lambda _: ReviewProvider(specs=specs, behavior=behavior))
    queries, analyses = [], []

    def query(spec):
        assert spec == specs[len(queries)]
        queries.append(spec)
        return {
            "result_id": f"handle-{len(queries)}",
            "next_query": specs[len(queries)] if len(queries) < count else None,
        }

    def analyze(handle):
        assert handle == f"handle-{count}"
        analyses.append(handle)
        return {"observations": ["确定性复盘"]}

    result = await agent.run_agent(
        "固定销售复盘",
        None,
        {},
        query,
        analyze,
        lambda: False,
        mode="live",
        api_key="LOCAL-NOT-A-CREDENTIAL",
        review_query=specs[0],
    )
    return result, queries, analyses


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("behavior", ["stale_handle", "prose_task"])
async def test_review_task_handoff_binds_final_authorized_handle(monkeypatch, count, behavior):
    result, queries, analyses = await run_review(monkeypatch, count, behavior)
    assert result["status"] == "SUCCEEDED"
    assert len(queries) == count
    assert analyses == [f"handle-{count}"]
    assert result["runtime"]["model_calls"] == count + 5
    assert result["runtime"]["max_query_specs"] == 4
    assert result["runtime"]["subagents"] == ["analysis"]


@pytest.mark.parametrize(
    "behavior,reason",
    [
        ("early_task", "review_incomplete"),
        ("repeat_task", "analysis_already_started"),
        ("foreign_child_handle", "result_handle_unauthorized"),
    ],
)
async def test_review_invalid_execution_is_rejected_with_safe_diagnostics(monkeypatch, behavior, reason):
    with pytest.raises(agent.AgentBoundaryError) as caught:
        await run_review(monkeypatch, 3, behavior)
    details = caught.value.failure_diagnostics
    assert details["stage"] == "invoke"
    assert details["boundary_reason"] == reason
    assert details["query_specs"] <= 3
    assert set(details["tool_counts"]) <= agent.MAIN_TOOLS | agent.ANALYSIS_TOOLS
    assert "handle-" not in json.dumps(details)
    assert "LOCAL-NOT-A-CREDENTIAL" not in json.dumps(details)


async def test_failure_diagnostics_never_copy_arbitrary_exception_fields(monkeypatch):
    failure = RuntimeError("provider request contains secret material")
    failure.boundary_reason = "not-an-enum-sensitive-data"

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(agent, "build_runtime", fail)
    with pytest.raises(RuntimeError) as caught:
        await agent.run_agent("private input", None, {}, fail, fail, lambda: False)
    assert caught.value is failure
    assert caught.value.failure_diagnostics == {
        "stage": "build",
        "boundary_reason": "other_error",
        "model_calls": 0,
        "query_specs": 0,
        "tool_counts": {},
    }


def test_review_prompt_has_no_immediate_single_query_analysis_instruction(monkeypatch):
    prompts = {}
    original = agent.create_deep_agent

    def capture(**kwargs):
        prompts[kwargs["name"]] = kwargs["system_prompt"]
        return original(**kwargs)

    monkeypatch.setattr(agent, "create_deep_agent", capture)
    agent.build_runtime(
        "复盘",
        None,
        {},
        lambda _: {},
        lambda _: {},
        lambda: False,
        mode="mock",
        review_query={"test": "query"},
    )
    assert "完成 query_metrics 后，把返回 result_id" not in prompts["sales-main"]
    assert "所有复盘查询完成且 next_query 为 null 后" in prompts["sales-main"]
    assert "最多四次" in prompts["sales-main"]
