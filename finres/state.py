"""Everything the page needs, loaded from the DB. No math here: signals.py / model.py do all of it."""
import json
import math
from datetime import date

import pandas as pd

from finres import config, db, edgar, estimates, llm, model, prices, signals

BUDGET = 2500.0
FRESH_ROWS = 5  # a close within the last 5 trading rows counts as fresh
COVERAGE_MIN = 0.90
LAB_DIR = config.ROOT / "lab" / "results"  # oos.json + fidelity.json (shapes owned by lab/)
UPTREND, NO_UPTREND = "UPTREND", "NO UPTREND"  # ADR-005 labels; model.score's BUY/WATCH/... stay internal
SNAP_KEYS = ["composite", "grade_mom", "grade_qual", "grade_rev", "grade_composite", "label", "mom", "qual",
             "rev", "rev_chg", "rev_breadth", "trend", "close"]
SHADOW_START = "2026-09"  # ADR-008: forward shadow ledgers start at month-end 2026-09-30 (no hindsight)
SHADOWS = {"rule": "Shipped rule (B0R · S2)", "hold": "Buy uptrend, hold (B0H)", "ew": "Equal-weight universe",
           "smh": "SMH DCA"}  # ADR-011: hold = B0R buys, only the -35% stop sells
SHADOW_COST = 0.0015  # lab.backtest.COST (15 bps per side); SMH DCA pays none, as in lab `dca`
_fund_cache: dict[tuple[str, str], dict | None] = {}  # (ticker, asof) -> edgar.fundamentals output
_est_cache: dict[tuple[str, str], dict] = {}  # (ticker, day) -> estimates.snapshot for out-of-universe analyze
_sec_cache: dict[tuple[str, str], list] = {}  # (ticker, asof) -> edgar.recent_8k (severe 8-Ks, 45 days)


def clear_cache() -> None:
    """Drop in-process caches (after a refresh re-fetched EDGAR). Rebinds, so readers holding the old dict finish safely."""
    global _fund_cache, _est_cache, _sec_cache
    _fund_cache, _est_cache, _sec_cache = {}, {}, {}


def sec_flags(t: str, asof: date | None, fetch: bool = False) -> list[dict]:
    """Severe SEC 8-Ks in the 45 days before asof. Cache-only unless `fetch` (live analyze of an uncached ticker)."""
    key, cache = (t, (asof or date.today()).isoformat()), _sec_cache  # the module dict as of this call
    if key not in cache:
        try:
            cache[key] = edgar.recent_8k(t, asof or date.today(), max_age_days=36500 if fetch else None)
        except Exception:
            cache[key] = []
    return cache[key]


def sec_warnings(flags: list[dict]) -> list[str]:
    """model.warnings-style notes, e.g. 'SEC 8-K: auditor change (2026-09-12)'."""
    return [f"SEC 8-K: {', '.join(f['labels'])} ({f['date']})" for f in flags]


def clean(v):
    """NaN -> None, numpy scalars -> Python; containers and strings pass through."""
    if v is None or isinstance(v, (str, list, dict, tuple)):
        return v
    if pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def row_dict(r: pd.Series) -> dict:
    return {k: clean(v) for k, v in r.items()}


def rule_label(r) -> str:
    """Shipped-rule meaning of a scored row: UPTREND (buyable), NO UPTREND, or Insufficient data."""
    if r is None or r.get("label") == model.INSUFFICIENT:
        return model.INSUFFICIENT
    return UPTREND if bool(r.get("trend", False)) else NO_UPTREND


def llm_up() -> bool:
    """Is the local LLM server answering? (llm.up: one 1 s probe, cached 30 s)."""
    return llm.up()


def _fund(t: str, asof: date | None, close, raw: dict | None) -> dict:
    """Quality inputs: EDGAR point-in-time; live-only Yahoo fallback (not PIT) when EDGAR has no us-gaap facts."""
    day = asof or date.today()
    key, cache = (t, day.isoformat()), _fund_cache
    if key not in cache:
        try:  # huge max_age: page loads read the cache; only /refresh re-fetches (7-day default)
            facts = edgar.companyfacts(t, max_age_days=36500)
        except Exception:
            facts = None
        cache[key] = edgar.fundamentals(facts, day) if facts else None
    f = cache[key]
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
    uni = closes[[x for x in u["tickers"] if x in closes.columns]].notna().sum(axis=1)
    if closes.empty or not (uni >= uni.max() / 2).any():
        return None
    t = uni.index[uni >= uni.max() / 2][-1]  # as-of = last day most of the universe traded, not one extra ticker
    closes = closes.loc[:t]
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


