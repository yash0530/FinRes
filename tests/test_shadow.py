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
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 10)) == 6
    rows = _rows(shadow_db)
    assert set(rows) == {(s, m) for s in state.SHADOWS for m in ("2026-09", "2026-10")}
    assert [rows[("smh", m)]["fill_date"] for m in ("2026-09", "2026-10")] == ["2026-10-01", "2026-11-02"]
    smh = rows[("smh", "2026-10")]
    exp = 2500 / df.at[pd.Timestamp("2026-10-01"), "SMH"] + 2500 / df.at[pd.Timestamp("2026-11-02"), "SMH"]
    assert smh["port"]["shares"]["SMH"] == pytest.approx(exp) and smh["contributions"] == 2
    assert smh["port"]["cash"] == pytest.approx(0.0, abs=1e-9)
    ew = rows[("ew", "2026-09")]
    assert len(ew["port"]["shares"]) > 10 and ew["value_after"] == pytest.approx(
        sum(n * df.at[pd.Timestamp("2026-10-01"), k] for k, n in ew["port"]["shares"].items()) + ew["port"]["cash"])
    assert ew["value_after"] == pytest.approx(2500 * (1 - state.SHADOW_COST))  # valued at the fill closes
    rule = rows[("rule", "2026-09")]
    buys = [t for t in rule["trades"] if t["side"] == "buy"]
    assert 0 < len(buys) <= model.ROTATE_N and all(t["dollars"] == pytest.approx(2500 / len(buys)) for t in buys)
    assert rows[("rule", "2026-10")]["contributions"] == 2
    # second call: nothing new, nothing changed
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 10)) == 0
    assert _rows(shadow_db) == rows
    track = state._shadow_rows(shadow_db)
    assert [(r["months"], r["invested"]) for r in track] == [(2, 5000.0)] * 3
    assert all(r["value"] > 0 for r in track)


def test_month_without_fill_day_close_is_deferred(shadow_db):
    _prices(shadow_db, "2026-10-30")  # October's month-end is the last row: no fill day yet
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 2)) == 3
    assert {m for _, m in _rows(shadow_db)} == {"2026-09"}
    _prices(shadow_db, "2026-11-02")  # fill day arrives, but it is today (close may be partial): still deferred
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 2)) == 0
    assert state.step_shadows(shadow_db, ref=date(2026, 11, 3)) == 3
    assert {m for _, m in _rows(shadow_db)} == {"2026-09", "2026-10"}


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
