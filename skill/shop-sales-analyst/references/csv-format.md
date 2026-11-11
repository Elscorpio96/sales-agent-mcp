# CSV format for `sales-agent import`

One header row per file; column names are not case-sensitive; amounts are in normal currency units (e.g. `1500` or
`₦1,500` for fifteen hundred naira). Dates: `YYYY-MM-DD HH:MM`, `YYYY-MM-DD` or `DD/MM/YYYY HH:MM`.
Nothing is written unless every row is valid; errors list the file and line number.

## products.csv (required)

| Column | Required | Notes |
|---|---|---|
| sku | yes | Unique product code |
| name | yes | |
| category | yes | e.g. Grains, Drinks |
| price | yes | Current shelf price |
| cost | yes | Current cost per unit (for profit) |
| stock | yes | Units on hand now |
| reorder_level | no | Flag when stock falls below this (default 0) |

## sales.csv (required) — one row per item sold

| Column | Required | Notes |
|---|---|---|
| sale_id | yes | Receipt number; rows with the same id are one sale |
| sold_at | yes | Date and time |
| cashier | yes | |
| payment_method | yes | `cash`, `transfer`, `pos` or `credit` (also accepts card, bank transfer, debt, bashi) |
| sku | yes | Must exist in products.csv |
| quantity | yes | Whole number, at least 1 |
| unit_price | no | Price actually charged; blank = product price |
| unit_cost | no | Cost at the time; blank = product cost |
| customer_id | for credit | Must exist in customers.csv; required for credit sales |
| discount | no | Discount on the whole receipt (put it on one row of the sale) |

## customers.csv (optional)

`id, name, phone, language (en or ha), credit_limit`

## payments.csv (optional) — repayments of credit sales

`customer_id, paid_at, amount`

A customer's balance = credit sales − discounts on them − payments.
