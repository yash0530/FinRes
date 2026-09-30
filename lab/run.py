"""Lab runner: python -m lab.run {data|sanity|is|oos|report} [--force] [--smoke]. Pre-registered in ADR-004/004a."""
import bisect
import csv
import gzip
import json
import multiprocessing as mp
import pickle
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from finres import config, db, edgar, prices, signals
from lab import backtest as bt

LAB = Path(__file__).resolve().parent
DATA, RESULTS = LAB / "data", LAB / "results"
LAB_DB = DATA / "lab.db"
DATA.mkdir(exist_ok=True)  # git-ignored: a fresh clone doesn't have it
HISTORY = LAB / "sp500_history.csv"
HISTORY_SRC = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
               "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")
HISTORY_FETCHED = "2026-09-27"
BENCH = ["SPY", "QQQ", "SMH"]
IS, OOS, FULL = ("2013-01", "2018-12"), ("2019-01", "2026-08"), ("2013-01", "2026-08")
SELLS, GATES, WEIGHTS = ["S1", "S2", "S3"], ["G0", "G1"], ["W3", "W1", "W2"]  # listed in tie-break order
GRID = [{"weights": w, "gate": g, "sell": s, "buy": b}
        for w in ["W1", "W2", "W3"] for g in ["G1", "G0"] for s in SELLS for b in ["Nvar", "N3"]]
SANITY_CFG = {"weights": "W1", "gate": "G1", "sell": "S2", "buy": "Nvar"}
N_RANDOM = 1000
PIT = LAB.parent / "research" / "pit_universe"
H1, REV_FLOOR, STRESS_PAD = ("2010-01", "2026-08"), 100e6, 280  # ADR-007
RULE, EWN = "rule (ew_trend10)", "EW (ew_all)"
H2_IS, H2_OOS, VARS, B0R = ("2010-01", "2017-12"), ("2018-01", "2026-08"), ("V1", "V2", "V3"), "B0R"  # ADR-007 H2
ROBUST = ("ai_pit_ra", "ai_pit_rb")  # ADR-007a: R-A threshold 2; R-B compute + network groups only


def name(cfg: dict) -> str:
    return f"{cfg['weights']}-{cfg['gate']}-{cfg['sell']}-{cfg['buy']}"


def dash(t: str) -> str:
    return t.strip().upper().replace(".", "-")


# ---------- universes ----------

def ai_universe() -> tuple[list[str], dict]:
    uni = config.load_universe()
    groups = dict(uni["ticker_group"]) | {t: "laggards" for t in uni["laggards"] if t not in uni["ticker_group"]}
    return list(dict.fromkeys(uni["tickers"] + uni["laggards"])), groups


def sp_history() -> tuple[list[pd.Timestamp], list[set]]:
    with open(HISTORY, newline="") as f:
        rows = list(csv.DictReader(f))
    return [pd.Timestamp(r["date"]) for r in rows], [{dash(t) for t in r["tickers"].split(",") if t} for r in rows]


def sp_members_fn():
    dates, sets = sp_history()
    return lambda t: sets[bisect.bisect_right(dates, pd.Timestamp(t)) - 1]


def sp_union(since: str = "2012-01-01") -> list[str]:
    dates, sets = sp_history()
    i0 = max(bisect.bisect_right(dates, pd.Timestamp(since)) - 1, 0)
    return sorted(set().union(*sets[i0:]))


def sp_groups() -> dict:
    with open(LAB / "sp500.csv", newline="") as f:
        sectors = {dash(r["ticker"]): r["sector"] for r in csv.DictReader(f)}
    return {t: sectors.get(t, "other") for t in sp_union()}


# ---------- data phase ----------

def facts(t: str) -> dict | None:
    """Cached companyfacts only (no network in simulation phases). C{cik} -> the PIT research cache, by CIK."""
    if t[:1] == "C" and t[1:].isdigit():
        p = PIT / "data" / "facts" / f"{t[1:]}.json.gz"
        f = json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
        return f if f.get("facts", {}).get("us-gaap") else None
    return edgar.companyfacts(t, max_age_days=float("inf")) if (edgar.EDGAR_DIR / f"{t}.json.gz").exists() else None


def phase_data(args) -> None:
    DATA.mkdir(exist_ok=True), RESULTS.mkdir(exist_ok=True)
    ai, _ = ai_universe()
    sp = sp_union()
    tickers = list(dict.fromkeys(ai + sp + BENCH))
    t0 = time.time()
    closes, failed = prices.download(tickers, period="max")
    conn = db.connect(LAB_DB)
    prices.store(conn, closes)
    print(f"prices: {closes.shape[1]} tickers stored, {len(failed)} failed ({time.time() - t0:.0f}s): {failed}")
    no_facts, errors, err_t = [], [], []
    for i, t in enumerate(t for t in tickers if t not in BENCH):
        try:
            if edgar.companyfacts(t) is None:
                no_facts.append(t)
        except Exception as e:  # noqa: BLE001 - report and continue
            errors.append(f"{t}: {type(e).__name__}")
            err_t.append(t)
        if i % 100 == 0:
            print(f"  edgar {i}/{len(tickers)} ({time.time() - t0:.0f}s)", flush=True)
    for p in DATA.glob("ctx_*.pkl"):
        p.unlink()
    have = set(prices.load_closes(conn).columns)
    first = conn.execute("SELECT ticker, MIN(d) FROM prices GROUP BY ticker").fetchall()
    first = {r[0]: r[1][:4] for r in first}
    members = sp_members_fn()
    cov = {"history_source": HISTORY_SRC, "history_fetched": HISTORY_FETCHED, "failed_prices": failed,
           "edgar_errors": errors}
    for label, uni in (("ai", ai), ("sp500", sp)):
        years = pd.Series([first[t] for t in uni if t in first]).value_counts().sort_index()
        cov[label] = {"tickers": len(uni), "with_prices": sum(t in have for t in uni),
                      "with_facts": sum(t not in no_facts and t not in err_t for t in uni if t in have),
                      "first_price_year": {str(k): int(v) for k, v in years.items()}}
    cov["sp500"]["members_without_prices_by_year"] = {
        str(y): len([t for t in members(pd.Timestamp(f"{y}-06-30")) if t not in have]) for y in range(2013, 2027)}
    print(json.dumps({k: v for k, v in cov.items() if k != "failed_prices"}, indent=1))
    (RESULTS / "data.json").write_text(json.dumps(cov, indent=1))


