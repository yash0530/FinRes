"""Monthly DCA simulation on top of the app's pure logic (finres.signals / finres.model), plus metrics.

Timing: signals at month-end close t (rows <= t only); every trade fills at the close of the next trading day.
"""
import os
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

import numpy as np
import pandas as pd

from finres import model, signals

CONTRIB = 2500.0
COST = 0.0015  # 15 bps per side
MIN_SCORED = 40  # fewer eligible names -> unscored month (equal-weight into all eligible)
MAX_N, BRAKE_N = 10, 2  # Nvar: at most 10 names; brake: top 2 only (ADR-004)
CAP_POS, CAP_GROUP, CAP_SPEC_POS, CAP_SPEC_TOTAL, CAPS_FROM = 0.10, 0.30, 0.03, 0.10, 25_000
SLIM = ["eligible", "composite", "trend", "earnings_yield", "speculative", "sma200", "above200", "close"]
PICKS = ("rank", "random", "random_all", "ew_all", "ew_trend", "ew_trend10")


# ---------- precompute ----------

def build_ctx(closes: pd.DataFrame, spy: pd.Series, groups: dict, fund=None, members=None,
              start: str = "2013-01", end: str = "2026-08", bench: pd.DataFrame | None = None) -> dict:
    """Per-month factors, marks, next-day fills, regime and prior-month below-200DMA sets.

    closes: universe closes (no benchmarks); spy: SPY closes (defines the trading calendar).
    fund(t) -> {ticker: edgar.fundamentals dict | None} (point-in-time at t.date()); None -> no fundamentals.
    members(t) -> set of index members at t (eligibility additionally requires membership); None -> all.
    """
    spy = spy.dropna()
    closes = closes.reindex(spy.index)
    filled = closes.ffill()
    idx = spy.index
    months = [t for t in signals.month_ends(idx) if start <= t.strftime("%Y-%m") <= end and t < idx[-1]]
    ctx = {"months": months, "exec": {}, "fac": {}, "mark": {}, "px": {}, "reg": {}, "below_prev": {},
           "elig": {}, "groups": groups, "scored": {}, "bench": {}}
    prev_below: set = set()
    before = signals.month_ends(idx[idx <= months[0]]) if months else []
    if len(before) > 1:  # below-200DMA set at the month-end before the first simulated month
        f0 = signals.factors_at(closes, before[-2])
        prev_below = set(f0.index[f0["sma200"].notna() & ~f0["above200"]])
    for t in months:
        d = idx[idx.get_loc(t) + 1]
        fac = signals.factors_at(closes, t)
        fac = fac[fac["n_days"] > 0]
        fd = (fund(t) if fund else None) or {}
        ff = pd.DataFrame([signals.fund_factors(fd.get(k), fac.at[k, "close"]) for k in fac.index], index=fac.index)
        fac = fac.join(ff)
        if members is not None:
            fac["eligible"] &= fac.index.isin(members(t))
        s = spy.loc[:t]
        ctx["reg"][t] = model.regime(fac, float(s.iloc[-1]), float(s.iloc[-200:].mean()) if len(s) >= 200 else None)
        ctx["exec"][t], ctx["fac"][t] = d, fac
        ctx["mark"][t] = filled.loc[t].dropna().to_dict()
        ctx["px"][t] = filled.loc[d].dropna().to_dict()
        ctx["elig"][t] = list(fac.index[fac["eligible"]])
        ctx["below_prev"][t] = prev_below
        prev_below = set(fac.index[fac["sma200"].notna() & ~fac["above200"]])
        if bench is not None:
            ctx["bench"][t] = bench.reindex(idx).ffill().loc[d].to_dict()
    return ctx


def scored_at(ctx: dict, t, weights: str) -> pd.DataFrame:
    """model.score depends only on (month, weights): cache the slim frame."""
    key = (t, weights)
    if key not in ctx["scored"]:
        ctx["scored"][key] = model.score(ctx["fac"][t], weights, use_revisions=False)[SLIM]
    return ctx["scored"][key]


def warm(ctx: dict, weights) -> None:
    for w in weights:
        for t in ctx["months"]:
            scored_at(ctx, t, w)


# ---------- ranked buy rules (lab only) ----------

def capped(tickers, scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str]) -> set[str]:
    """Names that may receive no new money (caps apply only once the portfolio is >= CAPS_FROM)."""
    port = sum(positions.values())
    if port < CAPS_FROM:
        return set()
    spec = scored["speculative"].to_dict() if "speculative" in scored else {}
    group = lambda t: groups.get(t) or ("_own", t)  # noqa: E731 - unknown group = own group
    by_group: dict = {}
    for t, v in positions.items():
        by_group[group(t)] = by_group.get(group(t), 0.0) + v
    spec_total = sum(v for t, v in positions.items() if spec.get(t, False))
    out = set()
    for t in tickers:
        pos = positions.get(t, 0.0)
        if pos >= CAP_POS * port or by_group.get(group(t), 0.0) >= CAP_GROUP * port or (
                spec.get(t, False) and (pos >= CAP_SPEC_POS * port or spec_total >= CAP_SPEC_TOTAL * port)):
            out.add(t)
    return out


