from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sales_agent import db
from sales_agent.seed import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(tmp_path_factory.mktemp("data") / "shop.db")


@pytest.fixture
def conn(db_path: Path) -> sqlite3.Connection:
    c = db.connect_readonly(db_path)
    yield c
    c.close()


@pytest.fixture
def raw(db_path: Path) -> sqlite3.Connection:
    """A plain connection for computing expected values independently of the code under test."""
    c = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    yield c
    c.close()
