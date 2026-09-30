"""Forward shadow ledgers (ADR-008) and the weekly "what changed" block, on synthetic DBs (offline)."""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from finres import config, edgar, model, prices, state

UNI_T = [f"U{i:02d}" for i in range(45)]
UNI = {"categories": {"a": {"name": "Alpha", "group": "g1", "tickers": UNI_T[:25]},
                      "b": {"name": "Beta", "group": "g2", "tickers": UNI_T[25:]}},
       "ticker_category": {t: "a" if i < 25 else "b" for i, t in enumerate(UNI_T)},
       "ticker_group": {t: "g1" if i < 25 else "g2" for i, t in enumerate(UNI_T)},
       "tickers": UNI_T, "laggards": []}


@pytest.fixture
def shadow_db(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "load_universe", lambda *a, **k: UNI)
    monkeypatch.setattr(edgar, "companyfacts", lambda *a, **k: None)
    monkeypatch.setattr(edgar, "recent_8k", lambda *a, **k: [])
    state.clear_cache()
    return tmp_db


def _prices(conn, end: str):
    idx = pd.bdate_range("2025-01-01", end)
    rng = np.random.default_rng(5)
    names = UNI_T + config.BENCHMARKS
    drift = {t: (0.002 if i % 4 else -0.001) for i, t in enumerate(names)}
    df = pd.DataFrame({t: 40 * np.exp(np.cumsum(drift[t] + 0.015 * rng.standard_normal(len(idx)))) for t in names},
                      index=idx)
    prices.store(conn, df)
    return df


def _rows(conn):
    return {(r[0], r[1]): json.loads(r[2]) for r in conn.execute("SELECT * FROM shadow ORDER BY strategy, month")}


def test_two_month_ends_stored_per_strategy_and_idempotent(shadow_db):
    df = _prices(shadow_db, "2026-11-05")
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 10)) == 2 * len(state.SHADOWS)
    rows = _rows(shadow_db)
    assert set(rows) == {(s, m) for s in state.SHADOWS for m in ("2026-09", "2026-10")}
    assert [rows[("smh", m)]["fill_date"] for m in ("2026-09", "2026-10")] == ["2026-10-01", "2026-11-02"]
    assert all(set(r) == {"sells", "buys", "fill_date", "contrib"} for r in rows.values())  # M10: decisions only
    smh, _ = state.replay(shadow_db, "smh")
    exp = 2500 / df.at[pd.Timestamp("2026-10-01"), "SMH"] + 2500 / df.at[pd.Timestamp("2026-11-02"), "SMH"]
    assert smh["shares"]["SMH"] == pytest.approx(exp) and smh["cash"] == pytest.approx(0.0, abs=1e-9)
    assert rows[("smh", "2026-10")] == {"sells": [], "buys": ["SMH"], "fill_date": "2026-11-02", "contrib": 2500.0}
    ew = model.step({"shares": {}, "basis": {}, "cash": 0.0}, [], rows[("ew", "2026-09")]["buys"],
                    df.loc[pd.Timestamp("2026-10-01")].to_dict(), 2500, state.SHADOW_COST)[0]
    assert len(ew["shares"]) > 10 and sum(n * df.at[pd.Timestamp("2026-10-01"), k] for k, n in ew["shares"].items()) \
        + ew["cash"] == pytest.approx(2500 * (1 - state.SHADOW_COST))  # valued at the fill closes
    buys = rows[("rule", "2026-09")]["buys"]
    assert 0 < len(buys) <= model.ROTATE_N
    # second call: nothing new, nothing changed
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 10)) == 0
    assert _rows(shadow_db) == rows
    track = state._shadow_rows(shadow_db)
    assert [(r["months"], r["invested"]) for r in track] == [(2, 5000.0)] * len(state.SHADOWS)
    assert all(r["value"] > 0 for r in track)


