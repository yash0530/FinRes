"""Adjusted daily closes: yfinance download -> SQLite cache -> wide DataFrame."""
import logging
import sqlite3
import time
from datetime import date

import pandas as pd
import yfinance as yf

BATCH = 40
BACKOFF = [20, 60, 180]


class _Collect(logging.Handler):
    """Collects yfinance's own error messages so rate limits hidden inside download() are detectable."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _is_rate_limit(text: str) -> bool:
    return "Rate" in text or "429" in text or "Too Many Requests" in text


def _closes(raw: pd.DataFrame, batch: list[str]) -> pd.DataFrame:
    """Extract a wide Close frame from yf.download output (MultiIndex or flat)."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        lvl = next((i for i in range(raw.columns.nlevels) if "Close" in raw.columns.get_level_values(i)), None)
        if lvl is None:
            return pd.DataFrame()
        out = raw.xs("Close", axis=1, level=lvl)
    else:
        if "Close" not in raw.columns:
            return pd.DataFrame()
        out = raw[["Close"]].rename(columns={"Close": batch[0]})
    out = out.loc[:, ~out.columns.duplicated()].astype(float)
    out.columns = [str(c) for c in out.columns]
    out.index = pd.DatetimeIndex(out.index).tz_localize(None) if getattr(out.index, "tz", None) else pd.DatetimeIndex(out.index)
    return out


def _fetch(batch: list[str], period: str, start, end) -> tuple[pd.DataFrame, bool]:
    """One yf.download call. Returns (closes, rate_limited)."""
    handler = _Collect()
    logger = logging.getLogger("yfinance")
    logger.addHandler(handler)
    try:
        kw = {"start": start, "end": end} if start else {"period": period}
        raw = yf.download(batch, auto_adjust=True, group_by="ticker", threads=True, progress=False, **kw)
    except Exception as e:  # YFRateLimitError or transport errors
        return pd.DataFrame(), _is_rate_limit(f"{type(e).__name__} {e}")
    finally:
        logger.removeHandler(handler)
    return _closes(raw, batch), any(_is_rate_limit(m) for m in handler.messages)


def download(tickers: list[str], period: str = "3y", start=None, end=None) -> tuple[pd.DataFrame, list[str]]:
    """Download auto-adjusted closes in batches of 40 with rate-limit backoff. Returns (wide closes, failed tickers)."""
    tickers = list(dict.fromkeys(t.upper() for t in tickers))
    frames, failed = [], []
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        closes, limited = _fetch(batch, period, start, end)
        for wait in BACKOFF:
            if not limited:
                break
            time.sleep(wait)
            closes, limited = _fetch(batch, period, start, end)
        closes = closes.dropna(axis=1, how="all")
        failed += [t for t in batch if t not in closes.columns]
        if not closes.empty:
            frames.append(closes[[t for t in batch if t in closes.columns]])
    df = pd.concat(frames, axis=1).sort_index() if frames else pd.DataFrame()
    return df, failed


def store(conn: sqlite3.Connection, closes: pd.DataFrame) -> None:
    """Replace each ticker's full history with the given closes (adjusted history changes; never append)."""
    with conn:
        for t in closes.columns:
            s = closes[t].dropna()
            conn.execute("DELETE FROM prices WHERE ticker = ?", (t,))
            conn.executemany("INSERT INTO prices (ticker, d, close) VALUES (?,?,?)",
                             [(t, d.strftime("%Y-%m-%d"), float(v)) for d, v in s.items()])


def load_closes(conn: sqlite3.Connection, tickers: list[str] | None = None, asof: date | None = None) -> pd.DataFrame:
    """Wide close frame (DatetimeIndex, sorted), optionally limited to tickers and rows with d <= asof."""
    sql, args = "SELECT ticker, d, close FROM prices WHERE 1=1", []
    if tickers:
        sql += f" AND ticker IN ({','.join('?' * len(tickers))})"
        args += list(tickers)
    if asof:
        sql += " AND d <= ?"
        args.append(asof.isoformat())
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        return pd.DataFrame(index=pd.DatetimeIndex([]))
    long = pd.DataFrame([tuple(r) for r in rows], columns=["ticker", "d", "close"])
    wide = long.pivot(index="d", columns="ticker", values="close")
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    wide.columns.name = None
    return wide.sort_index()


def suspicious_moves(series: pd.Series, splits: pd.Series | None = None, threshold: float = 0.5) -> list[str]:
    """ISO dates with |daily return| > threshold and no split within ±3 days. A flag only; prices are never changed."""
    rets = series.dropna().pct_change()
    split_days = [] if splits is None or len(splits) == 0 else \
        [pd.Timestamp(d).tz_localize(None) if pd.Timestamp(d).tz else pd.Timestamp(d) for d in splits[splits != 0].index]
    out = []
    for d, r in rets[rets.abs() > threshold].items():
        d = pd.Timestamp(d)
        if not any(abs((d - s).days) <= 3 for s in split_days):
            out.append(d.strftime("%Y-%m-%d"))
    return out
