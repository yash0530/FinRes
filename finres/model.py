"""Pure scoring, labels and the monthly buy/sell rules (ADR-004 as amended by ADR-004a)."""
import numpy as np
import pandas as pd

WEIGHTS = {"W1": (0.8, 0.2), "W2": (0.6, 0.4), "W3": (1.0, 0.0)}
REV_WEIGHT = 0.15
BUY_ZONE = 0.85
HOLD_FLOOR = 0.70
STOP = 0.65
BRAKE_BREADTH = 0.40
MAX_PER_GROUP = 3  # ADR-004b: at most 3 of one month's buys from one cap group
SHIPPED = {"weights": "W2", "gate": "G1", "sell": "S2", "buy": "B0R"}  # ADR-005: lab verdict
ROTATE_N = 10  # B0R: 10 uptrend names per month, least-held first

NUMERIC = ["gp_assets", "rev_growth", "earnings_yield", "rev_chg", "rev_breadth"]
INSUFFICIENT = "Insufficient data"


def _ok(v) -> bool:
    """True for a present, non-NaN scalar."""
    return v is not None and not pd.isna(v)


def pct(s: pd.Series) -> pd.Series:
    """Percentile rank over non-NaN values (NaN stays NaN)."""
    return s.astype(float).rank(pct=True)


def grade(x) -> str:
    """A >= 0.8, B >= 0.6, C >= 0.4, D >= 0.2, else F; missing -> '–'."""
    if not _ok(x):
        return "–"
    return next((g for g, lo in (("A", 0.8), ("B", 0.6), ("C", 0.4), ("D", 0.2)) if x >= lo), "F")


def order(df: pd.DataFrame) -> pd.DataFrame:
    """Tie-break order: composite desc, earnings_yield desc (NaN last), ticker asc."""
    key = df.assign(_c=df["composite"], _e=df["earnings_yield"], _t=df.index.astype(str))
    key = key.sort_values(["_c", "_e", "_t"], ascending=[False, False, True], na_position="last")
    return df.loc[key.index]


def _ordinal(p: float) -> str:
    n = int(round(p * 100))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _clause(name: str, p, inner: list[str]) -> tuple[float, str] | None:
    if not _ok(p):
        return None
    text = f"{name} {_ordinal(p)} pct"
    return p, text + (f" ({', '.join(inner)})" if inner else "")


def reason(row) -> str:
    """Deterministic one-line reason: top-2 factor clauses by percentile + the trend clause."""
    if not bool(row.get("eligible", False)):
        n = row.get("n_days")
        if not _ok(row.get("close")):
            return f"{INSUFFICIENT}: no recent price"
        return f"{INSUFFICIENT}: {int(n) if _ok(n) else 0} trading days of history (need 273)"
    g = row.get
    fmt = [("Momentum", g("mom"), [("12-1m {:+.0%}", g("ret_12_1")), ("{:.0%} from high", g("dd_52w"))]),
           ("Quality", g("qual"), [("GP/A {:.2f}", g("gp_assets")), ("rev {:+.0%}", g("rev_growth"))]),
           ("Revisions", g("rev"), [("FY EPS est {:+.0%}/90d", g("rev_chg"))])]
    clauses = [_clause(name, p, [f.format(round(v, 2) + 0.0) for f, v in inner if _ok(v)])  # no "-0%"
               for name, p, inner in fmt]
    clauses = sorted((c for c in clauses if c), key=lambda c: -c[0])[:2]
    trend = "in uptrend" if bool(row.get("trend", False)) else \
        "above 200DMA, 50DMA below 200DMA (no uptrend)" if bool(row.get("above200", False)) else "below 200DMA"
    return " · ".join([text for _, text in clauses] + [trend])


def score(fac: pd.DataFrame, weights: str = "W1", use_revisions: bool = False) -> pd.DataFrame:
    """Composite percentile, grades, label and reason per ticker; returned in tie-break order."""
    df = fac.copy()
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float) if c in df else np.nan
    df["speculative"] = df["speculative"].fillna(False).astype(bool) if "speculative" in df else False
    df["eligible"] = df["eligible"].fillna(False).astype(bool)
    e = df[df["eligible"]]
    mom = 0.7 * pct(e["mom_raw"]) + 0.3 * pct(e["hi52"])
    qual = pd.concat([pct(e["gp_assets"]), pct(e["rev_growth"])], axis=1).mean(axis=1)
    rev = pd.concat([pct(e["rev_chg"]), pct(e["rev_breadth"])], axis=1).mean(axis=1) if use_revisions \
        else pd.Series(np.nan, index=e.index)
    w_mom, w_qual = WEIGHTS[weights]
    raw = (w_mom * mom + w_qual * qual).where(qual.notna(), mom)
    if use_revisions:
        raw = ((1 - REV_WEIGHT) * raw + REV_WEIGHT * rev).where(rev.notna(), raw)
    for name, s in (("mom", mom), ("qual", qual), ("rev", rev), ("raw", raw), ("composite", pct(raw))):
        df[name] = s.reindex(df.index).astype(float)
    for name in ("mom", "qual", "rev", "composite"):
        df[f"grade_{name}"] = df[name].map(grade)
    c, trend = df["composite"], df["trend"].fillna(False).astype(bool)
    label = np.select([c >= BUY_ZONE, c >= HOLD_FLOOR], [np.where(trend, "BUY", "WATCH"), "HOLD"], "AVOID")
    insufficient = ~df["eligible"] | c.isna()  # missing quality/revisions just re-weight (ADR-004c)
    df["label"] = np.where(insufficient, INSUFFICIENT, label)
    df["reason"] = [reason(r) for _, r in df.iterrows()] if len(df) else []
    return order(df)


