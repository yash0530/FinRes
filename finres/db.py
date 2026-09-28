"""SQLite schema (exactly 5 tables) and tiny helpers."""
import json
import sqlite3
from pathlib import Path

from finres import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices   (ticker TEXT, d TEXT, close REAL, PRIMARY KEY (ticker, d));
CREATE TABLE IF NOT EXISTS snapshot (ticker TEXT, date TEXT, factors TEXT, raw TEXT, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS thesis   (ticker TEXT, week TEXT, json TEXT, created TEXT, PRIMARY KEY (ticker, week));
CREATE TABLE IF NOT EXISTS holdings (ticker TEXT PRIMARY KEY, shares REAL NOT NULL, cost REAL NOT NULL, added TEXT);
CREATE TABLE IF NOT EXISTS picks    (month TEXT, ticker TEXT, price REAL, rank INTEGER, reason TEXT, d TEXT,
                                    PRIMARY KEY (month, ticker));
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the DB (WAL, Row factory, thread-shareable) and ensure the schema exists."""
    conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:  # M8b: picks.d (the pick price's close date) for DBs created before it existed
        conn.execute("ALTER TABLE picks ADD COLUMN d TEXT")
    except sqlite3.OperationalError:
        pass  # already there
    return conn


def upsert_snapshot(conn: sqlite3.Connection, ticker: str, date: str, factors: dict, raw: dict) -> None:
    """Insert or replace one ticker's snapshot for a date."""
    with conn:
        conn.execute("INSERT OR REPLACE INTO snapshot VALUES (?,?,?,?)",
                     (ticker, date, json.dumps(factors), json.dumps(raw)))


def latest_snapshots(conn: sqlite3.Connection) -> dict[str, tuple[str, dict, dict]]:
    """Most recent snapshot per ticker: {ticker: (date, factors, raw)}."""
    rows = conn.execute(
        "SELECT s.ticker, s.date, s.factors, s.raw FROM snapshot s "
        "JOIN (SELECT ticker, MAX(date) AS d FROM snapshot GROUP BY ticker) m "
        "ON s.ticker = m.ticker AND s.date = m.d").fetchall()
    return {r["ticker"]: (r["date"], json.loads(r["factors"]), json.loads(r["raw"])) for r in rows}
