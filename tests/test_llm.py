"""llm.py + the Explain routes, fully offline (httpx and the LLM are mocked)."""
import json
import time

import httpx
import pytest
from test_app import env  # noqa: F401  (fixture: temp DB with synthetic prices, network mocked)

from finres import app as appmod
from finres import estimates, llm

URL = "http://llm.test/v1"
GOOD = {"thesis": "Strong trend. Quality is high.",
        "bull": [{"point": "12-1m return of +85%", "evidence": ["ret_12_1"]},
                 {"point": "GP/assets 0.71", "evidence": ["gp_assets"]}],
        "bear": [{"point": "Worth $303B already", "evidence": ["market_cap"]},
                 {"point": "Priced at 14.4x forward", "evidence": ["forward_pe"]}],
        "change_my_mind": ["A monthly close below the 200-day average", "Estimate cuts"],
        "verdict": "hold", "confidence": "medium"}
ANALYSIS = {"ticker": "NVDA", "name": "NVIDIA", "price": 187.321, "ret_1m": 0.05, "ret_6m": float("nan"),
            "ret_12m": None, "ret_12_1": 0.851, "dd_52w": -0.03, "above200": True, "trend": True, "mom": 0.92,
            "qual": 0.88, "rev": None, "composite": 0.95, "label": "BUY", "rule": "UPTREND", "gp_assets": 0.7123, "rev_growth": 0.83,
            "rev_chg": 0.04, "forward_pe": 14.43, "earnings_yield": 0.031, "market_cap": 3.03e11, "sector": "Technology",
            "industry": "Semiconductors", "category": "AI chips", "speculative": False, "fund_pit": True,
            "eps_rev": {"0y": {"up30": 39, "down30": 1}}}
NEWS = [{"title": "Nvidia unveils chip", "publisher": "Reuters", "date": "2026-09-25"},
        {"title": "Second headline", "publisher": None, "date": None}]


def _resp(status=200, content=None, reasoning=None, text=None):
    req = httpx.Request("POST", URL + "/chat/completions")
    if text is not None:
        return httpx.Response(status, text=text, request=req)
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return httpx.Response(status, json={"choices": [{"message": msg}]}, request=req)


@pytest.fixture
def fake(monkeypatch):
    """Queue responses (or exceptions) for httpx.post; records every request body."""
    monkeypatch.setattr(llm.config, "LLM_URL", URL)
    q, bodies = [], []

    def post(url, json=None, timeout=None):
        bodies.append(json)
        r = q.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm.httpx, "post", post)
    return q, bodies


def test_facts_formatting_and_no_missing_values():
    f = llm.facts(ANALYSIS, NEWS)
    assert all(v and "None" not in v and "nan" not in v.lower() for v in f.values())
    assert "ret_6m" not in f and "ret_12m" not in f and "revisions_pct" not in f
    assert f["ret_12_1"] == "+85%" and f["gp_assets"] == "0.71" and f["market_cap"] == "$303B"
    assert f["forward_pe"] == "14.4x" and f["price"] == "$187.32" and f["from_52w_high"] == "-3%"
    assert f["above_200dma"] == "yes" and f["speculative"] == "no" and f["momentum_pct"] == "92nd percentile"
    assert f["eps_revisions_30d"] == "39 up / 1 down" and f["fundamentals_source"] == "SEC point-in-time"
    assert f["news_1"] == "2026-09-25 · Reuters · Nvidia unveils chip"
    assert f["news_2"] == "undated · unknown · Second headline" and "news_3" not in f
    assert f["rule_status"] == "in uptrend — buyable under the equal-weight rule"
    assert f["research_rank_pct"] == "95th percentile" and "model_label" not in f and "composite_pct" not in f
    assert "not buyable" in llm.facts(dict(ANALYSIS, rule="NO UPTREND"), [])["rule_status"]
    assert "rule_status" not in llm.facts(dict(ANALYSIS, rule="Insufficient data"), [])


def test_prompt_v2_mentions_rule_and_research_rank():
    assert llm.PROMPT_VERSION == "v2"
    assert "rule_status" in llm.SYSTEM and "research_rank_pct" in llm.SYSTEM and "does NOT beat equal weight" in llm.SYSTEM
    assert "model_label" not in llm.SYSTEM and "composite_pct" not in llm.SYSTEM


def test_explain_json_schema_success(fake):
    q, bodies = fake
    q.append(_resp(content="```json\n" + json.dumps(GOOD) + "\n```"))
    r = llm.explain(ANALYSIS, NEWS)
    assert r["ok"] and r["data"]["verdict"] == "hold" and r["prompt_version"] == "v2"
    assert r["grounding"] == {"bad_keys": [], "ungrounded_numbers": []}
    b = bodies[0]
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"]["strict"]
    assert b["reasoning_effort"] == "xhigh" and b["max_tokens"] == 12000 and b["temperature"] == 1.0
    assert "ret_12_1: +85%" in b["messages"][1]["content"]


def test_explain_falls_back_to_json_object(fake):
    q, bodies = fake
    q += [_resp(400, text='{"error": "response_format json_schema is not supported"}'), _resp(content=json.dumps(GOOD))]
    r = llm.explain(ANALYSIS, [])
    assert r["ok"] and len(bodies) == 2
    assert bodies[1]["response_format"] == {"type": "json_object"}
    assert '"change_my_mind"' in bodies[1]["messages"][0]["content"]


def test_explain_retries_once_on_invalid_json(fake):
    q, bodies = fake
    q += [_resp(content="{not json"), _resp(content=json.dumps(GOOD))]
    r = llm.explain(ANALYSIS, [])
    assert r["ok"] and len(bodies) == 2
    assert "invalid" in bodies[1]["messages"][-1]["content"]
    q += [_resp(content="nope"), _resp(content='{"thesis": "x"}')]
    r = llm.explain(ANALYSIS, [])
    assert not r["ok"] and "invalid JSON twice" in r["error"]


