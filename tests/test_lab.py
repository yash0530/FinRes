"""Lab: metrics, execution timing, lookahead guard, random-portfolio pool (offline, synthetic)."""
import numpy as np
import pandas as pd
import pytest

from finres import model
from lab import backtest as bt

CFG = {"weights": "W1", "gate": "G1", "sell": "S2", "buy": "Nvar"}


def _closes(n=60, days=700, flat=False, seed=1):
    idx = pd.bdate_range("2015-01-01", periods=days)
    rng = np.random.default_rng(seed)
    if flat:
        data = np.full((days, n), 50.0)
    else:
        drift = np.linspace(-0.0005, 0.0015, n)
        data = 40 * np.exp(np.cumsum(drift + 0.02 * rng.standard_normal((days, n)), axis=0))
    closes = pd.DataFrame(data, index=idx, columns=[f"T{i:02d}" for i in range(n)])
    spy = closes.mean(axis=1)
    return closes, spy, {t: f"g{i % 4}" for i, t in enumerate(closes.columns)}


def _ctx(closes, spy, groups, end="2026-08"):
    return bt.build_ctx(closes, spy, groups, start="2016-01", end=end, bench=pd.DataFrame({"SPY": spy}))


def _ledger(values, dates):
    return pd.DataFrame({"exec": dates, "date": dates, "contribution": 2500.0, "value": values, "turnover": 0.0})


def test_xirr_constant_growth():
    dates = [pd.Timestamp("2020-01-01") + pd.Timedelta(days=365 / 12 * i) for i in range(36)]
    v, vals = 0.0, []
    for i in range(36):
        v = v * 1.01 + 2500  # contribution invested at the valuation date
        vals.append(v)
    led = _ledger(vals, dates)
    assert bt.ledger_xirr(led) == pytest.approx(1.01 ** 12 - 1, abs=1e-4)  # 12.68%/yr
    assert bt.twr(led) == pytest.approx(np.full(35, 0.01))
    assert bt.metrics(led)["cagr"] == pytest.approx(0.1268, abs=1e-4)


def test_flat_prices_zero_twr_and_xirr_is_minus_costs(monkeypatch):
    closes, spy, groups = _closes(flat=True)
    ctx = _ctx(closes, spy, groups)
    monkeypatch.setattr(bt, "COST", 0.0)
    led = bt.simulate(ctx, {}, "2016-01", "2026-08", "ew_all")["ledger"]
    assert bt.twr(led) == pytest.approx(0.0, abs=1e-12)
    assert bt.ledger_xirr(led) == pytest.approx(0.0, abs=1e-8)
    monkeypatch.setattr(bt, "COST", 0.0015)
    led = bt.simulate(ctx, {}, "2016-01", "2026-08", "ew_all")["ledger"]
    assert led["value"].iloc[-1] == pytest.approx(2500 * len(led) * (1 - 0.0015))
    assert -0.01 < bt.ledger_xirr(led) < 0 and np.all(bt.twr(led) <= 0)


def test_next_day_execution_and_no_lookahead():
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    t = ctx["months"][3]
    d = ctx["exec"][t]
    assert d == closes.index[closes.index > t][0]
    # mutate every price after t (including the fill day): month-t signals and decisions must not change
    mutated = closes.copy()
    after = mutated.index > t
    mutated.loc[after] *= np.random.default_rng(9).uniform(0.3, 3.0, size=(after.sum(), closes.shape[1]))
    ctx2 = _ctx(mutated, spy, groups)
    pd.testing.assert_frame_equal(ctx["fac"][t], ctx2["fac"][t])
    end = t.strftime("%Y-%m")
    a, b = bt.simulate(ctx, CFG, "2016-01", end), bt.simulate(ctx2, CFG, "2016-01", end)
    buys = lambda r: [x for x in r["trades"] if x["date"] == d and x["side"] == "buy"]  # noqa: E731
    assert buys(a) and [x["ticker"] for x in buys(a)] == [x["ticker"] for x in buys(b)]
    assert [x["dollars"] for x in buys(a)] == pytest.approx([x["dollars"] for x in buys(b)])
    # fills happen at the t+1 close: the mutated t+1 price changes the fill, not the choice
    for x, y in zip(buys(a), buys(b)):
        assert x["price"] == closes.at[d, x["ticker"]] and y["price"] == mutated.at[d, y["ticker"]]


