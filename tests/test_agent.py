from __future__ import annotations

import json

import httpx
import pytest
from mcp import Client

from sales_agent.agent import SYSTEM_PROMPT, AnthropicModel, ModelTurn, OpenAICompatibleModel, ScriptedModel, ToolCall, run_agent
from sales_agent.server import create_server


async def ask(db_path, model, question="q", **kw):
    async with Client(create_server(db_path)) as c:
        return await run_agent(question, c, model, **kw)


async def test_tool_loop_feeds_real_results_back(db_path):
    def brain(messages):
        if len(messages) == 1:
            return ModelTurn("", [ToolCall("1", "customer_debts", {"limit": 1})], 100, 10)
        result = json.loads(messages[-1]["content"])
        top = result["rows"][0]
        return ModelTurn(f"{top['name']} owes ₦{top['balance']:,.0f}.", [], 200, 20)

    res = await ask(db_path, ScriptedModel(brain), "Who owes me the most?")
    assert res.answer == "Malam Sani Usman owes ₦50,250."
    assert res.tools_used == ["customer_debts"] and res.steps[0].ok
    assert (res.turns, res.input_tokens, res.output_tokens, res.stopped) == (2, 300, 30, "answered")


async def test_tool_errors_and_unknown_tools_are_returned_to_the_model(db_path):
    seen = []

    def brain(messages):
        if len(messages) == 1:
            return ModelTurn("", [ToolCall("a", "delete_all_sales", {}), ToolCall("b", "sales_summary", {"period": "forever"})])
        seen.extend(m for m in messages if m["role"] == "tool")
        return ModelTurn("I can't do that; I can only read data.", [])

    res = await ask(db_path, ScriptedModel(brain))
    assert [s.ok for s in res.steps] == [False, False]
    assert "Unknown tool 'delete_all_sales'" in seen[0]["content"] and seen[0]["is_error"]
    assert "validation error" in seen[1]["content"] and seen[1]["is_error"]
    assert res.stopped == "answered"


async def test_step_limit(db_path):
    loop = ScriptedModel(lambda m: ModelTurn("", [ToolCall(str(len(m)), "shop_info", {})]))
    res = await ask(db_path, loop, max_steps=3)
    assert res.stopped == "step_limit" and len(res.steps) == 3 and "stopped after 3 steps" in res.answer


async def test_long_results_are_truncated(db_path):
    captured = []

    def brain(messages):
        if len(messages) == 1:
            return ModelTurn("", [ToolCall("1", "sales_summary", {"period": "all", "group_by": "day"})])
        captured.append(messages[-1]["content"])
        return ModelTurn("done", [])

    await ask(db_path, ScriptedModel(brain), max_result_chars=500)
    assert len(captured[0]) < 520 and captured[0].endswith("…(truncated)")


async def test_on_step_callback(db_path):
    got = []
    script = ScriptedModel([ModelTurn("", [ToolCall("1", "low_stock", {})]), ModelTurn("ok", [])])
    await ask(db_path, script, on_step=got.append)
    assert [s.tool for s in got] == ["low_stock"]


async def test_anthropic_wire_format(db_path):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        sent.append((req, body))
        if len(sent) == 1:
            return httpx.Response(200, json={"content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tu_1", "name": "low_stock", "input": {}},
                {"type": "tool_use", "id": "tu_2", "name": "margin_alerts", "input": {"period": "last_30_days"}}],
                "usage": {"input_tokens": 1000, "output_tokens": 50}})
        final = {"content": [{"type": "text", "text": "Bread is out of stock."}], "usage": {"input_tokens": 3000, "output_tokens": 20}}
        return httpx.Response(200, json=final)

    model = AnthropicModel(model="claude-test", api_key="sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await ask(db_path, model, "What needs attention?")
    assert res.answer == "Bread is out of stock." and res.tools_used == ["low_stock", "margin_alerts"]
    req, first = sent[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-test" and req.headers["anthropic-version"] == "2023-06-01"
    assert first["model"] == "claude-test" and first["system"] == SYSTEM_PROMPT and len(first["tools"]) == 9
    assert set(first["tools"][0]) == {"name", "description", "input_schema"}
    second = sent[1][1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert [b["type"] for b in second[1]["content"]] == ["text", "tool_use", "tool_use"]
    results = second[2]["content"]
    assert [(b["type"], b["tool_use_id"], b["is_error"]) for b in results] == [("tool_result", "tu_1", False), ("tool_result", "tu_2", False)]
    assert "Bread" in results[0]["content"]


async def test_anthropic_errors_and_missing_key(db_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicModel()
    bad = AnthropicModel(api_key="x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))))
    with pytest.raises(BaseException) as e:  # surfaces through the MCP client's task group
        await ask(db_path, bad)
    err = e.value
    while isinstance(err, BaseExceptionGroup):
        err = err.exceptions[0]
    assert isinstance(err, RuntimeError) and "401" in str(err)


async def test_openai_compatible_wire_format(db_path):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        sent.append((req, body))
        if len(sent) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "top_products", "arguments": '{"limit": 2}'}},
                {"id": "c2", "type": "function", "function": {"name": "shop_info", "arguments": "{not json"}}]}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 40}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Rice leads."}}], "usage": {"prompt_tokens": 900, "completion_tokens": 9}})

    model = OpenAICompatibleModel("llama3.1", base_url="http://localhost:11434/v1/", api_key="",
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await ask(db_path, model)
    assert res.answer == "Rice leads." and res.input_tokens == 1400
    req, first = sent[0]
    assert str(req.url) == "http://localhost:11434/v1/chat/completions" and "authorization" not in req.headers
    assert first["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    msgs = sent[1][1]["messages"]
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "top_products", "arguments": '{"limit": 2}'}
    assert [m["role"] for m in msgs[3:]] == ["tool", "tool"] and msgs[3]["tool_call_id"] == "c1"
    assert not res.steps[1].ok  # bad JSON is reported back to the model, and the tool is not called
    assert "not valid JSON" in msgs[4]["content"]