def test_split_after_redownload_leaves_shadow_value_unchanged(shadow_db):
    """F4: a 2:1 split right after the last close; the re-download halves every earlier close of the split names.
    Replaying the stored decisions at the new closes doubles the shares: value and XIRR unchanged (frozen share
    counts would have halved those positions)."""
    df = _prices(shadow_db, "2026-12-15")
    assert state.step_shadows(shadow_db, ref=date(2026, 12, 16)) == 3 * len(state.SHADOWS)
    before = state._shadow_rows(shadow_db)
    held = list(state.replay(shadow_db, "rule")[0]["shares"]) + ["SMH", "U00"]
    split = df.copy()
    split[held] /= 2  # yfinance re-download after the 2:1 split: all closes before the split date halve
    prices.store(shadow_db, split)
    after = state._shadow_rows(shadow_db)
    assert [(r["value"], r["xirr"]) for r in after] == pytest.approx([(r["value"], r["xirr"]) for r in before])
    assert state.step_shadows(shadow_db, ref=date(2026, 12, 16)) == 0


def test_month_without_fill_day_close_is_deferred(shadow_db):
    _prices(shadow_db, "2026-10-30")  # October's month-end is the last row: no fill day yet
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 2)) == len(state.SHADOWS)
    assert {m for _, m in _rows(shadow_db)} == {"2026-09"}
    _prices(shadow_db, "2026-11-02")  # fill day arrives, but it is today (close may be partial): still deferred
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 2)) == 0
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 3)) == len(state.SHADOWS)
    assert {m for _, m in _rows(shadow_db)} == {"2026-09", "2026-10"}


def test_hold_ledger_sells_only_on_the_stop(shadow_db):
    """ADR-011: after a trend break (2 month-ends below the 200DMA, -30%) the rule ledger sells and the hold ledger
    keeps the name; after a -50% drop (the -35% stop) both sell."""
    idx = pd.bdate_range("2025-01-01", "2026-12-04")
    df = pd.DataFrame({t: 40 * np.exp(0.002 * np.arange(len(idx))) for t in UNI_T + config.BENCHMARKS}, index=idx)
    prices.store(shadow_db, df.loc[:"2026-10-02"])
    assert state.step_shadows(shadow_db, ref=date(2026, 10, 5)) == len(state.SHADOWS)
    a, b = _rows(shadow_db)[("hold", "2026-09")]["buys"][:2]
    assert _rows(shadow_db)[("rule", "2026-09")]["buys"][:2] == [a, b]  # same buys as B0R
    df.loc["2026-10-05":, a] *= 0.70  # below the 200DMA at the Oct and Nov month-ends, above the stop
    df.loc["2026-10-05":, b] *= 0.50  # the -35% stop
    prices.store(shadow_db, df)
    state.step_shadows(shadow_db, ref=date(2026, 12, 10))
    rows = _rows(shadow_db)
    sold = lambda x: [(m, t) for (s, m), r in rows.items() if s == x for t in r["sells"]]  # noqa: E731
    assert sold("hold") == [("2026-10", b)]
    assert sorted(sold("rule")) == [("2026-10", b), ("2026-11", a)]
    assert a in state.replay(shadow_db, "hold")[0]["shares"] and a not in state.replay(shadow_db, "rule")[0]["shares"]


def test_nothing_before_inception(shadow_db):
    _prices(shadow_db, "2026-09-29")
    assert state.step_shadows(shadow_db, ref=date(2026, 9, 29)) == 0 and state._shadow_rows(shadow_db) == []


def _snap(conn, day, trends: dict):
    with conn:
        for t, v in trends.items():
            conn.execute("INSERT INTO snapshot VALUES (?,?,?,?)", (t, day, json.dumps({"trend": v}), "{}"))


def test_changes_since_last_week(shadow_db, monkeypatch):
    _snap(shadow_db, "2026-09-20", {"AAA": False, "BBB": True, "CCC": True, "DDD": False})
    assert state.changes(shadow_db) is None  # one snapshot: nothing to compare
    _snap(shadow_db, "2026-09-24", {"AAA": True, "BBB": True, "CCC": True, "DDD": False})  # < 5 days: skipped
    _snap(shadow_db, "2026-09-27", {"AAA": True, "BBB": False, "CCC": True, "DDD": True, "EEE": True})
    flags = {"CCC": [{"date": "2026-09-25", "labels": ["auditor change"], "items": ["4.01"]}],
             "BBB": [{"date": "2026-09-10", "labels": ["restatement"], "items": ["4.02"]}]}  # old: not new
    monkeypatch.setattr(edgar, "recent_8k", lambda t, *a, **k: flags.get(t, []))
    c = state.changes(shadow_db)
    assert c == {"since": "2026-09-20", "up": ["AAA", "DDD"], "down": ["BBB"], "sec": ["CCC"]}