# ---------- contexts ----------

def _fund_rows(args):
    t, days = args
    f = facts(t)
    return t, [edgar.fundamentals(f, d) if f else None for d in days]


def fund_panel(tickers, months) -> dict:
    """{month: {ticker: fundamentals}} computed point-in-time at each month-end date."""
    days = [m.date() for m in months]
    with ProcessPoolExecutor(mp_context=mp.get_context("fork")) as ex:
        rows = dict(ex.map(_fund_rows, [(t, days) for t in tickers], chunksize=8))
    return {m: {t: rows[t][i] for t in tickers} for i, m in enumerate(months)}


def pit_rows(tag: str = "") -> list[dict]:
    with open(PIT / f"universe{tag}.csv", newline="") as f:
        return list(csv.DictReader(f))


def pit_members(by_year: dict, panel: dict, months) -> dict:
    """{t: members}: eligible in year(t) AND point-in-time TTM revenue >= $100M at t (no facts -> not a member)."""
    return {t: {k for k in by_year.get(t.year, ()) if ((panel[t].get(k) or {}).get("revenue_ttm") or 0) >= REV_FLOOR}
            for t in months}


def stress_closes(closes: pd.DataFrame, ctx: dict, rows: list[dict]) -> pd.DataFrame:
    """ADR-007 delisting STRESS TEST: each unpriced PIT company = the daily EW index of the priced PIT members eligible
    at the previous month-end, from STRESS_PAD trading days before its first eligible year (so the 273-day rule passes)
    to its last date (tiingo_end, else last 10-K + 365 d), which is one -55% (Nasdaq) / -30% day; no prices after."""
    ms = ctx["months"]
    k = np.clip(np.searchsorted(pd.DatetimeIndex(ms), closes.index, side="left") - 1, 0, None)
    rets = closes.pct_change(fill_method=None)
    ew = pd.concat([rets.loc[k == j, ctx["elig"][ms[j]]].mean(axis=1) for j in np.unique(k)]).sort_index()
    level, by, out = 100 * (1 + ew.fillna(0)).cumprod(), defaultdict(list), {}
    for r in (r for r in rows if f"C{r['cik']}" not in closes):
        by[r["cik"]].append(r)
    for cik, rs in by.items():
        ends = [r["tiingo_end"] for r in rs if r["tiingo_end"]]
        end = pd.Timestamp(max(ends)) if ends else pd.Timestamp(max(r["last_10k"] for r in rs)) + pd.Timedelta(days=365)
        i0 = max(level.index.searchsorted(pd.Timestamp(f"{min(int(r['year']) for r in rs)}-01-01")) - STRESS_PAD, 0)
        s = level.iloc[i0:level.index.searchsorted(end, side="right")].copy()
        if len(s) > 1 and end <= level.index[-1]:
            s.iloc[-1] = s.iloc[-2] * (0.45 if any(r["exchange"] == "NASDAQ" for r in rs) else 0.70)
        out[f"C{cik}"] = s
    return pd.concat([closes, pd.DataFrame(out)], axis=1)


def load_ctx(label: str, tickers=None, start=FULL[0], end=FULL[1], cache=True) -> dict:
    path = DATA / f"ctx_{label}.pkl"
    if cache and path.exists() and "var" in (ctx := pickle.loads(path.read_bytes())):
        return ctx  # a cache without the H2 variant signals is rebuilt
    t0 = time.time()
    conn = db.connect(LAB_DB)
    ai, groups = ai_universe()
    members = None
    if label == "sp500":
        groups, members = sp_groups(), sp_members_fn()
        tickers = sp_union()
    base = label.removesuffix("_stress")  # ai_pit[_ra|_rb][_stress]
    if label.startswith("ai_pit"):  # ADR-007: prices as C{cik}; members = eligible in year(t) and revenue floor at t
        rows, by_year = pit_rows(base[len("ai_pit"):]), defaultdict(set)
        groups = {f"C{r['cik']}": r["group"] for r in rows}
        for r in rows:
            by_year[int(r["year"])].add(f"C{r['cik']}")
        tickers = [t for (t,) in conn.execute("SELECT DISTINCT ticker FROM prices") if t in groups]
        members = lambda t: mem[t]  # noqa: E731 - mem is computed below from the PIT fundamentals panel
    tickers = tickers or ai
    closes = prices.load_closes(conn, list(tickers))
    if label.startswith("ai_pit") and label.endswith("_stress"):
        closes = stress_closes(closes, load_ctx(base, start=start, end=end), rows)
    bench = prices.load_closes(conn, BENCH)
    months = [m for m in signals.month_ends(bench["SPY"].dropna().index) if start <= m.strftime("%Y-%m") <= end]
    panel = fund_panel(list(closes.columns), months)
    mem = pit_members(by_year, panel, months) if label.startswith("ai_pit") else None
    ctx = bt.build_ctx(closes, bench["SPY"], groups, fund=panel.get, members=members, start=start, end=end,
                       bench=bench) | {"tickers": list(closes.columns), "members": mem}
    bt.warm(ctx, ["W1", "W2", "W3"] if label in ("ai", "smoke") else [])
    print(f"ctx {label}: {closes.shape[1]} tickers, {len(ctx['months'])} months ({time.time() - t0:.0f}s)")
    if cache:
        path.write_bytes(pickle.dumps(ctx))
    return ctx