def buy_pool(scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str], cfg: dict) -> pd.DataFrame:
    """Eligible, scored names (Trend-passing under G1), in tie-break order, minus capped names."""
    df = scored[scored["eligible"].astype(bool) & scored["composite"].notna()]
    if cfg["gate"] == "G1":
        df = df[df["trend"].astype(bool)]
    return model.order(df[~df.index.isin(capped(df.index, scored, positions, groups))])


def buy_list(scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str], cfg: dict,
             brake: bool, budget: float) -> list[dict]:
    """The lab's ranked buy rules (Nvar/N3 with caps and brake; moved from finres.model, ADR-008); B0R -> the app's."""
    if cfg["buy"] == "B0R":
        return model.buy_list(scored, positions, groups, cfg, brake, budget)
    pool = buy_pool(scored, positions, groups, cfg)
    if cfg["buy"] == "Nvar":
        pool, limit = pool[pool["composite"] >= model.BUY_ZONE], MAX_N
    elif cfg["buy"] == "N3":
        limit = 3
    else:
        raise ValueError(f"unknown buy rule {cfg['buy']!r}")
    pick = pool.loc[model.take_by_group(list(pool.index), groups, limit)]
    if brake:
        pick = pick.head(BRAKE_N)
    n = len(pick)
    return [{"ticker": t, "dollars": budget / n, "composite": float(r["composite"]), "reason": r.get("reason")}
            for t, r in pick.iterrows()]


# ---------- simulation ----------

def _months(ctx, start, end):
    return [t for t in ctx["months"] if start <= t.strftime("%Y-%m") <= end]


def simulate(ctx: dict, cfg: dict, start: str, end: str, pick: str = "rank", rng=None,
             rng_signal: bool = False, n_path: dict | None = None) -> dict:
    """Monthly loop. Returns {"ledger": DataFrame, "trades": list}.

    pick="random"/"random_all" draw n_path[t] names (the rank strategy's N that month) uniformly from
    buy_pool under cfg (random) or under the same cfg with gate G0 (random_all, context only).
    """
    assert pick in PICKS
    port = {"shares": {}, "basis": {}, "cash": 0.0}
    rows, trades = [], []
    groups = ctx["groups"]
    for t in _months(ctx, start, end):
        mark, px, el, reg = ctx["mark"][t], ctx["px"][t], ctx["elig"][t], ctx["reg"][t]
        shares, basis = port["shares"], port["basis"]
        unscored = len(el) < MIN_SCORED
        if rng_signal:  # sanity (a): the composite is a random percentile over eligible names
            fac = ctx["fac"][t]
            sig = pd.Series(rng.random(len(fac)), index=fac.index)
            scored = model.score(fac, cfg["weights"], use_revisions=False)
            e = scored.index[scored["eligible"]]
            scored["composite"] = model.pct(sig.reindex(e).astype(float)).reindex(scored.index).astype(float)
            scored = model.order(scored)[SLIM]
        else:
            scored = scored_at(ctx, t, cfg.get("weights", "W1"))
        positions = {k: s * mark.get(k, px[k]) for k, s in shares.items()}
        rule = {"ew_all": "S1", "ew_trend": "S2", "ew_trend10": "S2"}.get(pick, cfg.get("sell", "S1"))
        if unscored and rule == "S3":
            rule = "S2"  # no ranking in an unscored month; stop/trend rules still apply
        holdings = {k: {"cost": basis[k] / shares[k]} for k in shares}
        sells = model.sell_list(scored, holdings, mark, ctx["below_prev"][t], {"sell": rule})
        for s in sells:
            positions.pop(s["ticker"])
        n = None  # buy_list's budget only sizes its dicts; the names do not depend on it
        if unscored or pick == "ew_all":
            names = list(el)
        elif pick == "ew_trend":
            names = [k for k in el if bool(ctx["fac"][t].at[k, "trend"])]
        elif pick == "ew_trend10":  # the app's shipped rule (ADR-005), same code path
            names = [b["ticker"] for b in model.buy_list(scored, positions, groups, {"buy": "B0R"}, False, CONTRIB)]
        elif pick == "rank":
            names = [b["ticker"] for b in buy_list(scored, positions, groups, cfg, reg["brake"], CONTRIB)]
        else:
            n = n_path[t]
            pool = buy_pool(scored, positions, groups, cfg if pick == "random" else {**cfg, "gate": "G0"})
            names = model.take_by_group(list(rng.permutation(pool.index.to_numpy())), groups, n) if n else []
        port, tr = model.step(port, [s["ticker"] for s in sells], names, px, CONTRIB, COST)
        why = {s["ticker"]: s["rule"] for s in sells}
        trades += [{"date": ctx["exec"][t], **x, "rule": why[x["ticker"]] if x["side"] == "sell" else pick} for x in tr]
        sold = spent = 0.0  # plain += (Python 3.12 sum() is compensated and would change the last digits)
        for x in tr:
            sold, spent = (sold + x["dollars"], spent) if x["side"] == "sell" else (sold, spent + x["dollars"])
        value = sum(s * px[k] for k, s in port["shares"].items()) + port["cash"]
        rows.append({"date": t, "exec": ctx["exec"][t], "contribution": CONTRIB, "value": value, "cash": port["cash"],
                     "n_buys": sum(x["side"] == "buy" for x in tr), "n_sells": len(sells), "n_draw": n,
                     "turnover": (sold + spent) / value, "eligible": len(el), "brake": bool(reg["brake"]),
                     "unscored": unscored})
    return {"ledger": pd.DataFrame(rows), "trades": trades}


