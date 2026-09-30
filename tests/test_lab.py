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
