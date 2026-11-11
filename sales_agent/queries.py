"""Business questions as safe, parameterised, read-only queries.

Every function takes an open read-only connection and returns a pydantic model, so the same code serves the MCP
tools, the CLI and the tests. Inputs are validated here (dates, ranges, limits) so that a confused or manipulated
model cannot ask for something unbounded. Money is stored in kobo and returned in naira.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

MAX_RANGE_DAYS = 366
MAX_LIMIT = 50

Period = Literal["today", "yesterday", "this_week", "last_week", "last_7_days", "this_month", "last_month", "last_30_days",
                 "last_90_days", "all"]
GroupBy = Literal["none", "day", "week", "month", "hour", "weekday", "cashier", "payment_method", "category"]


class QueryError(ValueError):
    """A request the tools refuse; the message is meant for the model and the user."""


def _n(kobo: int | float | None) -> float:
    return round((kobo or 0) / 100, 2)


def _pct(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


# ---------------------------------------------------------------- shop and periods


class ShopInfo(BaseModel):
    shop_name: str
    city: str
    currency: str
    today: date = Field(description="Latest trading day in the data; relative periods are counted from here")
    data_start: date
    data_end: date
    products: int
    customers: int
    cashiers: list[str]
    categories: list[str]
    note: str


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}


def today(conn: sqlite3.Connection) -> date:
    return date.fromisoformat(_meta(conn)["data_end"])


def shop_info(conn: sqlite3.Connection) -> ShopInfo:
    m = _meta(conn)
    return ShopInfo(
        shop_name=m["shop_name"], city=m["city"], currency=m["currency"], today=date.fromisoformat(m["data_end"]),
        data_start=date.fromisoformat(m["data_start"]), data_end=date.fromisoformat(m["data_end"]),
        products=conn.execute("SELECT count(*) FROM products").fetchone()[0],
        customers=conn.execute("SELECT count(*) FROM customers").fetchone()[0],
        cashiers=[r[0] for r in conn.execute("SELECT DISTINCT cashier FROM sales ORDER BY 1")],
        categories=[r[0] for r in conn.execute("SELECT DISTINCT category FROM products ORDER BY 1")],
        note=m.get("note", ""),
    )


class DateRange(BaseModel):
    start: date
    end: date
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_range(conn: sqlite3.Connection, period: Period | None = None, start: str | None = None, end: str | None = None) -> DateRange:
    """Turn a named period or explicit ISO dates into an inclusive date range, validated and bounded."""
    t = today(conn)
    if start or end:
        if period:
            raise QueryError("give either `period` or `start`/`end`, not both")
        try:
            s = date.fromisoformat(start) if start else None
            e = date.fromisoformat(end) if end else t
        except ValueError as exc:
            raise QueryError(f"dates must be YYYY-MM-DD ({exc})") from None
        s = s or e
        label = f"{s.isoformat()} to {e.isoformat()}"
    else:
        p = period or "last_30_days"
        monday = t - timedelta(days=t.weekday())
        first = t.replace(day=1)
        prev_last = first - timedelta(days=1)
        ranges: dict[str, tuple[date, date]] = {
            "today": (t, t), "yesterday": (t - timedelta(days=1), t - timedelta(days=1)),
            "this_week": (monday, t), "last_week": (monday - timedelta(days=7), monday - timedelta(days=1)),
            "last_7_days": (t - timedelta(days=6), t), "this_month": (first, t), "last_month": (prev_last.replace(day=1), prev_last),
            "last_30_days": (t - timedelta(days=29), t), "last_90_days": (t - timedelta(days=89), t),
        }
        if p == "all":
            m = _meta(conn)
            s, e = date.fromisoformat(m["data_start"]), t
        elif p in ranges:
            s, e = ranges[p]
        else:
            raise QueryError(f"unknown period {p!r}; use one of: {', '.join([*ranges, 'all'])}")
        label = p.replace("_", " ")
    if e < s:
        raise QueryError("end date is before start date")
    if (e - s).days + 1 > MAX_RANGE_DAYS:
        raise QueryError(f"date range is too long (maximum {MAX_RANGE_DAYS} days)")
    return DateRange(start=s, end=e, label=label)


def _bounds(r: DateRange) -> tuple[str, str]:
    return r.start.isoformat(), (r.end + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- sales summary


class SummaryRow(BaseModel):
    group: str
    revenue: float
    gross_profit: float
    margin_pct: float | None
    transactions: int
    items_sold: int
    average_basket: float


class SalesSummary(BaseModel):
    currency: str = "NGN"
    period: DateRange
    revenue: float = Field(description="Net of discounts")
    cost_of_goods: float
    gross_profit: float
    margin_pct: float | None
    transactions: int
    items_sold: int
    average_basket: float
    discounts: float
    credit_sales: float = Field(description="Revenue sold on credit (not yet cash in hand)")
    group_by: str
    rows: list[SummaryRow]


_GROUP_SQL = {
    "day": "substr(s.sold_at, 1, 10)",
    "week": "date(s.sold_at, 'weekday 1', '-7 days')",
    "month": "substr(s.sold_at, 1, 7)",
    "hour": "substr(s.sold_at, 12, 2) || ':00'",
    "weekday": "CASE strftime('%w', s.sold_at) WHEN '0' THEN '7 Sun' WHEN '1' THEN '1 Mon' WHEN '2' THEN '2 Tue' WHEN '3' THEN '3 Wed' "
               "WHEN '4' THEN '4 Thu' WHEN '5' THEN '5 Fri' ELSE '6 Sat' END",
    "cashier": "s.cashier",
    "payment_method": "s.payment_method",
}


def sales_summary(conn: sqlite3.Connection, period: Period | None = None, start: str | None = None, end: str | None = None,
                  group_by: GroupBy = "none") -> SalesSummary:
    r = resolve_range(conn, period, start, end)
    lo, hi = _bounds(r)
    tot = conn.execute(
        """SELECT count(*) AS n, coalesce(sum(disc), 0) AS disc, coalesce(sum(gross), 0) AS gross, coalesce(sum(cost), 0) AS cost,
                  coalesce(sum(items), 0) AS items, coalesce(sum(CASE WHEN method = 'credit' THEN gross - disc END), 0) AS credit
           FROM (SELECT s.id, s.discount AS disc, s.payment_method AS method, sum(i.quantity * i.unit_price) AS gross,
                        sum(i.quantity * i.unit_cost) AS cost, sum(i.quantity) AS items
                 FROM sales s JOIN sale_items i ON i.sale_id = s.id
                 WHERE s.sold_at >= ? AND s.sold_at < ? GROUP BY s.id)""", (lo, hi)).fetchone()
    revenue = tot["gross"] - tot["disc"]
    rows: list[SummaryRow] = []
    if group_by == "category":
        # Discounts are per sale, so category revenue is shown before discounts.
        q = """SELECT p.category AS g, sum(i.quantity * i.unit_price) AS rev, sum(i.quantity * i.unit_cost) AS cost,
                      count(DISTINCT s.id) AS n, sum(i.quantity) AS items
               FROM sales s JOIN sale_items i ON i.sale_id = s.id JOIN products p ON p.id = i.product_id
               WHERE s.sold_at >= ? AND s.sold_at < ? GROUP BY g ORDER BY rev DESC"""
    elif group_by != "none":
        if group_by not in _GROUP_SQL:
            raise QueryError(f"unknown group_by {group_by!r}")
        q = f"""SELECT g, sum(gross - disc) AS rev, sum(cost) AS cost, count(*) AS n, sum(items) AS items FROM (
                  SELECT {_GROUP_SQL[group_by]} AS g, s.discount AS disc, sum(i.quantity * i.unit_price) AS gross,
                         sum(i.quantity * i.unit_cost) AS cost, sum(i.quantity) AS items
                  FROM sales s JOIN sale_items i ON i.sale_id = s.id
                  WHERE s.sold_at >= ? AND s.sold_at < ? GROUP BY s.id) GROUP BY g ORDER BY g"""  # noqa: S608 - g comes from a fixed whitelist
    if group_by != "none":
        for row in conn.execute(q, (lo, hi)):
            label = row["g"][2:] if group_by == "weekday" else row["g"]
            rows.append(SummaryRow(group=label, revenue=_n(row["rev"]), gross_profit=_n(row["rev"] - row["cost"]),
                                   margin_pct=_pct(row["rev"] - row["cost"], row["rev"]), transactions=row["n"], items_sold=row["items"],
                                   average_basket=_n(row["rev"] / row["n"]) if row["n"] else 0.0))
    return SalesSummary(
        period=r, revenue=_n(revenue), cost_of_goods=_n(tot["cost"]), gross_profit=_n(revenue - tot["cost"]),
        margin_pct=_pct(revenue - tot["cost"], revenue), transactions=tot["n"], items_sold=tot["items"],
        average_basket=_n(revenue / tot["n"]) if tot["n"] else 0.0, discounts=_n(tot["disc"]), credit_sales=_n(tot["credit"]),
        group_by=group_by, rows=rows,
    )


# ---------------------------------------------------------------- products


class ProductRow(BaseModel):
    sku: str
    name: str
    category: str
    quantity: int
    revenue: float
    gross_profit: float
    margin_pct: float | None
    share_of_revenue_pct: float | None


class TopProducts(BaseModel):
    currency: str = "NGN"
    period: DateRange
    ranked_by: str
    rows: list[ProductRow]


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def top_products(conn: sqlite3.Connection, period: Period | None = None, start: str | None = None, end: str | None = None,
                 by: Literal["revenue", "quantity", "profit"] = "revenue", limit: int = 10, category: str | None = None,
                 ascending: bool = False) -> TopProducts:
    r = resolve_range(conn, period, start, end)
    lo, hi = _bounds(r)
    order = {"revenue": "rev", "quantity": "qty", "profit": "rev - cost"}.get(by)
    if order is None:
        raise QueryError("`by` must be revenue, quantity or profit")
    total = conn.execute("""SELECT coalesce(sum(i.quantity * i.unit_price), 0) FROM sales s JOIN sale_items i ON i.sale_id = s.id
                            WHERE s.sold_at >= ? AND s.sold_at < ?""", (lo, hi)).fetchone()[0]
    rows = conn.execute(
        f"""SELECT p.sku, p.name, p.category, coalesce(sum(i.quantity), 0) AS qty, coalesce(sum(i.quantity * i.unit_price), 0) AS rev,
                   coalesce(sum(i.quantity * i.unit_cost), 0) AS cost
            FROM products p LEFT JOIN sale_items i ON i.product_id = p.id
                 AND i.sale_id IN (SELECT id FROM sales WHERE sold_at >= ? AND sold_at < ?)
            WHERE (? IS NULL OR lower(p.category) = lower(?))
            GROUP BY p.id ORDER BY {order} {"ASC" if ascending else "DESC"}, p.name LIMIT ?""",  # noqa: S608 - whitelisted
        (lo, hi, category, category, _limit(limit))).fetchall()
    if category and not rows:
        raise QueryError(f"no products in category {category!r}")
    return TopProducts(period=r, ranked_by=f"{by} ({'lowest' if ascending else 'highest'} first)", rows=[
        ProductRow(sku=x["sku"], name=x["name"], category=x["category"], quantity=x["qty"], revenue=_n(x["rev"]),
                   gross_profit=_n(x["rev"] - x["cost"]), margin_pct=_pct(x["rev"] - x["cost"], x["rev"]),
                   share_of_revenue_pct=_pct(x["rev"], total)) for x in rows])


StockStatus = Literal["out_of_stock", "below_reorder_level", "running_low"]


class StockRow(BaseModel):
    sku: str
    name: str
    stock: int
    reorder_level: int
    avg_daily_sales: float
    days_of_cover: float | None = Field(description="How many days the current stock lasts at the recent sales rate")
    suggested_order: int = Field(description="Units to bring stock up to the target number of days")
    status: StockStatus


class LowStock(BaseModel):
    as_of: date
    lookback_days: int
    target_days: int
    rows: list[StockRow]


def low_stock(conn: sqlite3.Connection, max_days_of_cover: float = 7, lookback_days: int = 28, target_days: int = 21) -> LowStock:
    if not 1 <= lookback_days <= 120 or not 1 <= target_days <= 90 or not 0 < max_days_of_cover <= 60:
        raise QueryError("lookback_days 1-120, target_days 1-90, max_days_of_cover 0-60")
    t = today(conn)
    lo = (t - timedelta(days=lookback_days - 1)).isoformat()
    hi = (t + timedelta(days=1)).isoformat()
    out = []
    for x in conn.execute("""SELECT p.sku, p.name, p.stock, p.reorder_level,
                                    coalesce((SELECT sum(i.quantity) FROM sale_items i JOIN sales s ON s.id = i.sale_id
                                              WHERE i.product_id = p.id AND s.sold_at >= ? AND s.sold_at < ?), 0) AS sold
                             FROM products p""", (lo, hi)):
        rate = x["sold"] / lookback_days
        cover = round(x["stock"] / rate, 1) if rate else None
        status: StockStatus | None = ("out_of_stock" if x["stock"] <= 0 else "below_reorder_level" if x["stock"] < x["reorder_level"]
                  else "running_low" if cover is not None and cover <= max_days_of_cover else None)
        if status:
            out.append(StockRow(sku=x["sku"], name=x["name"], stock=x["stock"], reorder_level=x["reorder_level"], avg_daily_sales=round(rate, 2),
                                days_of_cover=cover, suggested_order=max(0, round(rate * target_days) - x["stock"]), status=status))
    out.sort(key=lambda r: (r.days_of_cover if r.days_of_cover is not None else 1e9))
    return LowStock(as_of=t, lookback_days=lookback_days, target_days=target_days, rows=out)


class MarginAlert(BaseModel):
    sku: str
    name: str
    shelf_price: float
    current_cost: float
    margin_pct: float | None
    units_sold_below_cost: int
    loss_on_those_units: float
    issue: str


class MarginAlerts(BaseModel):
    currency: str = "NGN"
    period: DateRange
    min_margin_pct: float
    rows: list[MarginAlert]


def margin_alerts(conn: sqlite3.Connection, period: Period | None = None, min_margin_pct: float = 5.0) -> MarginAlerts:
    """Products priced at or below cost, or with a margin below the minimum, plus what selling below cost has lost."""
    r = resolve_range(conn, period or "last_30_days")
    lo, hi = _bounds(r)
    rows = []
    for x in conn.execute(
            """SELECT p.sku, p.name, p.unit_price, p.unit_cost,
                      coalesce(sum(CASE WHEN i.unit_price < i.unit_cost THEN i.quantity END), 0) AS n_below,
                      coalesce(sum(CASE WHEN i.unit_price < i.unit_cost THEN i.quantity * (i.unit_cost - i.unit_price) END), 0) AS loss
               FROM products p LEFT JOIN sale_items i ON i.product_id = p.id
                    AND i.sale_id IN (SELECT id FROM sales WHERE sold_at >= ? AND sold_at < ?)
               GROUP BY p.id""", (lo, hi)):
        margin = _pct(x["unit_price"] - x["unit_cost"], x["unit_price"])
        if x["unit_price"] <= x["unit_cost"]:
            issue = "shelf price is at or below the current cost"
        elif x["n_below"]:
            issue = "some sales were below cost"
        elif margin is not None and margin < min_margin_pct:
            issue = f"margin below {min_margin_pct:g}%"
        else:
            continue
        rows.append(MarginAlert(sku=x["sku"], name=x["name"], shelf_price=_n(x["unit_price"]), current_cost=_n(x["unit_cost"]),
                                margin_pct=margin, units_sold_below_cost=x["n_below"], loss_on_those_units=_n(x["loss"]), issue=issue))
    rows.sort(key=lambda a: a.margin_pct if a.margin_pct is not None else 0)
    return MarginAlerts(period=r, min_margin_pct=min_margin_pct, rows=rows)


class ProductMatch(BaseModel):
    sku: str
    name: str
    category: str
    price: float
    cost: float
    stock: int


def find_products(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[ProductMatch]:
    q = query.strip()
    if not 1 <= len(q) <= 60:
        raise QueryError("search text must be 1-60 characters")
    like = f"%{q.lower().replace('%', '').replace('_', '')}%"
    return [ProductMatch(sku=x["sku"], name=x["name"], category=x["category"], price=_n(x["unit_price"]), cost=_n(x["unit_cost"]), stock=x["stock"])
            for x in conn.execute("""SELECT * FROM products WHERE lower(name) LIKE ? OR lower(sku) LIKE ? OR lower(category) LIKE ?
                                     ORDER BY name LIMIT ?""", (like, like, like, _limit(limit)))]


# ---------------------------------------------------------------- customers and credit


def mask_phone(phone: str | None) -> str | None:
    """Show only the last three digits: the model never needs a customer's full number."""
    if not phone:
        return None
    return "*" * max(0, len(phone) - 3) + phone[-3:]


