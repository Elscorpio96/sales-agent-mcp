"""SQLite schema and connections. Money is stored in kobo (1/100 naira) as integers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
    unit_cost INTEGER NOT NULL CHECK (unit_cost >= 0),
    stock INTEGER NOT NULL,
    reorder_level INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en', 'ha')),
    credit_limit INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    sold_at TEXT NOT NULL,                 -- ISO 8601 local time, e.g. 2025-06-30T14:05:00
    cashier TEXT NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('cash', 'transfer', 'pos', 'credit')),
    customer_id INTEGER REFERENCES customers(id),
    discount INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price INTEGER NOT NULL,           -- price charged (can differ from the list price)
    unit_cost INTEGER NOT NULL             -- cost at the time of sale, for profit
);
CREATE TABLE payments (                    -- repayments of credit sales
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    paid_at TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0)
);
CREATE INDEX idx_sales_sold_at ON sales(sold_at);
CREATE INDEX idx_items_sale ON sale_items(sale_id);
CREATE INDEX idx_items_product ON sale_items(product_id);
CREATE INDEX idx_sales_customer ON sales(customer_id);
"""


def create(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    """Open the database read-only at the SQLite level: no tool can modify shop data, whatever it is asked."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"database not found: {p} (create the demo data with `sales-agent seed`)")
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn
