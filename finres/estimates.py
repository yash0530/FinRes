"""Live-only yfinance snapshot: info, EPS trend/revisions, splits, news. Never used by the lab."""
import math
import time
from datetime import datetime, timedelta, timezone

import yfinance as yf

BACKOFF = [20, 60, 180]
MIN_GAP = 0.5  # seconds between tickers
_last_ticker = 0.0

INFO_KEYS = {"name": "longName", "sector": "sector", "industry": "industry", "quote_type": "quoteType",
             "market_cap": "marketCap", "price": "currentPrice", "trailing_eps": "trailingEps",
             "forward_pe": "forwardPE", "gross_margins": "grossMargins", "return_on_assets": "returnOnAssets",
             "revenue_growth": "revenueGrowth", "total_revenue": "totalRevenue",
             "n_analysts": "numberOfAnalystOpinions"}


def _scrub(v):
    """NaN/inf -> None; numpy scalars -> Python."""
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _is_rate_limit(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}"
    return "Rate" in text or "429" in text or "Too Many Requests" in text


def _call(fn):
    """Run fn(); on rate-limit errors back off 20/60/180 s, retrying once per stage. Returns (value, error)."""
    for wait in [0] + BACKOFF:
        time.sleep(wait)
        try:
            return fn(), None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if not _is_rate_limit(e):
                return None, err
    return None, err


def _throttle() -> None:
    global _last_ticker
    wait = MIN_GAP - (time.monotonic() - _last_ticker)
    if wait > 0:
        time.sleep(wait)
    _last_ticker = time.monotonic()


def _table(df, cols: dict[str, str]) -> dict:
    """{'0y': {...}, '+1y': {...}} from an eps_trend / eps_revisions frame."""
    out = {}
    for period in ("0y", "+1y"):
        row = df.loc[period] if df is not None and period in df.index else None
        out[period] = {k: (_scrub(row[c]) if row is not None and c in row.index else None) for k, c in cols.items()}
    return out


def snapshot(ticker: str) -> dict:
    """Estimates + info snapshot for one ticker. Every attribute is fetched independently."""
    _throttle()
    t = yf.Ticker(ticker)
    errors = []
    info, err = _call(lambda: t.info)
    info = info or {}
    errors.append(err)
    out = {k: _scrub(info.get(src)) for k, src in INFO_KEYS.items()}
    if out["name"] is None:
        out["name"] = _scrub(info.get("shortName"))
    if out["price"] is None:
        out["price"] = _scrub(info.get("regularMarketPrice"))
    trend, err = _call(lambda: t.eps_trend)
    errors.append(err)
    out["eps_trend"] = _table(trend, {"current": "current", "d30": "30daysAgo", "d90": "90daysAgo"})
    rev, err = _call(lambda: t.eps_revisions)
    errors.append(err)
    out["eps_rev"] = _table(rev, {"up30": "upLast30days", "down30": "downLast30days"})
    splits, err = _call(lambda: t.splits)
    errors.append(err)
    cutoff = datetime.now(timezone.utc) - timedelta(days=3 * 365)
    out["splits"] = [] if splits is None else [
        [d.date().isoformat(), float(r)] for d, r in splits.items()
        if (d if d.tzinfo else d.tz_localize("UTC")) >= cutoff]
    out["ok"] = bool(info) and (out["price"] is not None or out["name"] is not None)
    out["error"] = next((e for e in errors if e), None if out["ok"] else "empty info")
    return out


def news(ticker: str, n: int = 10) -> list[dict]:
    """Recent headlines [{title, publisher, date}], handling old flat and new nested `content` shapes."""
    items, _ = _call(lambda: yf.Ticker(ticker).news)
    out = []
    for it in items or []:
        c = it.get("content") if isinstance(it.get("content"), dict) else None
        if c:
            title = c.get("title")
            publisher = (c.get("provider") or {}).get("displayName")
            d = (c.get("pubDate") or c.get("displayTime") or "")[:10] or None
        else:
            title, publisher = it.get("title"), it.get("publisher")
            ts = it.get("providerPublishTime")
            d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else None
        if title:
            out.append({"title": title, "publisher": publisher, "date": d})
        if len(out) >= n:
            break
    return out
