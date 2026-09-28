"""Paths, env settings and universe loading."""
import os
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

DB_PATH = Path(os.environ.get("FINRES_DB") or DATA / "finres.db")
LLM_URL = os.environ.get("FINRES_LLM_URL") or "http://127.0.0.1:8089/v1"
LLM_MODEL = os.environ.get("FINRES_LLM_MODEL") or "qwen-local"
_asof = os.environ.get("FINRES_ASOF")
ASOF: date | None = date.fromisoformat(_asof) if _asof else None
# SEC 403s the users.noreply.github.com domain; set FINRES_SEC_UA to "Name real@email" for fair-access compliance.
SEC_UA = os.environ.get("FINRES_SEC_UA") or "FinRes personal-research finres-app@example.com"
BENCHMARKS = ["SPY", "SMH", "QQQ"]


def load_universe(path: Path = ROOT / "universe.toml") -> dict:
    """Parse universe.toml into categories, per-ticker lookups and an ordered ticker list."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    laggards = list(raw.get("laggards", {}).get("tickers", [])) if isinstance(raw.get("laggards"), dict) \
        else list(raw.get("laggards", []))
    cats: dict = {}
    ticker_category: dict = {}
    ticker_group: dict = {}
    tickers: list = []
    for key, val in raw.get("categories", {}).items():
        group = val.get("group", key)
        cats[key] = {"name": val.get("name", key), "group": group, "tickers": list(val.get("tickers", []))}
        for t in cats[key]["tickers"]:
            if t not in ticker_category:
                ticker_category[t] = key
                ticker_group[t] = group
                tickers.append(t)
    return {"categories": cats, "ticker_category": ticker_category, "ticker_group": ticker_group,
            "tickers": tickers, "laggards": laggards}
