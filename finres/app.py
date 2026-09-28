"""FastAPI routes + the single on-demand refresh thread. All math lives in signals.py / model.py."""
import json
import math
import re
import threading
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from finres import config, db, edgar, estimates, llm, prices, state

HERE = Path(__file__).resolve().parent
TICKER = re.compile(r"^[A-Z0-9.\-]{1,10}$")
REFRESHED = {"HX-Refresh": "true"}

app = FastAPI(title="FinRes")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _fmt(spec: str):
    def f(v):
        try:
            return spec.format(v)
        except (TypeError, ValueError):
            return "–"
    return f


templates.env.filters.update(pct=_fmt("{:.0%}"), spct=_fmt("{:+.0%}"), spct1=_fmt("{:+.1%}"),
                             money=_fmt("${:,.2f}"), money0=_fmt("${:,.0f}"), num=_fmt("{:,.2f}"),
                             sh=_fmt("{:,.3f}"))

# One refresh at a time: the lock guards start; `progress` is read by the status partial.
_lock = threading.Lock()
_thread: threading.Thread | None = None
progress = {"running": False, "step": None, "done": 0, "total": 0, "errors": 0, "started": None, "finished": None}


def _conn():
    return db.connect(config.DB_PATH)


def _render(request: Request, name: str, headers: dict | None = None, **ctx):
    return templates.TemplateResponse(request, name, ctx, headers=headers)


def _step(name: str, total: int) -> None:
    progress.update(step=name, done=0, total=total)


def _refresh() -> None:
    """prices -> estimates -> EDGAR -> SEC 8-Ks -> one snapshot row per ticker for today. Per-ticker failures only count."""
    today = date.today().isoformat()
    conn = _conn()
    try:
        u = config.load_universe()
        tickers = u["tickers"]
        held = [r["ticker"] for r in conn.execute("SELECT ticker FROM holdings")]
        _step("prices", 1)
        try:
            df, failed = prices.download(list(dict.fromkeys(tickers + config.BENCHMARKS + held)), period="10y")
            prices.store(conn, df)
            progress["errors"] += len(failed)
        except Exception:
            progress["errors"] += 1
        progress["done"] = 1
        old, raws = db.latest_snapshots(conn), {}
        _step("estimates", len(tickers))
        for x in tickers:
            try:
                raw = estimates.snapshot(x)
            except Exception as e:
                raw = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if not raw.get("ok"):
                progress["errors"] += 1
                raw = old[x][2] if x in old else raw  # keep the last good estimates
            raws[x] = raw
            progress["done"] += 1
        _step("edgar", len(tickers))
        for x in tickers:
            try:
                edgar.companyfacts(x)
            except Exception:
                progress["errors"] += 1
            progress["done"] += 1
        sec = list(dict.fromkeys(tickers + held))
        _step("sec 8-K", len(sec))
        for x in sec:  # submissions JSON -> severe 8-K warnings (cached; page loads never fetch)
            try:
                edgar.recent_8k(x, date.today(), max_age_days=1)
            except Exception:
                progress["errors"] += 1
            progress["done"] += 1
        _step("snapshot", len(tickers))
        state.clear_cache()
        fr = state.frame(conn, None, raws=raws)
        for x in tickers:
            try:
                db.upsert_snapshot(conn, x, today, state.snapshot_factors(fr, x), raws[x])
            except Exception:
                progress["errors"] += 1
            progress["done"] += 1
    except Exception:
        progress["errors"] += 1
    finally:
        conn.close()
        progress.update(running=False, step="done", finished=time.time())


def start_refresh() -> bool:
    """Start the refresh thread unless one is running or we are in replay mode."""
    global _thread
    with _lock:
        if progress["running"] or config.ASOF is not None:
            return False
        progress.update(running=True, step="prices", done=0, total=1, errors=0, started=time.time(), finished=None)
        _thread = threading.Thread(target=_refresh, name="finres-refresh", daemon=True)
        _thread.start()
    return True