def _completed_month_ends(index: pd.DatetimeIndex, t: pd.Timestamp, ref: date) -> list[pd.Timestamp]:
    """Month-ends <= t, dropping t's month unless t is its last business day or `ref` (today/replay, rolled to the
    next business day: Sat 2024-03-30 -> Mon 04-01, after Good Friday) is in a later month."""
    me = signals.month_ends(index[index <= t])
    nxt = pd.Timestamp(ref) + pd.offsets.BDay(0)
    if me and (t + pd.offsets.BDay(1)).month == t.month and (nxt.year, nxt.month) <= (t.year, t.month):
        me = me[:-1]
    return me


def _meta(conn, fr: dict | None, asof: date | None) -> dict:
    t = fr["t"].date() if fr else None
    return {"asof": t, "age": ((asof or date.today()) - t).days if t else None,
            "last_refresh": conn.execute("SELECT MAX(date) FROM snapshot").fetchone()[0],
            "llm_up": llm_up(), "replay": asof is not None, "replay_date": asof}


def _lab() -> dict | None:
    """Lab headline from oos.json (selected, DCA SMH) + fidelity.json (ai_OOS ew_trend10 / ew_all); None if absent."""
    try:
        oos = json.loads((LAB_DIR / "oos.json").read_text())
        fid = json.loads((LAB_DIR / "fidelity.json").read_text())
        x = {r["name"]: float(r["xirr"]) for r in oos["rows"]} | {r["name"]: float(r["xirr"]) for r in fid["ai_OOS"]}
        rot, ew, smh, sel = x["ew_trend10"], x["ew_all"], x["DCA SMH"], x[oos["selected"]]
        y0, y1 = (str(p)[:4] for p in oos["period"])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    gates = "passed" if all((oos.get("rule") or {"_": False}).values()) else "failed"
    out = {"headline": f"Lab ({y0}–{y1}, out-of-sample): uptrend rotation {rot:.1%} vs equal-weight universe "
                       f"{ew:.1%} vs SMH DCA {smh:.1%} XIRR; the ranking model {sel:.1%} {gates} its gates. "
                       "Hindsight-biased universe: absolute numbers are inflated."}
    try:  # ADR-009: the hindsight-free re-test (point-in-time universe from 10-Ks)
        h = {r["name"]: float(r["xirr"]) for r in json.loads((LAB_DIR / "h1.json").read_text())["ai_pit"]["rows"]}
        out["pit"] = (f"Hindsight-free re-test (2010–2026, universe rebuilt each year from 10-Ks): rule "
                      f"{h['rule (ew_trend10)']:.1%} vs equal-weight hold {h['EW (ew_all)']:.1%} vs SMH DCA "
                      f"{h['DCA SMH']:.1%} XIRR. The rule did not beat SMH without hindsight (ADR-009).")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return out


def _track(conn, fr: dict | None) -> dict:
    """Live picks vs SMH and the equal-weight universe over the same window (pick's close date d -> last)."""
    picks = conn.execute("SELECT * FROM picks ORDER BY month DESC, rank").fetchall()
    rows = []
    if fr is not None:
        ff = fr["closes"].ffill()
        last = ff.iloc[-1]
        uni = [x for x in fr["u"]["tickers"] if x in ff.columns]
        for p in picks:
            if p["d"]:  # base = the close the pick price came from (on/before d), same as the pick's
                upto = ff.loc[:pd.Timestamp(p["d"])]
                base = upto.iloc[-1] if len(upto) else None
            else:  # pre-M8b rows: the month's first close
                after = ff.index[ff.index >= pd.Timestamp(p["month"] + "-01")]
                base = ff.loc[after[0]] if len(after) else None
            lp = clean(last.get(p["ticker"]))
            rows.append({"month": p["month"], "ticker": p["ticker"], "price": p["price"], "last": lp,
                         "ret": lp / p["price"] - 1 if lp and p["price"] else None,
                         "smh": clean(last.get("SMH") / base.get("SMH") - 1) if base is not None and "SMH" in ff else None,
                         "ew": clean((last[uni] / base[uni] - 1).mean()) if base is not None and uni else None})
    avg = {k: (sum(v) / len(v) if (v := [r[k] for r in rows if r[k] is not None]) else None) for k in ("ret", "smh", "ew")}
    return {"rows": rows, "avg": avg}


