from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sales_agent import cli, db
from sales_agent import queries as q
from sales_agent.importer import CSVImportError, import_csv

TEMPLATES = Path(__file__).resolve().parent.parent / "skill" / "shop-sales-analyst" / "templates"


@pytest.fixture
def files(tmp_path: Path) -> Path:
    d = tmp_path / "csv"
    shutil.copytree(TEMPLATES, d)
    return d


def run(files: Path, out: Path, **kw):
    return import_csv(out, files / "products.csv", files / "sales.csv", files / "customers.csv", files / "payments.csv", shop_name="Test Shop", **kw)


def test_templates_import_and_answer_correctly(files, tmp_path):
    s = run(files, tmp_path / "shop.db")
    assert (s.products, s.customers, s.sales, s.line_items, s.payments) == (3, 2, 4, 5, 1)
    conn = db.connect_readonly(tmp_path / "shop.db")
    info = q.shop_info(conn)
    assert info.shop_name == "Test Shop" and info.today.isoformat() == "2026-10-31"
    total = q.sales_summary(conn, "all")
    assert total.revenue == 9200 + 2 * 1800 + 3 * 3000 + 2 * 9200 + 1800  # blank unit_price falls back to the product price
    assert total.gross_profit == 42000 - (8300 + 2 * 1550 + 3 * 2650 + 2 * 8300 + 1550)
    assert total.credit_sales == 18400 and total.transactions == 4
    debt = q.customer_debts(conn).rows
    assert [(d.name, d.balance) for d in debt] == [("Malam Sani Usman", 13400)]
    assert [r.sku for r in q.low_stock(conn).rows] == ["OIL-1L"]  # 9 left after selling 3, reorder level 30


def test_errors_are_reported_with_line_numbers_and_nothing_is_written(files, tmp_path):
    (files / "sales.csv").write_text(
        "sale_id,sold_at,cashier,payment_method,sku,quantity\n"
        "1,2026-10-30 09:00,Aisha,cheque,RIC-05,1\n"
        "2,yesterday,Musa,cash,NOPE,1\n"
        "3,2026-10-30 10:00,Musa,credit,RIC-05,0\n", encoding="utf-8")
    out = tmp_path / "shop.db"
    with pytest.raises(CSVImportError) as e:
        run(files, out)
    msg = str(e.value)
    assert "sales.csv line 2: payment_method 'cheque' not recognised" in msg
    assert "sales.csv line 3: sku 'NOPE' is not in products.csv" in msg
    assert "sales.csv line 4: credit sales need a customer_id" in msg and "quantity must be at least 1" in msg
    assert not out.exists() and not out.with_suffix(".importing").exists()


def test_missing_columns_and_duplicates(files, tmp_path):
    (files / "products.csv").write_text("sku,name,price\nA,Thing,100\n", encoding="utf-8")
    with pytest.raises(CSVImportError, match="missing column\\(s\\): category, cost, stock"):
        run(files, tmp_path / "a.db")
    (files / "products.csv").write_text("sku,name,category,price,cost,stock\nA,X,C,1,1,1\nA,Y,C,1,1,1\n", encoding="utf-8")
    with pytest.raises(CSVImportError, match="duplicate sku 'A'"):
        run(files, tmp_path / "b.db")


def test_formats_aliases_and_overwrite(files, tmp_path):
    (files / "products.csv").write_text("SKU,Name,Category,Price,Cost,Stock\nA,Thing,C,\"₦1,500\",1000,10\n", encoding="utf-8-sig")
    (files / "sales.csv").write_text("sale_id,sold_at,cashier,payment_method,sku,quantity\n9,30/10/2026 08:05,Ali,Card,A,2\n", encoding="utf-8")
    out = tmp_path / "shop.db"
    s = import_csv(out, files / "products.csv", files / "sales.csv")
    conn = db.connect_readonly(out)
    assert s.sales == 1 and q.sales_summary(conn, "all", group_by="payment_method").rows[0].group == "pos"
    assert q.sales_summary(conn, "all").revenue == 3000
    conn.close()
    with pytest.raises(FileExistsError):
        import_csv(out, files / "products.csv", files / "sales.csv")
    assert import_csv(out, files / "products.csv", files / "sales.csv", overwrite=True).sales == 1


def test_cli_import(files, tmp_path, capsys):
    out = str(tmp_path / "shop.db")
    args = ["import", "--db", out, "--products", str(files / "products.csv"), "--sales", str(files / "sales.csv"),
            "--customers", str(files / "customers.csv"), "--payments", str(files / "payments.csv")]
    assert cli.main(args) == 0
    assert "Imported 4 sales" in capsys.readouterr().out
    assert cli.main(args) == 1
    assert "already exists" in capsys.readouterr().err


def test_receipt_discount_on_any_row(files, tmp_path):
    (files / "sales.csv").write_text("sale_id,sold_at,cashier,payment_method,sku,quantity,discount\n"
                                     "7,2026-10-30 09:00,Aisha,cash,RIC-05,1,\n7,2026-10-30 09:00,Aisha,cash,SUG-1K,1,500\n", encoding="utf-8")
    out = tmp_path / "shop.db"
    import_csv(out, files / "products.csv", files / "sales.csv")
    s = q.sales_summary(db.connect_readonly(out), "all")
    assert (s.discounts, s.revenue) == (500, 9200 + 1800 - 500)
