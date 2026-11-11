# Playbooks

## Weekly review

1. `compare_periods(period_a="last_week", period_b="this_week")` — note that this week may be partial (figures are per
   day when lengths differ).
2. `top_products(period="this_week", by="profit", limit=5)`
3. `low_stock()`
4. `margin_alerts()`
5. `customer_debts(sort_by="days_since_last_payment", limit=3)`

Write it as: one-line verdict (up or down, by how much) → top 5 by profit → stock to order → money leaks → oldest
debts → **three actions for next week**, each specific (item, quantity, customer, price).

## Restock list

`low_stock(target_days=N)` where N is how many days the order should last (ask if unclear; 14 is a good default for a
weekly market run). Present: out-of-stock first, then by days left. Columns: item, in stock, sells per day, days left,
order. Add the total number of lines and remind the owner that suggested orders assume recent sales continue.

## Debt follow-up

1. `customer_debts(only_over_limit=true)`; if none, `customer_debts(sort_by="days_since_last_payment", limit=5)`.
2. For each customer the owner picks, `draft_debt_reminder(customer_id, tone="friendly")` in the customer's language.
3. Show each draft in a quote block with the masked phone and the balance, and end with: "These are drafts — send
   them yourself on WhatsApp or SMS." Offer a `firm` version only if the owner asks.

## Margin check

1. `margin_alerts(min_margin_pct=10)`
2. `top_products(period="last_30_days", by="profit", ascending=true, limit=5)`
3. For items at or below cost, suggest a shelf price: current cost × (1 + the shop's typical margin). Get the typical
   margin from `sales_summary(period="last_30_days").margin_pct`. Present it as a suggestion and show the arithmetic.

## "How is today going?"

`sales_summary(period="today")` and `compare_periods(period_a="last_7_days", period_b="today")` (per day). If `today`
in `shop_info` is not the real date, say which day the data is for.
