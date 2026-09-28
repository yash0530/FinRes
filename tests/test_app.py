"""App routes on a temp DB with synthetic prices; every network function is mocked."""
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finres import app as appmod
from finres import config, db, edgar, estimates, prices, state

A = [f"AA{i:02d}" for i in range(25)]
B = [f"BB{i:02d}" for i in range(20)]
UNI = {"categories": {"a": {"name": "Alpha chips", "group": "g1", "tickers": A},
                      "b": {"name": "Beta power", "group": "g2", "tickers": B}},
       "ticker_category": {t: "a" for t in A} | {t: "b" for t in B},
       "ticker_group": {t: "g1" for t in A} | {t: "g2" for t in B},
       "tickers": A + B, "laggards": []}


def synth_closes(tickers, n=400, end="2026-09-25") -> pd.DataFrame:
    """Geometric random walks; most drift up (so trend passes and the brake is off)."""
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(3)
    out = {}
    for i, t in enumerate(tickers):
        drift = 0.0015 if t in ("SPY", "SMH", "QQQ") else (0.0025 - 0.00008 * i if i % 5 else -0.001)
        out[t] = 50 * np.exp(np.cumsum(drift + 0.012 * rng.standard_normal(n)))
    return pd.DataFrame(out, index=idx)


def fake_snapshot(t: str) -> dict:
    h = sum(map(ord, t))
    return {"name": f"{t} Corp", "sector": "Tech", "industry": "Chips", "price": 50.0, "trailing_eps": 1 + h % 5,
            "forward_pe": 20.0, "revenue_growth": (h % 40) / 100,
            "eps_trend": {"0y": {"current": 2 + h % 7 / 10, "d30": 2, "d90": 2}, "+1y": {"current": 3, "d30": 3, "d90": 3}},
            "eps_rev": {"0y": {"up30": h % 6, "down30": 1}, "+1y": {"up30": 2, "down30": 2}}, "ok": True, "error": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setenv("FINRES_DB", str(path))
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "ASOF", None)
    monkeypatch.setattr(config, "load_universe", lambda *a, **k: UNI)
    monkeypatch.setattr(edgar, "companyfacts", lambda *a, **k: None)
    monkeypatch.setattr(estimates, "snapshot", fake_snapshot)
    calls = []

    def fake_download(tickers, *a, **k):
        calls.append(list(tickers))
        known = [t for t in tickers if t in UNI["tickers"] + config.BENCHMARKS]
        return synth_closes(UNI["tickers"] + config.BENCHMARKS)[known], [t for t in tickers if t not in known]

    monkeypatch.setattr(prices, "download", fake_download)
    monkeypatch.setattr(state, "llm_up", lambda: False)
    state.clear_cache()
    appmod.progress.update(running=False, finished=None, errors=0)
    conn = db.connect(path)
    prices.store(conn, synth_closes(UNI["tickers"] + config.BENCHMARKS))
    yday = (date.today() - timedelta(days=1)).isoformat()  # recent snapshot: no auto-refresh on GET /
    for t in UNI["tickers"]:
        db.upsert_snapshot(conn, t, yday, {}, fake_snapshot(t))
    yield {"conn": conn, "client": TestClient(appmod.app), "downloads": calls}
    conn.close()


def _holdings(conn) -> dict:
    return {r["ticker"]: (r["shares"], r["cost"]) for r in conn.execute("SELECT * FROM holdings")}


def test_index_renders_all_sections(env):
    r = env["client"].get("/")
    assert r.status_code == 200
    for sid in ("plan", "holdings", "analyze", "categories", "track", "header"):
        assert f'id="{sid}"' in r.text
    assert "Buy with $2,500" in r.text
    assert r.text.count('class="chip') > 100
    assert 'id="explain-btn"' not in r.text  # only on the analyze card
    assert not env["downloads"] and not appmod.progress["running"]


