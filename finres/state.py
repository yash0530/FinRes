"""Everything the page needs, loaded from the DB. No math here: signals.py / model.py do all of it."""
import json
import math
from datetime import date

import pandas as pd

from finres import config, db, edgar, estimates, llm, model, prices, signals

BUDGET = 2500.0
FRESH_ROWS = 5  # a close within the last 5 trading rows counts as fresh
COVERAGE_MIN = 0.90
OOS_PATH = config.ROOT / "lab" / "results" / "oos.json"
SNAP_KEYS = ["composite", "grade_mom", "grade_qual", "grade_rev", "grade_composite", "label", "mom", "qual",
             "rev", "rev_chg", "rev_breadth", "trend", "close"]
_fund_cache: dict[tuple[str, str], dict | None] = {}  # (ticker, asof) -> edgar.fundamentals output
_est_cache: dict[tuple[str, str], dict] = {}  # (ticker, day) -> estimates.snapshot for out-of-universe analyze


def clear_cache() -> None:
    """Drop in-process fundamentals/estimates caches (after a refresh re-fetched EDGAR)."""
    _fund_cache.clear()
    _est_cache.clear()


def clean(v):
    """NaN -> None, numpy scalars -> Python; containers and strings pass through."""
    if v is None or isinstance(v, (str, list, dict, tuple)):
        return v
    if pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def row_dict(r: pd.Series) -> dict:
    return {k: clean(v) for k, v in r.items()}


def llm_up() -> bool:
    """Is the local LLM server answering? (llm.up: one 1 s probe, cached 30 s)."""
    return llm.up()


def _fund(t: str, asof: date | None, close, raw: dict | None) -> dict:
    """Quality inputs: EDGAR point-in-time; live-only Yahoo fallback (not PIT) when EDGAR has no us-gaap facts."""
    day = asof or date.today()
    key = (t, day.isoformat())
    if key not in _fund_cache:
        try:  # huge max_age: page loads read the cache; only /refresh re-fetches (7-day default)
            facts = edgar.companyfacts(t, max_age_days=36500)
        except Exception:
            facts = None
        _fund_cache[key] = edgar.fundamentals(facts, day) if facts else None
    f = _fund_cache[key]
    out = signals.fund_factors(f, close)
    if (f is None or f.get("revenue_ttm") is None) and asof is None and raw:  # no/partial us-gaap (IFRS filers)
        eps, px = raw.get("trailing_eps"), raw.get("price") or clean(close)
        out.update(rev_growth=raw.get("revenue_growth"), gp_assets=None, fund_pit=False, fund_missing=False,
                   earnings_yield=eps / px if eps is not None and px else None,
                   speculative=eps is not None and eps < 0)
    return out


def frame(conn, asof: date | None, extra: tuple = (), raws: dict | None = None) -> dict | None:
    """Closes, factors and the scored universe (+ `extra` tickers scored alongside). None when no prices."""
    u = config.load_universe()
    held = [r["ticker"] for r in conn.execute("SELECT ticker FROM holdings ORDER BY ticker")]
    snaps = db.latest_snapshots(conn)
    want = list(dict.fromkeys(u["tickers"] + config.BENCHMARKS + held + list(extra)))
    closes = prices.load_closes(conn, want, asof)
    if closes.empty:
        return None
    t = closes.index[-1]
    fac_all = signals.factors_at(closes, t)
    names = [x for x in dict.fromkeys(u["tickers"] + list(extra)) if x in closes.columns]
    raws = {k: v[2] for k, v in snaps.items()} | (raws or {})
    rows = []
    for x in names:
        raw = raws.get(x) if asof is None else None
        f = _fund(x, asof, fac_all.at[x, "close"], raw)
        if asof is None:  # revisions exist only live (no free history)
            f |= signals.revisions_raw(raw)
        rows.append(f)
    fac = fac_all.loc[names].join(pd.DataFrame(rows, index=names))
    scored = model.score(fac, weights=model.SHIPPED["weights"], use_revisions=asof is None)
    return {"u": u, "held": held, "snaps": snaps, "raws": raws, "closes": closes, "t": t,
            "fac_all": fac_all, "fac": fac, "scored": scored}


def snapshot_factors(fr: dict | None, x: str) -> dict:
    """The ticker's scored row as plain JSON for the weekly snapshot table."""
    if fr is None or x not in fr["scored"].index:
        return {}
    r = fr["scored"].loc[x]
    return {k: clean(r.get(k)) for k in SNAP_KEYS}


def _completed_month_ends(index: pd.DatetimeIndex, t: pd.Timestamp) -> list[pd.Timestamp]:
    """Month-ends <= t, dropping t's month unless t is its last business day."""
    me = signals.month_ends(index[index <= t])
    if me and (t + pd.offsets.BDay(1)).month == t.month:
        me = me[:-1]
    return me


def _meta(conn, fr: dict | None, asof: date | None) -> dict:
    t = fr["t"].date() if fr else None
    return {"asof": t, "age": ((asof or date.today()) - t).days if t else None,
            "last_refresh": conn.execute("SELECT MAX(date) FROM snapshot").fetchone()[0],
            "llm_up": llm_up(), "replay": asof is not None, "replay_date": asof}