def eligible_by_year(ctx, start, end) -> dict:
    s = pd.Series({t: len(ctx["elig"][t]) for t in bt._months(ctx, start, end)})
    return {str(y): [int(g.min()), int(g.median()), int(g.max())] for y, g in s.groupby(s.index.year)}


def row(label, res, bench=None) -> dict:
    return {"name": label, **bt.metrics(res["ledger"], bench), "unscored_months": int(res["ledger"]["unscored"].sum())
            if "unscored" in res["ledger"] else 0}


def _write(path: Path, obj, force: bool) -> None:
    if path.exists() and not force:
        sys.exit(f"{path} exists; refusing to overwrite without --force")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str))
    print(f"wrote {path}")


# ---------- phases ----------

def sanity(ctx: dict, closes: pd.DataFrame, start: str, end: str) -> dict:
    """ADR-004 sanity (a) random-signal ranker percentile, (b) EWU's first 3 months vs an independent recomputation."""
    t0 = time.time()
    # ADR-004d: one random-signal ranker's percentile is itself ~Uniform(0,100) under the null, so a single draw
    # fails a 35-65 band ~70% of the time. Use the MEAN percentile over 50 random-signal seeds (expect ~50).
    pcts, xs = [], []
    for seed in range(7, 57):
        ranked = bt.simulate(ctx, SANITY_CFG, start, end, "rank", np.random.default_rng(seed), rng_signal=True)
        n_path = dict(zip(ranked["ledger"]["date"], ranked["ledger"]["n_buys"]))
        xs.append(bt.ledger_xirr(ranked["ledger"]))
        pcts.append(bt.percentile(xs[-1], bt.random_xirrs(ctx, SANITY_CFG, start, end, n_path, 200, seed=seed)))
    x, pctl = float(np.mean(xs)), float(np.mean(pcts))
    print(f"(a) random-signal ranker {name(SANITY_CFG)}: mean XIRR {x:.2%}; mean percentile over 50 seeds "
          f"(200 random portfolios each) = {pctl:.1f} (expect 35-65) -> {'PASS' if 35 <= pctl <= 65 else 'FAIL'} "
          f"[{time.time() - t0:.0f}s]")
    m3, mem = bt._months(ctx, start, end)[:3], ctx.get("members")
    ew = bt.simulate(ctx, {}, m3[0].strftime("%Y-%m"), m3[-1].strftime("%Y-%m"), "ew_all")
    table, check = {}, []
    held, cash = {}, 0.0
    for t in m3:  # independent recomputation from raw closes (PIT: membership is an input)
        h = closes.loc[:t]
        d = closes.index[closes.index > t][0]
        last = h.iloc[-5:].ffill().iloc[-1]
        el = [k for k in closes.columns if h[k].notna().sum() >= 273 and last[k] > 0 and (mem is None or k in mem[t])]
        px = closes.ffill().loc[d]
        for k in el:
            bought = (2500 + cash) / len(el) * (1 - bt.COST) / px[k]
            held[k] = held.get(k, 0) + bought
            table.setdefault(k, []).append(f"{d:%Y-%m-%d} ${2500 / len(el):.2f} @ {px[k]:.4f} = {bought:.6f} sh")
        cash = 0.0 if el else cash + 2500  # no eligible name: the month's money is carried as cash
        check.append(sum(s * px[k] for k, s in held.items()) + cash)
    print("(b) EWU first 3 months: ticker | fill date, $ per name @ fill close = shares bought (15 bps cost)")
    for k in sorted(table):
        print(f"    {k:6s} | " + " | ".join(table[k]))
    got = ew["ledger"]["value"].tolist()
    ok_b = np.allclose(got, check, rtol=1e-9)
    print(f"    ledger values {np.round(got, 2).tolist()} vs recomputation {np.round(check, 2).tolist()} -> "
          f"{'PASS' if ok_b else 'FAIL'}")
    return {"random_signal_xirr": x, "random_signal_percentile": pctl, "pass_a": 35 <= pctl <= 65,
            "ewu_values": got, "ewu_recomputed": check, "pass_b": bool(ok_b)}


def phase_sanity(args) -> None:
    out = sanity(load_ctx("ai"), prices.load_closes(db.connect(LAB_DB), ai_universe()[0]), *FULL)
    tests = ["tests/test_lab.py", "tests/test_signals.py", "tests/test_edgar_pit.py"]
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests, "-k", "lookahead or filed_on_or_after"],
                       capture_output=True, text=True, cwd=LAB.parent)
    print("(c) lookahead tests:", r.stdout.strip().splitlines()[-1])
    out["pass_c"] = r.returncode == 0
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "sanity.json").write_text(json.dumps(out, indent=1))


