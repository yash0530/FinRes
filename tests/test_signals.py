"""signals.py: lookahead, hand-computed factors, eligibility, revisions, fundamentals, speed (offline)."""
import math
import time

import numpy as np
import pandas as pd
import pytest

from finres import signals


def _walk(n_rows=400, tickers=("AAA", "BBB", "CCC"), seed=7, start=50.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n_rows)
    steps = rng.normal(0.0005, 0.02, size=(n_rows, len(tickers)))
    return pd.DataFrame(start * np.exp(np.cumsum(steps, axis=0)), index=idx, columns=list(tickers))


def test_month_ends():
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    assert signals.month_ends(idx) == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29"),
                                       pd.Timestamp("2024-03-15")]
    assert signals.month_ends(pd.DatetimeIndex([])) == []


def test_no_lookahead():
    closes = _walk()
    closes.iloc[:30, 2] = np.nan  # CCC is pre-IPO for the first 30 rows
    t = closes.index[320]
    before = signals.factors_at(closes, t)
    tampered = closes.copy()
    tampered.iloc[321:] = tampered.iloc[321:] * 10
    tampered.iloc[330:340] = np.nan
    after = signals.factors_at(tampered, t)
    pd.testing.assert_frame_equal(before, after)


def test_factors_match_hand_computation_on_ramp():
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    vals = np.arange(1, n + 1, dtype=float) + 9.0  # 10, 11, ..., 309
    closes = pd.DataFrame({"R": vals, "D": vals[::-1].copy()}, index=idx)
    f = signals.factors_at(closes, idx[-1]).loc["R"]
    assert f["close"] == 309.0 and f["n_days"] == 300
    assert f["ret_12_1"] == pytest.approx(vals[-22] / vals[-253] - 1)
    assert f["ret_1m"] == pytest.approx(vals[-1] / vals[-22] - 1)
    daily = vals[-253:][1:] / vals[-253:][:-1] - 1
    assert f["vol_252"] == pytest.approx(np.std(daily, ddof=1) * math.sqrt(252))
    assert f["mom_raw"] == pytest.approx(f["ret_12_1"] / f["vol_252"])
    assert f["hi52"] == pytest.approx(1.0) and f["dd_52w"] == pytest.approx(0.0)
    assert f["sma200"] == pytest.approx(vals[-200:].mean())
    assert f["sma50"] == pytest.approx(vals[-50:].mean())
    assert bool(f["above200"]) and bool(f["trend"])
    d = signals.factors_at(closes, idx[-1]).loc["D"]
    assert d["hi52"] == pytest.approx(vals[0] / vals[251])  # falling: close vs max of last 252 closes
    assert not bool(d["above200"]) and not bool(d["trend"])


def test_short_history_gives_nan_and_false():
    idx = pd.bdate_range("2020-01-01", periods=150)
    closes = pd.DataFrame({"S": np.linspace(10, 20, 150)}, index=idx)
    f = signals.factors_at(closes, idx[-1]).loc["S"]
    assert math.isnan(f["ret_12m"]) and math.isnan(f["ret_12_1"]) and math.isnan(f["vol_252"])
    assert math.isnan(f["sma200"]) and not math.isnan(f["sma50"])
    assert not bool(f["above200"]) and not bool(f["trend"]) and not bool(f["eligible"])


def test_eligibility():
    idx = pd.bdate_range("2019-01-01", periods=400)
    closes = pd.DataFrame(10.0 + np.arange(400) * 0.01, index=idx, columns=["OK"])
    closes["IPO272"] = closes["OK"].where(np.arange(400) >= 400 - 272)
    closes["IPO273"] = closes["OK"].where(np.arange(400) >= 400 - 273)
    closes["CHEAP"] = 2.5
    closes["STALE"] = closes["OK"].where(np.arange(400) < 395)  # last close 5 rows before t
    closes["GAP"] = closes["OK"].where(np.arange(400) < 396)  # last close 4 rows before t: carried
    f = signals.factors_at(closes, idx[-1])
    assert f.loc["IPO272", "n_days"] == 272 and not f.loc["IPO272", "eligible"]
    assert f.loc["IPO273", "n_days"] == 273 and f.loc["IPO273", "eligible"]
    assert not f.loc["CHEAP", "eligible"]
    assert math.isnan(f.loc["STALE", "close"]) and not f.loc["STALE", "eligible"]
    assert f.loc["GAP", "close"] == closes["OK"].iloc[395] and f.loc["GAP", "eligible"]
    assert f.loc["OK", "eligible"]
    # pre-IPO NaNs never leak into returns
    assert not math.isnan(f.loc["IPO273", "ret_12_1"])
    assert not math.isnan(f.loc["IPO272", "ret_12m"])  # 272 rows >= 253 needed for 12m
    assert f["eligible"].dtype == bool


def test_revisions_raw():
    est = {"eps_trend": {"0y": {"current": -0.5, "d90": -1.0}, "+1y": {"current": 3.0, "d90": 1.0}},
           "eps_rev": {"0y": {"up30": 3, "down30": 1}, "+1y": {"up30": None, "down30": None}}}
    r = signals.revisions_raw(est)
    assert r["rev_chg"] == pytest.approx((0.5 + 0.5) / 2)  # (-0.5+1)/1 = +50%; +200% clipped to +50%
    assert r["rev_breadth"] == pytest.approx(0.5)
    r = signals.revisions_raw({"eps_trend": {"0y": {"current": 1.0, "d90": 0}}, "eps_rev": {"0y": {"up30": 0,
                                                                                                    "down30": 0}}})
    assert r["rev_chg"] is None and r["rev_breadth"] == 0.0
    assert signals.revisions_raw(None) == {"rev_chg": None, "rev_breadth": None}
    neg = signals.revisions_raw({"eps_trend": {"0y": {"current": -2.0, "d90": -1.0}}})
    assert neg["rev_chg"] == pytest.approx(-0.5)  # worse loss -> negative revision, clipped


def test_fund_factors():
    f = {"gp_assets": 0.4, "revenue_growth": 0.2, "net_income_ttm": 100.0, "shares": 10.0,
         "revenue_ttm": 1000.0, "pit": True}
    out = signals.fund_factors(f, 50.0)
    assert out["earnings_yield"] == pytest.approx(100 / 500) and out["speculative"] is False
    assert out["gp_assets"] == 0.4 and out["rev_growth"] == 0.2 and out["fund_missing"] is False
    assert signals.fund_factors({**f, "net_income_ttm": -1.0}, 50.0)["speculative"] is True
    assert signals.fund_factors({**f, "revenue_ttm": None}, 50.0)["speculative"] is False  # ADR-004c
    assert signals.fund_factors({**f, "net_income_ttm": None}, 50.0)["speculative"] is False
    assert signals.fund_factors({**f, "shares": None}, 50.0)["earnings_yield"] is None
    assert signals.fund_factors(f, None)["earnings_yield"] is None
    none = signals.fund_factors(None, 50.0)
    assert none["speculative"] is False and none["fund_missing"] is True
    assert none["gp_assets"] is None and none["earnings_yield"] is None


def test_performance_700_tickers():
    closes = _walk(n_rows=1500, tickers=tuple(f"T{i}" for i in range(700)), seed=1)
    t = closes.index[-1]
    signals.factors_at(closes, t)  # warm-up
    start = time.perf_counter()
    f = signals.factors_at(closes, t)
    assert time.perf_counter() - start < 0.5
    assert len(f) == 700 and f["eligible"].all()