def regime(fac: pd.DataFrame, spy_close: float, spy_sma200: float) -> dict:
    """Market brake: SPY below its 200DMA or fewer than 40% of eligible names above theirs."""
    el = fac["eligible"].fillna(False).astype(bool)
    breadth = float(fac.loc[el, "above200"].astype(float).mean()) if el.any() else None
    spy_above = bool(_ok(spy_close) and _ok(spy_sma200) and spy_close > spy_sma200)
    return {"spy_above200": spy_above, "breadth": breadth,
            "brake": (not spy_above) or breadth is None or breadth < BRAKE_BREADTH}


def take_by_group(tickers: list, groups: dict[str, str], limit: int) -> list:
    """First `limit` tickers in the given order, skipping any whose group already has MAX_PER_GROUP (ADR-004b)."""
    keep, per_group = [], {}
    for t in tickers:
        g = groups.get(t) or ("_own", t)
        if per_group.get(g, 0) < MAX_PER_GROUP:
            keep.append(t)
            per_group[g] = per_group.get(g, 0) + 1
        if len(keep) == limit:
            break
    return keep


def buy_list(scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str], cfg: dict,
             brake: bool, budget: float) -> list[dict]:
    """This month's buys under B0R (ADR-005): uptrend names, least-held first, split equally; no caps, no brake.
    [] means carry the cash. The lab's ranked rules (Nvar/N3, caps, brake) live in lab/backtest.py."""
    if cfg["buy"] != "B0R":
        raise ValueError(f"unknown buy rule {cfg['buy']!r}")
    up = scored[scored["eligible"].astype(bool) & scored["composite"].notna() & scored["trend"].astype(bool)]
    up = up.assign(_held=[positions.get(t, 0.0) for t in up.index], _t=up.index.astype(str))
    up = up.sort_values(["_held", "composite", "_t"], ascending=[True, False, True])
    pick = up.loc[take_by_group(list(up.index), groups, ROTATE_N)]
    n = len(pick)
    return [{"ticker": t, "dollars": budget / n, "composite": float(r["composite"]), "reason": r.get("reason"),
             "held": float(r["_held"])} for t, r in pick.iterrows()]


def sell_list(scored: pd.DataFrame, holdings: dict[str, dict], prices_now: dict[str, float],
              below200_prev: set[str], cfg: dict) -> list[dict]:
    """Holdings to sell and the first rule that fired (stop, trend, rank). S1 never sells."""
    if cfg["sell"] == "S1":
        return []
    out = []
    for t, h in holdings.items():
        r = scored.loc[t] if t in scored.index else None
        price = prices_now.get(t, r.get("close") if r is not None else None)
        cost, comp = h.get("cost"), r.get("composite") if r is not None else None
        rule = None
        if _ok(price) and _ok(cost) and cost > 0 and price <= STOP * cost:  # every holding, scored or not
            rule = "-35% stop"
        elif r is None:
            continue
        elif _ok(r.get("sma200")) and not bool(r.get("above200", False)) and t in below200_prev:
            rule = "2 month-ends below 200DMA"
        elif cfg["sell"] == "S3" and _ok(comp) and comp < HOLD_FLOOR:
            rule = "rank fell below 70th pct"
        if rule:
            out.append({"ticker": t, "rule": rule, "composite": float(comp) if _ok(comp) else None})
    return out


def step(port: dict, sells: list[str], buys: list[str], px: dict[str, float], contrib: float,
         cost: float) -> tuple[dict, list[dict]]:
    """One month's accounting (lab + shadow ledgers): sell `sells` at px, then split contrib + cash + proceeds
    equally across `buys` with a clean price; leftover -> cash. port = {shares, basis, cash}; never mutated."""
    shares, basis, trades, proceeds, spent = dict(port["shares"]), dict(port["basis"]), [], 0.0, 0.0
    for k in [k for k in sells if k in shares and _ok(px.get(k))]:
        trades.append({"ticker": k, "side": "sell", "dollars": shares.pop(k) * px[k], "price": px[k]})
        proceeds += trades[-1]["dollars"] * (1 - cost)
        basis.pop(k, None)
    budget, names = contrib + port["cash"] + proceeds, [k for k in buys if _ok(px.get(k)) and px[k] > 0]
    for k in names:
        dollars = budget / len(names)
        shares[k], basis[k] = shares.get(k, 0.0) + dollars * (1 - cost) / px[k], basis.get(k, 0.0) + dollars
        spent += dollars
        trades.append({"ticker": k, "side": "buy", "dollars": dollars, "price": px[k]})
    return {"shares": shares, "basis": basis, "cash": budget - spent}, trades


def xirr(dates, flows) -> float:
    """Annual money-weighted return: bisection on NPV (ACT/365); NaN when there is no sign change."""
    t0, f = pd.Timestamp(dates[0]), np.asarray(flows, float)
    yrs = np.array([(pd.Timestamp(d) - t0).days / 365.0 for d in dates])
    npv = lambda r: float(np.sum(f / (1 + r) ** yrs))  # noqa: E731
    lo, hi = -0.9999, 100.0
    if npv(lo) * npv(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if npv(lo) * npv(mid) <= 0 else (mid, hi)
    return (lo + hi) / 2


def warnings(r) -> list[str]:
    """Live-only caution flags for one scored row."""
    rb, pit = r.get("rev_breadth"), r.get("fund_pit")
    return [w for w, on in (("EPS revisions negative", _ok(rb) and rb < -0.3),
                            ("speculative", bool(r.get("speculative", False))),
                            ("not point-in-time fundamentals", _ok(pit) and not bool(pit))) if on]
