from __future__ import annotations

import json

import pytest

from sales_agent import cli
from sales_agent.agent import ModelTurn, ScriptedModel, ToolCall


def test_seed_and_call(tmp_path, capsys):
    db = str(tmp_path / "shop.db")
    assert cli.main(["seed", "--db", db]) == 0
    assert cli.main(["call", "top_products", "--db", db, "--args", '{"period": "this_month", "limit": 1, "by": "quantity"}']) == 0
    out = capsys.readouterr().out
    assert "Instant noodles 70g" in out


def test_call_errors(db_path, capsys):
    assert cli.main(["call", "sales_summary", "--db", str(db_path), "--args", '{"period": "today", "start": "2026-10-01"}']) == 1
    assert "not both" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="not valid JSON"):
        cli.main(["call", "shop_info", "--db", str(db_path), "--args", "{oops"])
    with pytest.raises(SystemExit, match="seed"):
        cli.main(["call", "shop_info", "--db", "/nonexistent/shop.db"])


def test_tools_and_config(db_path, capsys):
    assert cli.main(["tools", "--db", str(db_path)]) == 0
    assert "draft_debt_reminder" in capsys.readouterr().out
    assert cli.main(["config", "--db", str(db_path)]) == 0
    cfg = json.loads(capsys.readouterr().out)["mcpServers"]["sales-agent"]
    assert cfg["args"][:2] == ["-m", "sales_agent.server"] and str(db_path) in cfg["args"]


def test_ask_with_a_scripted_model(db_path, capsys, monkeypatch):
    script = ScriptedModel([ModelTurn("", [ToolCall("1", "low_stock", {})]), ModelTurn("Bread and sachet water are out of stock.", [])])
    monkeypatch.setattr(cli, "make_model", lambda a: script)
    assert cli.main(["ask", "What is out of stock?", "--db", str(db_path), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["answer"].startswith("Bread") and res["steps"][0]["tool"] == "low_stock" and res["steps"][0]["ok"]


def test_ask_reports_api_errors(db_path, monkeypatch):
    import httpx

    from sales_agent.agent import AnthropicModel
    bad = AnthropicModel(api_key="x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(529, text="overloaded"))))
    monkeypatch.setattr(cli, "make_model", lambda a: bad)
    with pytest.raises(SystemExit, match="error: Anthropic API error 529"):
        cli.main(["ask", "hi", "--db", str(db_path)])


def test_ask_needs_a_key(db_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main(["ask", "hi", "--db", str(db_path)])
    with pytest.raises(SystemExit, match="--model is required"):
        cli.main(["ask", "hi", "--db", str(db_path), "--provider", "openai"])