class DebtRow(BaseModel):
    customer_id: int
    name: str
    phone: str | None = Field(description="Masked")
    balance: float
    credit_limit: float
    over_limit: bool
    last_credit_purchase: date | None
    last_payment: date | None
    days_since_last_payment: int | None


class Debts(BaseModel):
    currency: str = "NGN"
    as_of: date
    total_outstanding: float
    customers_owing: int
    rows: list[DebtRow]


def _balances(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.id, c.name, c.phone, c.language, c.credit_limit,
                  coalesce((SELECT sum(t) FROM (SELECT sum(i.quantity * i.unit_price) - s.discount AS t
                                               FROM sales s JOIN sale_items i ON i.sale_id = s.id
                                               WHERE s.customer_id = c.id AND s.payment_method = 'credit' GROUP BY s.id)), 0)
                  - coalesce((SELECT sum(amount) FROM payments p WHERE p.customer_id = c.id), 0) AS balance,
                  (SELECT max(substr(sold_at, 1, 10)) FROM sales s WHERE s.customer_id = c.id AND s.payment_method = 'credit') AS last_credit,
                  (SELECT max(substr(paid_at, 1, 10)) FROM payments p WHERE p.customer_id = c.id) AS last_payment
           FROM customers c""").fetchall()


def customer_debts(conn: sqlite3.Connection, min_balance: float = 1, only_over_limit: bool = False,
                   sort_by: Literal["balance", "days_since_last_payment"] = "balance", limit: int = 20) -> Debts:
    t = today(conn)
    rows = []
    for x in _balances(conn):
        if x["balance"] < min_balance * 100:
            continue
        over = x["balance"] > x["credit_limit"]
        if only_over_limit and not over:
            continue
        lp = date.fromisoformat(x["last_payment"]) if x["last_payment"] else None
        first_credit = None if lp else conn.execute(
            "SELECT min(substr(sold_at, 1, 10)) FROM sales WHERE customer_id = ? AND payment_method = 'credit'", (x["id"],)).fetchone()[0]
        since = (t - lp).days if lp else ((t - date.fromisoformat(first_credit)).days if first_credit else None)
        rows.append(DebtRow(customer_id=x["id"], name=x["name"], phone=mask_phone(x["phone"]), balance=_n(x["balance"]),
                            credit_limit=_n(x["credit_limit"]), over_limit=over,
                            last_credit_purchase=date.fromisoformat(x["last_credit"]) if x["last_credit"] else None,
                            last_payment=lp, days_since_last_payment=since))
    rows.sort(key=(lambda d: -d.balance) if sort_by == "balance" else (lambda d: -(d.days_since_last_payment or 0)))
    return Debts(as_of=t, total_outstanding=round(sum(r.balance for r in rows), 2), customers_owing=len(rows), rows=rows[:_limit(limit)])


class Reminder(BaseModel):
    customer_id: int
    name: str
    phone: str | None
    language: Literal["en", "ha"]
    balance: float
    message: str
    status: str = "DRAFT — not sent. Review it, then send it yourself (WhatsApp or SMS)."


def _money(naira: float) -> str:
    return f"₦{naira:,.0f}" if naira == int(naira) else f"₦{naira:,.2f}"


def draft_debt_reminder(conn: sqlite3.Connection, customer_id: int, language: Literal["en", "ha"] | None = None,
                        tone: Literal["friendly", "firm"] = "friendly") -> Reminder:
    """Write a polite reminder. It is only a draft: this server has no way to send messages."""
    shop = shop_info(conn)
    row = next((x for x in _balances(conn) if x["id"] == customer_id), None)
    if row is None:
        raise QueryError(f"no customer with id {customer_id}")
    balance = _n(row["balance"])
    if balance <= 0:
        raise QueryError(f"{row['name']} has no outstanding balance")
    lang = language or row["language"]
    parts = row["name"].split()
    first = f"{parts[0]} {parts[-1]}" if parts[0] in {"Mr", "Mrs", "Ms", "Dr"} else parts[0] if parts[0] not in {"Malam", "Hajiya"} else row["name"]
    amount = _money(balance)
    if lang == "ha":
        polite = "Don Allah a biya idan dama ta samu." if tone == "friendly" else "Don Allah a biya kafin karshen wannan mako."
        msg = (f"Assalamu alaikum {row['name']}. Sako daga {shop.shop_name}, {shop.city}. Akwai sauran bashi na {amount} a kan asusunku. "
               f"{polite} Mun gode da kasuwancinku.")
    else:
        polite = ("Kindly settle it whenever you can — we appreciate your custom." if tone == "friendly"
                  else "Please settle it by the end of this week so we can keep your credit account open.")
        msg = f"Hello {first}, this is {shop.shop_name}, {shop.city}. Your account has an outstanding balance of {amount}. {polite} Thank you."
    return Reminder(customer_id=customer_id, name=row["name"], phone=mask_phone(row["phone"]), language=lang, balance=balance, message=msg)


# ---------------------------------------------------------------- comparisons


class Change(BaseModel):
    metric: str
    a: float
    b: float
    change: float
    change_pct: float | None


class Comparison(BaseModel):
    currency: str = "NGN"
    period_a: DateRange
    period_b: DateRange
    note: str
    changes: list[Change]


def compare_periods(conn: sqlite3.Connection, period_a: Period = "last_week", period_b: Period = "this_week") -> Comparison:
    """Compare two periods. Totals are scaled to a per-day rate when the periods differ in length."""
    a, b = sales_summary(conn, period_a), sales_summary(conn, period_b)
    da, db_ = a.period.days, b.period.days
    per_day = da != db_
    out = []
    for metric in ("revenue", "gross_profit", "transactions", "items_sold", "average_basket", "margin_pct"):
        va, vb = float(getattr(a, metric) or 0), float(getattr(b, metric) or 0)
        if per_day and metric in {"revenue", "gross_profit", "transactions", "items_sold"}:
            va, vb = va / da, vb / db_
            metric = f"{metric}_per_day"
        out.append(Change(metric=metric, a=round(va, 2), b=round(vb, 2), change=round(vb - va, 2), change_pct=_pct(vb - va, va)))
    note = (f"{a.period.label} ({da} days) vs {b.period.label} ({db_} days); " +
            ("totals shown per day because the periods differ in length" if per_day else "same length, totals compared directly"))
    return Comparison(period_a=a.period, period_b=b.period, note=note, changes=out)