def test_newey_west_lag0_is_plain_t():
    d = np.random.default_rng(3).normal(0.01, 0.05, 120)
    plain = d.mean() / (d.std(ddof=0) / np.sqrt(len(d)))
    assert bt.nw_tstat(d, lag=0) == pytest.approx(plain)
    assert np.isfinite(bt.nw_tstat(d, lag=6))


def test_random_picks_from_pool_with_rank_n(monkeypatch):
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    rank = bt.simulate(ctx, CFG, "2016-01", "2026-08")
    led = rank["ledger"]
    n_path = dict(zip(led["date"], led["n_buys"]))
    assert led["n_buys"].sum() > 0 and not led["unscored"].any()
    pools, real = [], bt.buy_pool
    monkeypatch.setattr(bt, "buy_pool", lambda *a, **k: pools.append(real(*a, **k)) or pools[-1])
    rnd = bt.simulate(ctx, CFG, "2016-01", "2026-08", "random", np.random.default_rng(0), n_path=n_path)
    by_date = {}
    for x in rnd["trades"]:
        if x["side"] == "buy":
            by_date.setdefault(x["date"], []).append(x["ticker"])
    assert len(pools) == len(led)
    for (_, r), pool in zip(rnd["ledger"].iterrows(), pools):
        got = by_date.get(r["exec"], [])
        assert set(got) <= set(pool.index)
        assert len(got) == min(n_path[r["date"]], len(pool)) == r["n_buys"]
    assert (rnd["ledger"]["n_buys"] == led["n_buys"]).all()
    assert [x["ticker"] for x in rnd["trades"]] != [x["ticker"] for x in rank["trades"]]


def test_unscored_month_goes_equal_weight():
    closes, spy, groups = _closes(n=30)
    ctx = _ctx(closes, spy, groups)
    led = bt.simulate(ctx, CFG, "2016-01", "2026-08")["ledger"]
    assert led["unscored"].all() and (led["n_buys"] == led["eligible"]).all()


def test_random_xirrs_reproducible_across_workers():
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    led = bt.simulate(ctx, CFG, "2016-01", "2026-08")["ledger"]
    n_path = dict(zip(led["date"], led["n_buys"]))
    a = bt.random_xirrs(ctx, CFG, "2016-01", "2026-08", n_path, n=6, seed=11, workers=1)
    b = bt.random_xirrs(ctx, CFG, "2016-01", "2026-08", n_path, n=6, seed=11, workers=3)
    assert a == pytest.approx(b) and len(set(a)) > 1
    assert bt.percentile(np.median(a), a) == pytest.approx(50, abs=20)


def test_runner_is_oos_report_on_synthetic(monkeypatch, tmp_path):
    """Exercise the runner phases end to end on synthetic data only (never the real OOS)."""
    from types import SimpleNamespace
    from lab import run
    closes, spy, groups = _closes()
    bench = pd.DataFrame({"SPY": spy, "QQQ": spy * 1.1, "SMH": spy * 0.9})
    ctx = bt.build_ctx(closes, spy, groups, start="2016-01", end="2026-08", bench=bench)
    monkeypatch.setattr(run, "load_ctx", lambda *a, **k: ctx)
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    monkeypatch.setattr(run, "LAB", tmp_path)
    monkeypatch.setattr(run, "N_RANDOM", 8)
    monkeypatch.setattr(run, "IS", ("2016-01", "2016-06"))
    monkeypatch.setattr(run, "OOS", ("2016-07", "2017-09"))
    monkeypatch.setattr(run, "FULL", ("2016-01", "2017-09"))
    args = SimpleNamespace(force=False, smoke=False)
    run.phase_is(args)
    is_ = __import__("json").loads((tmp_path / "is.json").read_text())
    assert len(is_["rows"]) == 36 and is_["selected"]
    with pytest.raises(SystemExit):
        run.phase_is(args)  # refuses to overwrite
    run.phase_oos(args)
    oos = __import__("json").loads((tmp_path / "oos.json").read_text())
    assert oos["ship"] in (oos["selected"], "B0") and set(oos["rule"]) == {"beats_b0", "ge_p60_random", "sp500_cogate"}
    run.phase_report(args)
    assert "Ship:" in (tmp_path / "REPORT.md").read_text()


