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
    assert led["unscored"].all() and (led["n_buys"] + led["n_sells"] >= led["eligible"]).all()
    assert (led["n_buys"] <= led["eligible"]).all()


@pytest.mark.parametrize("pick", ["rank", "ew_trend10", "b0h", "v1"])
def test_m10_unscored_month_never_rebuys_a_name_sold_that_month(pick):
    """F1 (ADR-009a): T00 falls below its 200DMA -> S2 sells it; it is not re-bought that month, and is bought again
    in every later month where it is eligible and not being sold."""
    closes, spy, groups = _closes(n=30)
    path = np.r_[np.full(300, 50.0), np.linspace(50, 25, 20), np.full(130, 25.0), np.linspace(25, 80, 250)]
    closes["T00"] = path
    ctx = _ctx(closes, spy, groups)
    res = bt.simulate(ctx, CFG, "2016-01", "2026-08", pick)
    assert res["ledger"]["unscored"].all()
    side = {}
    for x in res["trades"]:
        side.setdefault(x["date"], {"sell": set(), "buy": set()})[x["side"]].add(x["ticker"])
    assert all(not (v["sell"] & v["buy"]) for v in side.values())
    sold = [d for d, v in side.items() if "T00" in v["sell"]]
    assert sold and any(d > sold[0] and "T00" in v["buy"] for d, v in side.items())  # re-entered later
    for t in bt._months(ctx, "2016-01", "2026-08"):
        v = side.get(ctx["exec"][t], {"sell": set(), "buy": set()})
        assert ("T00" in v["buy"]) == ("T00" in ctx["elig"][t] and "T00" not in v["sell"])


def test_b0h_buys_like_b0r_and_sells_only_on_the_stop():
    """ADR-011: B0H = ew_trend10's buys; its only exit is the -35% stop (never the 2-month-end trend break)."""
    closes, spy, groups = _closes(n=60)
    closes["T59"] = np.r_[np.linspace(40, 90, 450), np.full(250, 70.0)]  # -22%: breaks the trend, not the stop
    closes["T58"] = np.r_[np.linspace(40, 90, 450), np.full(250, 30.0)]  # -67%: the stop
    ctx = _ctx(closes, spy, groups)
    r0, h = (bt.simulate(ctx, {}, "2016-01", "2026-08", p) for p in ("ew_trend10", "b0h"))
    assert not h["ledger"]["unscored"].all()
    first = lambda res: sorted(x["ticker"] for x in res["trades"] if x["date"] == res["trades"][0]["date"])  # noqa
    assert first(h) == first(r0)
    rules = lambda res: {x["rule"] for x in res["trades"] if x["side"] == "sell"}  # noqa: E731
    assert "2 month-ends below 200DMA" in rules(r0) and rules(h) == {"-35% stop"}
    sold = lambda res, t: any(x["ticker"] == t and x["side"] == "sell" for x in res["trades"])  # noqa: E731
    assert sold(h, "T58") and not sold(h, "T59") and sold(r0, "T59")