def _lab() -> dict | None:
    """Backtest headline from lab/results/oos.json (shape owned by lab/; read defensively)."""
    if not OOS_PATH.exists():
        return None
    try:
        d = json.loads(OOS_PATH.read_text())
    except (OSError, ValueError):
        return None
    parts = [f"Shipped: {d.get('ship', '?')}"]
    for k, v in (d.get("oos") or {}).items():
        if isinstance(v, dict) and clean(v.get("xirr")) is not None:
            parts.append(f"{k} OOS XIRR {v['xirr']:.1%}")
    for k in ("random_percentile", "random_pct", "percentile"):
        if clean(d.get(k)) is not None:
            parts.append(f"beat {d[k]:.0f}% of random portfolios" if d[k] > 1 else f"beat {d[k]:.0%} of random portfolios")
            break
    return {"headline": " · ".join(parts), "raw": d}


def _track(conn, fr: dict | None) -> dict:
    """Live picks vs SMH and the equal-weight universe over the same window (month's first close -> last)."""
    picks = conn.execute("SELECT * FROM picks ORDER BY month DESC, rank").fetchall()
    rows = []
    if fr is not None:
        ff = fr["closes"].ffill()
        last = ff.iloc[-1]
        uni = [x for x in fr["u"]["tickers"] if x in ff.columns]
        for p in picks:
            after = ff.index[ff.index >= pd.Timestamp(p["month"] + "-01")]
            base = ff.loc[after[0]] if len(after) else None
            lp = clean(last.get(p["ticker"]))
            rows.append({"month": p["month"], "ticker": p["ticker"], "price": p["price"], "last": lp,
                         "ret": lp / p["price"] - 1 if lp and p["price"] else None,
                         "smh": clean(last.get("SMH") / base.get("SMH") - 1) if base is not None and "SMH" in ff else None,
                         "ew": clean((last[uni] / base[uni] - 1).mean()) if base is not None and uni else None})
    avg = {k: (sum(v) / len(v) if (v := [r[k] for r in rows if r[k] is not None]) else None) for k in ("ret", "smh", "ew")}
    return {"rows": rows, "avg": avg}


def build(conn, asof: date | None = None) -> dict:
    """The whole page state. asof = replay date (config.ASOF) or None for live."""
    fr = frame(conn, asof)
    s = {"meta": _meta(conn, fr, asof), "buys": [], "sells": [], "watch": [], "holdings": [], "totals": None,
         "categories": [], "regime": None, "coverage": 0.0, "withheld": True, "track": _track(conn, fr),
         "lab": _lab(), "model": "·".join(model.SHIPPED.values()), "budget": BUDGET}
    if fr is None:
        return s
    u, closes, scored, fac_all = fr["u"], fr["closes"], fr["scored"], fr["fac_all"]
    last = {c: float(v) for c, v in closes.ffill().iloc[-1].items() if pd.notna(v)}
    fresh = closes.iloc[-FRESH_ROWS:].notna().any()
    s["coverage"] = sum(bool(fresh.get(x, False)) for x in u["tickers"]) / max(len(u["tickers"]), 1)
    s["withheld"] = s["coverage"] < COVERAGE_MIN
    spy = fac_all.loc["SPY"] if "SPY" in fac_all.index else pd.Series({"close": math.nan, "sma200": math.nan})
    s["regime"] = model.regime(fr["fac"], spy["close"], spy["sma200"])

    # Holdings, positions and sells.
    hold = {r["ticker"]: {"shares": r["shares"], "cost": r["cost"]}
            for r in conn.execute("SELECT * FROM holdings ORDER BY ticker")}
    positions = {x: h["shares"] * last.get(x, h["cost"]) for x, h in hold.items()}
    # S2 "two consecutive month-ends below 200DMA" is judged on the last two COMPLETED month-ends ME0 < ME1,
    # not on today's close: the row's `above200` is replaced by its state at ME1, `below200_prev` is ME0's.
    # The -35% stop and the S3 rank rule use the latest close / current composite.
    me = _completed_month_ends(closes.index, fr["t"])
    sell_frame, below_prev = scored.copy(), set()
    if len(me) >= 2:
        sig1, sig0 = signals.above200_at(closes, me[-1]), signals.above200_at(closes, me[-2])
        sell_frame["above200"] = sig1.reindex(sell_frame.index).fillna(False).astype(bool)
        below_prev = {x for x, v in sig0.items() if not v}
    else:
        sell_frame["above200"] = True  # not enough history: the trend rule cannot fire
    sells = model.sell_list(sell_frame, hold, last, below_prev, model.SHIPPED)
    rule = {x["ticker"]: x["rule"] for x in sells}
    capped = model.capped(list(hold), scored, positions, u["ticker_group"])
    total = sum(positions.values())
    for x, h in hold.items():
        r = scored.loc[x] if x in scored.index else None
        lp = last.get(x)
        s["holdings"].append({
            "ticker": x, "shares": h["shares"], "cost": h["cost"], "last": lp, "value": positions[x],
            "weight": positions[x] / total if total else None, "pl": lp / h["cost"] - 1 if lp and h["cost"] else None,
            "grade": r["grade_composite"] if r is not None else None, "label": r["label"] if r is not None else None,
            "in_universe": r is not None,
            "status": rule.get(x) or ("at cap: no new money" if x in capped else "OK" if r is not None
                                      else "not in universe")})
        if r is not None:
            s["watch"] += [{"ticker": x, "text": w} for w in model.warnings(r) if w != "speculative"]
    s["totals"] = {"value": total, "cost": sum(h["shares"] * h["cost"] for h in hold.values())}
    if not s["withheld"]:
        s["sells"] = [dict(x, pl=next(h["pl"] for h in s["holdings"] if h["ticker"] == x["ticker"])) for x in sells]
        buys = model.buy_list(scored, positions, u["ticker_group"], model.SHIPPED, s["regime"]["brake"], BUDGET)
        for b in buys:
            x, close = b["ticker"], last.get(b["ticker"])
            b.update(category=u["categories"][u["ticker_category"][x]]["name"], close=close,
                     shares=b["dollars"] / close if close else None, grade=scored.at[x, "grade_composite"])
            s["watch"] += [{"ticker": x, "text": w} for w in model.warnings(scored.loc[x]) if w != "speculative"]
        s["buys"] = buys

    # Categories in universe order; rows in scored (tie-break) order, NaN composites last.
    live = asof is None
    for key, c in u["categories"].items():
        rows = []
        for x in [x for x in scored.index if u["ticker_category"].get(x) == key]:
            r = row_dict(scored.loc[x])
            raw = fr["raws"].get(x) or {}
            r.update(ticker=x, name=raw.get("name") or "", stale=not bool(fresh.get(x, False)) or (live and not raw))
            rows.append(r)
        rows += [{"ticker": x, "name": (fr["raws"].get(x) or {}).get("name") or "", "stale": True,
                  "label": model.INSUFFICIENT, "reason": "no price data"}
                 for x in c["tickers"] if x not in scored.index]
        counts = {k: sum(r.get("label") == k for r in rows) for k in ("BUY", "WATCH", "HOLD", "AVOID")}
        best = next((r for r in rows if r.get("composite") is not None), None)
        s["categories"].append({"key": key, "name": c["name"], "rows": rows, "counts": counts, "best": best})
    return s