def test_select_tie_break():
    from lab import run
    mk = lambda w, g, s, x=0.2, dd=-0.1: {"name": f"{w}-{g}-{s}-Nvar", "xirr": x, "maxdd": dd,  # noqa: E731
                                        "cfg": {"weights": w, "gate": g, "sell": s, "buy": "Nvar"}}
    rows = [mk("W2", "G0", "S1"), mk("W3", "G1", "S1"), mk("W1", "G0", "S2"), mk("W3", "G0", "S1"),
            mk("W1", "G1", "S1", x=0.5, dd=-0.3)]
    assert run.select(rows, ewu_dd=-0.15)["name"] == "W3-G0-S1-Nvar"  # the 0.5 config breaks the DD rule


# ---------- ADR-007 PIT universe (H1) ----------

def _facts_gz(path, items):
    import gzip
    import json
    path.write_bytes(gzip.compress(json.dumps({"facts": {"us-gaap": {"Revenues": {"units": {"USD": items}}}}}).encode()))


def test_pit_members_revenue_floor_is_point_in_time(monkeypatch, tmp_path):
    """members(t) = eligible in year(t) AND TTM revenue >= $100M known at t: a 10-K filed after t must not count."""
    from lab import run
    (tmp_path / "data" / "facts").mkdir(parents=True)
    _facts_gz(tmp_path / "data" / "facts" / "7.json.gz", [
        {"start": "2013-01-01", "end": "2013-12-31", "val": 5e7, "filed": "2014-03-01", "form": "10-K"},
        {"start": "2014-01-01", "end": "2014-12-31", "val": 2e8, "filed": "2015-03-02", "form": "10-K"}])
    monkeypatch.setattr(run, "PIT", tmp_path)
    assert run.facts("C7") is not None and run.facts("C8") is None  # C{cik} reads the research cache by CIK
    months = [pd.Timestamp(d) for d in ("2015-01-30", "2015-02-27", "2015-03-02", "2015-03-31", "2016-01-29")]
    mem = run.pit_members({2015: {"C7", "C8"}}, run.fund_panel(["C7", "C8"], months), months)
    assert [mem[t] for t in months] == [set(), set(), set(), {"C7"}, set()]  # filed == t: not yet; 2016: not eligible


def test_stress_series_follows_ew_index_then_drops_once_and_ends(monkeypatch):
    from lab import run
    monkeypatch.setattr(run, "STRESS_PAD", 100)
    closes, _, _ = _closes(n=3, days=500)
    closes["T02"] = closes["T02"].where(closes.index >= "2015-06-01")  # a name without history early on
    ms = bt.signals.month_ends(closes.index)[:-1]
    ctx = {"months": ms, "elig": {t: ["T00", "T01"] if t < pd.Timestamp("2015-09-01") else ["T01", "T02"] for t in ms}}
    rows = [{"cik": "9", "year": "2016", "tiingo_end": "2016-03-15", "exchange": "NASDAQ", "last_10k": "2015-02-01"},
            {"cik": "9", "year": "2015", "tiingo_end": "", "exchange": "", "last_10k": "2015-02-01"},
            {"cik": "10", "year": "2016", "tiingo_end": "", "exchange": "", "last_10k": "2015-06-30"}]
    out = run.stress_closes(closes, ctx, rows)
    s9, s10 = out["C9"].dropna(), out["C10"].dropna()
    assert s9.index[0] == closes.index[0]  # padded back STRESS_PAD rows before 2015-01-01 (clipped to data start)
    assert s9.index[-1] == pd.Timestamp("2016-03-15") and out["C9"].loc["2016-03-16":].isna().all()
    assert s9.iloc[-1] / s9.iloc[-2] == pytest.approx(0.45)  # Nasdaq: -55% on the last day, once
    r = s9.pct_change().iloc[1:-1]
    d = pd.Timestamp("2015-10-15")  # previous month-end 2015-09-30 -> members T01, T02
    assert r.loc[d] == pytest.approx(closes.loc[:d, ["T01", "T02"]].pct_change().iloc[-1].mean())
    d = pd.Timestamp("2015-03-10")
    assert r.loc[d] == pytest.approx(closes.loc[:d, ["T00", "T01"]].pct_change().iloc[-1].mean())
    assert s10.index[-1] == pd.Timestamp("2016-06-29")  # last 10-K 2015-06-30 + 365 d
    assert s10.iloc[-1] / s10.iloc[-2] == pytest.approx(0.70)  # unmapped / non-Nasdaq: -30%, at last 10-K + 365 d
    assert s10.index[0] == closes.index[closes.index.searchsorted(pd.Timestamp("2016-01-01")) - run.STRESS_PAD]
    assert list(out.columns[:3]) == list(closes.columns)  # priced names untouched


