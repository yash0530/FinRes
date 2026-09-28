"""Pure scoring, labels and the monthly buy/sell rules (ADR-004 as amended by ADR-004a)."""
import numpy as np
import pandas as pd

WEIGHTS = {"W1": (0.8, 0.2), "W2": (0.6, 0.4), "W3": (1.0, 0.0)}
REV_WEIGHT = 0.15
BUY_ZONE = 0.85
HOLD_FLOOR = 0.70
MAX_N = 10
CAP_POS = 0.10
CAP_GROUP = 0.30
CAP_SPEC_POS = 0.03
CAP_SPEC_TOTAL = 0.10
CAPS_FROM = 25_000
STOP = 0.65
BRAKE_BREADTH = 0.40
BRAKE_N = 2
SHIPPED = {"weights": "W1", "gate": "G1", "sell": "S3", "buy": "Nvar"}  # placeholder until ADR-005

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
        if _ok(n) and n < 273:
            return f"{INSUFFICIENT}: {int(n)} trading days of history (need 273)"
        return f"{INSUFFICIENT}: price below $3"
    g = row.get
    fmt = [("Momentum", g("mom"), [("12-1m {:+.0%}", g("ret_12_1")), ("{:.0%} from high", g("dd_52w"))]),
           ("Quality", g("qual"), [("GP/A {:.2f}", g("gp_assets")), ("rev {:+.0%}", g("rev_growth"))]),
           ("Revisions", g("rev"), [("FY EPS est {:+.0%}/90d", g("rev_chg"))])]
    clauses = [_clause(name, p, [f.format(round(v, 2) + 0.0) for f, v in inner if _ok(v)])  # no "-0%"
               for name, p, inner in fmt]
    clauses = sorted((c for c in clauses if c), key=lambda c: -c[0])[:2]
    trend = "above" if bool(row.get("trend", False)) else "below"
    return " · ".join([text for _, text in clauses] + [f"{trend} 200DMA"])


def score(fac: pd.DataFrame, weights: str = "W1", use_revisions: bool = False,
          rng_signal: pd.Series | None = None) -> pd.DataFrame:
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
    if rng_signal is not None:
        raw = rng_signal.reindex(e.index).astype(float)
    for name, s in (("mom", mom), ("qual", qual), ("rev", rev), ("raw", raw), ("composite", pct(raw))):
        df[name] = s.reindex(df.index).astype(float)
    for name in ("mom", "qual", "rev", "composite"):
        df[f"grade_{name}"] = df[name].map(grade)
    c, trend = df["composite"], df["trend"].fillna(False).astype(bool)
    label = np.select([c >= BUY_ZONE, c >= HOLD_FLOOR], [np.where(trend, "BUY", "WATCH"), "HOLD"], "AVOID")
    insufficient = ~df["eligible"] | c.isna()
    if use_revisions:
        insufficient |= df[["qual", "rev", "earnings_yield"]].isna().sum(axis=1) >= 2
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


def _capped(tickers, scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str]) -> set[str]:
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
    return order(df[~df.index.isin(_capped(df.index, scored, positions, groups))])


def buy_list(scored: pd.DataFrame, positions: dict[str, float], groups: dict[str, str], cfg: dict,
             brake: bool, budget: float) -> list[dict]:
    """This month's buys, split equally; [] means carry the cash."""
    pool = buy_pool(scored, positions, groups, cfg)
    if cfg["buy"] == "Nvar":
        pick = pool[pool["composite"] >= BUY_ZONE].head(MAX_N)
    elif cfg["buy"] == "N3":
        pick = pool.head(3)
    else:
        raise ValueError(f"unknown buy rule {cfg['buy']!r}")
    if brake:
        pick = pick.head(BRAKE_N)
    n = len(pick)
    return [{"ticker": t, "dollars": budget / n, "composite": float(r["composite"]), "reason": r.get("reason")}
            for t, r in pick.iterrows()]


def sell_list(scored: pd.DataFrame, holdings: dict[str, dict], prices_now: dict[str, float],
              below200_prev: set[str], cfg: dict) -> list[dict]:
    """Holdings to sell and the first rule that fired (stop, trend, rank). S1 never sells."""
    if cfg["sell"] == "S1":
        return []
    out = []
    for t, h in holdings.items():
        if t not in scored.index:
            continue
        r = scored.loc[t]
        price = prices_now.get(t, r.get("close"))
        cost, comp = h.get("cost"), r.get("composite")
        below_now = _ok(r.get("sma200")) and not bool(r.get("above200", False))
        rule = None
        if _ok(price) and _ok(cost) and cost > 0 and price <= STOP * cost:
            rule = "-35% stop"
        elif below_now and t in below200_prev:
            rule = "2 month-ends below 200DMA"
        elif cfg["sell"] == "S3" and _ok(comp) and comp < HOLD_FLOOR:
            rule = "rank fell below 70th pct"
        if rule:
            out.append({"ticker": t, "rule": rule, "composite": float(comp) if _ok(comp) else None})
    return out


def warnings(scored_row) -> list[str]:
    """Live-only caution flags for one scored row."""
    out = []
    rb = scored_row.get("rev_breadth")
    if _ok(rb) and rb < -0.3:
        out.append("EPS revisions negative")
    if bool(scored_row.get("speculative", False)):
        out.append("speculative")
    pit = scored_row.get("fund_pit")
    if _ok(pit) and not bool(pit):
        out.append("not point-in-time fundamentals")
    return out
