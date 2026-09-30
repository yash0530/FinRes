"""model.py: scoring, labels, buy/sell rules, reasons (offline, synthetic frames)."""
import numpy as np
import pandas as pd
import pytest

from finres import model
from lab import backtest as bt

G1N = {"weights": "W1", "gate": "G1", "sell": "S3", "buy": "Nvar"}


def _fac(n=20, **cols):
    """Synthetic factors_at-style frame: T00 has the lowest momentum, T{n-1} the highest."""
    idx = [f"T{i:02d}" for i in range(n)]
    base = {
        "close": 50.0, "n_days": 400, "ret_12_1": np.linspace(-0.2, 0.8, n), "vol_252": 0.4,
        "mom_raw": np.linspace(-0.5, 2.0, n), "hi52": np.linspace(0.6, 1.0, n),
        "dd_52w": np.linspace(-0.4, 0.0, n), "sma50": 48.0, "sma200": 45.0,
        "above200": True, "trend": True, "eligible": True,
        "gp_assets": np.nan, "rev_growth": np.nan, "earnings_yield": np.nan, "speculative": False,
    }
    base.update(cols)
    df = pd.DataFrame(base, index=pd.Index(idx, name="ticker"))
    return df


def test_pct_keeps_nan():
    p = model.pct(pd.Series([3.0, np.nan, 1.0, 2.0]))
    assert p.tolist()[0] == 1.0 and np.isnan(p.iloc[1]) and p.iloc[2] == pytest.approx(1 / 3)


def test_composite_order_and_tie_break():
    fac = _fac(4, mom_raw=[1.0, 1.0, 1.0, 0.0], hi52=[0.9, 0.9, 0.9, 0.5],
               earnings_yield=[0.01, np.nan, 0.05, 0.1])
    fac.index = pd.Index(["BBB", "AAA", "CCC", "ZZZ"], name="ticker")
    s = model.score(fac, "W3")
    # three-way tie on composite -> earnings_yield desc (NaN last), then ticker
    assert list(s.index) == ["CCC", "BBB", "AAA", "ZZZ"]
    fac["earnings_yield"] = np.nan
    assert list(model.score(fac, "W3").index) == ["AAA", "BBB", "CCC", "ZZZ"]


def test_only_eligible_scored_and_percentiles_over_eligible():
    fac = _fac(5, eligible=[True, True, True, True, False])
    s = model.score(fac)
    assert np.isnan(s.loc["T04", "composite"]) and s.loc["T04", "label"] == "Insufficient data"
    assert s.loc["T04", "grade_composite"] == "–"
    assert s.loc["T03", "composite"] == 1.0  # top among eligible, not T04


def test_qual_missing_gives_mom_full_weight_and_revision_blend():
    fac = _fac(4, gp_assets=[0.1, 0.2, np.nan, 0.4], rev_growth=[0.4, 0.3, np.nan, 0.1])
    s = model.score(fac, "W2")
    mom = 0.7 * model.pct(fac["mom_raw"]) + 0.3 * model.pct(fac["hi52"])
    qual = pd.concat([model.pct(fac["gp_assets"]), model.pct(fac["rev_growth"])], axis=1).mean(axis=1)
    raw = (0.6 * mom + 0.4 * qual).where(qual.notna(), mom)
    assert s.loc["T02", "raw"] == pytest.approx(mom["T02"])  # qual missing -> mom only
    assert s["raw"].reindex(fac.index).tolist() == pytest.approx(raw.tolist())
    assert np.isnan(s.loc["T00", "rev"])  # revisions ignored unless asked
    fac["rev_chg"], fac["rev_breadth"] = [0.3, 0.2, 0.1, 0.0], [np.nan, np.nan, np.nan, np.nan]
    r = model.score(fac, "W2", use_revisions=True)
    rev = model.pct(fac["rev_chg"])
    assert r["raw"].reindex(fac.index).tolist() == pytest.approx((0.85 * raw + 0.15 * rev).tolist())


def test_labels_each_branch():
    fac = _fac(20, trend=[True] * 19 + [False], earnings_yield=0.05)
    s = model.score(fac, "W3")
    assert s.loc["T19", "label"] == "WATCH"  # top, no trend
    assert s.loc["T18", "label"] == "BUY"
    assert s.loc["T15", "label"] == "HOLD"  # composite 0.80
    assert s.loc["T00", "label"] == "AVOID"
    assert s.loc["T19", "grade_composite"] == "A" and s.loc["T00", "grade_composite"] == "F"
    # ADR-004c: missing quality/revisions/earnings yield only re-weight; never "Insufficient data"
    live = _fac(4, earnings_yield=[0.1, np.nan, 0.1, np.nan], rev_chg=[0.1, 0.2, np.nan, np.nan])
    assert model.score(live, use_revisions=True)["label"].ne("Insufficient data").all()
    assert model.score(live)["label"].ne("Insufficient data").all()


