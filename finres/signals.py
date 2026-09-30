"""Pure, lookahead-free per-ticker signals (ADR-004/004a). No I/O; shared by the app and the lab."""
import math

import numpy as np
import pandas as pd

MIN_DAYS = 273
MIN_PRICE = 3.0
STALE_ROWS = 5  # a close must exist within the last 5 rows <= t
TAIL = 273  # rows needed for every window below (252 + 21)


def month_ends(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Last date present in each calendar month (callers decide whether the last month is complete)."""
    if len(index) == 0:
        return []
    s = pd.Series(index, index=index)
    return list(s.groupby(index.to_period("M")).max())


def _back(tail: pd.DataFrame, k: int) -> pd.Series:
    """Row t-k of the tail (t = last row); all-NaN when there is not enough history."""
    if len(tail) <= k:
        return pd.Series(np.nan, index=tail.columns)
    return tail.iloc[-1 - k]


def _window_mean(tail: pd.DataFrame, n: int) -> pd.Series:
    """Mean of the last n rows; NaN unless all n are present."""
    w = tail.iloc[-n:]
    return w.mean().where((len(w) == n) & (w.count() == n))


def factors_at(closes: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """Price factors per ticker using only rows with index <= t."""
    hist = closes.loc[:t]
    n_days = hist.notna().sum()
    tail = hist.iloc[-TAIL - 1:].ffill(limit=STALE_ROWS - 1)
    close = _back(tail, 0)
    c21, c126, c252 = _back(tail, 21), _back(tail, 126), _back(tail, 252)
    rets = tail.pct_change(fill_method=None).iloc[-252:]
    vol = (rets.std() * math.sqrt(252)).where(c252.notna())
    ret_12_1 = c21 / c252 - 1
    mom_raw = (ret_12_1 / vol).replace([np.inf, -np.inf], np.nan)
    hi52 = close / tail.iloc[-252:].max()
    sma50, sma200 = _window_mean(tail, 50), _window_mean(tail, 200)
    above200 = (close > sma200).fillna(False).astype(bool)
    trend = (above200 & (sma50 > sma200)).fillna(False).astype(bool)
    out = pd.DataFrame({
        "close": close, "n_days": n_days.astype(int),
        "ret_1m": close / c21 - 1, "ret_6m": close / c126 - 1, "ret_12m": close / c252 - 1,
        "ret_12_1": ret_12_1, "vol_252": vol, "mom_raw": mom_raw,
        "hi52": hi52, "dd_52w": hi52 - 1, "sma50": sma50, "sma200": sma200,
        "above200": above200, "trend": trend,
    })
    out["eligible"] = (out["n_days"] >= MIN_DAYS) & (out["close"] >= MIN_PRICE) & out["close"].notna()
    out.index.name = "ticker"
    return out


def revisions_raw(est: dict | None) -> dict:
    """rev_chg (90-day FY EPS estimate change, clipped ±50%) and rev_breadth (net up-revisions share)."""
    est = est or {}
    chg, breadth = [], []
    for p in ("0y", "+1y"):
        tr = (est.get("eps_trend") or {}).get(p) or {}
        cur, d90 = tr.get("current"), tr.get("d90")
        if cur is not None and d90:
            chg.append(min(max((cur - d90) / abs(d90), -0.5), 0.5))
        rv = (est.get("eps_rev") or {}).get(p) or {}
        up, down = rv.get("up30"), rv.get("down30")
        if up is not None and down is not None:
            breadth.append((up - down) / max(up + down, 1))
    return {"rev_chg": sum(chg) / len(chg) if chg else None,
            "rev_breadth": sum(breadth) / len(breadth) if breadth else None}


def fund_factors(f: dict | None, close: float | None) -> dict:
    """Quality inputs, earnings yield and the speculative flag from `edgar.fundamentals` output."""
    if f is None:
        return {"gp_assets": None, "rev_growth": None, "earnings_yield": None, "speculative": False,
                "fund_missing": True, "fund_pit": None}
    ni, sh = f.get("net_income_ttm"), f.get("shares")
    cap = close * sh if close is not None and sh is not None and not math.isnan(close) else None
    return {
        "gp_assets": f.get("gp_assets"),
        "rev_growth": f.get("revenue_growth"),
        "earnings_yield": ni / cap if ni is not None and cap and cap > 0 else None,
        "speculative": ni is not None and ni < 0,  # ADR-004c: missing revenue = unknown, not speculative
        "fund_missing": False,
        "fund_pit": f.get("pit", True),
    }
