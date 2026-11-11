"""The MCP server: shop analytics tools for any MCP client (Claude Desktop, Claude Code, Cursor, the bundled agent...).

Guardrails, in layers:
1. The database is opened read-only by SQLite itself, so no tool can change shop data.
2. Every query is parameterised and every input is validated and bounded (periods, limits, search text).
3. Customer phone numbers are masked; reminders are drafts the owner sends personally — this server cannot send anything.
4. Each call is written to an audit log (tool, arguments, duration, outcome).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__, db
from . import queries as q

log = logging.getLogger("sales_agent")
F = TypeVar("F", bound=Callable[..., Any])

INSTRUCTIONS = """Tools for analysing one shop's sales, stock, margins and customer credit (all read-only).
Start with `shop_info` to learn the shop, its currency (NGN, amounts in naira) and "today" — the latest trading day in
the data, from which periods such as this_week or last_30_days are counted. Prefer named periods; use start/end
(YYYY-MM-DD) for custom ranges. Quote numbers exactly as the tools return them and say which period they cover.
Reminders are drafts only; never claim a message was sent."""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

PeriodArg = Annotated[q.Period | None, Field(description="Named period counted back from the shop's latest trading day. Default: last_30_days")]
StartArg = Annotated[str | None, Field(description="Custom range start, YYYY-MM-DD (use instead of period)")]
EndArg = Annotated[str | None, Field(description="Custom range end, YYYY-MM-DD, inclusive (default: the latest trading day)")]


class AuditLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, tool: str, args: dict[str, Any], ok: bool, ms: float, error: str = "") -> None:
        entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "tool": tool, "args": args, "ok": ok, "ms": round(ms, 1)}
        if error:
            entry["error"] = error
        log.info("tool %s ok=%s %.1fms", tool, ok, ms)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")


def create_server(db_path: str | Path, audit_path: str | Path | None = None) -> MCPServer:
    conn = db.connect_readonly(db_path)
    audit = AuditLog(Path(audit_path) if audit_path else None)
    mcp = MCPServer("sales-agent", title="Shop sales analyst", instructions=INSTRUCTIONS, version=__version__)

    def tool(title: str) -> Callable[[F], F]:
        def deco(fn: F) -> F:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                t0 = time.perf_counter()
                try:
                    out = fn(*args, **kwargs)
                except q.QueryError as e:
                    audit.write(fn.__name__, kwargs, False, (time.perf_counter() - t0) * 1000, str(e))
                    raise ToolError(str(e)) from None
                audit.write(fn.__name__, kwargs, True, (time.perf_counter() - t0) * 1000)
                return out

            mcp.tool(title=title, annotations=READ_ONLY)(wrapper)
            return fn

        return deco

    @tool("Shop information")
    def shop_info() -> q.ShopInfo:
        """Shop name, currency, the date range of the data, the latest trading day ("today"), cashiers and product categories."""
        return q.shop_info(conn)

    @tool("Sales summary")
    def sales_summary(period: PeriodArg = None, start: StartArg = None, end: EndArg = None,
                      group_by: Annotated[q.GroupBy, Field(description="Break totals down by this dimension")] = "none") -> q.SalesSummary:
        """Revenue (after discounts), cost, gross profit, margin, transactions, items sold, average basket and credit sales for a period,
        optionally broken down by day, week, month, hour, weekday, cashier, payment method or product category."""
        return q.sales_summary(conn, period, start, end, group_by)

    @tool("Top products")
    def top_products(period: PeriodArg = None, start: StartArg = None, end: EndArg = None,
                     by: Annotated[Literal["revenue", "quantity", "profit"], Field(description="Ranking measure")] = "revenue",
                     limit: Annotated[int, Field(ge=1, le=q.MAX_LIMIT)] = 10,
                     category: Annotated[str | None, Field(description="Only this category (see shop_info)")] = None,
                     ascending: Annotated[bool, Field(description="True for the worst performers first")] = False) -> q.TopProducts:
        """Rank products by revenue, quantity or gross profit in a period, with margin and share of revenue. Use ascending=True for slow
        movers or least profitable lines."""
        return q.top_products(conn, period, start, end, by, limit, category, ascending)

    @tool("Low stock")
    def low_stock(max_days_of_cover: Annotated[float, Field(gt=0, le=60, description="Flag items that will run out within this many days")] = 7,
                  lookback_days: Annotated[int, Field(ge=1, le=120, description="Days of sales used for the sales rate")] = 28,
                  target_days: Annotated[int, Field(ge=1, le=90, description="Days of stock a suggested order should cover")] = 21) -> q.LowStock:
        """Products that are out of stock, below their reorder level, or will run out soon at the recent sales rate, with a suggested
        order quantity."""
        return q.low_stock(conn, max_days_of_cover, lookback_days, target_days)

    @tool("Margin alerts")
    def margin_alerts(period: PeriodArg = None,
                      min_margin_pct: Annotated[float, Field(ge=0, le=100, description="Flag margins below this percentage")] = 5.0) -> q.MarginAlerts:
        """Products whose shelf price is at or below the current cost, that were sold below cost in the period (with the money lost),
        or whose margin is below a minimum."""
        return q.margin_alerts(conn, period, min_margin_pct)

    @tool("Compare periods")
    def compare_periods(period_a: Annotated[q.Period, Field(description="The earlier/baseline period")] = "last_week",
                        period_b: Annotated[q.Period, Field(description="The period to compare with it")] = "this_week") -> q.Comparison:
        """Compare revenue, profit, transactions, items, basket size and margin between two named periods (per day when lengths differ)."""
        return q.compare_periods(conn, period_a, period_b)

    @tool("Find products")
    def find_products(query: Annotated[str, Field(min_length=1, max_length=60, description="Part of a product name, SKU or category")],
                      limit: Annotated[int, Field(ge=1, le=q.MAX_LIMIT)] = 10) -> list[q.ProductMatch]:
        """Look up products by name, SKU or category, with price, cost and stock on hand."""
        return q.find_products(conn, query, limit)

    @tool("Customer debts")
    def customer_debts(min_balance: Annotated[float, Field(ge=0, description="Only balances of at least this many naira")] = 1,
                       only_over_limit: bool = False,
                       sort_by: Literal["balance", "days_since_last_payment"] = "balance",
                       limit: Annotated[int, Field(ge=1, le=q.MAX_LIMIT)] = 20) -> q.Debts:
        """Customers who owe money from credit sales: balance, credit limit, whether they are over it, and how long since they last paid.
        Phone numbers are masked."""
        return q.customer_debts(conn, min_balance, only_over_limit, sort_by, limit)

    @tool("Draft a debt reminder")
    def draft_debt_reminder(customer_id: Annotated[int, Field(ge=1, description="customer_id from customer_debts")],
                            language: Annotated[Literal["en", "ha"] | None, Field(description="en English, ha Hausa")] = None,
                            tone: Literal["friendly", "firm"] = "friendly") -> q.Reminder:
        """Draft a polite payment reminder in English or Hausa for the owner to review and send personally. Nothing is sent."""
        return q.draft_debt_reminder(conn, customer_id, language, tone)

    @mcp.resource("shop://schema", name="Database schema", description="Tables and columns behind the tools", mime_type="text/plain")
    def schema() -> str:
        return db.SCHEMA.strip()

    @mcp.prompt(title="Weekly business review")
    def weekly_review() -> str:
        """A ready-made prompt for a weekly review of the shop."""
        return ("Give me a weekly review of my shop. Compare this week with last week, list the top 5 products by profit, any stock "
                "that will run out within a week with how much to order, any products selling below cost, and the three customers "
                "who have gone longest without paying. Finish with three concrete actions for next week. Keep it short.")

    return mcp


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - exercised through the stdio test
    import argparse

    p = argparse.ArgumentParser(prog="sales-agent-server", description="Run the sales MCP server (stdio by default).")
    p.add_argument("--db", default=os.environ.get("SALES_AGENT_DB", "data/shop.db"))
    p.add_argument("--audit-log", default=os.environ.get("SALES_AGENT_AUDIT_LOG"))
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1", help="interface for --http (default: this machine only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default=os.environ.get("SALES_AGENT_LOG_LEVEL", "INFO"), choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    a = p.parse_args(argv)
    logging.basicConfig(level=a.log_level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")  # never log to stdout
    logging.getLogger("mcp").setLevel(max(logging.WARNING, logging.getLevelName(a.log_level)))
    server = create_server(a.db, a.audit_log)
    if a.http:
        server.run(transport="streamable-http", host=a.host, port=a.port)
    else:
        server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
