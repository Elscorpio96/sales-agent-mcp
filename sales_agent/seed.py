"""Deterministic demo data: 120 days of trading at a fictional neighbourhood store in Bauchi, Nigeria.

The numbers are generated, not real. They are shaped to look like a real shop (busier Fridays and month ends,
a few customers who buy on credit, some lines running low, one product whose supplier cost went up past its
shelf price) so that the tools and the agent have something meaningful to find.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import db

SHOP_NAME = "Mai Kanti Mini Mart"
SHOP_CITY = "Bauchi"
END_DATE = date(2026, 10, 31)
DAYS = 120
SEED = 2026

# sku, name, category, price (naira), cost (naira), daily demand (units), start stock, reorder level
PRODUCTS: list[tuple[str, str, str, int, int, float, int, int]] = [
    ("RIC-50", "Rice 50kg bag", "Grains", 82000, 76500, 0.6, 14, 5),
    ("RIC-05", "Rice 5kg bag", "Grains", 9200, 8300, 3.0, 70, 20),
    ("BEA-MD", "Beans (1 mudu)", "Grains", 2600, 2150, 4.0, 90, 30),
    ("GAR-2K", "Garri 2kg", "Grains", 2900, 2400, 2.5, 60, 20),
    ("SEM-1K", "Semolina 1kg", "Flour", 1750, 1480, 3.5, 80, 25),
    ("YAM-1K", "Yam flour 1kg", "Flour", 2300, 1900, 1.5, 35, 12),
    ("SPA-500", "Spaghetti 500g", "Pasta & noodles", 1300, 1080, 9.0, 160, 60),
    ("NOO-70", "Instant noodles 70g", "Pasta & noodles", 350, 285, 38.0, 600, 200),
    ("NOO-CT", "Instant noodles carton (40)", "Pasta & noodles", 13200, 11300, 0.8, 22, 8),
    ("OIL-5L", "Groundnut oil 5L", "Oil", 14000, 12600, 1.8, 45, 15),
    ("OIL-1L", "Groundnut oil 1L", "Oil", 3100, 2650, 4.0, 85, 30),
    ("PAL-1L", "Palm oil 1L", "Oil", 2500, 2050, 3.0, 70, 25),
    ("SUG-1K", "Sugar 1kg", "Baking & sweeteners", 1800, 1550, 6.0, 120, 40),
    ("SAL-500", "Salt 500g", "Spices & seasoning", 400, 310, 5.0, 110, 40),
    ("CUB-50", "Seasoning cubes (pack of 50)", "Spices & seasoning", 1200, 960, 7.0, 140, 50),
    ("TOM-210", "Tomato paste 210g", "Spices & seasoning", 700, 560, 11.0, 220, 80),
    ("CUR-SM", "Curry powder (small)", "Spices & seasoning", 300, 220, 3.0, 80, 25),
    ("MLK-400", "Powdered milk 400g", "Beverages", 3800, 3300, 2.5, 55, 20),
    ("COC-500", "Cocoa beverage 500g", "Beverages", 3500, 3020, 1.6, 40, 15),
    ("TEA-50", "Tea bags (50)", "Beverages", 1200, 980, 2.0, 50, 15),
    ("SDR-50", "Soft drink 50cl", "Drinks", 500, 400, 22.0, 300, 120),
    ("MAL-33", "Malt drink 33cl", "Drinks", 700, 575, 9.0, 160, 60),
    ("WAT-75", "Bottled water 75cl", "Drinks", 250, 185, 18.0, 260, 100),
    ("WAT-BAG", "Sachet water (bag of 20)", "Drinks", 400, 300, 14.0, 160, 60),
    ("BIS-SM", "Biscuits (small pack)", "Snacks", 200, 150, 20.0, 260, 100),
    ("BRD-LF", "Bread (family loaf)", "Bakery", 1300, 1100, 10.0, 25, 15),
    ("EGG-CR", "Eggs (crate of 30)", "Dairy & eggs", 5600, 5000, 1.5, 30, 10),
    ("SAR-125", "Sardines 125g", "Canned food", 1100, 920, 3.0, 70, 25),
    ("COR-340", "Corned beef 340g", "Canned food", 2900, 2500, 1.0, 26, 10),
    ("DET-500", "Detergent 500g", "Household", 1400, 1150, 4.0, 85, 30),
    ("SOA-BAR", "Bar soap", "Household", 600, 470, 6.0, 120, 40),
    ("TIS-RL", "Toilet tissue (roll)", "Household", 400, 300, 6.0, 120, 40),
    ("MAT-BX", "Matches (box)", "Household", 100, 70, 4.0, 150, 50),
    ("TOO-140", "Toothpaste 140g", "Personal care", 1100, 900, 2.5, 55, 20),
    ("PAD-PK", "Sanitary pads (pack)", "Personal care", 1300, 1060, 2.0, 45, 15),
    ("DIA-MD", "Baby diapers (medium, 40)", "Baby", 7800, 6900, 0.7, 16, 6),
]

CUSTOMERS: list[tuple[str, str, str, int]] = [  # name, phone (fictional), language, credit limit (naira)
    ("Malam Sani Usman", "08035550101", "ha", 60000),
    ("Hajiya Hauwa Bello", "08065550102", "ha", 80000),
    ("Mrs Ngozi Okafor", "08025550103", "en", 50000),
    ("Abubakar Lawal", "07035550104", "ha", 30000),
    ("Mr Tunde Adeyemi", "08135550105", "en", 40000),
    ("Zainab Garba", "09035550106", "ha", 25000),
    ("Ibrahim Danladi", "08055550107", "ha", 40000),
    ("Mrs Blessing Audu", "08145550108", "en", 30000),
    ("Yusuf Shehu", "07065550109", "ha", 20000),
    ("Fatima Abdullahi", "08095550110", "ha", 35000),
    ("Emeka Nwosu", "08165550111", "en", 25000),
    ("Kabiru Haruna", "08075550112", "ha", 30000),
]
CASHIERS = ["Aisha", "Musa", "Grace"]
# From this day the supplier charges more for 5L groundnut oil than its shelf price.
OIL_COST_RISE_DAY = 99
SUPPLY_GAPS = {1, 13, 24, 26, 33, 36}  # products the supplier failed to deliver in the last three weeks


def _naira(x: int) -> int:
    return x * 100


def build(path: Path) -> Path:
    rng = random.Random(SEED)  # noqa: S311 - demo data, not security
    conn = db.create(path)
    start = END_DATE - timedelta(days=DAYS - 1)
    with conn:
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("shop_name", SHOP_NAME), ("city", SHOP_CITY), ("currency", "NGN"), ("data_start", start.isoformat()),
            ("data_end", END_DATE.isoformat()), ("note", "Generated demonstration data — not a real business."),
        ])
        conn.executemany(
            "INSERT INTO products (id, sku, name, category, unit_price, unit_cost, stock, reorder_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(i + 1, s, n, c, _naira(p), _naira(k), st, rl) for i, (s, n, c, p, k, _d, st, rl) in enumerate(PRODUCTS)],
        )
        conn.executemany("INSERT INTO customers (id, name, phone, language, credit_limit) VALUES (?, ?, ?, ?, ?)",
                         [(i + 1, n, ph, lang, _naira(lim)) for i, (n, ph, lang, lim) in enumerate(CUSTOMERS)])
        stock = {i + 1: p[6] for i, p in enumerate(PRODUCTS)}
        restock_qty = {i + 1: max(p[6], int(p[5] * 21)) for i, p in enumerate(PRODUCTS)}
        # Five customers keep paying; the rest fall behind to different degrees.
        repay_rate = {1: 0.95, 2: 0.9, 3: 0.85, 4: 0.3, 5: 0.9, 6: 0.0, 7: 0.6, 8: 0.95, 9: 0.2, 10: 0.8, 11: 0.5, 12: 0.0}
        balance = dict.fromkeys(repay_rate, 0)
        sale_id = item_id = pay_id = 0

        for day in range(DAYS):
            d = start + timedelta(days=day)
            weekday_factor = {4: 1.35, 5: 1.25, 6: 0.7}.get(d.weekday(), 1.0)  # Friday, Saturday busy; Sunday quiet
            month_end = 1.3 if d.day >= 26 or d.day <= 2 else 1.0
            trend = 1.0 + 0.12 * day / DAYS
            n_sales = max(8, int(rng.gauss(34, 5) * weekday_factor * month_end * trend))
            # Restock overnight (weekly for most, the supplier skips some weeks for the slow movers).
            if d.weekday() == 0:
                for pid, p in enumerate(PRODUCTS, start=1):
                    if stock[pid] < p[7] * 2 and not (pid in SUPPLY_GAPS and day > DAYS - 21):
                        stock[pid] += restock_qty[pid]
            for _ in range(n_sales):
                sale_id += 1
                hh = rng.choices(range(7, 22), weights=[2, 5, 6, 5, 4, 5, 6, 5, 5, 6, 8, 9, 7, 4, 2])[0]
                ts = datetime.combine(d, time(hh, rng.randrange(60), rng.randrange(60)))
                cashier = rng.choices(CASHIERS, weights=[5, 4, 3])[0] if hh < 16 else rng.choice(["Musa", "Grace"])
                cust = rng.randrange(1, len(CUSTOMERS) + 1) if rng.random() < 0.18 else None
                method = rng.choices(["cash", "transfer", "pos"], weights=[50, 35, 15])[0]
                if cust and rng.random() < 0.55:
                    method = "credit"
                items: dict[int, int] = {}
                for _k in range(rng.choices([1, 2, 3, 4, 5], weights=[30, 30, 20, 12, 8])[0]):
                    pid = rng.choices(range(1, len(PRODUCTS) + 1), weights=[p[5] for p in PRODUCTS])[0]
                    q = 1 if PRODUCTS[pid - 1][3] > 5000 else rng.choices([1, 2, 3, 4, 6], weights=[50, 25, 12, 8, 5])[0]
                    if stock[pid] - items.get(pid, 0) >= q:
                        items[pid] = items.get(pid, 0) + q
                if not items:
                    sale_id -= 1
                    continue
                total = 0
                rows = []
                for pid, q in items.items():
                    _s, _n, _c, price, cost, *_ = PRODUCTS[pid - 1]
                    if pid == 10 and day >= OIL_COST_RISE_DAY:
                        cost = 14500
                    charged = price
                    if price > 10000 and rng.random() < 0.15:
                        charged = price - rng.choice([200, 300, 500])  # haggled
                    item_id += 1
                    rows.append((item_id, sale_id, pid, q, _naira(charged), _naira(cost)))
                    total += _naira(charged) * q
                    stock[pid] -= q
                discount = _naira(100) if total > _naira(20000) and rng.random() < 0.2 else 0
                if method == "credit" and cust and balance[cust] + total - discount > _naira(CUSTOMERS[cust - 1][3]) * 1.15:
                    method = "transfer"  # over the credit limit: the shop asks for payment
                conn.execute("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?)", (sale_id, ts.isoformat(), cashier, method, cust, discount))
                conn.executemany("INSERT INTO sale_items VALUES (?, ?, ?, ?, ?, ?)", rows)
                if method == "credit" and cust:
                    balance[cust] += total - discount
            # Repayments, usually a few days after buying
            for cid, rate in repay_rate.items():
                if balance[cid] > 0 and rng.random() < 0.12 and rate > 0:
                    amount = int(balance[cid] * rate) // 5000 * 5000
                    if amount > 0:
                        pay_id += 1
                        conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?)",
                                     (pay_id, cid, datetime.combine(d, time(18, 30)).isoformat(), amount))
                        balance[cid] -= amount
        for pid, s in stock.items():
            conn.execute("UPDATE products SET stock = ? WHERE id = ?", (s, pid))
        # The supplier's new price for 5L oil is now the recorded cost.
        conn.execute("UPDATE products SET unit_cost = ? WHERE sku = 'OIL-5L'", (_naira(14500),))
    conn.execute("VACUUM")
    conn.close()
    return path


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "data/shop.db")))
