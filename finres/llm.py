"""The ONE LLM role (ADR-002): Qwen narrates facts the code already computed. It never produces a signal."""
import json
import math
import re
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finres import config

PROMPT_VERSION = "v2"
HINT = "Start it with: llm-serve start splash4"
_up = {"at": -1e9, "up": False}

_point = {"type": "object", "additionalProperties": False, "required": ["point", "evidence"],
          "properties": {"point": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["thesis", "bull", "bear", "change_my_mind", "verdict", "confidence"],
          "properties": {"thesis": {"type": "string"},
                         "bull": {"type": "array", "items": _point, "minItems": 2, "maxItems": 4},
                         "bear": {"type": "array", "items": _point, "minItems": 2, "maxItems": 4},
                         "change_my_mind": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
                         "verdict": {"type": "string", "enum": ["buy", "hold", "avoid"]},
                         "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: str
    evidence: list[str]


class Thesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thesis: str
    bull: list[Point] = Field(min_length=2, max_length=4)
    bear: list[Point] = Field(min_length=2, max_length=4)
    change_my_mind: list[str] = Field(min_length=2, max_length=3)
    verdict: Literal["buy", "hold", "avoid"]
    confidence: Literal["low", "medium", "high"]


SYSTEM = """You are a skeptical buy-side equity analyst. Your one reader is a long-only individual investor who adds new money to US AI and AI-adjacent stocks once a month and holds for months to years. The app buys by a fixed rule (equal dollars into every name in an uptrend, least-held first); your job is to give an independent, skeptical opinion on the business and setup of ONE stock, not to cheerlead it.

The user message is a FACTS block of `key: value` lines computed by code. It is your ONLY source of numbers.
Key glossary: ret_12_1 = return from 12 months ago to 1 month ago; from_52w_high = distance below the 52-week high; gp_assets = gross profit / total assets (profitability); revenue_growth_ttm = trailing-twelve-month revenue growth; fy_eps_est_change_90d = change in analysts' fiscal-year EPS estimates over 90 days; eps_revisions_30d = analysts raising vs cutting estimates in 30 days; trend_gate = price above its 200-day average AND 50-day above 200-day; volatility_1y = annualized volatility; research_rank_pct = research rank; the lab found it does NOT beat equal weight — treat as context only; rule_status = what the app's shipped buy/sell rule says about this stock; model_reason = the research ranking's one-line reason.

Rules:
1. Use ONLY the FACTS. Never invent, estimate or recall numbers (prices, revenue, margins, growth, multiples, targets, dates). Never compute new ratios, differences or sums. When you use a number, copy it exactly as written in the FACTS.
2. You may use general background knowledge of what the company does (products, customers, competitors, industry dynamics), but that background must contain no numbers.
3. Every bull and bear point must list in `evidence` the exact FACTS keys it relies on (e.g. "ret_12_1", "gp_assets", "news_3"). At least one key per point; only keys that appear in the FACTS.
4. If a fact you would need is missing from the FACTS, say so plainly (e.g. "no estimate-revision data") instead of guessing.
5. How to weigh the evidence:
   - momentum_pct, quality_pct, revisions_pct and research_rank_pct are percentile ranks within the app's AI-stock universe (higher is better). They are research context, not a validated edge.
   - Momentum and the 200-day trend have the longest published evidence; quality is secondary; revisions are unvalidated and carry little weight.
   - Failing the trend gate or trading below the 200-day average is a real negative for new money, however good the story.
   - speculative = yes means the company is losing money. Fundamentals that are not point-in-time deserve less trust.
   - News headlines are context, not proof. Do not build a point on one headline alone.
6. Your verdict is YOUR opinion on the business and setup. rule_status is the app's rule (uptrend = buyable). If your verdict disagrees with rule_status (e.g. "avoid" for a name in an uptrend, or "buy" for one with no uptrend), the thesis must say so and why.
7. verdict is about NEW money this month: "buy", "hold" (keep if owned, do not add) or "avoid". confidence reflects how consistent the facts are and how much is missing.
8. change_my_mind: 2-3 concrete, observable developments that would flip your verdict (e.g. "a monthly close below the 200-day average", "analysts start cutting estimates"). Do not invent numeric thresholds.
9. Style: thesis is 2-3 sentences. Each point is one short sentence. Plain English, no hype, no disclaimers.

Reply with ONLY the JSON object."""


def up() -> bool:
    """Is the local LLM server answering? One 1 s probe, cached 30 s."""
    if time.monotonic() - _up["at"] > 30:
        try:
            _up["up"] = httpx.get(f"{config.LLM_URL}/models", timeout=1).status_code == 200
        except httpx.HTTPError:
            _up["up"] = False
        _up["at"] = time.monotonic()
    return _up["up"]


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and not math.isfinite(v))


def _money(v: float) -> str:
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            x = v / div
            return f"${x:.0f}{unit}" if abs(x) >= 100 else f"${x:.1f}{unit}"
    return f"${v:,.0f}"


def _ord(v: float) -> str:
    n = round(v * 100)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')} percentile"


RULE_STATUS = {"UPTREND": "in uptrend — buyable under the equal-weight rule",
               "NO UPTREND": "no uptrend — not buyable; held positions sell after 2 month-ends below 200DMA"}
_sp, _p, _yn = (lambda v: f"{v:+.0%}"), (lambda v: f"{v:.1%}"), (lambda v: "yes" if v else "no")
FIELDS = [("company", "name", str), ("ticker", "ticker", str), ("price", "price", lambda v: f"${v:,.2f}"),
          ("ret_1m", "ret_1m", _sp), ("ret_6m", "ret_6m", _sp), ("ret_12m", "ret_12m", _sp),
          ("ret_12_1", "ret_12_1", _sp), ("from_52w_high", "dd_52w", _sp), ("volatility_1y", "vol_252", lambda v: f"{v:.0%}"),
          ("above_200dma", "above200", _yn), ("trend_gate", "trend", lambda v: "passes" if v else "fails"),
          ("momentum_pct", "mom", _ord), ("quality_pct", "qual", _ord), ("revisions_pct", "rev", _ord),
          ("research_rank_pct", "composite", _ord), ("rule_status", "rule", lambda v: RULE_STATUS[v]),
          ("model_reason", "reason", str),
          ("gp_assets", "gp_assets", lambda v: f"{v:.2f}"), ("revenue_growth_ttm", "rev_growth", _sp),
          ("fy_eps_est_change_90d", "rev_chg", _sp), ("forward_pe", "forward_pe", lambda v: f"{v:.1f}x"),
          ("earnings_yield", "earnings_yield", _p), ("market_cap", "market_cap", _money), ("sector", "sector", str),
          ("industry", "industry", str), ("category", "category", str), ("speculative", "speculative", _yn)]


def facts(analysis: dict, news: list[dict]) -> dict[str, str]:
    """Flat, human-formatted facts from state.analyze() output; missing values are skipped, never 'None'."""
    out = {}
    for key, src, fmt in FIELDS:
        v = analysis.get(src)
        if _ok(v) and v != "" and (src != "rule" or v in RULE_STATUS):
            out[key] = fmt(v)
    rv = (analysis.get("eps_rev") or {}).get("0y") or {}
    if _ok(rv.get("up30")) and _ok(rv.get("down30")):
        out["eps_revisions_30d"] = f"{int(rv['up30'])} up / {int(rv['down30'])} down"
    if analysis.get("fund_pit") is not None:
        out["fundamentals_source"] = "SEC point-in-time" if analysis["fund_pit"] else "Yahoo, not point-in-time"
    for i, n in enumerate([n for n in news or [] if n.get("title")][:10], 1):
        out[f"news_{i}"] = f"{n.get('date') or 'undated'} · {n.get('publisher') or 'unknown'} · {n['title']}"
    return out


NUM = re.compile(r"(?<![A-Za-z0-9.])[-+]?\$?\d[\d,]*(?:\.\d+)?(?:\s?%|[xXBMTK]\b)?")


def _nums(text: str) -> list[tuple[str, float, int, bool]]:
    """(raw, value, decimals, bare integer) for each number-looking token."""
    out = []
    for m in NUM.finditer(text):
        raw = m.group().strip()
        s = re.sub(r"[+\-$,%xXBMTK\s]", "", raw)
        if not s or s.count(".") > 1:
            continue
        dec = len(s.split(".")[1]) if "." in s else 0
        out.append((raw, float(s), dec, dec == 0 and not re.search(r"[%$xXBMTK]", raw)))
    return out


def grounding(data: dict, facts: dict) -> dict:
    """Evidence keys not in facts, and numbers in the text that no fact (key or value) contains."""
    points = data.get("bull", []) + data.get("bear", [])
    bad = sorted({k for p in points for k in p.get("evidence", []) if k not in facts})
    known = [v for s in list(facts) + list(facts.values()) for _, v, _, _ in _nums(str(s).replace("_", " "))]
    texts = [data.get("thesis", "")] + [p.get("point", "") for p in points] + list(data.get("change_my_mind", []))
    ungrounded = []
    for raw, v, dec, bare in (n for t in texts for n in _nums(t)):
        if bare and (v <= 12 or 1990 <= v <= 2035):
            continue
        if not any(round(f, dec) == round(v, dec) or abs(f - v) <= 0.01 * max(abs(f), abs(v)) for f in known):
            if raw not in ungrounded:
                ungrounded.append(raw)
    return {"bad_keys": bad, "ungrounded_numbers": ungrounded}


def _parse(content: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
    return Thesis.model_validate_json(s).model_dump()


def explain(analysis: dict, news: list[dict]) -> dict:
    """Ask Qwen for a grounded thesis. Returns {"ok": True, data, grounding, seconds, ...} or {"ok": False, error}."""
    f = facts(analysis, news)
    block = "FACTS\n" + "\n".join(f"{k}: {v}" for k, v in f.items())
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": block}]
    body = {"model": config.LLM_MODEL, "messages": msgs, "temperature": 1.0, "max_tokens": 12000,
            "reasoning_effort": "xhigh",
            "response_format": {"type": "json_schema", "json_schema": {"name": "thesis", "strict": True, "schema": SCHEMA}}}
    t0, fell_back = time.monotonic(), False
    try:
        for attempt in range(2):
            r = httpx.post(f"{config.LLM_URL}/chat/completions", json=body, timeout=900)
            if r.status_code in (400, 422) and not fell_back and re.search(r"response_format|json_schema", r.text):
                fell_back = True  # server can't do json_schema: plain JSON mode + schema in the prompt
                msgs[0] = {"role": "system", "content": SYSTEM + "\n\nThe JSON must match this JSON Schema:\n" + json.dumps(SCHEMA)}
                body.update(messages=msgs, response_format={"type": "json_object"})
                r = httpx.post(f"{config.LLM_URL}/chat/completions", json=body, timeout=900)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content") or ""  # never the reasoning field
            try:
                data = _parse(content)
                break
            except ValidationError as e:
                if attempt:
                    return {"ok": False, "error": f"Qwen returned invalid JSON twice: {str(e)[:300]}"}
                body["messages"] = msgs + [{"role": "assistant", "content": content[:4000]},
                                           {"role": "user", "content": f"That reply was invalid:\n{str(e)[:1500]}\n"
                                                                       "Reply again with ONLY the corrected JSON object."}]
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Qwen timed out after 15 minutes. {HINT}"}
    except httpx.ConnectError:
        return {"ok": False, "error": f"Qwen is not running (connection refused). {HINT}"}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        return {"ok": False, "error": f"Qwen request failed: {type(e).__name__}: {str(e)[:300]}. {HINT}"}
    return {"ok": True, "data": data, "grounding": grounding(data, f), "facts": f,
            "seconds": round(time.monotonic() - t0), "prompt_version": PROMPT_VERSION}