def dca(ctx: dict, ticker: str, start: str, end: str) -> pd.DataFrame:
    """$2,500 into one benchmark at each month's fill date; no costs."""
    sh, rows = 0.0, []
    for t in _months(ctx, start, end):
        p = ctx["bench"][t][ticker]
        sh += CONTRIB / p
        rows.append({"date": t, "exec": ctx["exec"][t], "contribution": CONTRIB, "value": sh * p, "cash": 0.0,
                     "turnover": 0.0, "eligible": np.nan, "brake": False, "unscored": False})
    return pd.DataFrame(rows)


# ---------- metrics ----------

xirr = model.xirr  # moved to finres/model.py (shadow ledgers); same bisection


def twr(ledger: pd.DataFrame) -> np.ndarray:
    """r_m = (V_m - flow_m) / V_{m-1} - 1. V_m is valued right after month m's contribution is invested, so the
    flow sits at the end of the period; first month skipped."""
    v, c = ledger["value"].to_numpy(float), ledger["contribution"].to_numpy(float)
    return (v[1:] - c[1:]) / v[:-1] - 1


def yearly(ledger: pd.DataFrame) -> dict:
    """Calendar-year time-weighted return per year (context for how often a rule wins, not a selection metric)."""
    r = pd.Series(twr(ledger), index=pd.to_datetime(ledger["date"].iloc[1:]))
    return {str(y): float(np.prod(1 + g) - 1) for y, g in r.groupby(r.index.year)}


def ledger_xirr(ledger: pd.DataFrame) -> float:
    flows = -ledger["contribution"].to_numpy(float)
    flows[-1] += ledger["value"].iloc[-1]
    return xirr(list(ledger["exec"]), flows)


def nw_tstat(d, lag: int = 6) -> float:
    """Newey-West (Bartlett) t-statistic of the mean of d."""
    d = np.asarray(d, float)
    n = len(d)
    e = d - d.mean()
    s = e @ e / n
    for j in range(1, lag + 1):
        s += 2 * (1 - j / (lag + 1)) * (e[j:] @ e[:-j]) / n
    return float(d.mean() / np.sqrt(s / n)) if s > 0 else float("nan")


def metrics(ledger: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    r = twr(ledger)
    idx = np.cumprod(1 + r)
    dd = idx / np.maximum.accumulate(np.concatenate([[1.0], idx]))[1:] - 1
    vol = float(np.std(r, ddof=1) * np.sqrt(12)) if len(r) > 1 else float("nan")
    out = {"xirr": ledger_xirr(ledger), "cagr": float(idx[-1] ** (12 / len(r)) - 1) if len(r) else float("nan"),
           "vol": vol, "maxdd": float(min(dd.min(), 0.0)) if len(r) else 0.0,
           "sharpe": float(np.mean(r) * 12 / vol) if vol and vol > 0 else float("nan"),
           "turnover": float(ledger["turnover"].mean()), "final": float(ledger["value"].iloc[-1]),
           "invested": float(ledger["contribution"].sum()), "months": len(ledger)}
    if bench is not None:
        assert list(bench["date"]) == list(ledger["date"])
        out["nw_t"] = nw_tstat(r - twr(bench), 6)
    return out


# ---------- random portfolios (parallel; ctx shared via fork) ----------

_CTX: dict = {}


def _rand_chunk(args):
    cfg, start, end, pick, n_path, seed, ids = args
    return [ledger_xirr(simulate(_CTX["ctx"], cfg, start, end, pick, np.random.default_rng([seed, i]),
                                 n_path=n_path)["ledger"]) for i in ids]


def random_xirrs(ctx, cfg, start, end, n_path, n=1000, seed=11, pick="random", workers=None) -> np.ndarray:
    """XIRR of n random-pick simulations; sim i uses default_rng([seed, i]) so results don't depend on workers."""
    warm(ctx, [cfg["weights"]])
    _CTX["ctx"] = ctx
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    chunks = [list(range(i, n, workers)) for i in range(workers)]
    jobs = [(cfg, start, end, pick, n_path, seed, c) for c in chunks if c]
    if workers == 1:
        res = [_rand_chunk(j) for j in jobs]
    else:
        with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork")) as ex:
            res = list(ex.map(_rand_chunk, jobs))
    out = np.empty(n)
    for c, r in zip([j[-1] for j in jobs], res):
        out[c] = r
    return out


def percentile(x: float, dist) -> float:
    """Share of the distribution strictly below x (ties count half), in percent."""
    d = np.asarray(dist)
    return float(100 * (np.mean(d < x) + 0.5 * np.mean(d == x)))
