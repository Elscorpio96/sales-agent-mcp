# Tool reference

All tools are read-only. Money is in the shop's currency units (naira for NGN). Periods are counted back from
`today` (the latest trading day in the data).

| Tool | Parameters (defaults) | Returns |
|---|---|---|
| `shop_info` | — | shop_name, city, currency, today, data_start, data_end, products, customers, cashiers, categories |
| `sales_summary` | period (last_30_days) or start/end; group_by: none, day, week, month, hour, weekday, cashier, payment_method, category | revenue, cost_of_goods, gross_profit, margin_pct, transactions, items_sold, average_basket, discounts, credit_sales, rows[] |
| `top_products` | period/start/end; by: revenue, quantity, profit; limit (10, max 50); category; ascending (false) | rows[]: sku, name, quantity, revenue, gross_profit, margin_pct, share_of_revenue_pct |
| `low_stock` | max_days_of_cover (7); lookback_days (28); target_days (21) | rows[]: stock, avg_daily_sales, days_of_cover, suggested_order, status (out_of_stock, below_reorder_level, running_low) |
| `margin_alerts` | period (last_30_days); min_margin_pct (5) | rows[]: shelf_price, current_cost, margin_pct, units_sold_below_cost, loss_on_those_units, issue |
| `compare_periods` | period_a (last_week); period_b (this_week) | changes[]: metric, a, b, change, change_pct (per day when lengths differ) |
| `find_products` | query (1–60 chars); limit | sku, name, category, price, cost, stock |
| `customer_debts` | min_balance (1); only_over_limit (false); sort_by: balance or days_since_last_payment; limit (20) | total_outstanding, rows[]: balance, credit_limit, over_limit, last_payment, days_since_last_payment, masked phone |
| `draft_debt_reminder` | customer_id; language: en or ha (default: the customer's); tone: friendly or firm | message (draft, not sent), masked phone, balance |

Also available: resource `shop://schema` (the database tables) and prompt `weekly_review`.
