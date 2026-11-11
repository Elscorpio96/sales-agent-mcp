"""The facts the live agent evals expect (evals/evalsentry.yaml) must stay true of the demo data."""

from sales_agent import queries as q


def test_golden_facts(conn):
    assert {r.name for r in q.low_stock(conn).rows if r.status == "out_of_stock"} == {"Bread (family loaf)", "Sachet water (bag of 20)"}
    loss = q.margin_alerts(conn).rows
    assert [a.name for a in loss] == ["Groundnut oil 5L"] and loss[0].current_cost == 14500
    top = q.customer_debts(conn).rows[0]
    assert (top.name, top.balance) == ("Malam Sani Usman", 50250)
    assert q.draft_debt_reminder(conn, 6).balance == 28650
    assert q.top_products(conn, "this_month", by="quantity", limit=1).rows[0].name == "Instant noodles 70g"
    days = q.sales_summary(conn, "last_90_days", group_by="weekday").rows
    assert max(days, key=lambda r: r.revenue).group == "Fri"
    assert q.shop_info(conn).data_start.isoformat() == "2026-07-04"