def test_explain_never_reads_reasoning_field(fake):
    q, _ = fake
    decoy = dict(GOOD, thesis="DECOY from reasoning")
    q += [_resp(content="garbage", reasoning=json.dumps(decoy)), _resp(content=json.dumps(GOOD), reasoning=json.dumps(decoy))]
    r = llm.explain(ANALYSIS, [])
    assert r["ok"] and r["data"]["thesis"] == GOOD["thesis"]


@pytest.mark.parametrize("exc, word", [(httpx.ReadTimeout("slow"), "timed out"),
                                       (httpx.ConnectError("refused"), "not running")])
def test_explain_timeout_and_refused(fake, exc, word):
    fake[0].append(exc)
    r = llm.explain(ANALYSIS, [])
    assert not r["ok"] and word in r["error"] and "llm-serve start splash4" in r["error"]


def test_grounding():
    f = {"ret_12_1": "+85%", "gp_assets": "0.71", "market_cap": "$303B", "from_52w_high": "-3%"}
    d = {"thesis": "Up +85% with GP/A 0.71 and worth $303B; 3% below the high. In 2026 it beat for 3 quarters.",
         "bull": [{"point": "Margin 42% is great", "evidence": ["gp_assets", "made_up"]}],
         "bear": [{"point": "Costs 1.2T", "evidence": ["market_cap"]}], "change_my_mind": ["Revenue near 85.4%"]}
    g = llm.grounding(d, f)
    assert g["bad_keys"] == ["made_up"]
    assert g["ungrounded_numbers"] == ["42%", "1.2T"]  # 2026 and "3" ignored; 85.4% is within 1% of 85%


def test_grounding_numbers_from_keys_count():
    g = llm.grounding({"thesis": "It trades above its 200-day average and near its 52-week high.", "bull": [],
                       "bear": [], "change_my_mind": []}, {"above_200dma": "yes", "from_52w_high": "-3%"})
    assert g["ungrounded_numbers"] == []


# ---- routes ---------------------------------------------------------------------------------------------------

@pytest.fixture
def job_reset():
    appmod.job.update(ticker=None, status=None, started=None, result=None)
    yield appmod.job
    appmod.job.update(ticker=None, status=None, started=None, result=None)


def _wait(job, timeout=60):
    end = time.time() + timeout
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.05)


def test_explain_route_llm_down(env, job_reset, monkeypatch):  # noqa: F811
    monkeypatch.setattr(llm, "up", lambda: False)
    r = env["client"].post("/explain/AA01")
    assert "Qwen is off" in r.text and "llm-serve start splash4" in r.text and job_reset["status"] is None


def test_explain_route_busy(env, job_reset, monkeypatch):  # noqa: F811
    monkeypatch.setattr(llm, "up", lambda: True)
    job_reset.update(ticker="AA02", status="running", started=time.time() - 40)
    r = env["client"].post("/explain/AA01")
    assert "busy explaining AA02" in r.text and "started 40 s ago" in r.text


def test_explain_route_cache_and_force(env, job_reset, monkeypatch):  # noqa: F811
    calls = []

    def fake_explain(a, news):
        calls.append((a["ticker"], news))
        return {"ok": True, "data": dict(GOOD, thesis="Fresh thesis."), "grounding": {"bad_keys": [], "ungrounded_numbers": ["42%"]},
                "facts": {}, "seconds": 7, "prompt_version": llm.PROMPT_VERSION}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "explain", fake_explain)
    monkeypatch.setattr(estimates, "news", lambda t, n=10: NEWS)
    rec = {"data": GOOD, "grounding": {"bad_keys": [], "ungrounded_numbers": []}, "seconds": 38,
           "prompt_version": llm.PROMPT_VERSION, "rule_label": "UPTREND", "created": "2026-09-27T10:00:00"}
    with env["conn"]:
        env["conn"].execute("INSERT INTO thesis VALUES (?,?,?,?)", ("AA01", appmod._week(), json.dumps(rec), rec["created"]))
    c = env["client"]
    r = c.post("/explain/AA01")
    assert "cached this week" in r.text and "Strong trend." in r.text and "All numbers traced to the data" in r.text
    assert "Rule: " in r.text and "UPTREND" in r.text and "disagrees" in r.text and calls == []
    r = c.post("/explain/AA01", params={"force": 1})
    assert 'hx-get="/explain/AA01"' in r.text and 'every 3s' in r.text
    _wait(job_reset)
    assert job_reset["status"] == "done" and calls == [("AA01", NEWS)]
    r = c.get("/explain/AA01")
    assert "Fresh thesis." in r.text and "Numbers not found in the data: 42%" in r.text and "7 s" in r.text
    row = env["conn"].execute("SELECT json FROM thesis WHERE ticker = 'AA01'").fetchone()
    saved = json.loads(row["json"])
    assert saved["data"]["thesis"] == "Fresh thesis." and saved["rule_label"] in ("UPTREND", "NO UPTREND")
    assert f"Rule: <span class=\"lbl l-{saved['rule_label'].lower().replace(' ', '-')}\">" in r.text


def test_explain_route_error_has_retry(env, job_reset, monkeypatch):  # noqa: F811
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "explain", lambda a, n: {"ok": False, "error": "Qwen timed out. llm-serve start splash4"})
    monkeypatch.setattr(estimates, "news", lambda t, n=10: [])
    env["client"].post("/explain/AA03")
    _wait(job_reset)
    r = env["client"].get("/explain/AA03")
    assert "Qwen timed out" in r.text and "Retry" in r.text and "force=1" in r.text