def _sells(closes: pd.DataFrame, scored: pd.DataFrame, hold: dict, now: dict, me: list) -> list[dict]:
    """S2 on the last two COMPLETED month-ends me[-2] < me[-1] (never today's close; a missing SMA200 never counts as
    below), over every loaded column (held out-of-universe names too); the -35% stop uses `now`."""
    f = signals.factors_at(closes, me[-1] if me else closes.index[-1])
    f = f.loc[[x for x in hold if x in f.index]].assign(composite=scored["composite"])  # rank rules (S3) see it
    f0 = signals.factors_at(closes, me[-2]) if len(me) >= 2 else f.iloc[:0]
    if len(me) < 2:
        f["sma200"] = math.nan  # not enough completed month-ends: the trend rule cannot fire
    return model.sell_list(f, hold, now, set(f0.index[f0["sma200"].notna() & ~f0["above200"]]), model.SHIPPED)


def replay(conn, x: str, closes: pd.DataFrame | None = None) -> tuple[dict, list[dict]]:
    """(portfolio, decisions) of shadow ledger x rebuilt from inception: model.step over the stored decisions at the
    CURRENT closes of each fill date, so a split (re-adjusted history) cannot desync shares from prices (M10/F4)."""
    recs, port = db.shadow_decisions(conn, x), {"shares": {}, "basis": {}, "cash": 0.0}
    ff = (closes if closes is not None else prices.load_closes(conn, sorted({k for r in recs for k in r["sells"] + r["buys"]}) or ["SMH"])).ffill()
    for r in recs:
        px = ff.loc[:pd.Timestamp(r["fill_date"])].iloc[-1].dropna().to_dict()
        port = model.step(port, r["sells"], r["buys"], px, r["contrib"], 0.0 if x == "smh" else SHADOW_COST)[0]
    return port, recs


def step_shadows(conn, ref: date | None = None) -> int:
    """Advance the shadow ledgers (ADR-008) through each completed month-end >= SHADOW_START, point-in-time like replay
    mode, filled at the first close after it (strictly before `ref`). Stores DECISIONS only (M10/F4); idempotent;
    stops at a missing fill day."""
    closes, ref, added = prices.load_closes(conn), ref or date.today(), 0
    done = {tuple(r) for r in conn.execute("SELECT strategy, month FROM shadow")}
    mes = _completed_month_ends(closes.index, closes.index[-1], ref) if len(closes) else []
    for i, me in enumerate(mes):
        month, later = me.strftime("%Y-%m"), closes.index[closes.index > me]
        if month < SHADOW_START or not (todo := [x for x in SHADOWS if (x, month) not in done]):
            continue
        if not len(later) or later[0].date() >= ref:
            break
        fr, d, mark = frame(conn, me.date()), later[0], closes.ffill().loc[me].dropna().to_dict()
        sc = fr["scored"]
        for x in todo:
            sh, basis = (p := replay(conn, x, closes)[0])["shares"], p["basis"]
            hold = {k: {"cost": basis[k] / n} for k, n in sh.items()}
            sells = [r["ticker"] for r in _sells(closes, sc, hold, mark, mes[:i + 1])
                     if x == "rule" or r["rule"] == "-35% stop"] if x in ("rule", "hold") else []
            pos = {k: n * mark.get(k, 0.0) for k, n in sh.items() if k not in sells}
            buys = ["SMH"] if x == "smh" else list(sc.index[sc["eligible"].astype(bool)]) if x == "ew" else \
                [b["ticker"] for b in model.buy_list(sc, pos, fr["u"]["ticker_group"], model.SHIPPED, False, BUDGET)]
            if d == closes.index[-1] and any(pd.isna(closes.at[d, k]) for k in sells + buys if k in closes):
                return added  # the fill day's closes are incomplete: the next refresh continues
            rec = {"sells": sells, "buys": buys, "fill_date": d.date().isoformat(), "contrib": BUDGET}
            conn.execute("INSERT OR IGNORE INTO shadow VALUES (?,?,?)", (x, month, json.dumps(rec)))
            conn.commit()
            added += 1
    return added