def test_h3_phase_and_report_on_synthetic(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from lab import run
    closes, spy, groups = _closes()
    bench = pd.DataFrame({"SPY": spy, "QQQ": spy * 1.1, "SMH": spy * 0.9})
    ctx = bt.build_ctx(closes, spy, groups, start="2016-01", end="2026-08", bench=bench)
    monkeypatch.setattr(run, "load_ctx", lambda *a, **k: ctx)
    monkeypatch.setattr(run, "H3", {k: ("2016-01", "2017-09") for k in run.H3})
    for k in ("RESULTS", "LAB", "PIT"):
        monkeypatch.setattr(run, k, tmp_path)
    (tmp_path / "DECISIONS.md").write_text("")
    args = SimpleNamespace(force=False, smoke=False)
    run.phase_h3(args)
    h3 = json.loads((tmp_path / "h3.json").read_text())
    assert list(h3["sets"]) == ["ai", "ai_pit", "ai_pit_stress", "ai_pit_ra", "sp500"]
    for v in h3["sets"].values():
        b0r, b0h = v["rows"][0], v["rows"][1]
        assert [r["name"] for r in v["rows"]] == [run.B0R, "B0H", run.EWN, "DCA SMH"]
        assert v["pass"] == (b0h["xirr"] >= b0r["xirr"] and b0h["maxdd"] >= b0r["maxdd"] - 0.10)
    assert h3["switch"] == all(v["pass"] for v in h3["sets"].values())
    assert h3["ship"] == ("B0H" if h3["switch"] else "B0R")
    with pytest.raises(SystemExit):
        run.phase_h3(args)  # write-once
    run.phase_report(args)
    assert "## ADR-011: B0H vs B0R" in (tmp_path / "REPORT.md").read_text()


def test_h1q_phase_and_report_on_synthetic(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from lab import run
    closes, spy, groups = _closes()
    bench = pd.DataFrame({"SPY": spy, "QQQ": spy * 1.1, "SMH": spy * 0.9})
    ctx = bt.build_ctx(closes, spy, groups, start="2016-01", end="2026-08", bench=bench)
    ctx |= {"tickers": list(closes.columns), "members": None}
    labels = []
    monkeypatch.setattr(run, "load_ctx", lambda label, *a, **k: labels.append(label) or ctx)
    for k, v in {"RESULTS": tmp_path, "LAB": tmp_path, "PIT": tmp_path, "DATA": tmp_path, "H1": ("2016-01", "2017-09")}.items():
        monkeypatch.setattr(run, k, v)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "qwen_class.csv").write_text("acc,sells_into\na1,True\n")
    (tmp_path / "data" / "business.csv").write_text("acc\na1\na2\n")
    (tmp_path / "ctx_ai_pit_q.pkl").write_bytes(b"stale")
    (tmp_path / "DECISIONS.md").write_text("")
    args = SimpleNamespace(force=False, smoke=False)
    run.phase_h1q(args)
    h1q = json.loads((tmp_path / "h1q.json").read_text())
    assert labels == list(run.H1Q) and not (tmp_path / "ctx_ai_pit_q.pkl").exists()  # stale cache dropped
    assert (h1q["partial"], h1q["classified"], h1q["filings"]) == (True, 1, 2)
    for k in run.H1Q:
        assert [r["name"] for r in h1q[k]["rows"]] == [run.B0R, "B0H", run.EWN, "DCA SMH", "DCA QQQ"]
        assert {"xirr", "cagr", "maxdd", "sharpe", "unscored_months"} <= set(h1q[k]["rows"][0])
        assert h1q[k]["names"] == 60 and "2016" in h1q[k]["eligible_by_year"]
    with pytest.raises(SystemExit):
        run.phase_h1q(args)  # write-once
    run.phase_report(args)
    md = (tmp_path / "REPORT.md").read_text()
    assert "## ADR-010: Qwen-cleaned universe (context only)" in md and "**PARTIAL" in md and "ai_pit_ra_q_stress" in md


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


# ---------- ADR-007 H2 variants ----------

def _fac(closes, t):
    f = bt.signals.factors_at(closes, t)
    return f[f["n_days"] > 0]


def test_v1_month_end_sma10_on_monthly_ramp():
    """Monthly ramp: each month's closes are constant at the month number; SMA10 = mean of the last 10 month-ends."""
    idx = pd.bdate_range("2015-01-01", "2017-06-30")
    k = (idx.year - 2015) * 12 + idx.month  # 1, 2, 3, ...
    up = pd.Series(k.astype(float), index=idx)
    closes = pd.DataFrame({"UP": up, "DN": 100.0 - up})
    monthly = closes.ffill(limit=4).loc[bt.signals.month_ends(idx)]
    t = bt.signals.month_ends(idx)[20]  # month 21
    v = bt.variant_signals(closes, monthly, t, _fac(closes, t))
    assert v.at["UP", "v1_level"] == pytest.approx(np.mean(np.arange(12, 22)))  # months 12..21, t included
    assert v.at["DN", "v1_level"] == pytest.approx(100 - np.mean(np.arange(12, 22)))
    assert bool(v.at["UP", "v1_up"]) and not bool(v.at["UP", "v1_below"])
    assert not bool(v.at["DN", "v1_up"]) and bool(v.at["DN", "v1_below"])
    t = bt.signals.month_ends(idx)[8]  # only 9 month-ends: no SMA10 -> never buyable, exit can't fire
    v = bt.variant_signals(closes, monthly, t, _fac(closes, t))
    assert v["v1_level"].isna().all() and not v["v1_up"].any() and not v["v1_below"].any()


def test_v2_band_entry_and_exit():
    fac = pd.DataFrame({"close": [103.0, 101.0, 103.0, 99.0, 97.0], "sma200": 100.0,
                        "sma50": [101.0, 101.0, 99.0, 101.0, 101.0], "ret_12_1": 0.1}, index=list("ABCDE"))
    closes = pd.DataFrame(100.0, index=pd.bdate_range("2016-01-01", periods=30), columns=fac.index)
    v = bt.variant_signals(closes, closes.iloc[[-1]], closes.index[-1], fac)
    assert list(v["v2_up"]) == [True, False, False, False, False]  # > 1.02 x SMA200 and SMA50 > SMA200
    assert list(v["v2_below"]) == [False, False, False, False, True]  # < 0.98 x SMA200 only


def test_fip_id_smooth_vs_jumpy_and_zero_days():
    w = pd.DataFrame({"smooth": [100, 101, 102, 103, 104.0], "jumpy": [100, 100, 100, 100, 130.0],
                      "flat_mix": [100, 110, 110, 110, 105.0], "down": [100, 99, 98, 97, 120.0],
                      "neg": [100, 90, 95, 95, 94.0]})
    idv = bt.fip_id(w)
    assert idv["smooth"] == pytest.approx(-1.0)  # 4 up days of 4
    assert idv["jumpy"] == pytest.approx(-0.25)  # 1 up day; the 3 zero days only enter the denominator
    assert idv["flat_mix"] == pytest.approx(0.0)  # 1 up, 1 down, 2 zero
    assert idv["down"] == pytest.approx(0.5)  # PRET > 0 but 3 of 4 days down: jumpy, 2/4 net negative
    assert idv["neg"] == pytest.approx(-0.25)  # PRET < 0 flips the sign: -(2/4 - 1/4)
    assert idv["smooth"] < idv["jumpy"]  # smooth momentum has the lower ID (kept by V3)


def test_fip_pool_top_third_then_lower_half():
    names = [f"N{i}" for i in range(10)] + ["OFF"]
    scored = pd.DataFrame({"eligible": True, "composite": 0.5, "trend": [True] * 10 + [False]}, index=names)
    var = pd.DataFrame({"mom": [0.1 * i for i in range(10)] + [9.0],  # OFF has the best momentum but no uptrend
                        "fip": [0, 0, 0, 0, 0, 0, -0.2, -0.9, -0.1, -0.5, -1.0]}, index=names)
    keep = bt.fip_pool(scored, var)
    # pool 10 -> top ceil(10/3)=4 by momentum (N9, N8, N7, N6) -> lower ceil(4/2)=2 by ID (N7 -0.9, N9 -0.5)
    assert keep == ["N7", "N9"]
    assert bt.fip_pool(scored.iloc[:7], var) == ["N6", "N5"]  # 7 -> 3 (N6, N5, N4) -> 2: N6 (-0.2), then the N5/N4 tie by momentum order
    assert len(bt.fip_pool(scored.iloc[:7], var.assign(fip=-var["mom"]))) == 2


@pytest.mark.parametrize("pick", ["v1", "v2", "v3"])
def test_variants_buy_only_gate_passers_least_held_and_group_limit(monkeypatch, pick):
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    calls, real = [], model.buy_list

    def spy_buy(scored, positions, groups_, cfg, brake, budget):
        out = real(scored, positions, groups_, cfg, brake, budget)
        calls.append((scored, positions, cfg, out))
        return out
    monkeypatch.setattr(model, "buy_list", spy_buy)
    res = bt.simulate(ctx, {}, "2016-01", "2026-08", pick)
    assert not res["ledger"]["unscored"].any() and len(calls) == len(res["ledger"])
    bought = 0
    for t, (scored, positions, cfg, out) in zip(bt._months(ctx, "2016-01", "2026-08"), calls):
        gate = bt.variant_gate(pick, bt.scored_at(ctx, t, "W1"), ctx["var"][t])
        assert cfg == {"buy": "B0R"} and scored["trend"].equals(gate)
        names = [b["ticker"] for b in out]
        assert set(names) <= set(gate.index[gate]) and len(names) <= 10
        assert max(pd.Series([groups[k] for k in names]).value_counts(), default=0) <= model.MAX_PER_GROUP
        held = [positions.get(k, 0.0) for k in names]
        assert held == sorted(held)  # least-held first
        full = {g for g, c in pd.Series([groups[k] for k in names]).value_counts().items() if c >= 3}
        rest = [positions.get(k, 0.0) for k in gate.index[gate] if k not in names and groups[k] not in full]
        if len(names) == 10 and rest:
            assert max(held) <= min(rest)
        bought += len(names)
        by_exec = [x["ticker"] for x in res["trades"] if x["date"] == ctx["exec"][t] and x["side"] == "buy"]
        assert by_exec == names
    assert bought > 0


def test_v2_exit_needs_two_consecutive_month_ends_below_band():
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    ms = ctx["months"]
    first = bt.simulate(ctx, {}, "2016-01", ms[5].strftime("%Y-%m"), "v2")["trades"]
    k = next(x["ticker"] for x in first if x["side"] == "buy")
    for t in ms:  # k never below the band ... except at month 7, then at months 9 and 10
        for key in ("var", "var_prev"):
            ctx[key][t] = ctx[key][t].copy()
            ctx[key][t].loc[:, "v2_below"] = False
    for j in (7, 9, 10):
        ctx["var"][ms[j]].loc[k, "v2_below"] = True
        ctx["var_prev"][ms[j + 1]].loc[k, "v2_below"] = True
    trades = bt.simulate(ctx, {}, "2016-01", ms[12].strftime("%Y-%m"), "v2")["trades"]
    sells = [x for x in trades if x["side"] == "sell" and x["ticker"] == k and x["rule"] != "-35% stop"]
    assert [x["date"] for x in sells] == [ctx["exec"][ms[10]]]  # month 7 alone does not sell; 9 + 10 does


@pytest.mark.parametrize("pick", ["v1", "v2", "v3"])
def test_variant_picks_have_no_lookahead(pick):
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    t = ctx["months"][8]
    mutated = closes.copy()
    after = mutated.index > t
    mutated.loc[after] *= np.random.default_rng(5).uniform(0.3, 3.0, size=(after.sum(), closes.shape[1]))
    ctx2 = _ctx(mutated, spy, groups)
    pd.testing.assert_frame_equal(ctx["var"][t], ctx2["var"][t])
    d, end = ctx["exec"][t], t.strftime("%Y-%m")
    buys = lambda r: [x["ticker"] for x in r["trades"] if x["date"] == d and x["side"] == "buy"]  # noqa: E731
    a, b = bt.simulate(ctx, {}, "2016-01", end, pick), bt.simulate(ctx2, {}, "2016-01", end, pick)
    assert buys(a) and buys(a) == buys(b)


def test_random_up_draws_from_uptrend_pool():
    closes, spy, groups = _closes()
    ctx = _ctx(closes, spy, groups)
    led = bt.simulate(ctx, {}, "2016-01", "2017-06", "v3")["ledger"]
    n_path = dict(zip(led["date"], led["n_buys"]))
    rnd = bt.simulate(ctx, {"weights": "W1"}, "2016-01", "2017-06", "random_up", np.random.default_rng(1), n_path=n_path)
    for t in bt._months(ctx, "2016-01", "2017-06"):
        got = [x["ticker"] for x in rnd["trades"] if x["date"] == ctx["exec"][t] and x["side"] == "buy"]
        assert set(got) <= set(bt.uptrend(bt.scored_at(ctx, t, "W1")))
    assert (rnd["ledger"]["n_buys"] <= led["n_buys"]).all()


def test_h2_h1r_phases_and_report_on_synthetic(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from lab import run
    closes, spy, groups = _closes()
    bench = pd.DataFrame({"SPY": spy, "QQQ": spy * 1.1, "SMH": spy * 0.9})
    ctx = bt.build_ctx(closes, spy, groups, start="2016-01", end="2026-08", bench=bench)
    ctx |= {"tickers": list(closes.columns), "members": None}
    monkeypatch.setattr(run, "load_ctx", lambda *a, **k: ctx)
    for k, v in {"RESULTS": tmp_path, "LAB": tmp_path, "PIT": tmp_path, "N_RANDOM": 8, "H1": ("2016-01", "2017-09"),
                 "H2_IS": ("2016-01", "2016-09"), "H2_OOS": ("2016-10", "2017-09"), "FULL": ("2016-01", "2017-09"),
                 "VARS": ("V3",)}.items():
        monkeypatch.setattr(run, k, v)
    (tmp_path / "DECISIONS.md").write_text("")
    args = SimpleNamespace(force=False, smoke=False)
    run.phase_h2(args)
    h2 = json.loads((tmp_path / "h2.json").read_text())
    assert [r["name"] for r in h2["is"]] == ["V3", run.B0R, run.EWN] and h2["selected"] == "V3"
    assert [r["name"] for r in h2["oos"]][:3] == ["V3", run.B0R, run.EWN] and "random" in h2
    assert set(h2["gates"]) == {"beats_b0r_is", "beats_b0r_oos", "ge_ew_pit_oos", "sp500_cogate", "ge_p60_random"}
    assert h2["ship"] == ("V3" if all(h2["gates"].values()) else "B0R")
    with pytest.raises(SystemExit):
        run.phase_h2(args)  # write-once
    run.phase_h1r(args)
    rob = json.loads((tmp_path / "h1_robust.json").read_text())
    assert set(run.ROBUST) | {f"{r}_stress" for r in run.ROBUST} <= set(rob)
    assert [r["name"] for r in rob["ai_pit_ra"]["rows"]] == [run.RULE, run.EWN, "DCA SMH", "DCA QQQ"]
    run.phase_report(args)
    md = (tmp_path / "REPORT.md").read_text()
    assert "## H2 variants (ADR-007)" in md and "Ship: " in md and "## H1 robustness (context only)" in md


def test_robustness_universe_rule_recomputes_eligible_text():
    from research.pit_universe import build

    def sc(cik, sic, words, **hits):
        return {"cik": str(cik), "acc": "a", "filed": "2014-03-01", "sic_filing": sic, "in_group": "1",
                "eligible_text": "0", "words": str(words),
                **{f"hits_{g}": str(hits.get(g, 0)) for g in ("compute", "ai", "network", "power", "cooling")}}
    scores = [sc(1, "3674", 10000, ai=3), sc(2, "3674", 10000, compute=2, network=3), sc(3, "4911", 10000, power=2),
              sc(4, "4911", 10000, compute=9), sc(5, "7372", 10000, cooling=1, ai=1)]
    ciks = lambda rule: [r["cik"] for r in build.eligible(scores, rule=rule)]  # noqa: E731
    assert ciks(None) == []  # default: the frozen eligible_text column
    assert ciks((2.0, build.GROUPS)) == [1, 2, 3, 5]  # R-A: total >= 2; utilities: power >= 2 (CIK 4: power 0)
    assert ciks((5.0, ["compute", "network"])) == [2, 4]  # R-B: compute + network >= 5, utilities included
    assert ciks((5.0, build.GROUPS)) == [2, 3]  # the frozen rule (utility 3: power 2/10k)