def select(rows: list[dict], ewu_dd: float) -> dict:
    """Highest IS XIRR among configs whose maxDD is not > 10 pp worse than EWU's; ties S1<S2<S3, G0<G1, W3<W1<W2."""
    ok = [r for r in rows if r["maxdd"] >= ewu_dd - 0.10]
    key = lambda r: (-round(r["xirr"], 10), SELLS.index(r["cfg"]["sell"]), GATES.index(r["cfg"]["gate"]),  # noqa
                     WEIGHTS.index(r["cfg"]["weights"]), r["name"])
    return min(ok, key=key) if ok else None


def phase_is(args) -> None:
    start, end = IS
    if args.smoke:
        start, end = "2015-01", "2016-12"
        ctx = load_ctx("smoke", ai_universe()[0][:60], "2015-01", "2016-12", cache=False)
    else:
        if (RESULTS / "is.json").exists() and not args.force:
            sys.exit("lab/results/is.json exists; refusing to overwrite without --force")
        ctx = load_ctx("ai")
    t0 = time.time()
    ewu = bt.simulate(ctx, {}, start, end, "ew_all")
    b0 = bt.simulate(ctx, {}, start, end, "ew_trend")
    rows = []
    for cfg in GRID:
        rows.append({**row(name(cfg), bt.simulate(ctx, cfg, start, end), ewu["ledger"]), "cfg": cfg})
    ref = [row("EWU", ewu, ewu["ledger"]), row("B0", b0, ewu["ledger"])]
    best = select(rows, ref[0]["maxdd"])
    out = {"period": [start, end], "rows": sorted(rows, key=lambda r: -r["xirr"]), "reference": ref,
           "selected": best["name"] if best else None, "selected_cfg": best["cfg"] if best else None,
           "eligible_by_year": eligible_by_year(ctx, start, end), "runtime_s": round(time.time() - t0, 1)}
    for r in out["rows"] + ref:
        print(f"{r['name']:16s} XIRR {r['xirr']:7.2%}  CAGR {r['cagr']:7.2%}  maxDD {r['maxdd']:7.2%}  "
              f"turn {r['turnover']:.2f}  NW t {r.get('nw_t', float('nan')):5.2f}")
    print(f"selected: {out['selected']}  ({out['runtime_s']}s)")
    _write(Path("/tmp/finres_is_smoke.json") if args.smoke else RESULTS / "is.json", out, args.force or args.smoke)


def phase_oos(args) -> None:
    is_ = json.loads((RESULTS / "is.json").read_text())
    if (RESULTS / "oos.json").exists() and not args.force:
        sys.exit("lab/results/oos.json exists; refusing to overwrite without --force")
    cfg, (start, end) = is_["selected_cfg"], OOS
    ctx = load_ctx("ai")
    t0 = time.time()
    sel = bt.simulate(ctx, cfg, start, end)
    ewu, b0 = bt.simulate(ctx, {}, start, end, "ew_all"), bt.simulate(ctx, {}, start, end, "ew_trend")
    n_path = dict(zip(sel["ledger"]["date"], sel["ledger"]["n_buys"]))
    rand = bt.random_xirrs(ctx, cfg, start, end, n_path, N_RANDOM, seed=11, pick="random")
    rand_all = bt.random_xirrs(ctx, cfg, start, end, n_path, N_RANDOM, seed=13, pick="random_all")
    rows = [row(name(cfg), sel, ewu["ledger"]), row("B0", b0, ewu["ledger"]), row("EWU", ewu, ewu["ledger"])]
    rows += [{**row(f"DCA {b}", {"ledger": bt.dca(ctx, b, start, end)}, ewu["ledger"])} for b in BENCH]
    sp = load_ctx("sp500")
    sp_sel, sp_ewu = bt.simulate(sp, cfg, *FULL), bt.simulate(sp, {}, *FULL, "ew_all")
    sp_rows = [row(name(cfg), sp_sel, sp_ewu["ledger"]), row("EWU", sp_ewu, sp_ewu["ledger"])]
    x = rows[0]["xirr"]
    rp = {"percentile": bt.percentile(x, rand), "p60": float(np.percentile(rand, 60)),
          "p75": float(np.percentile(rand, 75)), "median": float(np.median(rand)),
          "all_percentile": bt.percentile(x, rand_all), "all_p60": float(np.percentile(rand_all, 60)),
          "all_p75": float(np.percentile(rand_all, 75)), "all_median": float(np.median(rand_all))}
    rule = {"beats_b0": x > rows[1]["xirr"], "ge_p60_random": x >= rp["p60"],
            "sp500_cogate": sp_rows[0]["xirr"] >= sp_rows[1]["xirr"]}
    out = {"period": [start, end], "selected": name(cfg), "rows": rows, "random": rp, "sp500": sp_rows,
           "rule": rule, "ship": name(cfg) if all(rule.values()) else "B0",
           "eligible_by_year": eligible_by_year(ctx, *FULL), "sp500_eligible_by_year": eligible_by_year(sp, *FULL),
           "runtime_s": round(time.time() - t0, 1)}
    print(json.dumps({k: out[k] for k in ("random", "rule", "ship", "runtime_s")}, indent=1))
    _write(RESULTS / "oos.json", out, args.force)