def _needs_refresh(conn) -> bool:
    last = conn.execute("SELECT MAX(date) FROM snapshot").fetchone()[0]
    return last is None or (date.today() - date.fromisoformat(last)).days > 6


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with closing(_conn()) as conn:
        if config.ASOF is None and not progress["running"] and _needs_refresh(conn):
            start_refresh()
        s = state.build(conn, config.ASOF)
    return _render(request, "index.html", s=s, p=progress)


@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    start_refresh()
    return _render(request, "_progress.html", p=progress, replay=config.ASOF is not None)


@app.get("/refresh/status", response_class=HTMLResponse)
def refresh_status(request: Request):
    done = not progress["running"] and progress["finished"] is not None
    return _render(request, "_progress.html", headers=REFRESHED if done else None, p=progress,
                   replay=config.ASOF is not None)


@app.get("/analyze", response_class=HTMLResponse)
def analyze(request: Request, t: str = ""):
    t = t.strip().upper()
    if not TICKER.match(t):
        a = {"error": f"“{t[:20]}” is not a valid ticker (letters, digits, . or -, up to 10)."}
    else:
        with closing(_conn()) as conn:
            a = state.analyze(conn, t, config.ASOF)
    return _render(request, "_analyze.html", a=a, llm_up=state.llm_up())


def parse_holdings(text: str) -> tuple[list[tuple[str, float, float]], list[tuple[int, str, str]]]:
    """Lines `TICKER SHARES COST` -> (rows, errors[(line_no, line, why)]). Blank and # lines are skipped."""
    rows, errors, seen = [], [], set()
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p for p in re.split(r"[\s,]+", s) if p]
        if len(parts) != 3:
            errors.append((i, s, f"expected 3 fields (TICKER SHARES COST), got {len(parts)}"))
            continue
        tk = parts[0].upper()
        try:
            shares, cost = float(parts[1]), float(parts[2].replace("$", ""))
        except ValueError:
            errors.append((i, s, "shares and cost must be numbers"))
            continue
        if not TICKER.match(tk):
            errors.append((i, s, f"“{tk}” is not a valid ticker"))
        elif not (math.isfinite(shares) and shares > 0):
            errors.append((i, s, "shares must be greater than 0"))
        elif not (math.isfinite(cost) and cost >= 0):
            errors.append((i, s, "cost must be 0 or more"))
        elif tk in seen:
            errors.append((i, s, f"{tk} is listed twice"))
        else:
            seen.add(tk)
            rows.append((tk, shares, cost))
    return rows, errors


@app.post("/holdings", response_class=HTMLResponse)
def holdings(request: Request, text: str = Form("")):
    rows, errors = parse_holdings(text)
    with closing(_conn()) as conn:
        if errors:  # all-or-nothing: change nothing, show every bad line
            s = state.build(conn, config.ASOF)
            return _render(request, "_holdings.html", s=s, errors=errors, text=text)
        added = {r["ticker"]: r["added"] for r in conn.execute("SELECT ticker, added FROM holdings")}
        with conn:
            conn.execute("DELETE FROM holdings")
            conn.executemany("INSERT INTO holdings VALUES (?,?,?,?)",
                             [(t, sh, c, added.get(t) or date.today().isoformat()) for t, sh, c in rows])
    return HTMLResponse("", headers=REFRESHED)


@app.post("/plan/done", response_class=HTMLResponse)
def plan_done():
    """Record this month's buys in the picks ledger and add them to holdings (idempotent per month)."""
    with closing(_conn()) as conn:
        s = state.build(conn, config.ASOF)
        if s["meta"]["asof"] is None or not s["buys"]:
            return HTMLResponse('<p class="note">Nothing to record: there are no buys this month.</p>')
        month = s["meta"]["asof"].strftime("%Y-%m")
        if conn.execute("SELECT 1 FROM picks WHERE month = ?", (month,)).fetchone():
            return HTMLResponse(f'<p class="note">Already recorded for {month}.</p>')
        with conn:
            for rank, b in enumerate(s["buys"], 1):
                conn.execute("INSERT OR IGNORE INTO picks VALUES (?,?,?,?,?)",
                             (month, b["ticker"], b["close"], rank, b["reason"]))
                h = conn.execute("SELECT shares, cost FROM holdings WHERE ticker = ?", (b["ticker"],)).fetchone()
                if h:
                    total = h["shares"] + b["shares"]
                    cost = (h["shares"] * h["cost"] + b["shares"] * b["close"]) / total
                    conn.execute("UPDATE holdings SET shares = ?, cost = ? WHERE ticker = ?", (total, cost, b["ticker"]))
                else:
                    conn.execute("INSERT INTO holdings VALUES (?,?,?,?)",
                                 (b["ticker"], b["shares"], b["close"], date.today().isoformat()))
    return HTMLResponse("", headers=REFRESHED)


