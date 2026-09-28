"""Shared fixtures."""
import pytest

from finres import db


@pytest.fixture
def tmp_db(tmp_path):
    """A fresh SQLite DB with the FinRes schema."""
    conn = db.connect(tmp_path / "test.db")
    yield conn
    conn.close()
