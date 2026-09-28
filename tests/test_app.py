"""App routes on a temp DB with synthetic prices; every network function is mocked."""
import time
from datetime import date, timedelta

import httpx
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
    monkeypatch.setattr(edgar, "recent_8k", lambda *a, **k: [])
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
    up = [x for x in s["categories"][0]["rows"] + s["categories"][1]["rows"] if x["rule"] == "UPTREND"]
    c.post("/holdings", data={"text": "\n".join(f"{x['ticker']} 10 10" for x in up)})  # all held: buys top them up
    s = state.build(conn)
    buys = {b["ticker"]: b for b in s["buys"]}
    first = s["buys"][0]
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


def test_buys_are_b0r_least_held_equal_dollars(env, monkeypatch):
    c, conn = env["client"], env["conn"]
    monkeypatch.setitem(UNI, "ticker_group", {t: t[:3] for t in UNI["tickers"]})  # 5 groups: the 3-per-group cap won't bind
    s = state.build(conn)
    rows = {x["ticker"]: x for cat in s["categories"] for x in cat["rows"]}
    up = [t for t, x in rows.items() if x["rule"] == "UPTREND"]
    assert len(up) > 10 and any(x["rule"] == "NO UPTREND" for x in rows.values())
    buys = s["buys"]
    assert len(buys) == 10 and all(b["ticker"] in up for b in buys)
    assert {b["dollars"] for b in buys} == {250.0} and all(b["held"] == 0 for b in buys)
    first = buys[0]["ticker"]
    c.post("/holdings", data={"text": f"{first} 1 10"})
    buys = state.build(conn)["buys"]
    assert buys[0]["ticker"] != first and first not in [b["ticker"] for b in buys]  # held name goes to the back
    assert all(b["ticker"] in up for b in buys) and len(buys) <= 10
    html = c.get("/").text
    assert "10 uptrend names × $250" in html and "you hold $0" in html and "Rules: B0R · S2" in html


def test_page_shows_rule_labels_not_ranking_labels(env):
    c, conn = env["client"], env["conn"]
    down = [x["ticker"] for cat in state.build(conn)["categories"] for x in cat["rows"] if x["rule"] == "NO UPTREND"]
    c.post("/holdings", data={"text": "\n".join(f"{t} 5 1" for t in down)})  # some already sell, some not yet
    html = c.get("/").text
    for word in ("at cap", "Brake", "BUY", "AVOID", "WATCH"):
        assert word not in html, word
    assert "UPTREND" in html and "NO UPTREND" in html and "in uptrend" in html and "top research rank" in html
    assert "no uptrend (sell after 2 month-ends below 200DMA)" in html and 'class="warn-t">2 month-ends below 200DMA' in html
    assert 'title="Research rank (momentum 60% / quality 40%' in html


def test_sells_are_s2_only(env):
    conn = env["conn"]
    s = state.build(conn)
    worst = min((x for cat in s["categories"] for x in cat["rows"] if x.get("composite") is not None),
                key=lambda x: x["composite"])
    with conn:  # a -35% stop on AA02 and a low-rank name at a fine cost: only the stop fires
        conn.executemany("INSERT INTO holdings VALUES (?,?,?,?)",
                         [("AA02", 1, 1e6, "2026-01-01"), (worst["ticker"], 1, 0.01, "2026-01-01")])
    sells = {x["ticker"]: x["rule"] for x in state.build(conn)["sells"]}
    assert sells.get("AA02") == "-35% stop"
    assert set(sells.values()) <= {"-35% stop", "2 month-ends below 200DMA"}


def test_lab_headline_from_json(env, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(state, "LAB_DIR", tmp_path)
    assert "Validation pending" in env["client"].get("/").text
    (tmp_path / "oos.json").write_text(json.dumps({
        "period": ["2019-01", "2026-08"], "selected": "W2", "rule": {"beats_b0": False},
        "rows": [{"name": "W2", "xirr": 0.361}, {"name": "DCA SMH", "xirr": 0.399}]}))
    assert "Validation pending" in env["client"].get("/").text  # fidelity.json still missing
    (tmp_path / "fidelity.json").write_text(json.dumps({"ai_OOS": [{"name": "ew_trend10", "xirr": 0.403},
                                                                   {"name": "ew_all", "xirr": 0.361}]}))
    html = env["client"].get("/").text
    assert ("Lab (2019–2026, out-of-sample): uptrend rotation 40.3% vs equal-weight universe 36.1% vs SMH DCA "
            "39.9% XIRR; the ranking model 36.1% failed its gates.") in html and "Validation pending" not in html


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


def test_sec_8k_flags_tag_rows_and_warn_under_buy(env, monkeypatch):
    c, conn = env["client"], env["conn"]
    target = state.build(conn)["buys"][0]["ticker"]
    flag = [{"date": "2026-09-12", "items": ["4.01"], "labels": ["auditor change"]}]
    calls = []

    def fake(t, asof, days=45, max_age_days=1):
        calls.append((t, max_age_days))
        return flag if t == target else []

    monkeypatch.setattr(edgar, "recent_8k", fake)
    state.clear_cache()
    c.post("/holdings", data={"text": f"{target} 1 10"})
    s = state.build(conn)
    assert calls and all(m is None for _, m in calls)  # page loads are cache-only (never network)
    rows = {x["ticker"]: x for cat in s["categories"] for x in cat["rows"]}
    other = next(t for t in rows if t != target)
    assert rows[target]["sec_flags"] == flag and rows[other]["sec_flags"] == []
    assert {"ticker": target, "text": "SEC 8-K: auditor change (2026-09-12)"} in s["watch"]
    html = c.get("/").text
    assert html.count('class="tag red" title="Serious SEC 8-K in the last 45 days: auditor change (2026-09-12)">8-K</span>') >= 2
    with conn:
        conn.execute("DELETE FROM holdings")  # unheld: back in the buy list
    assert target in [b["ticker"] for b in state.build(conn)["buys"]]
    html = c.get("/").text
    assert "⚠ 8-K 4.01 auditor change on 2026-09-12 — read the filing before buying" in html
    a = state.analyze(conn, target)
    assert a["sec_flags"] == flag and "SEC 8-K: auditor change (2026-09-12)" in a["warnings"]
    assert "8-K</span> Watch:" in c.get("/analyze", params={"t": target}).text


def test_refresh_fetches_sec_8k_for_universe_and_holdings(env, monkeypatch):
    c, conn = env["client"], env["conn"]
    seen = []

    def fake(t, asof, days=45, max_age_days=1):
        seen.append((t, max_age_days))
        if t == "AA05":
            raise httpx.ConnectError("down")
        return []

    monkeypatch.setattr(edgar, "recent_8k", fake)
    with conn:
        conn.execute("INSERT INTO holdings VALUES ('ZZZ', 1, 1, '2026-01-01')")
    c.post("/refresh")
    appmod._thread.join(timeout=60)
    refreshed = [t for t, m in seen if m == 1]
    assert set(refreshed) == set(UNI["tickers"]) | {"ZZZ"} and len(refreshed) == len(UNI["tickers"]) + 1
    assert appmod.progress["errors"] >= 1 and appmod.progress["step"] == "done"  # 8-K failure counted, not fatal
    assert conn.execute("SELECT COUNT(*) FROM snapshot WHERE date = ?", (date.today().isoformat(),)).fetchone()[0] == len(A + B)
