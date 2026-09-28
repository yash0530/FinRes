"""Parity: the app's scoring path == the lab's scoring path at two month-ends (replay semantics).

APP path: state.frame(conn, asof) on the proof DB (EDGAR point-in-time, no revisions).
LAB path: lab.backtest month context for the AI universe (lab.run.load_ctx("ai")), restricted to the app's
universe tickers (laggards dropped) and re-scored with model.score so both rank the same set.
Asserts: same eligible set, same trend flags, composite equal within 1e-9, identical B0R buy list.

Usage: FINRES_DB=/tmp/finres_proof.db PYTHONPATH=. .venv/bin/python -m e2e.parity   (exit 1 on mismatch)
"""
import math
import os
import sys
from contextlib import closing
from datetime import date

import pandas as pd

from finres import config, db, model, state

DATES = ["2022-06-30", "2024-12-31"]
TOL = 1e-9
LAB_DB = config.ROOT / "lab" / "data" / "lab.db"


def _eq(a, b) -> bool:
    a, b = float(a), float(b)
    return (math.isnan(a) and math.isnan(b)) or abs(a - b) <= TOL


def compare(conn, ctx, day: str) -> dict:
    asof = date.fromisoformat(day)
    fr = state.frame(conn, asof)
    app, groups = fr["scored"], fr["u"]["ticker_group"]
    t = pd.Timestamp(day)
    if t not in ctx["fac"]:
        return {"date": day, "ok": False, "why": f"{day} is not a lab month-end ({fr['t'].date()} in app)"}
    uni = [x for x in fr["u"]["tickers"] if x in ctx["fac"][t].index]
    lab_full = model.score(ctx["fac"][t], model.SHIPPED["weights"], use_revisions=False)  # universe + laggards
    lab = model.score(ctx["fac"][t].loc[uni], model.SHIPPED["weights"], use_revisions=False)
    names = sorted(set(app.index) | set(lab.index))
    both = sorted(set(app.index) & set(lab.index))
    el_app = set(app.index[app["eligible"].astype(bool)])
    el_lab = set(lab.index[lab["eligible"].astype(bool)])
    trend_diff = [x for x in both if bool(app.at[x, "trend"]) != bool(lab.at[x, "trend"])]
    comp_diff = [x for x in both if not _eq(app.at[x, "composite"], lab.at[x, "composite"])]
    dcomp = max((abs(app.at[x, "composite"] - lab.at[x, "composite"]) for x in both
                 if pd.notna(app.at[x, "composite"]) and pd.notna(lab.at[x, "composite"])), default=0.0)
    dclose = max((abs(app.at[x, "close"] / lab.at[x, "close"] - 1) for x in both
                  if pd.notna(app.at[x, "close"]) and pd.notna(lab.at[x, "close"])), default=0.0)
    buys_app = [(b["ticker"], round(b["dollars"], 6)) for b in
                model.buy_list(app, {}, groups, model.SHIPPED, False, state.BUDGET)]
    buys_lab = [(b["ticker"], round(b["dollars"], 6)) for b in
                model.buy_list(lab, {}, ctx["groups"], model.SHIPPED, False, state.BUDGET)]
    unrestricted = sum(not _eq(lab.at[x, "composite"], lab_full.at[x, "composite"]) for x in lab.index)
    ok = (set(app.index) == set(lab.index) and el_app == el_lab and not trend_diff and not comp_diff
          and buys_app == buys_lab)
    return {"date": day, "app_t": str(fr["t"].date()), "tickers": len(names), "only_app": sorted(set(app.index) - set(lab.index)),
            "only_lab": sorted(set(lab.index) - set(app.index)), "eligible_app": len(el_app), "eligible_lab": len(el_lab),
            "eligible_diff": sorted(el_app ^ el_lab), "trend_up": int(app["trend"].astype(bool).sum()),
            "trend_diff": trend_diff, "composite_diff": comp_diff, "max_dcomposite": float(dcomp),
            "max_rel_dclose": float(dclose), "buys_app": [b[0] for b in buys_app], "buys_lab": [b[0] for b in buys_lab],
            "buys_equal": buys_app == buys_lab, "lab_unrestricted_composite_changes": int(unrestricted), "ok": ok}


def main() -> int:
    if not LAB_DB.exists():
        print(f"SKIP: {LAB_DB.relative_to(config.ROOT)} does not exist (run: python -m lab.run data)")
        return 0
    from lab import run as labrun
    ctx = labrun.load_ctx("ai")
    path = os.environ.get("FINRES_DB") or "/tmp/finres_proof.db"
    rows = []
    with closing(db.connect(path)) as conn:
        for d in DATES:
            rows.append(compare(conn, ctx, d))
    print(f"parity: app DB {path} vs lab ctx_ai ({LAB_DB.relative_to(config.ROOT)})")
    print(f"{'as-of':<11} {'tickers':>7} {'elig app/lab':>12} {'uptrend':>7} {'trend Δ':>7} {'max|Δcomp|':>10} "
          f"{'max Δclose':>10} {'buys =':>6}  result")
    for r in rows:
        if "why" in r:
            print(f"{r['date']:<11} FAIL: {r['why']}")
            continue
        print(f"{r['date']:<11} {r['tickers']:>7} {r['eligible_app']:>5}/{r['eligible_lab']:<6} {r['trend_up']:>7} "
              f"{len(r['trend_diff']):>7} {r['max_dcomposite']:>10.1e} {r['max_rel_dclose']:>10.1e} "
              f"{'yes' if r['buys_equal'] else 'NO':>6}  {'PASS' if r['ok'] else 'FAIL'}")
        print(f"            B0R buys (app): {', '.join(r['buys_app']) or '(none)'}")
        if not r["buys_equal"]:
            print(f"            B0R buys (lab): {', '.join(r['buys_lab']) or '(none)'}")
        for k in ("only_app", "only_lab", "eligible_diff", "trend_diff", "composite_diff"):
            if r[k]:
                print(f"            {k}: {r[k]}")
        print(f"            (lab's own universe+laggards ranking would change {r['lab_unrestricted_composite_changes']} "
              "composites; the restricted re-score is what is compared)")
    ok = all(r.get("ok") for r in rows)
    print("PARITY PASS" if ok else "PARITY FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