def test_regime():
    fac = _fac(10, above200=[True] * 3 + [False] * 7)
    r = model.regime(fac, 500.0, 450.0)
    assert r["spy_above200"] and r["breadth"] == pytest.approx(0.3) and r["brake"]
    fac["above200"] = True
    assert not model.regime(fac, 500.0, 450.0)["brake"]
    assert model.regime(fac, 400.0, 450.0)["brake"]


def test_buy_list_nvar_gate_and_max():
    fac = _fac(100, trend=[i % 2 == 0 for i in range(100)])
    s = model.score(fac, "W3")
    buys = bt.buy_list(s, {}, {}, G1N, brake=False, budget=2500)
    assert buys and all(s.loc[b["ticker"], "composite"] >= 0.85 and s.loc[b["ticker"], "trend"] for b in buys)
    assert len(buys) == 8 and sum(b["dollars"] for b in buys) == pytest.approx(2500)
    g0 = bt.buy_list(s, {}, {}, {**G1N, "gate": "G0"}, brake=False, budget=2500)
    assert len(g0) == 10 and [b["ticker"] for b in g0][:2] == ["T99", "T98"]  # capped at MAX_N, ignores trend
    assert set(buys[0]) == {"ticker", "dollars", "composite", "reason"}


def test_buy_list_n3_brake_empty():
    s = model.score(_fac(20), "W3")
    n3 = bt.buy_list(s, {}, {}, {**G1N, "buy": "N3"}, brake=False, budget=3000)
    assert [b["ticker"] for b in n3] == ["T19", "T18", "T17"] and n3[0]["dollars"] == 1000
    braked = bt.buy_list(s, {}, {}, G1N, brake=True, budget=2500)
    assert [b["ticker"] for b in braked] == ["T19", "T18"] and braked[0]["dollars"] == 1250
    none = model.score(_fac(20, trend=False), "W3")
    assert bt.buy_list(none, {}, {}, G1N, brake=False, budget=2500) == []
    assert bt.buy_pool(none, {}, {}, G1N).empty


def test_caps_inactive_below_threshold_and_active_above():
    s = model.score(_fac(20, speculative=[False] * 17 + [True, False, False]), "W3")
    groups = {"T19": "semis", "T18": "semis", "T17": "software", "T16": "software"}
    small = {"T19": 20_000.0, "T00": 1_000.0}  # 95% in one name but portfolio < $25k
    assert "T19" in bt.buy_pool(s, small, groups, G1N).index
    # position cap: T19 = 10% of 100k
    big = {"T19": 10_000.0, "T00": 90_000.0}
    pool = bt.buy_pool(s, big, groups, G1N).index
    assert "T19" not in pool and "T18" in pool
    # group cap: semis = 30% via T18 -> T19 blocked too
    grp = {"T18": 30_000.0, "T00": 70_000.0}
    pool = bt.buy_pool(s, grp, groups, G1N).index
    assert "T19" not in pool and "T18" not in pool and "T16" in pool
    # unknown group = own group: T00 at 70% is blocked, but T01 is not
    assert "T00" not in pool and "T01" in pool
    # speculative: T17 position >= 3%
    spec = {"T17": 3_000.0, "T00": 9_000.0, "T01": 9_000.0, "T02": 9_000.0, "T03": 9_000.0, "T04": 9_000.0,
            "T05": 9_000.0, "T06": 9_000.0, "T07": 9_000.0, "T08": 9_000.0, "T09": 9_000.0, "T10": 7_000.0}
    pool = bt.buy_pool(s, spec, {}, G1N).index
    assert "T17" not in pool and "T16" in pool


def test_speculative_total_cap():
    s = model.score(_fac(20, speculative=[True] * 3 + [False] * 14 + [True] * 3), "W3")
    positions = {"T00": 2_500.0, "T01": 2_500.0, "T02": 7_000.0, "T05": 9_000.0, "T06": 9_000.0,
                 "T07": 9_000.0, "T08": 9_000.0, "T09": 9_000.0, "T10": 9_000.0, "T11": 9_000.0,
                 "T12": 9_000.0, "T13": 9_000.0, "T14": 9_000.0}  # 111k, speculative total 10.8%
    pool = bt.buy_pool(s, positions, {}, G1N).index
    assert not {"T17", "T18", "T19"} & set(pool) and "T16" in pool