def _estimates(t: str) -> dict | None:
    key = (t, date.today().isoformat())
    if key not in _est_cache:
        try:
            _est_cache[key] = estimates.snapshot(t)
        except Exception:
            return None
    return _est_cache[key]


def _spark(a: list, b: list, w: int = 600, h: int = 120) -> dict:
    """SVG polyline points for closes (a) and SMA200 (b) sharing one y-scale."""
    vals = [v for v in a + b if v is not None and v == v]
    if not vals:
        return {"price": "", "sma": ""}
    lo, hi = min(vals), max(vals)
    span, n = (hi - lo) or 1.0, max(len(a) - 1, 1)
    pts = lambda xs: " ".join(f"{i * w / n:.1f},{h - 4 - (v - lo) / span * (h - 8):.1f}"  # noqa: E731
                              for i, v in enumerate(xs) if v is not None and v == v)
    return {"price": pts(a), "sma": pts(b)}


def analyze(conn, ticker: str, asof: date | None = None) -> dict:
    """Factor card for ANY ticker, percentiles relative to the universe (scored alongside it)."""
    have = prices.load_closes(conn, [ticker], asof)
    if asof is None and (have.empty or (date.today() - have.index[-1].date()).days > 7):
        try:
            df, _ = prices.download([ticker], period="10y")
            if not df.empty:
                prices.store(conn, df)
        except Exception:
            pass
    raw = None
    if asof is None:
        snaps = db.latest_snapshots(conn)
        raw = snaps[ticker][2] if ticker in snaps else _estimates(ticker)
    fr = frame(conn, asof, extra=(ticker,), raws={ticker: raw} if raw else None)
    if fr is None or ticker not in fr["scored"].index or fr["closes"][ticker].dropna().empty:
        return {"error": f"No price data for {ticker}"}
    row = fr["scored"].loc[ticker]
    s = fr["closes"][ticker].dropna()
    sma = s.rolling(200).mean()
    a, b = [clean(v) for v in s.iloc[-252:]], [clean(v) for v in sma.iloc[-252:]]
    raw = raw or {}
    out = row_dict(row)
    out.update(ticker=ticker, name=raw.get("name") or ticker, sector=raw.get("sector"), industry=raw.get("industry"),
               forward_pe=raw.get("forward_pe"), price=clean(row["close"]), warnings=model.warnings(row),
               in_universe=ticker in fr["u"]["ticker_category"], asof=fr["t"].date(),
               market_cap=raw.get("market_cap"), eps_rev=raw.get("eps_rev"),  # passthrough for llm.facts
               category=fr["u"]["categories"].get(fr["u"]["ticker_category"].get(ticker), {}).get("name"),
               closes=a, sma200_series=b, spark=_spark(a, b))
    return out
