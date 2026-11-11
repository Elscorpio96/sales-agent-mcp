"""Load a shop's own data from CSV exports into the server's SQLite schema.

Most POS apps and spreadsheets can export CSV. Four files, one header row each (amounts in main currency units, e.g. naira):

  products.csv   sku, name, category, price, cost, stock[, reorder_level]
  sales.csv      sale_id, sold_at, cashier, payment_method, sku, quantity[, unit_price, unit_cost, customer_id, discount]
                 one row per line item; rows with the same sale_id form one sale
  customers.csv  id, name[, phone, language, credit_limit]            (optional)
  payments.csv   customer_id, paid_at, amount                          (optional; repayments of credit sales)

Every problem is reported with its file and line number, and nothing is written unless the whole import is valid.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db

PAYMENT_ALIASES = {"cash": "cash", "transfer": "transfer", "bank transfer": "transfer", "bank": "transfer", "pos": "pos", "card": "pos",
                   "credit": "credit", "debt": "credit", "on credit": "credit", "bashi": "credit"}
MAX_ERRORS = 25


class CSVImportError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        more = f"\n…and more ({len(errors)} shown, stopped at {MAX_ERRORS})" if len(errors) >= MAX_ERRORS else ""
        super().__init__("import failed:\n" + "\n".join(f"  - {e}" for e in errors) + more)


@dataclass
class ImportSummary:
    db_path: Path
    products: int
    customers: int
    sales: int
    line_items: int
    payments: int
    first_sale: str
    last_sale: str
    warnings: list[str] = field(default_factory=list)


class _Reader:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, where: str, msg: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(f"{where}: {msg}")

    def rows(self, path: Path, required: set[str]) -> list[tuple[str, dict[str, str]]]:
        if not path.exists():
            self.fail(path.name, "file not found")
            return []
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = {(c or "").strip().lower() for c in reader.fieldnames or []}
            missing = required - cols
            if missing:
                self.fail(path.name, f"missing column(s): {', '.join(sorted(missing))} (found: {', '.join(sorted(cols)) or 'none'})")
                return []
            out = []
            for i, row in enumerate(reader, start=2):
                clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
                if any(clean.values()):
                    out.append((f"{path.name} line {i}", clean))
            return out

    def money(self, where: str, value: str, name: str, default: int | None = None) -> int:
        if value == "" and default is not None:
            return default
        try:
            amount = float(value.replace(",", "").replace("₦", "").replace("NGN", "").strip())
        except ValueError:
            self.fail(where, f"{name} {value!r} is not a number")
            return 0
        if amount < 0:
            self.fail(where, f"{name} cannot be negative")
        return round(amount * 100)

    def integer(self, where: str, value: str, name: str, default: int | None = None, minimum: int | None = None) -> int:
        if value == "" and default is not None:
            return default
        try:
            n = int(float(value.replace(",", "")))
        except ValueError:
            self.fail(where, f"{name} {value!r} is not a whole number")
            return 0
        if minimum is not None and n < minimum:
            self.fail(where, f"{name} must be at least {minimum}")
        return n

    def timestamp(self, where: str, value: str, name: str) -> str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).isoformat()
            except ValueError:
                continue
        self.fail(where, f"{name} {value!r} is not a date (use YYYY-MM-DD HH:MM or DD/MM/YYYY HH:MM)")
        return ""


def _id(value: str) -> str:
    """Normalise ids so that "7", "07" and "7.0" from a spreadsheet all match."""
    try:
        return str(int(float(value)))
    except ValueError:
        return value


def import_csv(db_path: Path, products: Path, sales: Path, customers: Path | None = None, payments: Path | None = None, *,
               shop_name: str = "My shop", city: str = "", currency: str = "NGN", overwrite: bool = False) -> ImportSummary:
    if db_path.exists() and not overwrite:
        raise FileExistsError(f"{db_path} already exists (use --force to replace it)")
    r = _Reader()
    warnings: list[str] = []

    prod: dict[str, tuple[Any, ...]] = {}
    for where, row in r.rows(products, {"sku", "name", "category", "price", "cost", "stock"}):
        sku = row["sku"]
        if not sku or not row["name"]:
            r.fail(where, "sku and name are required")
            continue
        if sku in prod:
            r.fail(where, f"duplicate sku {sku!r}")
            continue
        price, cost = r.money(where, row["price"], "price"), r.money(where, row["cost"], "cost")
        prod[sku] = (len(prod) + 1, sku, row["name"], row["category"] or "Uncategorised", price, cost,
                     r.integer(where, row["stock"], "stock"), r.integer(where, row.get("reorder_level", ""), "reorder_level", 0, 0))

    cust: dict[str, tuple[Any, ...]] = {}
    if customers:
        for where, row in r.rows(customers, {"id", "name"}):
            cid = r.integer(where, row["id"], "id", minimum=1)
            lang = (row.get("language") or "en").lower()[:2]
            if lang not in {"en", "ha"}:
                warnings.append(f"{where}: language {row.get('language')!r} not supported, using English")
                lang = "en"
            if str(cid) in cust:
                r.fail(where, f"duplicate customer id {cid}")
            cust[str(cid)] = (cid, row["name"], row.get("phone") or None, lang, r.money(where, row.get("credit_limit", ""), "credit_limit", 0))

    sale_rows: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, int, int, int, int]] = []
    for where, row in r.rows(sales, {"sale_id", "sold_at", "cashier", "payment_method", "sku", "quantity"}):
        sid = row["sale_id"]
        method = PAYMENT_ALIASES.get(row["payment_method"].lower())
        if method is None:
            r.fail(where, f"payment_method {row['payment_method']!r} not recognised (use cash, transfer, pos or credit)")
        p = prod.get(row["sku"])
        if p is None:
            r.fail(where, f"sku {row['sku']!r} is not in {products.name}")
            continue
        buyer = _id(row.get("customer_id", ""))
        if buyer and buyer not in cust:
            r.fail(where, f"customer_id {buyer} is not in the customers file")
        if method == "credit" and not buyer:
            r.fail(where, "credit sales need a customer_id")
        sold_at = r.timestamp(where, row["sold_at"], "sold_at")
        header = {"sold_at": sold_at, "cashier": row["cashier"] or "unknown", "method": method, "customer": int(buyer) if buyer else None,
                  "discount": r.money(where, row.get("discount", ""), "discount", 0)}
        if sid in sale_rows:
            first = sale_rows[sid]
            if (first["sold_at"], first["method"], first["customer"]) != (header["sold_at"], header["method"], header["customer"]):
                r.fail(where, f"sale {sid} has different date, payment method or customer on different rows")
            first["discount"] = max(first["discount"], header["discount"])  # the receipt discount may be on any one row
        else:
            sale_rows[sid] = header
        qty = r.integer(where, row["quantity"], "quantity", minimum=1)
        items.append((sid, p[0], qty, r.money(where, row.get("unit_price", ""), "unit_price", p[4]),
                      r.money(where, row.get("unit_cost", ""), "unit_cost", p[5])))

    pays: list[tuple[int, str, int]] = []
    if payments:
        for where, row in r.rows(payments, {"customer_id", "paid_at", "amount"}):
            payer = _id(row["customer_id"])
            if payer not in cust:
                r.fail(where, f"customer_id {row['customer_id']} is not in the customers file")
                continue
            pays.append((int(payer), r.timestamp(where, row["paid_at"], "paid_at"), r.money(where, row["amount"], "amount")))

    if not sale_rows and not r.errors:
        r.fail(sales.name, "no sales rows")
    if r.errors:
        raise CSVImportError(r.errors)

    dates = sorted(h["sold_at"] for h in sale_rows.values())
    tmp = db_path.with_suffix(".importing")
    conn = db.create(tmp)
    with conn:
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("shop_name", shop_name), ("city", city), ("currency", currency), ("data_start", dates[0][:10]), ("data_end", dates[-1][:10]),
            ("note", f"Imported from {products.name} and {sales.name}.")])
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(prod.values()))
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", list(cust.values()))
        ids = {sid: n for n, sid in enumerate(sorted(sale_rows, key=lambda s: (sale_rows[s]["sold_at"], s)), start=1)}
        conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?)",
                         [(ids[s], h["sold_at"], h["cashier"], h["method"], h["customer"], h["discount"]) for s, h in sale_rows.items()])
        conn.executemany("INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, unit_cost) VALUES (?, ?, ?, ?, ?)",
                         [(ids[s], pid, q, up, uc) for s, pid, q, up, uc in items])
        conn.executemany("INSERT INTO payments (customer_id, paid_at, amount) VALUES (?, ?, ?)", pays)
    conn.close()
    tmp.replace(db_path)
    return ImportSummary(db_path, len(prod), len(cust), len(sale_rows), len(items), len(pays), dates[0], dates[-1], warnings)