def test_sell_list_rules():
    fac = _fac(10, above200=[True] * 7 + [False] * 3, trend=[True] * 7 + [False] * 3)
    s = model.score(fac, "W3")
    holdings = {t: {"shares": 1.0, "cost": 50.0} for t in ["T00", "T07", "T08", "T09", "GONE"]}
    prices = {"T00": 50.0, "T07": 50.0, "T08": 30.0, "T09": 50.0, "GONE": 1.0}
    prev = {"T08", "T09", "GONE"}
    assert model.sell_list(s, holdings, prices, prev, {**G1N, "sell": "S1"}) == []
    s2 = {r["ticker"]: r["rule"] for r in model.sell_list(s, holdings, prices, prev, {**G1N, "sell": "S2"})}
    assert s2 == {"T08": "-35% stop", "T09": "2 month-ends below 200DMA", "GONE": "-35% stop"}  # stop wins
    s3 = model.sell_list(s, holdings, prices, prev, G1N)
    rules = {r["ticker"]: r["rule"] for r in s3}
    assert rules == {"T00": "rank fell below 70th pct", "T08": "-35% stop", "T09": "2 month-ends below 200DMA",
                     "GONE": "-35% stop"}  # GONE is not scored but the stop applies to every holding
    assert "T07" not in rules  # T07 above 200DMA, rank 0.8
    assert len(s3) == len({r["ticker"] for r in s3})
    # below 200DMA now but not at the previous month-end -> no trend sell
    assert not model.sell_list(s, {"T09": {"shares": 1, "cost": 50}}, {"T09": 50.0}, set(),
                               {**G1N, "sell": "S2"})


def test_reason_top_two_and_no_nan():
    fac = _fac(10, gp_assets=np.linspace(0.1, 1.0, 10)[::-1], rev_growth=np.linspace(0.0, 0.9, 10)[::-1],
               earnings_yield=0.02, rev_chg=np.linspace(-0.1, 0.1, 10), rev_breadth=np.nan)
    s = model.score(fac, "W1", use_revisions=True)
    r = s.loc["T09", "reason"]  # top momentum and revisions, bottom quality
    assert r.startswith("Momentum 100th pct (12-1m +80%, 0% from high)")
    assert "Revisions 100th pct (FY EPS est +10%/90d)" in r and "Quality" not in r
    assert r.endswith(" · in uptrend") and r.count(" · ") == 2
    lab = model.score(_fac(10, trend=False, above200=False, eligible=[False] + [True] * 9,
                           n_days=[100] + [400] * 9), "W3")
    assert lab.loc["T05", "reason"].startswith("Momentum") and lab.loc["T05", "reason"].endswith(" · below 200DMA")
    flat = model.score(_fac(10, trend=False, above200=True), "W3")  # above the 200DMA, 50DMA under it
    assert flat.loc["T05", "reason"].endswith(" · above 200DMA, 50DMA below 200DMA (no uptrend)")
    assert "Quality" not in lab.loc["T05", "reason"]
    assert lab.loc["T00", "reason"].startswith("Insufficient data")
    everything = pd.concat([s, lab])["reason"]
    assert not everything.str.contains("nan", case=False).any()


def test_ordinals_and_warnings():
    assert model._ordinal(0.01) == "1st" and model._ordinal(0.12) == "12th" and model._ordinal(0.23) == "23rd"
    row = pd.Series({"rev_breadth": -0.5, "speculative": True, "fund_pit": False})
    assert model.warnings(row) == ["EPS revisions negative", "speculative", "not point-in-time fundamentals"]
    assert model.warnings(pd.Series({"rev_breadth": np.nan, "speculative": False, "fund_pit": None})) == []


def test_buy_list_max_per_group(monkeypatch):
    """ADR-004b: one month's buys hold at most MAX_PER_GROUP names from one cap group (skips, keeps order)."""
    import pandas as pd
    from finres import model
    idx = [f"S{i}" for i in range(6)] + ["P0", "P1"]
    fac = pd.DataFrame({"eligible": True, "trend": True, "mom_raw": range(8, 0, -1), "hi52": 1.0,
                        "gp_assets": None, "rev_growth": None, "earnings_yield": None, "speculative": False,
                        "close": 10.0, "n_days": 300, "sma200": 9.0, "above200": True}, index=idx)
    scored = model.score(fac)
    groups = {t: ("semis" if t.startswith("S") else "infra") for t in idx}
    cfg = {"weights": "W1", "gate": "G1", "sell": "S3", "buy": "N3"}
    assert [b["ticker"] for b in bt.buy_list(scored, {}, groups, cfg, False, 2500)] == ["S0", "S1", "S2"]
    monkeypatch.setattr(model, "MAX_PER_GROUP", 2)
    assert [b["ticker"] for b in bt.buy_list(scored, {}, groups, cfg, False, 2500)] == ["S0", "S1", "P0"]


