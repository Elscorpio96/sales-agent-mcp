from __future__ import annotations

import json
import sys

from mcp import Client, StdioServerParameters

from sales_agent.server import create_server

EXPECTED_TOOLS = {"shop_info", "sales_summary", "top_products", "low_stock", "margin_alerts", "compare_periods", "find_products",
                  "customer_debts", "draft_debt_reminder"}


async def test_tools_are_listed_with_schemas_and_read_only_annotations(db_path):
    async with Client(create_server(db_path)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.description and len(t.description) > 40
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert t.input_schema["type"] == "object"
    period = tools["sales_summary"].input_schema["properties"]["period"]
    assert "last_30_days" in json.dumps(period)
    assert tools["top_products"].input_schema["properties"]["limit"]["maximum"] == 50


async def test_structured_results(db_path):
    async with Client(create_server(db_path)) as c:
        r = await c.call_tool("sales_summary", {"period": "this_week", "group_by": "day"})
        assert not r.is_error
        data = r.structured_content
        assert data["period"]["start"] == "2026-10-26" and len(data["rows"]) == 6
        assert json.loads(r.content[0].text)["revenue"] == data["revenue"]
        found = await c.call_tool("find_products", {"query": "oil"})
        assert [p["sku"] for p in found.structured_content["result"]] == ["OIL-1L", "OIL-5L", "PAL-1L", "TIS-RL"]  # "toilet" contains "oil"


async def test_bad_input_comes_back_as_a_tool_error_the_model_can_read(db_path):
    async with Client(create_server(db_path)) as c:
        schema_error = await c.call_tool("sales_summary", {"period": "forever"})
        rule_error = await c.call_tool("sales_summary", {"period": "today", "start": "2026-10-01"})
        missing = await c.call_tool("draft_debt_reminder", {"customer_id": 404})
        too_many = await c.call_tool("top_products", {"limit": 1000})
    for r in (schema_error, rule_error, missing, too_many):
        assert r.is_error
    assert "not both" in rule_error.content[0].text and "no customer with id 404" in missing.content[0].text


async def test_audit_log(db_path, tmp_path):
    log = tmp_path / "audit.jsonl"
    async with Client(create_server(db_path, log)) as c:
        await c.call_tool("low_stock", {})
        await c.call_tool("draft_debt_reminder", {"customer_id": 404})
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["tool"], e["ok"]) for e in entries] == [("low_stock", True), ("draft_debt_reminder", False)]
    assert entries[1]["args"] == {"customer_id": 404, "language": None, "tone": "friendly"} and "no customer" in entries[1]["error"]
    assert all(e["ms"] >= 0 and e["ts"] for e in entries)


async def test_resource_and_prompt(db_path):
    async with Client(create_server(db_path)) as c:
        res = await c.read_resource("shop://schema")
        prompt = await c.get_prompt("weekly_review")
    assert "CREATE TABLE sales" in res.contents[0].text
    assert "weekly review" in prompt.messages[0].content.text


async def test_stdio_transport_end_to_end(db_path):
    """The same path Claude Desktop uses: launch the server as a subprocess and talk JSON-RPC over stdio."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "sales_agent.server", "--db", str(db_path), "--log-level", "WARNING"])
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        r = await c.call_tool("customer_debts", {"limit": 1})
    assert names == EXPECTED_TOOLS
    assert r.structured_content["rows"][0]["name"] == "Malam Sani Usman"
