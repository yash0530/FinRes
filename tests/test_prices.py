"""Price cache and data-quality flag (offline)."""
from datetime import date

import pandas as pd

from finres import prices


def _frame(days, values, ticker="AAA"):
    return pd.DataFrame({ticker: values}, index=pd.DatetimeIndex(pd.to_datetime(days)))


def test_store_replaces_history(tmp_db):
    prices.store(tmp_db, _frame(["2024-01-02", "2024-01-03", "2024-01-04"], [10.0, 11.0, 12.0]))
    prices.store(tmp_db, _frame(["2024-01-03", "2024-01-04"], [5.5, 6.0]))  # re-adjusted, shorter
    out = prices.load_closes(tmp_db, ["AAA"])
    assert list(out.index.strftime("%Y-%m-%d")) == ["2024-01-03", "2024-01-04"]
    assert out["AAA"].tolist() == [5.5, 6.0]


def test_store_leaves_other_tickers_and_skips_nan(tmp_db):
    prices.store(tmp_db, _frame(["2024-01-02"], [1.0], "BBB"))
    prices.store(tmp_db, _frame(["2024-01-02", "2024-01-03"], [2.0, float("nan")]))
    out = prices.load_closes(tmp_db)
    assert set(out.columns) == {"AAA", "BBB"}
    assert tmp_db.execute("SELECT COUNT(*) FROM prices WHERE ticker='AAA'").fetchone()[0] == 1


def test_load_closes_asof_excludes_later_rows(tmp_db):
    prices.store(tmp_db, _frame(["2024-01-02", "2024-01-03", "2024-01-04"], [1.0, 2.0, 3.0]))
    out = prices.load_closes(tmp_db, asof=date(2024, 1, 3))
    assert isinstance(out.index, pd.DatetimeIndex) and out.index.is_monotonic_increasing
    assert out.index.max() == pd.Timestamp("2024-01-03")
    assert len(out) == 2


def test_suspicious_moves_flags_unexplained_jump_only():
    days = pd.bdate_range("2024-01-01", periods=6)
    s = pd.Series([10, 10, 16, 16, 16, 16], index=days, dtype=float)  # +60% on day 3
    assert prices.suspicious_moves(s) == [days[2].strftime("%Y-%m-%d")]
    splits = pd.Series([2.0], index=[days[3]])  # split within 3 days explains it
    assert prices.suspicious_moves(s, splits) == []
    assert s.tolist() == [10, 10, 16, 16, 16, 16]  # never modified