def test_h1_phase_and_report_on_synthetic(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from lab import run
    closes, spy, groups = _closes()
    bench = pd.DataFrame({"SPY": spy, "QQQ": spy * 1.1, "SMH": spy * 0.9})
    ctx = bt.build_ctx(closes, spy, groups, start="2016-01", end="2026-08", bench=bench)
    ctx |= {"tickers": list(closes.columns), "members": None}
    stress = dict(ctx, tickers=ctx["tickers"] + ["C1"])
    monkeypatch.setattr(run, "load_ctx", lambda label, *a, **k: stress if label == "ai_pit_stress" else ctx)
    monkeypatch.setattr(run.prices, "load_closes", lambda *a, **k: closes)
    for k, v in {"RESULTS": tmp_path, "LAB": tmp_path / "lab", "PIT": tmp_path, "N_RANDOM": 8,
                 "H1": ("2016-01", "2017-09")}.items():
        monkeypatch.setattr(run, k, v)
    (tmp_path / "lab").mkdir()
    (tmp_path / "coverage.md").write_text("# PIT universe coverage\n\n| year | eligible |\n|---|---|\n| 2016 | 60 |\n")
    (tmp_path / "DECISIONS.md").write_text("**Honesty (fixed text for the report).** Frozen words here.\n")
    args = SimpleNamespace(force=False, smoke=False)
    monkeypatch.setattr(run, "sanity", lambda *a: {"pass_a": False, "pass_b": True})
    with pytest.raises(SystemExit):
        run.phase_h1(args)  # sanity fails -> H1 does not count
    h1 = __import__("json").loads((tmp_path / "h1.json").read_text())
    assert h1["status"].startswith("SANITY FAILED") and "headline" not in h1
    monkeypatch.setattr(run, "sanity", lambda *a: {"pass_a": True, "pass_b": True, "random_signal_percentile": 50.0})
    with pytest.raises(SystemExit):
        run.phase_h1(args)  # write-once
    run.phase_h1(SimpleNamespace(force=True, smoke=False))
    h1 = __import__("json").loads((tmp_path / "h1.json").read_text())
    assert [r["name"] for r in h1["ai_pit"]["rows"]] == [run.RULE, run.EWN, "DCA SMH", "DCA QQQ"]
    assert [r["name"] for r in h1["ai_hand"]["rows"]] == [run.RULE, run.EWN] and h1["stress_names"] == 1
    assert h1["headline"]["bias_hand_minus_pit"] == pytest.approx(0.0)  # same ctx on both sides here
    run.phase_report(args)
    md = (tmp_path / "lab" / "REPORT.md").read_text()
    assert "## Hindsight-free re-test (ADR-007)" in md and "STRESS TEST" in md and "Frozen words here." in md
    assert "| 2016 | 60 |" in md