def test_holdings_paste_replace_and_all_or_nothing(env):
    c, conn = env["client"], env["conn"]
    r = c.post("/holdings", data={"text": "# mine\nAA01 10 $40.5\n\nBB02, 3.5, 20\n"})
    assert r.headers.get("HX-Refresh") == "true"
    assert _holdings(conn) == {"AA01": (10, 40.5), "BB02": (3.5, 20)}
    r = c.post("/holdings", data={"text": "AA03 5 10\nBB04 five 10\nAA05 1\n"})
    assert "HX-Refresh" not in r.headers
    assert "Line 2" in r.text and "Line 3" in r.text and "Nothing saved" in r.text
    assert _holdings(conn) == {"AA01": (10, 40.5), "BB02": (3.5, 20)}
    c.post("/holdings", data={"text": "AA07 2 30"})
    assert _holdings(conn) == {"AA07": (2, 30)}
    assert "AA07" in c.get("/").text


def test_plan_done_idempotent_and_weighted_cost(env):
    c, conn = env["client"], env["conn"]
    s = state.build(conn)
    assert s["buys"], "synthetic data should produce buys"
    first = s["buys"][0]
    c.post("/holdings", data={"text": f"{first['ticker']} 10 10"})
    s = state.build(conn)
    buys = {b["ticker"]: b for b in s["buys"]}
    r = c.post("/plan/done")
    assert r.headers.get("HX-Refresh") == "true"
    n = conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
    assert n == len(buys)
    r = c.post("/plan/done")
    assert "Already recorded for 2026-09" in r.text
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == n
    h = _holdings(conn)
    b = buys[first["ticker"]]
    sh = 10 + b["shares"]
    assert h[first["ticker"]][0] == pytest.approx(sh)
    assert h[first["ticker"]][1] == pytest.approx((10 * 10 + b["shares"] * b["close"]) / sh)
    assert set(buys) <= set(h)
    assert "No picks recorded yet" not in c.get("/").text


def test_analyze_errors_and_card(env):
    c = env["client"]
    assert "not a valid ticker" in c.get("/analyze", params={"t": "BAD$$"}).text
    assert "No price data for ZZZZ" in c.get("/analyze", params={"t": "zzzz"}).text
    r = c.get("/analyze", params={"t": "AA01"})
    assert "AA01 Corp" in r.text and "<polyline" in r.text and 'id="explain-btn"' in r.text
    a = state.analyze(env["conn"], "AA01")
    assert len(a["closes"]) == 252 and a["grade_composite"] in "ABCDF"


def test_refresh_thread_one_snapshot_row_per_ticker(env):
    c, conn = env["client"], env["conn"]
    today = date.today().isoformat()
    for _ in range(2):
        r = c.post("/refresh")
        assert "hx-get=\"/refresh/status\"" in r.text or not appmod.progress["running"]
        appmod._thread.join(timeout=60)
        assert not appmod.progress["running"] and appmod.progress["errors"] == 0
        n = conn.execute("SELECT COUNT(*) FROM snapshot WHERE date = ?", (today,)).fetchone()[0]
        assert n == len(UNI["tickers"])
    assert c.get("/refresh/status").headers.get("HX-Refresh") == "true"
    snap = db.latest_snapshots(conn)["AA01"]
    assert snap[0] == today and "composite" in snap[1] and snap[2]["name"] == "AA01 Corp"


def test_coverage_below_90_withholds_ranking(env):
    conn = env["conn"]
    with conn:
        for t in A[:6]:
            conn.execute("DELETE FROM prices WHERE ticker = ? AND d > '2026-09-10'", (t,))
    s = state.build(conn)
    assert s["withheld"] and s["coverage"] < 0.9 and s["buys"] == []
    r = env["client"].get("/")
    assert "Ranking withheld" in r.text and "Buy with $2,500" not in r.text
    assert "stale" in r.text


def test_replay_never_downloads(env, monkeypatch):
    monkeypatch.setattr(config, "ASOF", date(2026, 6, 30))
    with env["conn"]:
        env["conn"].execute("DELETE FROM snapshot")  # would trigger an auto-refresh when live
    c = env["client"]
    r = c.get("/")
    assert r.status_code == 200 and "Replay as of 2026-06-30" in r.text
    assert "Data as of 2026-06-30" in r.text
    c.post("/refresh")
    c.get("/analyze", params={"t": "AA02"})
    assert "No price data for QQQQ" in c.get("/analyze", params={"t": "QQQQ"}).text
    time.sleep(0.05)
    assert env["downloads"] == [] and not appmod.progress["running"]