def phase_fidelity(args) -> None:
    """Post-hoc IMPLEMENTATION check (ADR-005), not a selection: does the practical 10-names/month
    rotation ('ew_trend10') track the validated B0 ('ew_trend') it approximates?"""
    out = {"note": "post-hoc implementation-fidelity check; B0 was chosen by the pre-registered rule, not by this"}
    for label, periods in (("ai", {"IS": IS, "OOS": OOS, "FULL": FULL}), ("sp500", {"FULL": FULL})):
        ctx = load_ctx(label)
        for per, (a, b) in periods.items():
            ewu = bt.simulate(ctx, {}, a, b, "ew_all")
            out[f"{label}_{per}"] = [row(p_, bt.simulate(ctx, {}, a, b, p_), ewu["ledger"])
                                     for p_ in ("ew_trend", "ew_trend10", "ew_all")]
            print(label, per, " | ".join(f"{r['name']} {r['xirr']:.2%} dd {r['maxdd']:.1%}" for r in out[f"{label}_{per}"]))
    ctx = load_ctx("ai")
    runs = {"rule (ew_trend10)": bt.simulate(ctx, {}, *FULL, "ew_trend10")["ledger"],
            "EWU": bt.simulate(ctx, {}, *FULL, "ew_all")["ledger"], "DCA SMH": bt.dca(ctx, "SMH", *FULL)}
    out["yearly_ai"] = {k: bt.yearly(v) for k, v in runs.items()}
    _write(RESULTS / "fidelity.json", out, True)


def h1_block(ctx: dict, dca: bool = True) -> dict:
    a, b = H1
    led = {RULE: bt.simulate(ctx, {}, a, b, "ew_trend10")["ledger"], EWN: bt.simulate(ctx, {}, a, b, "ew_all")["ledger"]}
    led |= {f"DCA {k}": bt.dca(ctx, k, a, b) for k in ("SMH", "QQQ") if dca}
    return {"rows": [row(k, {"ledger": v}, led[EWN]) for k, v in led.items()],
            "yearly": {k: bt.yearly(v) for k, v in led.items()}, "eligible_by_year": eligible_by_year(ctx, a, b)}


def phase_h1(args) -> None:
    """ADR-007 H1: one run, no selection. Sanity on ai_pit first; the run does not count if it fails."""
    path = RESULTS / "h1.json"
    if path.exists() and not args.force:
        sys.exit(f"{path} exists; refusing to overwrite without --force")
    pit = load_ctx("ai_pit", start=H1[0], end=H1[1])
    closes = prices.load_closes(db.connect(LAB_DB), pit["tickers"])
    first = next(t for t in pit["months"] if len(pit["elig"][t]) >= 10).strftime("%Y-%m")  # early months ~empty
    out = {"period": H1, "sanity": sanity(pit, closes, *H1), "sanity_from": [first, sanity(pit, closes, first, H1[1])]}
    if not all(s["pass_a"] and s["pass_b"] for s in (out["sanity"], out["sanity_from"][1])):
        _write(path, out | {"status": "SANITY FAILED: H1 not counted"}, True)
        sys.exit("sanity failed on ai_pit; H1 not counted")
    stress = load_ctx("ai_pit_stress", start=H1[0], end=H1[1])
    out |= {"ai_pit": h1_block(pit), "ai_pit_stress": h1_block(stress), "stress_names": len(stress["tickers"]) - len(
        pit["tickers"]), "ai_hand": h1_block(load_ctx("ai_2010", ai_universe()[0], *H1), dca=False)}
    x = lambda k, n: next(r["xirr"] for r in out[k]["rows"] if r["name"] == n)  # noqa: E731
    out["headline"] = {"rule_minus_smh_priced": x("ai_pit", RULE) - x("ai_pit", "DCA SMH"),
                       "rule_minus_smh_stressed": x("ai_pit_stress", RULE) - x("ai_pit_stress", "DCA SMH"),
                       "rule_minus_ew_pit": x("ai_pit", RULE) - x("ai_pit", EWN),
                       "bias_hand_minus_pit": x("ai_hand", RULE) - x("ai_pit", RULE)}
    print(json.dumps(out["headline"], indent=1))
    _write(path, out, True)


def phase_h2(args) -> None:
    """ADR-007 H2: V1-V3 in-sample on ai_pit; only the best IS variant (+ B0R) runs OOS, once; ALL gates or B0R ships."""
    path = RESULTS / "h2.json"
    if path.exists() and not args.force:
        sys.exit(f"{path} exists; refusing to overwrite without --force")
    pit, t0 = load_ctx("ai_pit", start=H1[0], end=H1[1]), time.time()
    sim = lambda ctx, p, per: bt.simulate(ctx, {}, *per, p)  # noqa: E731
    table = lambda ctx, picks, per: (lambda ew: [row(n, sim(ctx, p, per), ew) for n, p in picks])(  # noqa: E731
        sim(ctx, "ew_all", per)["ledger"])
    is_rows = table(pit, [(v, v.lower()) for v in VARS] + [(B0R, "ew_trend10"), (EWN, "ew_all")], H2_IS)
    best = max(is_rows[:len(VARS)], key=lambda r: r["xirr"])  # ties -> the first listed (V1 < V2 < V3)
    v = best["name"]
    oos = table(pit, [(v, v.lower()), (B0R, "ew_trend10"), (EWN, "ew_all")], H2_OOS)
    oos += [row(f"DCA {k}", {"ledger": bt.dca(pit, k, *H2_OOS)}) for k in ("SMH", "QQQ")]
    sp = table(load_ctx("sp500"), [(v, v.lower()), (EWN, "ew_all")], FULL)
    x = lambda rows, n: next(r["xirr"] for r in rows if r["name"] == n)  # noqa: E731
    gates = {"beats_b0r_is": x(is_rows, v) > x(is_rows, B0R), "beats_b0r_oos": x(oos, v) > x(oos, B0R),
             "ge_ew_pit_oos": x(oos, v) >= x(oos, EWN), "sp500_cogate": x(sp, v) >= x(sp, EWN)}
    out = {"is_period": H2_IS, "oos_period": H2_OOS, "is": is_rows, "selected": v, "oos": oos, "sp500": sp}
    if v == "V3":  # same pool = the B0R uptrend names, same number of buys per month as V3
        led = sim(pit, "v3", H2_OOS)["ledger"]
        rand = bt.random_xirrs(pit, {"weights": "W1"}, *H2_OOS, dict(zip(led["date"], led["n_buys"])), N_RANDOM,
                               seed=17, pick="random_up")
        out["random"] = {"percentile": bt.percentile(x(oos, v), rand), "p60": float(np.percentile(rand, 60)),
                         "median": float(np.median(rand))}
        gates["ge_p60_random"] = x(oos, v) >= out["random"]["p60"]
    out |= {"gates": gates, "ship": v if all(gates.values()) else B0R, "runtime_s": round(time.time() - t0, 1)}
    print(json.dumps({k: out[k] for k in ("selected", "gates", "ship", "runtime_s")}, indent=1))
    _write(path, out, True)