def test_b0r_rotation_least_held_first():
    """ADR-005 shipped rule: uptrend names only, least-held first, composite breaks ties, <=3 per group, <=10."""
    import pandas as pd
    from finres import model
    idx = [f"T{i:02d}" for i in range(14)]
    fac = pd.DataFrame({"eligible": True, "trend": [True] * 12 + [False] * 2, "mom_raw": range(14, 0, -1),
                        "hi52": 1.0, "gp_assets": None, "rev_growth": None, "earnings_yield": None,
                        "speculative": False, "close": 10.0, "n_days": 300, "sma200": 9.0, "above200": True},
                       index=idx)
    scored = model.score(fac)
    groups = {t: f"g{i % 5}" for i, t in enumerate(idx)}
    held = {"T00": 500.0, "T01": 100.0}
    got = model.buy_list(scored, held, groups, {"buy": "B0R"}, brake=True, budget=2500)
    names = [b["ticker"] for b in got]
    assert len(names) == 10 and "T12" not in names and "T13" not in names  # no-uptrend names never bought
    assert names == [f"T{i:02d}" for i in range(2, 12)]  # the two held names wait; unheld go first by composite
    assert all(abs(b["dollars"] - 250) < 1e-9 for b in got)
    assert max(sum(1 for t in names if groups[t] == g) for g in set(groups.values())) <= model.MAX_PER_GROUP


def test_stop_fires_for_unscored_holding():
    """M8b: the -35% stop applies to holdings outside the scored universe; they never get trend/rank sells."""
    s = model.score(_fac(5), "W3")
    got = model.sell_list(s, {"COST": {"shares": 1, "cost": 2000.0}, "UP": {"shares": 1, "cost": 10.0}},
                          {"COST": 1000.0, "UP": 50.0}, {"COST", "UP"}, {"sell": "S3"})
    assert got == [{"ticker": "COST", "rule": "-35% stop", "composite": None}]


def test_step_sells_then_equal_buys_with_costs_and_leftover():
    port = {"shares": {"A": 10.0, "B": 5.0}, "basis": {"A": 400.0, "B": 250.0}, "cash": 100.0}
    frozen = {"shares": dict(port["shares"]), "basis": dict(port["basis"]), "cash": 100.0}
    px = {"A": 50.0, "B": 60.0, "C": 20.0, "D": 0.0}
    new, trades = model.step(port, ["A"], ["B", "C", "D", "E"], px, 2500.0, 0.01)  # D price 0, E missing: skipped
    assert port == frozen  # input never mutated
    budget = 2500 + 100 + 10 * 50 * 0.99
    assert "A" not in new["shares"] and "A" not in new["basis"]
    assert new["shares"]["B"] == pytest.approx(5 + budget / 2 * 0.99 / 60)
    assert new["shares"]["C"] == pytest.approx(budget / 2 * 0.99 / 20)
    assert new["basis"] == pytest.approx({"B": 250 + budget / 2, "C": budget / 2})
    assert new["cash"] == pytest.approx(0.0, abs=1e-9) and set(new["shares"]) == {"B", "C"}
    assert [(t["ticker"], t["side"]) for t in trades] == [("A", "sell"), ("B", "buy"), ("C", "buy")]
    assert trades[0] == {"ticker": "A", "side": "sell", "dollars": 500.0, "price": 50.0}


def test_step_no_buys_keeps_cash_and_unpriced_sell_is_held():
    port = {"shares": {"A": 1.0}, "basis": {"A": 10.0}, "cash": 0.0}
    new, trades = model.step(port, ["A"], [], {}, 2500.0, 0.0015)
    assert new == {"shares": {"A": 1.0}, "basis": {"A": 10.0}, "cash": 2500.0} and trades == []


def test_xirr_matches_constant_growth():
    dates = ["2020-01-01", "2021-01-01"]
    assert model.xirr(dates, [-100.0, 110.0]) == pytest.approx(1.1 ** (365 / 366) - 1, abs=1e-9)  # 2020 is a leap year
    assert np.isnan(model.xirr(["2020-01-01", "2020-01-01"], [-1.0, 2.0]))