def _shadow_rows(conn) -> list[dict]:
    """Per shadow ledger: months, invested, value marked at the latest close, XIRR since inception (replayed)."""
    out = []
    for x, label in SHADOWS.items():
        port, recs = replay(conn, x)
        if recs:
            last = prices.load_closes(conn, list(port["shares"]) or ["SMH"]).ffill()
            value = port["cash"] + sum(n * float(last[k].iloc[-1]) for k, n in port["shares"].items() if k in last)
            out.append({"strategy": label, "months": len(recs), "invested": sum(r["contrib"] for r in recs), "value": value,
                        "xirr": clean(model.xirr([r["fill_date"] for r in recs] + [last.index[-1]],
                                                 [-r["contrib"] for r in recs] + [value]))})
    return out


def changes(conn) -> dict | None:
    """Since the latest snapshot >= 5 days before the newest: trend flips and severe 8-Ks filed after it."""
    days = [r[0] for r in conn.execute("SELECT DISTINCT date FROM snapshot ORDER BY date DESC")]
    prev = next((x for x in days if (date.fromisoformat(days[0]) - date.fromisoformat(x)).days >= 5), None)
    if prev is None:
        return None
    tr: dict = {}
    for r in conn.execute("SELECT ticker, date, factors FROM snapshot WHERE date IN (?, ?) ORDER BY ticker", (days[0], prev)):
        tr.setdefault(r[0], {})[r[1]] = json.loads(r[2]).get("trend")
    flip = lambda a, b: [t for t, v in tr.items() if v.get(days[0]) is a and v.get(prev) is b]  # noqa: E731
    out = {"since": prev, "up": flip(True, False), "down": flip(False, True),
           "sec": [t for t, v in tr.items() if days[0] in v and any(f["date"] > prev for f in sec_flags(t, None))]}
    return out if out["up"] or out["down"] or out["sec"] else None