def phase_h1r(args) -> None:
    """ADR-007a robustness (reported only, never a selection): H1 on the R-A / R-B universes, priced and stressed."""
    path = RESULTS / "h1_robust.json"
    if path.exists() and not args.force:
        sys.exit(f"{path} exists; refusing to overwrite without --force")
    out = {"period": H1, "note": "ADR-007a robustness: reported only, never used for selection"}
    for label in [x for r in ROBUST for x in (r, f"{r}_stress")]:
        ctx = load_ctx(label, start=H1[0], end=H1[1])
        out[label] = h1_block(ctx) | {"names": len(ctx["tickers"])}
        print(label, " | ".join(f"{r['name']} {r['xirr']:.1%}" for r in out[label]["rows"]))
    _write(path, out, True)


# ---------- report ----------

def _p(x) -> str:
    return "–" if x is None or pd.isna(x) else f"{x:.1%}"


def _table(rows) -> list[str]:
    out = ["| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['name']} | {_p(r['xirr'])} | {_p(r['cagr'])} | {_p(r['vol'])} | {_p(r['maxdd'])} | "
                   f"{r['sharpe']:.2f} | {r['turnover']:.2f} | {r.get('nw_t', float('nan')):.2f} |")
    return out


def phase_report(args) -> None:
    load = lambda f: json.loads((RESULTS / f).read_text()) if (RESULTS / f).exists() else None  # noqa: E731
    data, san, is_, oos = load("data.json"), load("sanity.json"), load("is.json"), load("oos.json")
    md = ["# FinRes validation lab — report", "",
          "Method: pre-registered in [ADR-004 / ADR-004a](../DECISIONS.md) — 36 configs "
          "({W1,W2,W3} × {G1,G0} × {S1,S2,S3} × {Nvar,N3}), IS 2013-01→2018-12, OOS 2019-01→2026-08, "
          "$2,500/month, signals at month-end close, fills at next-day close, 15 bps per side, cash earns 0.", ""]
    if data:
        md += ["## Data", f"- S&P 500 point-in-time membership: `lab/sp500_history.csv` from {data['history_source']} "
               f"(fetched {data['history_fetched']}).",
               *[f"- {u}: {data[u]['tickers']} tickers, {data[u]['with_prices']} with prices, "
                 f"{data[u]['with_facts']} with EDGAR facts." for u in ("ai", "sp500")],
               f"- S&P members (as of mid-year) without Yahoo prices: {data['sp500']['members_without_prices_by_year']}",
               ""]
    if san:
        md += ["## Sanity checks", f"- Random-signal ranker percentile: {san['random_signal_percentile']:.1f} "
               f"(expect 35–65): {'pass' if san['pass_a'] else 'FAIL'}",
               f"- EWU first 3 months match hand recomputation: {'pass' if san['pass_b'] else 'FAIL'}",
               f"- Lookahead tests: {'pass' if san['pass_c'] else 'FAIL'}", ""]
    if is_:
        md += ["## In-sample (2013-01→2018-12), sorted by XIRR", *_table(is_["rows"] + is_["reference"]), "",
               f"**Selected:** {is_['selected']} (highest IS XIRR with maxDD not >10 pp worse than EWU's).", ""]
    if oos:
        rp = oos["random"]
        md += ["## Out-of-sample (2019-01→2026-08)", *_table(oos["rows"]), "",
               "## Random portfolios (1,000 each)", "| Variant | Selected percentile | Median | 60th (threshold) "
               "| 75th |", "|---|---|---|---|---|",
               f"| Same pool (seed 11) | {rp['percentile']:.1f} | {_p(rp['median'])} | **{_p(rp['p60'])}** | "
               f"{_p(rp['p75'])} |",
               f"| All eligible, no gate (context, seed 13) | {rp['all_percentile']:.1f} | {_p(rp['all_median'])} | "
               f"{_p(rp['all_p60'])} | {_p(rp['all_p75'])} |", "",
               "## S&P 500 co-gate (point-in-time members, 2013-01→2026-08)", *_table(oos["sp500"]), "",
               "## Decision", *[f"- {k}: {'yes' if v else 'no'}" for k, v in oos["rule"].items()],
               f"- **Ship: {oos['ship']}**", ""]
        md += ["## Eligible names per year (min / median / max)", "| Year | AI | S&P 500 |", "|---|---|---|",
               *[f"| {y} | {v} | {oos['sp500_eligible_by_year'].get(y)} |" for y, v in oos["eligible_by_year"].items()],
               ""]
    elif is_:
        md += ["## Eligible names per year, IS (min / median / max)",
               *[f"- {y}: {v}" for y, v in is_["eligible_by_year"].items()], ""]
    fid = RESULTS / "fidelity.json"
    if fid.exists():
        f = json.loads(fid.read_text())
        md += ["## Implementation fidelity (post-hoc, not a selection)",
               "B0 buys every uptrend name each month (~60–130 orders). The app ships the practical version "
               "`ew_trend10`: 10 uptrend names/month, least-held first (≤3 per group), S2 sells. It should track B0.",
               "| Universe · period | B0 (ew_trend) | Practical (ew_trend10) | EWU |", "|---|---|---|---|",
               *[f"| {k.replace('_', ' · ')} | " + " | ".join(f"{r['xirr']:.1%} (dd {r['maxdd']:.0%})" for r in v) + " |"
                 for k, v in f.items() if k not in ("note", "yearly_ai")], ""]
    if fid.exists() and "yearly_ai" in f:
        y = f["yearly_ai"]; names = list(y); years = list(y[names[0]])
        wins = sum(y[names[0]][k] > y["EWU"][k] for k in years)
        md += ["## Year by year, AI universe (time-weighted return; context only)",
               f"The shipped rule beat the equal-weight universe in {wins} of {len(years)} calendar years.",
               "| Year | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|",
               *[f"| {k} | " + " | ".join(f"{y[n][k]:+.1%}" for n in names) + " |" for k in years], ""]
    h1 = load("h1.json")
    if h1 and "headline" in h1:
        hl, s, cov = h1["headline"], h1["sanity"], PIT / "coverage.md"
        adr = (LAB.parent / "DECISIONS.md").read_text()
        md += ["## Hindsight-free re-test (ADR-007)", f"H1, one run, no selection, {h1['period'][0]}→{h1['period'][1]}: "
               "the shipped rule (B0R + S2, `ew_trend10`) on a universe rebuilt each year from the 10-Ks filed the year "
               "before (SIC + frozen dictionary), with PIT revenue ≥ $100M. Sanity on the PIT universe: random-signal "
               f"percentile {s['random_signal_percentile']:.1f}, EW first 3 months {'pass' if s['pass_b'] else 'FAIL'}; "
               f"repeated from {h1['sanity_from'][0]} (first month with ≥ 10 eligible names): percentile "
               f"{h1['sanity_from'][1]['random_signal_percentile']:.1f}, EW first 3 months "
               f"{'pass' if h1['sanity_from'][1]['pass_b'] else 'FAIL'}.", ""]
        for k, title in (("ai_pit", "PIT universe, priced names only"), ("ai_pit_stress", f"STRESS TEST: PIT + "
                         f"{h1['stress_names']} unpriced names as EW-index clones ending in a −55% (Nasdaq) / −30% day"),
                         ("ai_hand", "2026 hand-picked AI universe, same period (bias estimate)")):
            md += [f"### {title}", *_table(h1[k]["rows"]), ""]
        md += ["Stress clones die on post-t dates (Tiingo end date, else last 10-K + 365 days), known only after the "
               "fact: a stress assumption about how the unpriced names ended, not a signal the rule could trade.", ""]
        md += ["### Headline (XIRR differences)", *[f"- {k}: {v:+.1%}" for k, v in hl.items()],
               "- **The rule's XIRR on the PIT universe was " + ("BELOW monthly SMH DCA. Per ADR-007 the app says so "
               "plainly and Yash decides; there is no automatic switch.**" if hl["rule_minus_smh_priced"] < 0 else
               "at or above monthly SMH DCA.**"), ""]
        y = h1["ai_pit"]["yearly"]; names = list(y)  # noqa: E702
        md += ["### Year by year, PIT priced (time-weighted)", "| Year | " + " | ".join(names) + " |",
               "|---" * (len(names) + 1) + "|", *[f"| {k} | " + " | ".join(f"{y[n][k]:+.1%}" for n in names) + " |"
                                                 for k in y[names[0]]], "",
               "### Eligible names per month-end (min / median / max; < 40 = unscored)",
               "| Year | PIT priced | PIT stress | hand-picked |", "|---|---|---|---|",
               *[f"| {k} | {v} | {h1['ai_pit_stress']['eligible_by_year'].get(k)} | "
                 f"{h1['ai_hand']['eligible_by_year'].get(k)} |" for k, v in h1["ai_pit"]["eligible_by_year"].items()],
               "", "### Coverage", *(cov.read_text().splitlines()[2:] if cov.exists() else []), "",
               "**Honesty (ADR-007).** " + re.search(r"\*\*Honesty \(fixed text for the report\)\.\*\* (.+)", adr)[1], ""]
    h2 = load("h2.json")
    if h2:
        g, sel = h2["gates"], h2["selected"]
        md += ["## H2 variants (ADR-007)", "V1 Faber monthly (month-end close vs 10-month SMA), V2 buffer (enter > SMA200 "
               "× 1.02 with SMA50 > SMA200, exit 2 month-ends < SMA200 × 0.98), V3 FIP (uptrend → top third by 12-2 "
               "momentum → lower half by ID). All use B0R's least-held rotation (10 names, ≤ 3/group) and the −35% stop. "
               "PIT universe, priced names; months with < 40 eligible names are unscored (equal weight into all).", "",
               f"### In-sample {h2['is_period'][0]}→{h2['is_period'][1]}", *_table(h2["is"]), "",
               "Unscored months (< 40 eligible: buys = all eligible names, so only the exits differ): " + "; ".join(
                   f"{p} " + ", ".join(f"{r['name']} {r['unscored_months']}/{r['months']}" for r in h2[p.lower()] if "DCA" not in r['name'])
                   for p in ("IS", "OOS")) + ".", "",
               f"**Best IS variant: {sel}.** Out-of-sample {h2['oos_period'][0]}→{h2['oos_period'][1]} (run once):",
               *_table(h2["oos"]), "", f"### S&P 500 co-gate ({FULL[0]}→{FULL[1]})", *_table(h2["sp500"]), "",
               *([f"Random portfolios (1,000, same uptrend pool): percentile {h2['random']['percentile']:.1f}, "
                  f"60th {_p(h2['random']['p60'])}", ""] if "random" in h2 else []),
               "### Gates", *[f"- {k}: {'yes' if v else 'no'}" for k, v in g.items()], f"- **Ship: {h2['ship']}**", ""]
        b0r = next(r for r in h2["is"] if r["name"] == B0R)
        if b0r["unscored_months"] == b0r["months"]:  # review #2 F3
            md += [f"**H2 in-sample was a null test.** All {b0r['months']} IS months had < 40 eligible names (unscored), "
                   "so every variant bought the same names as B0R and differed only in its exits; `beats_b0r_is` could "
                   "hardly pass by construction. H2 is **inconclusive** (not \"the variants failed\"); the pre-registered "
                   "decision (B0R stays) stands.", ""]
    rob = load("h1_robust.json")
    if rob:
        md += ["## H1 robustness (context only)", "ADR-007a: reported, never used for selection. R-A = same "
               "dictionary, threshold ≥ 2 per 10k words; R-B = compute + network groups only, threshold ≥ 5. "
               f"{rob['period'][0]}→{rob['period'][1]}.", "",
               "| Universe | names | rule (ew_trend10) | EW | DCA SMH | DCA QQQ | rule − EW | unscored months |",
               "|---|---|---|---|---|---|---|---|"]
        for k in [x for r in ROBUST for x in (r, f"{r}_stress")]:
            xs = {r["name"]: r for r in rob[k]["rows"]}
            md.append(f"| {k} | {rob[k]['names']} | " + " | ".join(_p(xs[n]["xirr"]) for n in (RULE, EWN, "DCA SMH",
                      "DCA QQQ")) + f" | {xs[RULE]['xirr'] - xs[EWN]['xirr']:+.1%} | {xs[RULE]['unscored_months']} |")
        md += ["", "Eligible names per month-end (min / median / max):", "| Year | " + " | ".join(ROBUST) + " |",
               "|---|---|---|", *[f"| {y} | {v} | {rob[ROBUST[1]]['eligible_by_year'].get(y)} |"
                                  for y, v in rob[ROBUST[0]]["eligible_by_year"].items()], ""]
    md += ["## Bug-fix log", "",
           "- 2026-09-30 (ADR-009a, review #2): F1: in unscored months (< 40 eligible) the simulator sold S2 trend "
           "failures and re-bought them at the same fill (equal weight into all eligible): 748 of 950 H1 rule sells were "
           "same-month re-buys. A name sold in a month is now never bought that month. F2: `listed` (Yahoo-priced) only "
           "via the CIK's current SEC ticker; index words (CRSP, NASDAQ, …) are no longer read as symbols. 8 PIT CIKs lost "
           "their Yahoo series and are now stress-test names: 6 carried another company's prices (Cray and Zix = CRISPR, "
           "Rockley = Worthington Steel, Lyris = NOV, CoreSite = Cencora, SGI = Somnigroup), ANSYS and the pre-2021 "
           "Marvell CIK have no current SEC ticker; Alphabet (GOOGL) and Rigetti (RGTI, not its warrant) were re-priced. "
           "H1, H1 robustness and H2 were re-run. The hand-picked IS/OOS/fidelity/sanity runs have 0 unscored "
           "months and were not re-run.",
           "- 2026-09-30 (ADR-004d): the $3 eligibility floor was applied to split-adjusted closes, which uses "
           "future splits (lookahead) and wrongly excluded later winners (e.g. NVDA until ~2019). Floor removed; "
           "sanity, IS, OOS, fidelity and H1 were all re-run. Pre-fix results remain in git history.",
           "- 2026-09-30 (ADR-004d): sanity (a) used ONE random-signal ranker, whose percentile is ~Uniform(0,100) "
           "under the null (a 35-65 band fails ~70% by chance). Now the mean percentile over 50 seeds. The EWU hand "
           "recomputation still applied the old $3 floor; aligned.",
           "- Before the IS run: random portfolios changed to honor the ADR-004b group limit "
           "(`model.take_by_group`), so they differ from the ranked strategy only in *which* names are picked.", "",
           "## Honesty",
           "Absolute returns are an upper bound, because of survivorship and hindsight in the universe. About 92 OOS "
           "months cannot prove a modest edge. The lab can catch bugs, disasters and fragility. It cannot prove alpha.",
           ""]
    (LAB / "REPORT.md").write_text("\n".join(md))
    print(f"wrote {LAB / 'REPORT.md'}")


def main(argv=None) -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m lab.run")
    ap.add_argument("phase", choices=["data", "sanity", "is", "oos", "fidelity", "h1", "h2", "h1r", "report"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="is only: 60 AI tickers, 2015-2016, writes to /tmp")
    args = ap.parse_args(argv)
    {"data": phase_data, "sanity": phase_sanity, "is": phase_is, "oos": phase_oos, "fidelity": phase_fidelity, "h1": phase_h1,
     "h2": phase_h2, "h1r": phase_h1r, "report": phase_report}[
        args.phase](args)


if __name__ == "__main__":
    main()
