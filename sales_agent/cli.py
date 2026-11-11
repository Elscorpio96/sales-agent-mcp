"""Command line: seed demo data, run the server, call tools directly, ask the agent, print a client config."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp import Client, StdioServerParameters
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel

from . import __version__
from .agent import AnthropicModel, Model, OpenAICompatibleModel, Step, run_agent

console = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)
DEFAULT_DB = os.environ.get("SALES_AGENT_DB", "data/shop.db")


def _server_params(db: str, audit: str | None = None) -> StdioServerParameters:
    args = ["-m", "sales_agent.server", "--db", str(Path(db).resolve()), "--log-level", "WARNING"]
    if audit:
        args += ["--audit-log", str(Path(audit).resolve())]
    return StdioServerParameters(command=sys.executable, args=args)


def _require_db(db: str) -> None:
    if not Path(db).exists():
        raise SystemExit(f"error: {db} not found. Create the demo database first:  sales-agent seed --db {db}")


def cmd_seed(a: argparse.Namespace) -> int:
    from .seed import build

    path = build(Path(a.db))
    console.print(f"Demo database written to [bold]{path}[/] (generated data for a fictional shop).")
    return 0


def cmd_import(a: argparse.Namespace) -> int:
    from .importer import CSVImportError, import_csv

    try:
        s = import_csv(Path(a.db), Path(a.products), Path(a.sales), Path(a.customers) if a.customers else None,
                       Path(a.payments) if a.payments else None, shop_name=a.shop_name, city=a.city, currency=a.currency, overwrite=a.force)
    except (CSVImportError, FileExistsError) as e:
        err.print(f"error: {e}", style="red", markup=False)
        return 1
    for w in s.warnings:
        err.print(f"[yellow]warning:[/] {w}")
    console.print(f"Imported [bold]{s.sales}[/] sales ({s.line_items} items), {s.products} products, {s.customers} customers and "
                  f"{s.payments} payments into [bold]{s.db_path}[/]. Data covers {s.first_sale[:10]} to {s.last_sale[:10]}.")
    return 0


def cmd_tools(a: argparse.Namespace) -> int:
    _require_db(a.db)

    async def go() -> None:
        async with Client(_server_params(a.db)) as c:
            for t in (await c.list_tools()).tools:
                props = ", ".join(t.input_schema.get("properties", {}))
                console.print(f"[bold cyan]{t.name}[/]({props})")
                console.print(f"  [dim]{' '.join((t.description or '').split())}[/]", highlight=False, soft_wrap=False)

    asyncio.run(go())
    return 0


def cmd_call(a: argparse.Namespace) -> int:
    _require_db(a.db)
    try:
        args: dict[str, Any] = json.loads(a.args)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: --args is not valid JSON ({e})") from None

    async def go() -> int:
        async with Client(_server_params(a.db)) as c:
            r = await c.call_tool(a.tool, args)
        if r.is_error:
            err.print(f"[red]{getattr(r.content[0], 'text', 'error') if r.content else 'error'}[/]", markup=False)
            return 1
        if r.structured_content is not None:
            console.print(JSON.from_data(r.structured_content.get("result", r.structured_content), ensure_ascii=False), soft_wrap=False)
        else:
            console.print("\n".join(getattr(x, "text", "") for x in r.content))
        return 0

    return asyncio.run(go())


def make_model(a: argparse.Namespace) -> Model:
    if a.provider == "anthropic":
        return AnthropicModel(model=a.model)
    if not a.model:
        raise SystemExit("error: --model is required for --provider openai (e.g. gpt-4o-mini, or llama3.1 on Ollama)")
    return OpenAICompatibleModel(model=a.model, base_url=a.base_url or "https://api.openai.com/v1")


def cmd_ask(a: argparse.Namespace) -> int:
    _require_db(a.db)
    try:
        model = make_model(a)
    except RuntimeError as e:
        raise SystemExit(f"error: {e}") from None

    def show(step: Step) -> None:
        if not a.json:
            mark = "[green]✓[/]" if step.ok else "[red]✕[/]"
            err.print(f"  {mark} [cyan]{step.tool}[/]({json.dumps(step.arguments)}) [dim]{step.ms:.0f} ms[/]")

    async def go() -> int:
        async with Client(_server_params(a.db, a.audit_log)) as c:
            res = await run_agent(a.question, c, model, max_steps=a.max_steps, on_step=show)
        if a.json:
            print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
        else:
            console.print(Panel(Markdown(res.answer or "(no answer)"), title=a.question, title_align="left"))
            console.print(f"[dim]{res.model} · {res.turns} turns · {len(res.steps)} tool calls · {res.input_tokens}+{res.output_tokens} tokens · "
                          f"{res.seconds:.1f} s[/]")
        return 0 if res.stopped == "answered" else 1

    try:
        return asyncio.run(go())
    except BaseException as e:  # errors raised inside the MCP client arrive wrapped in exception groups
        root = _root_cause(e)
        if isinstance(root, (RuntimeError, httpx.HTTPError)):
            raise SystemExit(f"error: {root}") from None
        raise


def _root_cause(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


def cmd_config(a: argparse.Namespace) -> int:
    """Print the JSON block to paste into Claude Desktop (claude_desktop_config.json) or another MCP client."""
    p = _server_params(a.db)
    print(json.dumps({"mcpServers": {"sales-agent": {"command": p.command, "args": p.args}}}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sales-agent", description="Ask questions about a shop's sales through an MCP server.")
    p.add_argument("--version", action="version", version=f"sales-agent {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_db(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB}, or $SALES_AGENT_DB)")
        return sp

    with_db(sub.add_parser("seed", help="create the demo database")).set_defaults(fn=cmd_seed)
    im = with_db(sub.add_parser("import", help="load your own data from CSV exports"))
    im.add_argument("--products", required=True, help="products.csv: sku, name, category, price, cost, stock[, reorder_level]")
    im.add_argument("--sales", required=True, help="sales.csv: sale_id, sold_at, cashier, payment_method, sku, quantity[, unit_price, ...]")
    im.add_argument("--customers", help="customers.csv: id, name[, phone, language, credit_limit]")
    im.add_argument("--payments", help="payments.csv: customer_id, paid_at, amount")
    im.add_argument("--shop-name", default="My shop")
    im.add_argument("--city", default="")
    im.add_argument("--currency", default="NGN")
    im.add_argument("--force", action="store_true", help="replace the database if it exists")
    im.set_defaults(fn=cmd_import)
    with_db(sub.add_parser("tools", help="list the server's tools")).set_defaults(fn=cmd_tools)
    c = with_db(sub.add_parser("call", help="call one tool directly (no model, no API key)"))
    c.add_argument("tool")
    c.add_argument("--args", default="{}", help='JSON arguments, e.g. \'{"period": "this_week"}\'')
    c.set_defaults(fn=cmd_call)
    k = with_db(sub.add_parser("ask", help="ask the agent a question in plain language"))
    k.add_argument("question")
    k.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    k.add_argument("--model", help="model name (default $SALES_AGENT_MODEL or claude-sonnet-5-5)")
    k.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. http://localhost:11434/v1 for Ollama")
    k.add_argument("--max-steps", type=int, default=8)
    k.add_argument("--audit-log", help="append every tool call to this JSONL file")
    k.add_argument("--json", action="store_true", help="print the full result, including every tool call, as JSON")
    k.set_defaults(fn=cmd_ask)
    with_db(sub.add_parser("config", help="print a Claude Desktop / MCP client config block")).set_defaults(fn=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