@app.post("/holdings/{ticker}/sold", response_class=HTMLResponse)
def sold(ticker: str):
    with closing(_conn()) as conn, conn:
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker.upper(),))
    return HTMLResponse("", headers=REFRESHED)


# "Explain with Qwen": ONE job at a time (LM Studio has one slot). Cached per ticker per ISO week.
_llm_lock = threading.Lock()
job = {"ticker": None, "status": None, "started": None, "result": None}


def _week() -> str:
    y, w, _ = (config.ASOF or date.today()).isocalendar()
    return f"{y}-W{w:02d}"


def _cached(conn, t: str) -> dict | None:
    row = conn.execute("SELECT json FROM thesis WHERE ticker = ? AND week = ?", (t, _week())).fetchone()
    rec = json.loads(row["json"]) if row else None
    return rec if rec and rec.get("prompt_version") == llm.PROMPT_VERSION else None


def _explain_job(t: str) -> None:
    try:
        with closing(_conn()) as conn:
            a = state.analyze(conn, t, config.ASOF)
            if a.get("error"):
                raise ValueError(a["error"])
            try:
                news = estimates.news(t, 10) if config.ASOF is None else []  # live only
            except Exception:
                news = []
            res = llm.explain(a, news)
            if res["ok"]:
                created = datetime.now().isoformat(timespec="seconds")
                res |= {"ticker": t, "rule_label": a.get("rule"), "created": created}
                with conn:
                    conn.execute("INSERT OR REPLACE INTO thesis VALUES (?,?,?,?)", (t, _week(), json.dumps(res), created))
        job.update(status="done" if res["ok"] else "error", result=res)
    except Exception as e:
        job.update(status="error", result={"ok": False, "error": f"{type(e).__name__}: {e}"})


def _explain_view(request: Request, t: str, mode: str, **ctx):
    return _render(request, "_explain.html", t=t, mode=mode, job=job,
                   elapsed=round(time.time() - (job["started"] or time.time())), **ctx)


@app.post("/explain/{t}", response_class=HTMLResponse)
def explain_start(request: Request, t: str, force: int = 0):
    t = t.strip().upper()
    if not TICKER.match(t):
        return _explain_view(request, t[:10], "error", r={"error": "Not a valid ticker."})
    if not force:
        with closing(_conn()) as conn:
            rec = _cached(conn, t)
        if rec:
            return _explain_view(request, t, "card", r=rec, cached=True)
    if not llm.up():
        return _explain_view(request, t, "off")
    with _llm_lock:
        if job["status"] == "running" and job["ticker"] != t:
            return _explain_view(request, t, "busy")
        if job["status"] != "running":
            job.update(ticker=t, status="running", started=time.time(), result=None)
            threading.Thread(target=_explain_job, args=(t,), name="finres-explain", daemon=True).start()
    return _explain_view(request, t, "poll")


@app.get("/explain/{t}", response_class=HTMLResponse)
def explain_status(request: Request, t: str):
    t = t.strip().upper()
    if job["ticker"] == t and job["status"] == "running":
        return _explain_view(request, t, "poll")
    if job["ticker"] == t and job["status"] == "error":
        return _explain_view(request, t, "error", r=job["result"])
    with closing(_conn()) as conn:
        rec = _cached(conn, t) if TICKER.match(t) else None
    if job["ticker"] == t and job["status"] == "done":
        rec = job["result"]
    return _explain_view(request, t, "card", r=rec, cached=False) if rec else _explain_view(request, t, "none")


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
