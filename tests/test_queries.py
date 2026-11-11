"""Every figure the tools report is re-derived here from raw rows in plain Python, not with the same SQL."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

import pytest

from sales_agent import queries as q
from sales_agent.seed import build


def kobo_to_naira(x: float) -> float:
    return round(x / 100, 2)


def sales_in(raw: sqlite3.Connection, start: date, end: date) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for sid, sold_at, cashier, method, cust, disc in raw.execute("SELECT * FROM sales"):
        if start <= date.fromisoformat(sold_at[:10]) <= end:
            out[sid] = {"sold_at": sold_at, "cashier": cashier, "method": method, "customer": cust, "discount": disc, "gross": 0, "cost": 0, "qty": 0}
    for _id, sid, _pid, qty, price, cost in raw.execute("SELECT * FROM sale_items"):
        if sid in out:
            out[sid]["gross"] += qty * price
            out[sid]["cost"] += qty * cost
            out[sid]["qty"] += qty
    return out


def test_shop_info(conn):
    info = q.shop_info(conn)
    assert info.shop_name == "Mai Kanti Mini Mart" and info.currency == "NGN"
    assert info.today == date(2026, 10, 31) and info.data_start == date(2026, 7, 4)
    assert info.products == 36 and info.customers == 12 and info.cashiers == ["Aisha", "Grace", "Musa"]
    assert "not a real business" in info.note


@pytest.mark.parametrize("period, start, end", [
    ("today", "2026-10-31", "2026-10-31"),
    ("yesterday", "2026-10-30", "2026-10-30"),
    ("this_week", "2026-10-26", "2026-10-31"),   # 31 Oct 2026 is a Saturday
    ("last_week", "2026-10-19", "2026-10-25"),
    ("last_7_days", "2026-10-25", "2026-10-31"),
    ("this_month", "2026-10-01", "2026-10-31"),
    ("last_month", "2026-09-01", "2026-09-30"),
    ("last_30_days", "2026-10-02", "2026-10-31"),
    ("last_90_days", "2026-08-03", "2026-10-31"),
    ("all", "2026-07-04", "2026-10-31"),
])
def test_named_periods(conn, period, start, end):
    r = q.resolve_range(conn, period)
    assert (r.start.isoformat(), r.end.isoformat()) == (start, end)


def test_custom_and_invalid_ranges(conn):
    assert q.resolve_range(conn, start="2026-10-01").end == date(2026, 10, 31)
    assert q.resolve_range(conn, end="2026-10-05").start == date(2026, 10, 5)
    for kwargs, msg in [({"period": "forever"}, "unknown period"), ({"start": "31/10/2026"}, "YYYY-MM-DD"),
                        ({"start": "2026-10-10", "end": "2026-10-01"}, "before start"), ({"start": "2024-01-01"}, "too long"),
                        ({"period": "today", "start": "2026-10-01"}, "not both")]:
        with pytest.raises(q.QueryError, match=msg):
            q.resolve_range(conn, **kwargs)


@pytest.mark.parametrize("period", ["today", "this_week", "last_month", "all"])
def test_sales_summary_totals_match_raw_rows(conn, raw, period):
    s = q.sales_summary(conn, period)
    rows = sales_in(raw, s.period.start, s.period.end)
    revenue = sum(r["gross"] - r["discount"] for r in rows.values())
    cost = sum(r["cost"] for r in rows.values())
    assert s.transactions == len(rows)
    assert s.items_sold == sum(r["qty"] for r in rows.values())
    assert s.revenue == kobo_to_naira(revenue)
    assert s.gross_profit == kobo_to_naira(revenue - cost)
    assert s.credit_sales == kobo_to_naira(sum(r["gross"] - r["discount"] for r in rows.values() if r["method"] == "credit"))
    assert s.margin_pct == round(100 * (revenue - cost) / revenue, 1)


@pytest.mark.parametrize("group_by", ["day", "week", "month", "hour", "weekday", "cashier", "payment_method"])
def test_group_rows_add_up_to_the_total(conn, group_by):
    s = q.sales_summary(conn, "last_90_days", group_by=group_by)
    assert len(s.rows) > 1
    assert sum(r.transactions for r in s.rows) == s.transactions
    assert sum(r.revenue for r in s.rows) == pytest.approx(s.revenue, abs=0.05)


def test_group_by_cashier_matches_raw(conn, raw):
    s = q.sales_summary(conn, "this_month", group_by="cashier")
    rows = sales_in(raw, s.period.start, s.period.end)
    expected: dict[str, int] = defaultdict(int)
    for r in rows.values():
        expected[r["cashier"]] += r["gross"] - r["discount"]
    assert {r.group: r.revenue for r in s.rows} == {k: kobo_to_naira(v) for k, v in expected.items()}


def test_weekday_labels_and_category_breakdown(conn):
    s = q.sales_summary(conn, "last_90_days", group_by="weekday")
    assert [r.group for r in s.rows] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    busiest = max(s.rows, key=lambda r: r.revenue).group
    assert busiest == "Fri"  # the generator makes Fridays busiest
    cat = q.sales_summary(conn, "last_90_days", group_by="category")
    assert sum(r.revenue for r in cat.rows) == pytest.approx(s.revenue + s.discounts, abs=0.05)  # categories are before discounts


def test_top_products_ranking_and_share(conn, raw):
    t = q.top_products(conn, "last_30_days", limit=50)
    lo, hi = t.period.start, t.period.end
    rows = sales_in(raw, lo, hi)
    per: dict[int, int] = defaultdict(int)
    for _id, sid, pid, qty, price, _cost in raw.execute("SELECT * FROM sale_items"):
        if sid in rows:
            per[pid] += qty * price
    names = dict(raw.execute("SELECT id, name FROM products"))
    best = max(per, key=lambda p: per[p])
    assert t.rows[0].name == names[best] and t.rows[0].revenue == kobo_to_naira(per[best])
    assert [r.revenue for r in t.rows] == sorted((r.revenue for r in t.rows), reverse=True)
    assert sum(r.share_of_revenue_pct or 0 for r in t.rows) == pytest.approx(100, abs=0.5)


def test_top_products_options(conn):
    by_qty = q.top_products(conn, "this_month", by="quantity", limit=3)
    assert by_qty.rows[0].name == "Instant noodles 70g"
    worst = q.top_products(conn, "last_30_days", by="profit", ascending=True, limit=1)
    assert worst.rows[0].name == "Groundnut oil 5L"  # sold near or below cost
    oil = q.top_products(conn, "all", category="oil")
    assert {r.category for r in oil.rows} == {"Oil"} and len(oil.rows) == 3
    with pytest.raises(q.QueryError, match="no products in category"):
        q.top_products(conn, "all", category="Electronics")
    with pytest.raises(q.QueryError, match="limit"):
        q.top_products(conn, limit=500)


def test_low_stock(conn, raw):
    ls = q.low_stock(conn)
    names = {r.name: r for r in ls.rows}
    assert names["Bread (family loaf)"].status == "out_of_stock"
    assert names["Sachet water (bag of 20)"].status == "out_of_stock"
    for r in ls.rows:
        stock, reorder = raw.execute("SELECT stock, reorder_level FROM products WHERE sku = ?", (r.sku,)).fetchone()
        assert (r.stock, r.reorder_level) == (stock, reorder)
        assert r.suggested_order == max(0, round(r.avg_daily_sales * 21) - r.stock) or abs(
            r.suggested_order - (round(r.avg_daily_sales * 21) - r.stock)) <= 1
    assert [r.days_of_cover for r in ls.rows] == sorted(r.days_of_cover for r in ls.rows)
    assert len(q.low_stock(conn, max_days_of_cover=30).rows) > len(ls.rows)
    with pytest.raises(q.QueryError):
        q.low_stock(conn, lookback_days=0)


def test_margin_alerts_find_oil_sold_below_cost(conn, raw):
    m = q.margin_alerts(conn, "last_30_days")
    assert [a.sku for a in m.rows] == ["OIL-5L"]
    a = m.rows[0]
    assert a.shelf_price == 14000 and a.current_cost == 14500 and a.margin_pct == -3.6
    lo, hi = m.period.start.isoformat(), (m.period.end + timedelta(days=1)).isoformat()
    n, loss = raw.execute("""SELECT sum(i.quantity), sum(i.quantity * (i.unit_cost - i.unit_price)) FROM sale_items i JOIN sales s ON s.id = i.sale_id
                             WHERE i.product_id = 10 AND i.unit_price < i.unit_cost AND s.sold_at >= ? AND s.sold_at < ?""", (lo, hi)).fetchone()
    assert a.units_sold_below_cost == n and a.loss_on_those_units == kobo_to_naira(loss)
    assert len(q.margin_alerts(conn, "last_30_days", min_margin_pct=10).rows) > 1


def test_customer_debts_match_raw(conn, raw):
    d = q.customer_debts(conn, limit=50)
    owed: dict[int, int] = defaultdict(int)
    rows = sales_in(raw, date(2000, 1, 1), date(2100, 1, 1))
    for r in rows.values():
        if r["method"] == "credit":
            owed[r["customer"]] += r["gross"] - r["discount"]
    for cid, amount in raw.execute("SELECT customer_id, sum(amount) FROM payments GROUP BY 1"):
        owed[cid] -= amount
    expected = {cid: kobo_to_naira(v) for cid, v in owed.items() if v >= 100}
    assert {r.customer_id: r.balance for r in d.rows} == expected
    assert d.total_outstanding == pytest.approx(sum(expected.values()))
    assert [r.balance for r in d.rows] == sorted((r.balance for r in d.rows), reverse=True)
    top = d.rows[0]
    assert top.name == "Malam Sani Usman" and top.balance == 50250
    over = q.customer_debts(conn, only_over_limit=True)
    assert {r.name for r in over.rows} == {"Kabiru Haruna", "Zainab Garba"} and all(r.over_limit for r in over.rows)
    oldest = q.customer_debts(conn, sort_by="days_since_last_payment", limit=2)
    assert {r.name for r in oldest.rows} == {"Kabiru Haruna", "Zainab Garba"}  # never paid anything back


def test_phone_numbers_are_masked(conn):
    for r in q.customer_debts(conn, limit=50).rows:
        assert r.phone is not None and r.phone.startswith("*") and r.phone[-3:].isdigit() and r.phone.count("*") == 8
    assert q.mask_phone(None) is None and q.mask_phone("12") == "12"


def test_reminders(conn):
    ha = q.draft_debt_reminder(conn, 6)  # Zainab Garba prefers Hausa
    assert ha.language == "ha" and "Assalamu alaikum Zainab Garba" in ha.message and "₦28,650" in ha.message
    en = q.draft_debt_reminder(conn, 6, language="en", tone="firm")
    assert en.message.startswith("Hello Zainab,") and "end of this week" in en.message
    assert q.draft_debt_reminder(conn, 3).message.startswith("Hello Mrs Okafor,")
    for r in (ha, en):
        assert "not sent" in r.status and "08065550106" not in r.message and r.phone == "********106"
    with pytest.raises(q.QueryError, match="no customer"):
        q.draft_debt_reminder(conn, 999)


def test_compare_periods(conn):
    same = q.compare_periods(conn, "last_month", "last_month")
    assert all(c.change == 0 for c in same.changes)
    week = q.compare_periods(conn, "last_week", "this_week")  # 7 days vs 6 days -> per-day figures
    assert week.changes[0].metric == "revenue_per_day" and "per day" in week.note
    a, b = q.sales_summary(conn, "last_week"), q.sales_summary(conn, "this_week")
    assert week.changes[0].a == round(a.revenue / 7, 2) and week.changes[0].b == round(b.revenue / 6, 2)


def test_find_products(conn):
    assert [p.sku for p in q.find_products(conn, "RIC")] == ["RIC-50", "RIC-05"]
    assert q.find_products(conn, "%") == q.find_products(conn, "%")  # wildcards are stripped, not interpreted
    with pytest.raises(q.QueryError):
        q.find_products(conn, "")


def test_database_is_read_only(conn):
    for sql in ("DELETE FROM sales", "UPDATE products SET unit_price = 1", "DROP TABLE customers", "INSERT INTO payments VALUES (999, 1, 'x', 1)"):
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql)


def test_seed_is_deterministic(tmp_path, db_path):
    other = build(tmp_path / "again.db")

    def digest(p):
        c = sqlite3.connect(p)
        h = hashlib.sha256()
        for table in ("products", "customers", "sales", "sale_items", "payments"):
            for row in c.execute(f"SELECT * FROM {table} ORDER BY id"):  # noqa: S608
                h.update(repr(row).encode())
        return h.hexdigest()

    assert digest(other) == digest(db_path)