def build(conn, asof: date | None = None) -> dict:
    """The whole page state. asof = replay date (config.ASOF) or None for live."""
    fr = frame(conn, asof)
    s = {"meta": _meta(conn, fr, asof), "buys": [], "sells": [], "watch": [], "holdings": [], "totals": None,
         "categories": [], "regime": None, "coverage": 0.0, "withheld": True, "track": _track(conn, fr),
         "lab": _lab(), "shadow": _shadow_rows(conn), "changes": changes(conn) if asof is None else None,
         "shadow_start": SHADOW_START, "model": f"{model.SHIPPED['buy']} · {model.SHIPPED['sell']}", "budget": BUDGET}
    hold = {r["ticker"]: {"shares": r["shares"], "cost": r["cost"]}
            for r in conn.execute("SELECT * FROM holdings ORDER BY ticker")}
    if fr is None:  # no prices: still list saved holdings, value unknown
        s["holdings"] = [dict(ticker=x, **h, last=None, value=None, weight=None, pl=None, grade=None, sell=False,
                              rule=model.INSUFFICIENT, in_universe=False, sec_flags=[], status="no price data")
                         for x, h in hold.items()]
        s["totals"] = {"value": None, "cost": sum(h["shares"] * h["cost"] for h in hold.values())}
        return s
    u, closes, scored, fac_all = fr["u"], fr["closes"], fr["scored"], fr["fac_all"]
    last = {c: float(v) for c, v in closes.ffill().iloc[-1].items() if pd.notna(v)}
    fresh = closes.iloc[-FRESH_ROWS:].notna().any()
    s["coverage"] = sum(bool(fresh.get(x, False)) for x in u["tickers"]) / max(len(u["tickers"]), 1)
    s["withheld"] = s["coverage"] < COVERAGE_MIN
    spy = fac_all.loc["SPY"] if "SPY" in fac_all.index else pd.Series({"close": math.nan, "sma200": math.nan})
    s["regime"] = model.regime(fr["fac"], spy["close"], spy["sma200"])  # information only (ADR-005: no brake)
    el = scored[scored["eligible"].astype(bool)]
    s["regime"]["uptrend"] = float(el["trend"].fillna(False).astype(bool).mean()) if len(el) else None

    # Holdings, positions and sells.
    positions = {x: h["shares"] * last.get(x, h["cost"]) for x, h in hold.items()}
    sells = _sells(closes, scored, hold, last, _completed_month_ends(closes.index, fr["t"], asof or date.today()))
    rule = {x["ticker"]: x["rule"] for x in sells}
    total = sum(positions.values())
    for x, h in hold.items():
        r = scored.loc[x] if x in scored.index else None
        lp = last.get(x)
        s["holdings"].append({
            "ticker": x, "shares": h["shares"], "cost": h["cost"], "last": lp, "value": positions[x],
            "weight": positions[x] / total if total else None, "pl": lp / h["cost"] - 1 if lp and h["cost"] else None,
            "grade": r["grade_composite"] if r is not None else None, "rule": rule_label(r),
            "in_universe": r is not None, "sec_flags": sec_flags(x, asof), "sell": x in rule,
            "status": rule.get(x) or ("not in universe" if r is None else "OK" if rule_label(r) != NO_UPTREND
                                      else "watching: no uptrend. Sells after 2 month-ends below 200DMA")})
        warns = (model.warnings(r) if r is not None else []) + sec_warnings(sec_flags(x, asof))
        s["watch"] += [{"ticker": x, "text": w} for w in warns if w != "speculative"]
    s["totals"] = {"value": total, "cost": sum(h["shares"] * h["cost"] for h in hold.values())}
    if not s["withheld"]:
        s["sells"] = [dict(x, pl=next(h["pl"] for h in s["holdings"] if h["ticker"] == x["ticker"])) for x in sells]
        buys = model.buy_list(scored.drop(index=[x["ticker"] for x in sells], errors="ignore"), positions,
                              u["ticker_group"], model.SHIPPED, False, BUDGET)  # B0R; never re-buy a name being sold
        for b in buys:
            x, close = b["ticker"], last.get(b["ticker"])
            b.update(category=u["categories"][u["ticker_category"][x]]["name"], close=close,
                     shares=b["dollars"] / close if close else None, grade=scored.at[x, "grade_composite"],
                     sec_flags=sec_flags(x, asof))
            warns = model.warnings(scored.loc[x]) + sec_warnings(b["sec_flags"])
            s["watch"] += [{"ticker": x, "text": w} for w in warns if w != "speculative"]
        s["buys"] = buys

    # Categories in universe order; rows in scored (tie-break) order, NaN composites last.
    live = asof is None
    for key, c in u["categories"].items():
        rows = []
        for x in [x for x in scored.index if u["ticker_category"].get(x) == key]:
            r = row_dict(scored.loc[x])
            raw = fr["raws"].get(x) or {}
            r.update(ticker=x, name=raw.get("name") or "", rule=rule_label(r), sec_flags=sec_flags(x, asof),
                     stale=not bool(fresh.get(x, False)) or (live and not raw))
            rows.append(r)
        rows += [{"ticker": x, "name": (fr["raws"].get(x) or {}).get("name") or "", "stale": True,
                  "label": model.INSUFFICIENT, "rule": model.INSUFFICIENT, "sec_flags": sec_flags(x, asof),
                  "reason": "no price data"}
                 for x in c["tickers"] if x not in scored.index]
        up = sum(r["rule"] == UPTREND for r in rows)
        best = next((r for r in rows if r.get("composite") is not None), None)
        s["categories"].append({"key": key, "name": c["name"], "rows": rows, "up": up, "best": best})
    return s


def _estimates(t: str) -> dict | None:
    key, cache = (t, date.today().isoformat()), _est_cache
    if key not in cache:
        try:
            cache[key] = estimates.snapshot(t)
        except Exception:
            return None
    return cache[key]


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
    sec = sec_flags(ticker, asof, fetch=asof is None)
    out.update(ticker=ticker, rule=rule_label(row), name=raw.get("name") or ticker, sector=raw.get("sector"), industry=raw.get("industry"),
               forward_pe=raw.get("forward_pe"), price=clean(row["close"]), warnings=model.warnings(row) + sec_warnings(sec),
               sec_flags=sec,
               in_universe=ticker in fr["u"]["ticker_category"], asof=fr["t"].date(),
               market_cap=raw.get("market_cap"), eps_rev=raw.get("eps_rev"),  # passthrough for llm.facts
               category=fr["u"]["categories"].get(fr["u"]["ticker_category"].get(ticker), {}).get("name"),
               closes=a, sma200_series=b, spark=_spark(a, b))
    return out
