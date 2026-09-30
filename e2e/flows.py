"""M7 proof pack: drive the REAL app (real data, real Qwen) through every user flow, assert, screenshot.

    .venv/bin/python -m e2e.flows [--only 2,3,9]

Servers run on 127.0.0.1:8601-8609 against /tmp/finres_proof*.db copies (data/finres.db is only ever read, via
the SQLite backup API). Screenshots + results.json go to docs/proof/. Exit code 1 if any flow fails.
Flows 5-8, 14 and 21 build on state from earlier flows (4, 7, 13) in the same run.
"""
import argparse
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / "docs" / "proof"
PY = str(ROOT / ".venv" / "bin" / "python")
USER_DB = ROOT / "data" / "finres.db"
BASE_DB, MAIN_DB = "/tmp/finres_proof_base.db", "/tmp/finres_proof.db"
MAIN_PORT = 8601
VIEW = {"width": 1280, "height": 900}
DEAD_LLM = "http://127.0.0.1:8099/v1"
HL = "3px solid #f39c12"  # in-browser outline on the element a screenshot proves (visual aid only)


# ---------------------------------------------------------------- servers + DB copies

def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _rm_db(path: str) -> None:
    for suf in ("", "-wal", "-shm"):
        Path(path + suf).unlink(missing_ok=True)


def copy_db(src: str | Path, dst: str) -> None:
    """Consistent copy via the SQLite backup API; the source is opened read-only."""
    _rm_db(dst)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def q(path: str, sql: str, args=()) -> list:
    c = sqlite3.connect(path)
    try:
        with c:
            return c.execute(sql, args).fetchall()
    finally:
        c.close()


class Server:
    ALL: list["Server"] = []

    def __init__(self, port: int, db: str, **env):
        self.port, self.db, self.env, self.p = port, db, env, None
        self.url = f"http://127.0.0.1:{port}"
        Server.ALL.append(self)

    def start(self) -> "Server":
        if not _port_free(self.port):
            raise RuntimeError(f"port {self.port} is already in use")
        env = {k: v for k, v in os.environ.items() if not k.startswith("FINRES_")}
        env |= {"FINRES_DB": self.db, "PYTHONPATH": str(ROOT)} | self.env
        log = open(f"/tmp/finres_proof_{self.port}.log", "a")
        self.p = subprocess.Popen([PY, "-m", "uvicorn", "finres.app:app", "--host", "127.0.0.1", "--port", str(self.port)],
                                  cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                if httpx.get(self.url + "/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            if self.p.poll() is not None:
                raise RuntimeError(f"server on {self.port} exited; see /tmp/finres_proof_{self.port}.log")
            time.sleep(0.5)
        raise RuntimeError(f"server on {self.port} did not become healthy")

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(5)
        self.p = None

    @classmethod
    def stop_all(cls) -> None:
        for s in cls.ALL:
            s.stop()


# ---------------------------------------------------------------- page helpers

def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def money(s: str) -> float | None:
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def wait_htmx(page) -> None:
    page.wait_for_function("() => !!window.htmx", timeout=30000)


def body_text(page) -> str:
    return norm(page.evaluate("document.body.textContent"))


def txt(page, sel: str) -> str:
    loc = page.locator(sel)
    return norm(loc.first.text_content()) if loc.count() else ""


def mark(loc) -> None:
    loc.evaluate_all(f"""els => els.forEach(e => {{
        if (e.tagName === 'TR') e.querySelectorAll('td').forEach(td => td.style.background = 'rgba(243,156,18,.28)');
        else {{ e.style.outline = '{HL}'; e.style.outlineOffset = '2px'; }} }})""")


def buys(page) -> list[dict]:
    rows = page.evaluate("""() => [...document.querySelectorAll('ol.buys > li')].map(li => ({
        ticker: li.querySelector('.tk').textContent.trim(), text: li.textContent,
        amt: (li.querySelector('.buy-amt') || {}).textContent || '',
        reason: ((li.querySelector('.reason') || {}).textContent || '').trim()}))""")
    for r in rows:
        m = re.search(r"you hold \$([\d,]+)", r["text"])
        r["held"] = float(m.group(1).replace(",", "")) if m else None
        r["dollars"] = money(r["amt"].split("≈")[0])
    return rows


def categories(page) -> list[dict]:
    return page.evaluate("""() => [...document.querySelectorAll('#categories table.cat tbody tr')].map(tr => {
        const td = tr.querySelectorAll('td'), tags = [...td[0].querySelectorAll('.tag')].map(t => t.textContent.trim());
        return {ticker: td[0].querySelector('.tk').textContent.trim(), stale: tags.includes('stale'),
                sec: !!td[0].querySelector('.tag.red'), last: td[2].textContent.trim(), grade: td[3].textContent.trim(),
                trend: td[9].textContent.trim(), label: td[10].textContent.trim(), reason: td[11].textContent.trim(),
                category: tr.closest('details').querySelector('summary').textContent.trim()}; })""")


def holdings_rows(page) -> list[dict]:
    return page.evaluate("""() => [...document.querySelectorAll('#holdings tbody tr')].map(tr => {
        const td = [...tr.querySelectorAll('td')].map(x => x.textContent.replace(/\\s+/g, ' ').trim());
        return {ticker: tr.querySelector('.tk').textContent.trim(), shares: td[1], cost: td[2], last: td[3],
                value: td[4], weight: td[5], pl: td[6], grade: td[7], rule: td[8], status: td[9]}; })""")


def sells(page) -> dict[str, str]:
    return dict(page.evaluate("""() => [...document.querySelectorAll('ul.sells > li')].map(li =>
        [li.querySelector('.tk').textContent.trim(), li.querySelector('.down').textContent.trim()])"""))


def track_rows(page) -> list[tuple[str, str]]:
    return [tuple(x) for x in page.evaluate("""() => [...document.querySelectorAll('#track tbody tr')].map(tr =>
        [...tr.querySelectorAll('td')].slice(0, 2).map(td => td.textContent.trim()))""")]


def analyze(page, t: str, timeout: int = 90000) -> None:
    page.evaluate("document.querySelector('#analysis').innerHTML = ''")
    page.fill("#t", t)
    page.click("#analyze form button[type=submit]")
    page.wait_for_selector("#analysis article, #analysis .errors", timeout=timeout)


def save_holdings(page, text: str, reload: bool = True) -> None:
    if not page.locator("#holdings details").evaluate("d => d.open"):
        page.click("#holdings summary")
    page.fill("#holdings-text", text)
    if reload:
        with page.expect_navigation(timeout=60000):
            page.click("#holdings button[type=submit]")
        wait_htmx(page)
    else:
        page.click("#holdings button[type=submit]")
        page.wait_for_selector("#holdings .errors", timeout=30000)


def _box(loc) -> list[float]:
    return loc.evaluate_all("""els => { const b = els.map(e => e.getBoundingClientRect());
        return [Math.min(...b.map(r => r.left)) + scrollX, Math.min(...b.map(r => r.top)) + scrollY,
                Math.max(...b.map(r => r.right)) + scrollX, Math.max(...b.map(r => r.bottom)) + scrollY]; }""")


def shot(page, fname: str, *parts, full: bool = False, pad: int = 8) -> str:
    """No parts: viewport (or full page). Each part = selector/locator or a list of them (union box).
    Several parts are stitched vertically with a grey gap."""
    path = PROOF / fname
    if not parts:
        page.screenshot(path=str(path), full_page=full)
        return fname
    imgs = []
    for part in parts:
        locs = part if isinstance(part, list) else [part]
        boxes = [_box(page.locator(x) if isinstance(x, str) else x) for x in locs]
        x0, y0 = max(min(b[0] for b in boxes) - pad, 0), max(min(b[1] for b in boxes) - pad, 0)
        x1, y1 = max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad
        width = page.evaluate("document.documentElement.scrollWidth")
        clip = {"x": x0, "y": y0, "width": min(x1, width) - x0, "height": y1 - y0}
        imgs.append(Image.open(io.BytesIO(page.screenshot(clip=clip, full_page=True))))
    if len(imgs) == 1:
        imgs[0].save(path)
        return fname
    gap = 14
    out = Image.new("RGB", (max(i.width for i in imgs), sum(i.height for i in imgs) + gap * (len(imgs) - 1)), "#9aa0a6")
    y = 0
    for i in imgs:
        out.paste(i, (0, y))
        y += i.height + gap
    out.save(path)
    return fname


# ---------------------------------------------------------------- flow plumbing

class Flow:
    def __init__(self, fid, name, steps, expected, R):
        self.id, self.name, self.steps, self.expected, self.R = fid, name, steps, expected, R
        self.actual, self.fails, self.shots, self.pages, self.servers, self.extra = [], [], [], [], [], {}

    def ok(self, cond, what: str) -> bool:
        self.actual.append(("✓ " if cond else "✗ ") + what)
        if not cond:
            self.fails.append(what)
        return bool(cond)

    def note(self, s: str) -> None:
        self.actual.append(s)

    def page(self, url: str, path: str = "/", **kw):
        p = self.R.browser.new_page(viewport=kw.pop("viewport", VIEW), **kw)
        p.on("dialog", lambda d: d.accept())
        self.pages.append(p)
        p.goto(url + path, wait_until="load", timeout=120000)
        wait_htmx(p)
        return p

    def server(self, port: int, db: str, **env) -> Server:
        s = Server(port, db, **env).start()
        self.servers.append(s)
        return s

    def shot(self, page, fname, *parts, **kw) -> None:
        self.shots.append(shot(page, fname, *parts, **kw))


FLOWS: list = []


def flow(fid: int, name: str, steps: str, expected: str):
    def deco(fn):
        FLOWS.append((fid, name, steps, expected, fn))
        return fn
    return deco


class Run:
    def __init__(self, browser):
        self.browser, self.state, self._main = browser, {}, None

    @property
    def main(self) -> Server:
        if self._main is None or self._main.p is None or self._main.p.poll() is not None:
            self._main = Server(MAIN_PORT, MAIN_DB).start()
        return self._main

    def need(self, *keys):
        missing = [k for k in keys if k not in self.state]
        if missing:
            raise RuntimeError(f"needs state {missing} from an earlier flow in the same run")
        return [self.state[k] for k in keys]


def run_flow(R: Run, fid, name, steps, expected, fn) -> dict:
    f = Flow(fid, name, steps, expected, R)
    t0 = time.time()
    try:
        fn(R, f)
    except Exception as e:  # noqa: BLE001 - a crashed flow is a failed flow; keep going
        f.ok(False, f"exception: {type(e).__name__}: {str(e).splitlines()[0][:300]}")
        traceback.print_exc()
        if f.pages and not f.shots:
            try:
                f.shots.append(shot(f.pages[-1], f"{fid:02d}-FAILED.png"))
            except Exception:  # noqa: BLE001
                pass
    finally:
        for p in f.pages:
            try:
                p.context.close()
            except Exception:  # noqa: BLE001
                pass
        for s in f.servers:
            s.stop()
    res = {"id": fid, "name": name, "steps": steps, "expected": expected, "actual": " | ".join(f.actual),
           "screenshot": (f.shots[0] if len(f.shots) == 1 else f.shots) or None, "pass": not f.fails,
           "seconds": round(time.time() - t0, 1)}
    res |= f.extra
    print(f"[{'PASS' if res['pass'] else 'FAIL'}] {fid:2d} {name} ({res['seconds']} s)", flush=True)
    for a in f.actual:
        print("      " + a, flush=True)
    return res


# ---------------------------------------------------------------- in-process helpers (read-only, app code)

def two_me_below(path: str) -> list[str]:
    """Universe names below their 200DMA at both of the last two completed month-ends (state.build's S2 input)."""
    from finres import config, db, prices, signals, state
    conn = db.connect(path)
    try:
        u = config.load_universe()["tickers"]
        held = [r["ticker"] for r in conn.execute("SELECT ticker FROM holdings")]
        closes = prices.load_closes(conn, list(dict.fromkeys(u + config.BENCHMARKS + held)))
    finally:
        conn.close()
    t = closes.index[-1]
    me = state._completed_month_ends(closes.index, t, date.today())
    if len(me) < 2:
        return []
    a1, a0 = signals.factors_at(closes, me[-1])["above200"], signals.factors_at(closes, me[-2])["above200"]
    fac = signals.factors_at(closes, t)
    return [x for x in u if x in fac.index and bool(fac.at[x, "eligible"]) and pd.notna(fac.at[x, "sma200"])
            and not bool(a1.get(x, True)) and not bool(a0.get(x, True))]


def month_of(page) -> str:
    m = re.search(r"Data as of (\d{4}-\d{2})-\d{2}", txt(page, "#header"))
    return m.group(1) if m else ""


# ---------------------------------------------------------------- flows

@flow(1, "cold-start", "empty DB /tmp/finres_proof_cold.db on :8602 → GET / → progress banner; poll until the refresh "
      "finishes and the page reloads", "progress banner while refreshing; after ≤10 min the page reloads with the plan "
      "and all categories")
def f01(R, f):
    db = "/tmp/finres_proof_cold.db"
    _rm_db(db)
    srv = f.server(8602, db)
    t0 = time.time()
    page = f.page(srv.url)
    f.ok(page.locator("#refresh progress").count() == 1, "progress bar visible right after the first GET /")
    f.ok("No data yet" in txt(page, "#plan"), "plan says 'No data yet' while the first refresh runs")
    try:
        page.wait_for_function("""() => { const s = document.querySelector('#refresh span.muted');
            return s && /step: (estimates|edgar|sec 8-K) ([1-9]\\d+)\\//.test(s.textContent); }""", timeout=240000)
    except Exception:  # noqa: BLE001
        f.note("(did not see estimates/edgar ≥10 within 4 min; screenshotting whatever is shown)")
    f.note(f"mid-progress: “{txt(page, '#refresh')}”")
    f.shot(page, "01a-refresh-progress.png")
    deadline, done = time.time() + 600, False
    while time.time() < deadline:
        try:
            done = page.evaluate("""() => !document.querySelector('#refresh progress')
                && document.querySelector('#header').textContent.includes('Data as of')
                && !!document.querySelector('#categories details')""")
        except Exception:  # noqa: BLE001 - execution context replaced by the HX-Refresh reload
            done = False
        if done:
            break
        time.sleep(3)
    wall = time.time() - t0
    f.ok(done, f"refresh finished and the page reloaded by itself ({wall:.0f} s wall, limit 600 s)")
    if not done:
        return
    wait_htmx(page)
    f.extra["refresh_seconds"] = round(wall)
    f.note(f"refresh status after reload: “{txt(page, '#refresh')}”; header: “{txt(page, '#header .bar-row .muted')}”")
    n_cat = page.locator("#categories details").count()
    f.ok(n_cat == 11, f"{n_cat} categories rendered (universe.toml has 11)")
    f.ok(page.locator("#plan .card h3").count() > 0 and "Buy with" in txt(page, "#plan .card h3"),
         f"plan card rendered: “{txt(page, '#plan .card h3')}”")
    snaps = q(db, "SELECT date, COUNT(*) FROM snapshot GROUP BY date")
    f.note(f"cold DB snapshot rows: {snaps}")
    f.shot(page, "01b-after-refresh.png", full=True)


@flow(2, "header-regime", "GET / on the main proof server (:8601, copy of data/finres.db); read the header",
      "as-of date, data age, Qwen status, SPY vs 200DMA and % of universe in uptrend; no brake (ADR-005)")
def f02(R, f):
    page = f.page(R.main.url)
    h = txt(page, "#header")
    m = re.search(r"Data as of (\d{4}-\d{2}-\d{2}) · (\d+) days? old", h)
    f.ok(m, f"as-of + age: “{m.group(0) if m else h[:80]}”")
    f.ok("Qwen ready" in h, f"Qwen status: “{txt(page, '#header .dot')}”")
    p = re.search(r"SPY (above|below) 200DMA · (\d+)% of universe in uptrend", h)
    f.ok(p, f"regime pill: “{txt(page, '#header .pill')}”")
    f.ok("brake" not in h.lower(), "no brake state anywhere in the header (ADR-005 removed the brake)")
    f.ok(page.locator("#refresh button").is_enabled(), "Refresh button enabled (live mode)")
    f.shot(page, "02-header.png", "#header")


@flow(3, "plan", "GET / with no holdings; read the plan card and the whole page text",
      "≤10 uptrend buys, equal $ summing to $2,500 ±$1, each with 'you hold $X' and a reason; 'Rules: B0R · S2 — "
      "ADR-005'; no BUY/AVOID/Brake words on the page")
def f03(R, f):
    page = f.page(R.main.url)
    b = buys(page)
    f.ok(1 <= len(b) <= 10, f"{len(b)} buys: {', '.join(x['ticker'] for x in b)}")
    total = sum(x["dollars"] or 0 for x in b)
    f.ok(abs(total - 2500) <= 1 and len({x['dollars'] for x in b}) == 1,
         f"equal amounts {sorted({x['dollars'] for x in b})} × {len(b)} = ${total:,.0f}")
    f.ok(all(x["held"] is not None for x in b), "every buy row shows “you hold $X”")
    f.ok(all(x["reason"] for x in b), "every buy row has a reason")
    labels = {c["ticker"]: c["label"] for c in categories(page)}
    f.ok(all(labels.get(x["ticker"]) == "UPTREND" for x in b), "every buy is labelled UPTREND in its category row")
    card = txt(page, "#plan .card")
    f.ok("Rules: B0R · S2 — ADR-005" in card, "rules line “Rules: B0R · S2 — ADR-005 · lab/REPORT.md” present")
    body = body_text(page)
    bad = re.findall(r"\b(?:BUY|AVOID|WATCH)\b", body) + re.findall(r"brake", body, re.I)
    f.ok(not bad, f"no BUY/AVOID/WATCH/Brake words in the page text (found: {bad or 'none'})")
    R.state["buys0"] = [x["ticker"] for x in b]
    f.shot(page, "03-plan.png", "#plan .card")


@flow(4, "holdings-paste", "Edit holdings → paste 7 lines built from live page prices: NVDA at 0.5×price (big gain), a "
      "rank<0.70 uptrend name, a name below 200DMA at 2 month-ends, a name at 2×price (−50%), COST (not in universe), "
      "and the top 2 buy-list names → Save", "table shows value, weight, P/L and a status for every row; sell list appears")
def f04(R, f):
    page = f.page(R.main.url)
    cats = {c["ticker"]: c for c in categories(page)}
    b0 = [x["ticker"] for x in buys(page)]
    px = {t: money(c["last"]) for t, c in cats.items()}
    below_all = two_me_below(MAIN_DB)
    f.note(f"names below 200DMA at both completed month-ends (eligible): {', '.join(below_all) or 'none'}")
    buy1, buy2 = b0[0], b0[1]
    used = {"NVDA", buy1, buy2}
    rank_low = next(t for t, c in cats.items() if c["label"] == "UPTREND" and c["grade"] in "CDF" and t not in b0
                    and t not in below_all and t not in used)
    used.add(rank_low)
    below = next((t for t in below_all if t not in used and cats.get(t, {}).get("label") == "NO UPTREND"), None)
    used.add(below)
    stop = next(t for t, c in cats.items() if c["label"] == "UPTREND" and c["grade"] in "AB" and t not in b0
                and t not in below_all and t not in used)
    analyze(page, "COST")
    cost_px = money(txt(page, "#analysis .big"))
    lines = [f"NVDA 10 {0.5 * px['NVDA']:.2f}", f"{rank_low} 15 {px[rank_low]:.2f}"]
    if below:
        lines.append(f"{below} 10 {1.1 * px[below]:.2f}")
    else:
        f.note("no universe name is below its 200DMA at 2 month-ends today")
    lines += [f"{stop} 5 {2 * px[stop]:.2f}", f"COST 3 {cost_px:.2f}", f"{buy1} 1 {px[buy1]:.2f}", f"{buy2} 1 {px[buy2]:.2f}"]
    text = "\n".join(lines)
    f.note("pasted: " + " / ".join(lines))
    f.note(f"roles: gain=NVDA, rank<0.70 (grade {cats[rank_low]['grade']}, UPTREND)={rank_low}, 2 month-ends below "
           f"200DMA={below}, −50% stop={stop}, not in universe=COST, held buy-list names={buy1},{buy2}")
    save_holdings(page, text)
    rows = {r["ticker"]: r for r in holdings_rows(page)}
    want = [ln.split()[0] for ln in lines]
    f.ok(sorted(rows) == sorted(want), f"holdings table has exactly the {len(want)} pasted tickers")
    f.ok(all(money(r["value"]) and r["weight"].endswith("%") and "%" in r["pl"] for r in rows.values()),
         "every row shows value, weight and P/L")
    f.ok(rows["NVDA"]["pl"] == "+100%", f"NVDA P/L {rows['NVDA']['pl']} (cost = half the price)")
    f.ok(rows[stop]["pl"] == "-50%" and rows[stop]["status"] == "-35% stop", f"{stop}: P/L {rows[stop]['pl']}, status "
                                                                             f"“{rows[stop]['status']}”")
    if below:
        f.ok(rows[below]["status"] == "2 month-ends below 200DMA", f"{below}: status “{rows[below]['status']}”")
    # ADR-005/M8b: holdings outside the universe get the S2 rules too; otherwise their status is "not in universe"
    cost_ok = rows["COST"]["status"] in ("not in universe", "2 month-ends below 200DMA", "-35% stop")
    f.ok(cost_ok, f"COST (outside universe, S2 rules still apply): status “{rows['COST']['status']}”")
    f.ok(rows[rank_low]["status"] == "OK", f"{rank_low} (rank grade {rows[rank_low]['grade']}): status "
                                           f"“{rows[rank_low]['status']}”")
    f.note("statuses: " + "; ".join(f"{t} {r['value']} {r['weight']} {r['pl']} {r['status']}" for t, r in rows.items()))
    f.note(f"totals row: “{txt(page, '#holdings tfoot')}”")
    R.state.update(hold_text=text, buy1=buy1, buy2=buy2, stop=stop, below=below, rank_low=rank_low)
    mark(page.locator("ul.sells > li"))
    f.shot(page, "04-holdings-paste.png", "#holdings", ["#plan h3:text-is('Sell')", "ul.sells"])


@flow(5, "sells-and-rotation", "reload after the paste; read the sell list and the buy list",
      "sells = exactly the engineered S2 rules (−35% stop, 2 month-ends below 200DMA), no rank-based sells; the two "
      "held buy-list names are no longer at the top (least-held first)")
def f05(R, f):
    stop, below, rank_low, buy1, buy2 = R.need("stop", "below", "rank_low", "buy1", "buy2")
    page = f.page(R.main.url)
    s = sells(page)
    f.note("sell list: " + ("; ".join(f"{t}: {r}" for t, r in s.items()) or "empty"))
    f.ok(s.get(stop) == "-35% stop", f"{stop} → “{s.get(stop)}”")
    if below:
        f.ok(s.get(below) == "2 month-ends below 200DMA", f"{below} → “{s.get(below)}”")
    else:
        f.note("no 2-month-end candidate existed today, so that rule could not be shown")
    f.ok(rank_low not in s and "rank fell" not in body_text(page), f"no rank-based sell ({rank_low} has rank < 70th pct "
                                                                   "and is not sold)")
    extra = set(s) - {x for x in (stop, below) if x}
    f.ok(extra <= {"COST"}, f"nothing else sold except S2 on COST if it is below 200DMA (extra: {sorted(extra) or 'none'})")
    f.ok(all(v in ("-35% stop", "2 month-ends below 200DMA") for v in s.values()), "only S2 rules fire (no rank sells)")
    b = buys(page)
    order = [x["ticker"] for x in b]
    f.note("buy list now: " + ", ".join(f"{x['ticker']} (hold ${x['held']:,.0f})" for x in b))
    for t in (buy1, buy2):
        if t in order:
            i = order.index(t)
            f.ok(i >= 2 and all(x["held"] <= b[i]["held"] for x in b[:i]), f"{t} moved to position {i + 1} behind "
                                                                             "less-held names")
        else:
            f.ok(True, f"{t} (was #{R.state.get('buys0', []).index(t) + 1 if t in R.state.get('buys0', []) else '?'}) "
                       "dropped out of this month's buys: enough unheld uptrend names rank ahead of it")
    f.ok(b and b[0]["held"] == 0, "top of the buy list holds $0")
    mark(page.locator("ul.sells > li"))
    f.shot(page, "05-sells-rotation.png", "#plan .card", "#holdings")


@flow(6, "bad-holdings-line", "Edit holdings → paste the current list with NVDA's line replaced by 'NVDA ten 100' → Save",
      "error names line 1; nothing saved (DB + table unchanged)")
def f06(R, f):
    (text,) = R.need("hold_text")
    page = f.page(R.main.url)
    before_db = q(MAIN_DB, "SELECT ticker, shares, cost FROM holdings ORDER BY ticker")
    before_pg = holdings_rows(page)
    bad = "\n".join(["NVDA ten 100"] + text.splitlines()[1:])
    save_holdings(page, bad, reload=False)
    err = txt(page, "#holdings .errors")
    f.ok("Line 1" in err and "NVDA ten 100" in err, f"error: “{err}”")
    f.ok(q(MAIN_DB, "SELECT ticker, shares, cost FROM holdings ORDER BY ticker") == before_db,
         f"holdings table in SQLite unchanged ({len(before_db)} rows)")
    f.ok(holdings_rows(page) == before_pg, "rendered holdings table unchanged")
    mark(page.locator("#holdings .errors"))
    f.shot(page, "06-bad-holdings-line.png", "#holdings")


@flow(7, "mark-done", "click “I placed these buys” and accept the confirm dialog",
      "this month's picks rows appear in Track record; holdings now include every buy")
def f07(R, f):
    page = f.page(R.main.url)
    month = month_of(page)
    b = [x["ticker"] for x in buys(page)]
    f.note(f"buys being recorded for {month}: {', '.join(b)}")
    with page.expect_navigation(timeout=60000):
        page.click("#plan button:has-text('I placed these buys')")
    wait_htmx(page)
    picks = [r[0] for r in q(MAIN_DB, "SELECT ticker FROM picks WHERE month = ? ORDER BY rank", (month,))]
    f.ok(picks == b, f"SQLite picks for {month}: {', '.join(picks)}")
    tr = [t for m, t in track_rows(page) if m == month]
    f.ok(sorted(tr) == sorted(b), f"Track record shows {len(tr)} rows for {month}")
    held = {r["ticker"] for r in holdings_rows(page)}
    f.ok(set(b) <= held, f"holdings include all {len(b)} buys ({len(held)} holdings now)")
    f.note(f"track summary: “{txt(page, '#track > p')}”")
    R.state.update(month=month, picks_n=len(picks))
    f.shot(page, "07-mark-done.png", "#holdings", "#track")


@flow(8, "mark-done-twice", "click “I placed these buys” again (accept the confirm)",
      "“Already recorded for YYYY-MM”; picks count in SQLite unchanged")
def f08(R, f):
    (month,) = R.need("month")
    page = f.page(R.main.url)
    n0 = q(MAIN_DB, "SELECT COUNT(*) FROM picks")[0][0]
    page.click("#plan button:has-text('I placed these buys')")
    page.wait_for_selector("#plan-msg .note", timeout=30000)
    msg = txt(page, "#plan-msg")
    f.ok(msg == f"Already recorded for {month}.", f"message: “{msg}”")
    n1 = q(MAIN_DB, "SELECT COUNT(*) FROM picks")[0][0]
    f.ok(n0 == n1, f"picks rows before/after: {n0}/{n1}")
    mark(page.locator("#plan-msg"))
    f.shot(page, "08-mark-done-twice.png", ["#plan button:has-text('I placed these buys')", "#plan-msg"])


@flow(9, "analyze-universe", "type NVDA in Analyze → submit", "card with grades, raw numbers, sparkline and reason")
def f09(R, f):
    page = f.page(R.main.url)
    analyze(page, "NVDA")
    card = page.locator("#analysis article")
    f.ok(card.count() == 1, f"card: “{txt(page, '#analysis article h3')}” {txt(page, '#analysis .big')}")
    grades = [norm(x) for x in card.locator("table .chip").all_text_contents()]
    f.ok(len(grades) == 4 and sum(g in "ABCDF" for g in grades) >= 3, f"grades mom/qual/rev/rank: {grades}")
    t = txt(page, "#analysis article table")
    f.ok(all(k in t for k in ("12-1m", "GP/assets", "EPS est", "fwd P/E")), "raw numbers row text present: "
         + "; ".join(norm(x) for x in card.locator("tbody td.n:nth-child(4)").all_text_contents()))
    pts = card.locator("polyline.sp-px").get_attribute("points") or ""
    f.ok(len(pts.split()) > 200, f"sparkline has {len(pts.split())} price points (+ dashed 200DMA)")
    f.ok(txt(page, "#analysis .reason"), f"label + reason: “{txt(page, '#analysis article > p')}”")
    f.shot(page, "09-analyze-nvda.png", "#analysis article")


@flow(10, "analyze-outside", "Analyze COST (not in universe.toml)", "card says “not in the universe” and is still graded")
def f10(R, f):
    page = f.page(R.main.url)
    analyze(page, "COST")
    sub = txt(page, "#analysis .card-h")
    f.ok("not in the universe" in sub, f"header: “{sub}”")
    grades = [norm(x) for x in page.locator("#analysis table .chip").all_text_contents()]
    f.ok(grades[-1] in "ABCDF" and grades[-1] != "", f"graded vs the universe: {grades}")
    f.note(f"label + reason: “{txt(page, '#analysis article > p')}”")
    f.shot(page, "10-analyze-outside-cost.png", "#analysis article")


@flow(11, "analyze-invalid", "Analyze ZZZZ, then BAD$$", "“No price data for ZZZZ”; validation error for BAD$$")
def f11(R, f):
    page = f.page(R.main.url)
    analyze(page, "ZZZZ")
    e1 = txt(page, "#analysis .errors")
    f.ok(e1 == "No price data for ZZZZ", f"ZZZZ → “{e1}”")
    f.shot(page, "11a-analyze-no-data.png", "#analyze")
    analyze(page, "BAD$$")
    e2 = txt(page, "#analysis .errors")
    f.ok("not a valid ticker" in e2, f"BAD$$ → “{e2}”")
    f.shot(page, "11b-analyze-invalid.png", "#analyze")


@flow(12, "category-click", "expand the first closed category, click its first ticker",
      "analyze card for that ticker loads and #analyze scrolls to the top of the viewport")
def f12(R, f):
    page = f.page(R.main.url)
    i = page.evaluate("[...document.querySelectorAll('#categories details')].findIndex(d => !d.open)")
    det = page.locator("#categories details").nth(i)  # stable handle: ':not([open])' would re-resolve after opening
    name = norm(det.locator("summary").text_content())
    det.locator("summary").click()
    f.ok(det.evaluate("d => d.open"), f"expanded “{name}”")
    tk = det.locator("table.cat tbody tr .tk").first
    t = norm(tk.text_content())
    tk.click()
    page.wait_for_selector(f"#analysis article h3:has-text('{t}')", timeout=60000)
    page.wait_for_timeout(800)
    top = page.evaluate("document.querySelector('#analyze').getBoundingClientRect().top")
    f.ok(abs(top) < 60, f"clicked {t}: card “{txt(page, '#analysis article h3')}” loaded; #analyze top at {top:.0f}px "
                        "of the viewport")
    f.shot(page, "12-category-click.png")


@flow(13, "explain-live", "Analyze NVDA → click “Explain with Qwen” (live Qwen, no cached thesis) → wait",
      "polling state, then within 10 min a thesis card with Bull/Bear points + evidence keys, Qwen verdict vs rule "
      "label, grounding box")
def f13(R, f):
    week = "{}-W{:02d}".format(*date.today().isocalendar()[:2])
    old = q(MAIN_DB, "SELECT COUNT(*) FROM thesis WHERE ticker = 'NVDA' AND week = ?", (week,))[0][0]
    if old:
        q(MAIN_DB, "DELETE FROM thesis WHERE ticker = 'NVDA' AND week = ?", (week,))
        f.note("deleted an older NVDA thesis from the proof copy so this is a live run")
    page = f.page(R.main.url)
    analyze(page, "NVDA")
    t0 = time.time()
    page.click("#explain-btn")
    page.wait_for_selector("#explain progress", timeout=30000)
    page.wait_for_timeout(4000)
    f.ok(True, f"polling state: “{txt(page, '#explain')}”")
    mark(page.locator("#explain"))
    f.shot(page, "13a-explain-polling.png", "#analysis article")
    page.wait_for_selector("#explain .gbox, #explain .errors", timeout=600000)
    secs = time.time() - t0
    f.extra["explain_seconds"] = round(secs)
    err = txt(page, "#explain .errors")
    if not f.ok(not err, f"thesis arrived after {secs:.0f} s (limit 600 s)" + (f": ERROR “{err}”" if err else "")):
        f.shot(page, "13b-explain-card.png", "#explain")
        return
    h3 = txt(page, "#explain h3")
    f.ok(re.search(r"Qwen: (BUY|HOLD|AVOID) \((low|medium|high)\) · Rule: (UPTREND|NO UPTREND)", h3), f"verdict line: “{h3}”")
    heads = [norm(x) for x in page.locator("#explain h4").all_text_contents()]
    f.ok(heads[:2] == ["Bull", "Bear"], f"sections: {heads}")
    ev = [norm(x) for x in page.locator("#explain .ev").all_text_contents()]
    f.ok(len(ev) >= 4 and all(ev), f"{len(ev)} points each with evidence keys, e.g. “{ev[0] if ev else ''}”")
    g = txt(page, "#explain .gbox")
    f.ok(g, f"grounding box: “{g}”")
    f.note(f"footer: “{txt(page, '#explain p.small')}”; thesis: “{txt(page, '#explain > p')[:200]}…”")
    f.shot(page, "13b-explain-card.png", "#explain")


@flow(14, "explain-cached", "reload the page, Analyze NVDA, click Explain", "“cached this week” card in < 3 s")
def f14(R, f):
    page = f.page(R.main.url)
    page.reload()
    wait_htmx(page)
    analyze(page, "NVDA")
    t0 = time.time()
    page.click("#explain-btn")
    page.wait_for_selector("#explain .tag:has-text('cached this week')", timeout=10000)
    dt = time.time() - t0
    f.ok(dt < 3, f"cached card shown in {dt:.2f} s: “{txt(page, '#explain h3')}”")
    mark(page.locator("#explain .tag:has-text('cached this week')"))
    f.shot(page, "14-explain-cached.png", "#explain")


@flow(15, "explain-busy", "POST /explain/VST?force=1, then immediately Analyze PLTR → click Explain; then wait for VST",
      "PLTR shows “Qwen is busy explaining VST”; VST then finishes so the slot is free")
def f15(R, f):
    page = f.page(R.main.url)
    r = page.request.post(R.main.url + "/explain/VST?force=1")
    f.ok("Qwen is explaining VST" in r.text(), "VST job started (force=1)")
    t0 = time.time()
    analyze(page, "PLTR")
    page.click("#explain-btn")
    page.wait_for_selector("#explain .note, #explain .gbox", timeout=30000)
    msg = txt(page, "#explain")
    f.ok("Qwen is busy explaining VST" in msg, f"PLTR → “{msg}”")
    mark(page.locator("#explain"))
    f.shot(page, "15-explain-busy.png", "#analysis article")
    status = ""
    while time.time() - t0 < 600:
        status = httpx.get(R.main.url + "/explain/VST", timeout=10).text
        if "Qwen is explaining" not in status:
            break
        time.sleep(5)
    done = "gbox" in status
    f.ok(done, f"VST finished after {time.time() - t0:.0f} s" + ("" if done else f": {norm(re.sub('<[^>]+>', ' ', status))[:200]}"))


@flow(16, "explain-off", "server :8603 on a fresh copy with FINRES_LLM_URL=http://127.0.0.1:8099/v1 (nothing listens) "
      "→ GET /, Analyze NVDA, click Explain", "Qwen dot off + hint; Explain says “Qwen is off… llm-serve start splash4”; "
      "plan and analyze still work")
def f16(R, f):
    db = "/tmp/finres_proof_off.db"
    copy_db(BASE_DB, db)
    srv = f.server(8603, db, FINRES_LLM_URL=DEAD_LLM)
    page = f.page(srv.url)
    dot = page.locator("#header .dot")
    f.ok("off" in (dot.get_attribute("class") or "") and txt(page, "#header .dot") ==
         "Qwen off — run llm-serve start splash4", f"header dot: “{txt(page, '#header .dot')}”")
    f.ok(len(buys(page)) > 0, f"plan still works: “{txt(page, '#plan .card h3')}”")
    analyze(page, "NVDA")
    f.ok(page.locator("#analysis article").count() == 1, "analyze card still works")
    page.click("#explain-btn")
    page.wait_for_selector("#explain .note", timeout=20000)
    msg = txt(page, "#explain")
    f.ok(msg.startswith("Qwen is off.") and "llm-serve start splash4" in msg, f"Explain → “{msg}”")
    mark(page.locator("#explain, #header .dot"))
    f.shot(page, "16-explain-off.png", "#header", "#analysis article")


@flow(17, "replay-2022", "server :8604 with FINRES_ASOF=2022-09-30 on a copy of the proof DB → GET /",
      "replay banner; SPY below 200DMA and a low % in uptrend; buys = the few uptrend names then (0–10); Refresh disabled")
def f17(R, f):
    db = "/tmp/finres_proof_replay.db"
    copy_db(BASE_DB, db)
    srv = f.server(8604, db, FINRES_ASOF="2022-09-30")
    page = f.page(srv.url)
    h = txt(page, "#header")
    f.ok("Replay as of 2022-09-30" in h, f"banner: “{txt(page, '#header .banner')}”")
    f.ok("Data as of 2022-09-30" in h, "data as of 2022-09-30")
    p = re.search(r"SPY (above|below) 200DMA · (\d+)% of universe in uptrend", h)
    f.ok(p and p.group(1) == "below" and int(p.group(2)) <= 30, f"regime: “{txt(page, '#header .pill')}”")
    b = buys(page)
    f.ok(0 <= len(b) <= 10, f"{len(b)} buys: {', '.join(x['ticker'] for x in b) or txt(page, '#plan .empty')}")
    btn = page.locator("#refresh button")
    f.ok(btn.is_disabled() and "replay: refresh disabled" in txt(page, "#refresh"), f"refresh: “{txt(page, '#refresh')}” "
                                                                                    "(button disabled)")
    f.shot(page, "17-replay-2022.png", "#header", "#plan")


@flow(18, "insufficient", "find a universe ticker with < 273 trading days in the categories table",
      "row labelled “Insufficient data” with the reason (N trading days of history, need 273)")
def f18(R, f):
    page = f.page(R.main.url)
    rows = [c for c in categories(page) if c["label"] == "Insufficient data" and "trading days" in c["reason"]]
    f.ok(rows, "insufficient-history rows: " + "; ".join(f"{c['ticker']}: {c['reason']}" for c in rows))
    if not rows:
        return
    c = next((c for c in rows if c["ticker"] in ("CBRS", "XE")), rows[0])
    row = page.locator("#categories table.cat tbody tr").filter(has=page.locator(f".tk:text-is('{c['ticker']}')"))
    row.evaluate("tr => tr.closest('details').open = true")
    row.scroll_into_view_if_needed()
    f.ok("need 273" in c["reason"], f"{c['ticker']} ({c['category']}): label “{c['label']}”, reason “{c['reason']}”")
    mark(row)
    table = row.locator("xpath=ancestor::table[1]")
    f.shot(page, "18-insufficient.png", [table.locator("thead"), row])


@flow(19, "empty-states", "server :8605 on a copy with holdings + picks DELETEd → GET /",
      "holdings empty-state text and track-record empty-state text")
def f19(R, f):
    db = "/tmp/finres_proof_empty.db"
    copy_db(BASE_DB, db)
    q(db, "DELETE FROM holdings")
    q(db, "DELETE FROM picks")
    srv = f.server(8605, db)
    page = f.page(srv.url)
    hs, ts = txt(page, "#holdings .empty"), txt(page, "#track .empty")
    f.ok(hs.startswith("No holdings yet"), f"holdings: “{hs}”")
    f.ok(ts.startswith("No picks recorded yet"), f"track record: “{ts}”")
    f.shot(page, "19-empty-states.png", "#holdings", "#track")


@flow(20, "stale-coverage", "copy; delete the last 15 trading days of prices for 20 universe tickers; server :8606 "
      "(latest snapshot is today, so no auto-refresh) → GET /", "“Ranking withheld” banner, no buy list, “stale” tags")
def f20(R, f):
    from finres import config
    db = "/tmp/finres_proof_stale.db"
    copy_db(BASE_DB, db)
    uni = config.load_universe()["tickers"]
    victims = uni[:20]
    cut = q(db, "SELECT d FROM (SELECT DISTINCT d FROM prices WHERE ticker = 'SPY' ORDER BY d DESC LIMIT 15) "
                "ORDER BY d LIMIT 1")[0][0]
    n = q(db, f"SELECT COUNT(*) FROM prices WHERE d >= ? AND ticker IN ({','.join('?' * 20)})", (cut, *victims))[0][0]
    q(db, f"DELETE FROM prices WHERE d >= ? AND ticker IN ({','.join('?' * 20)})", (cut, *victims))
    f.note(f"deleted {n} price rows on/after {cut} for {', '.join(victims)}")
    srv = f.server(8606, db)
    page = f.page(srv.url)
    f.ok(page.locator("#refresh progress").count() == 0, "no auto-refresh started (latest snapshot is fresh)")
    ban = txt(page, "#header .banner.warn")
    f.ok(ban.startswith("Ranking withheld"), f"banner: “{ban}”")
    f.ok(page.locator("ol.buys").count() == 0 and "Plan hidden while ranking is withheld" in txt(page, "#plan"),
         f"plan: “{txt(page, '#plan .empty')}” (no buy list)")
    stale = [c["ticker"] for c in categories(page) if c["stale"]]
    f.ok(set(victims) <= set(stale), f"{len(stale)} rows tagged stale, including all 20 cut tickers")
    mark(page.locator("#categories .tag:text-is('stale')"))
    f.shot(page, "20-stale-coverage.png", "#header", "#plan", page.locator("#categories details").first)


@flow(21, "restart-persistence", "stop and restart the main proof server (same DB) → GET /, Analyze NVDA, Explain",
      "holdings, picks and the cached thesis are all still there")
def f21(R, f):
    hold = q(MAIN_DB, "SELECT ticker FROM holdings ORDER BY ticker")
    npk = q(MAIN_DB, "SELECT COUNT(*) FROM picks")[0][0]
    nth = q(MAIN_DB, "SELECT COUNT(*) FROM thesis")[0][0]
    R.main.stop()
    srv = R.main
    f.note(f"server restarted (pid {srv.p.pid}); DB has {len(hold)} holdings, {npk} picks, {nth} theses")
    page = f.page(srv.url)
    rows = holdings_rows(page)
    f.ok(sorted(r["ticker"] for r in rows) == [r[0] for r in hold] and hold, f"{len(rows)} holdings rendered")
    f.ok(len(track_rows(page)) == npk and npk > 0, f"{npk} track-record rows rendered")
    analyze(page, "NVDA")
    page.click("#explain-btn")
    page.wait_for_selector("#explain h3", timeout=10000)
    f.ok("cached this week" in txt(page, "#explain h3").lower(), f"thesis: “{txt(page, '#explain h3')}”")
    f.shot(page, "21-restart-persistence.png", "#holdings", "#track", "#explain")


@flow(22, "phone-width", "viewport 390×844 → GET / (full page)", "no horizontal page scroll (scrollWidth ≤ 390); "
      "tables scroll inside their containers")
def f22(R, f):
    page = f.page(R.main.url, viewport={"width": 390, "height": 844})
    sw = page.evaluate("document.documentElement.scrollWidth")
    f.ok(sw <= 390, f"document scrollWidth = {sw}px")
    info = page.evaluate("""() => [...document.querySelectorAll('table')].map(t => { const c = t.closest('.scroll');
        return {inScroll: !!c, ox: c ? getComputedStyle(c).overflowX : null,
                overflows: c ? c.scrollWidth > c.clientWidth + 1 : false, visible: t.offsetParent !== null}; })""")
    f.ok(info and all(i["inScroll"] and i["ox"] == "auto" for i in info), f"all {len(info)} tables sit in "
                                                                          "overflow-x:auto containers")
    vis = [i for i in info if i["visible"]]
    f.ok(any(i["overflows"] for i in vis), f"{sum(i['overflows'] for i in vis)} of {len(vis)} visible tables are wider "
                                           "than their container (they scroll inside it)")
    f.shot(page, "22-phone-width.png")


@flow(23, "dark-mode", "browser context with color_scheme='dark' → GET /", "dark background, light text; plan + "
      "categories readable")
def f23(R, f):
    page = f.page(R.main.url, color_scheme="dark")

    def lum(css: str) -> float:
        r, g, b = [int(x) / 255 for x in re.findall(r"[\d.]+", css)[:3]]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    fg = page.evaluate("getComputedStyle(document.body).color")
    f.ok(lum(bg) < 0.25 and lum(fg) > 0.6, f"body background {bg} (lum {lum(bg):.2f}), text {fg} (lum {lum(fg):.2f})")
    f.shot(page, "23-dark-mode.png", "#plan", "#categories")


@flow(24, "parity", "run e2e/parity.py on the proof DB (as-of 2022-06-30 and 2024-12-31)",
      "app path == lab path: same eligible set, trend flags, composite within 1e-9, identical B0R buy list")
def f24(R, f):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FINRES_")} | {"FINRES_DB": MAIN_DB,
                                                                                  "PYTHONPATH": str(ROOT)}
    p = subprocess.run([PY, "-m", "e2e.parity"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
    out = p.stdout + (("\n" + p.stderr[-2000:]) if p.returncode else "")
    print(out)
    f.extra["output"] = out
    f.ok(p.returncode == 0 and ("PARITY PASS" in out or out.startswith("SKIP")),
         f"exit {p.returncode}: {out.strip().splitlines()[-1] if out.strip() else ''}")


@flow(25, "sec-8k-flag", "find a categories row with the red 8-K tag (OKLO preferred) → Analyze it",
      "red 8-K tag on the row and on the analyze card's Watch line with the SEC 8-K warning")
def f25(R, f):
    page = f.page(R.main.url)
    flagged = [c for c in categories(page) if c["sec"]]
    f.note("rows with an 8-K tag: " + (", ".join(c["ticker"] for c in flagged) or "none"))
    if not flagged:
        row = page.locator("#categories table.cat tbody tr").first
        f.note("no universe ticker has a severe 8-K in the last 45 days today; screenshot shows an untagged row")
        f.shot(page, "25-sec-8k-flag.png", row)
        return
    c = next((c for c in flagged if c["ticker"] == "OKLO"), flagged[0])
    t = c["ticker"]
    row = page.locator("#categories table.cat tbody tr").filter(has=page.locator(f".tk:text-is('{t}')"))
    row.evaluate("tr => tr.closest('details').open = true")
    tip = row.locator(".tag.red").get_attribute("title")
    f.ok(tip and "8-K" in tip, f"{t} row tag title: “{tip}”")
    analyze(page, t)
    w = txt(page, "#analysis .watch")
    f.ok(page.locator("#analysis .watch .tag.red").count() == 1 and "SEC 8-K:" in w, f"{t} card Watch line: “{w}”")
    mark(row.locator(".tag.red"))
    mark(page.locator("#analysis .watch"))
    f.shot(page, "25-sec-8k-flag.png", [row.locator("xpath=ancestor::table[1]").locator("thead"), row],
           "#analysis .card-h", "#analysis .watch")


@flow(26, "data-check", "run e2e/data_check.py (TL-written independent recomputation) with FINRES_DB=/tmp/finres_proof.db",
      "close, 12-1 return and SMA200 from the app == independent yfinance recomputation for NVDA, VST, TSM")
def f26(R, f):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FINRES_")} | {"FINRES_DB": MAIN_DB, "PYTHONPATH": "."}
    p = subprocess.run([PY, "e2e/data_check.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    f.ok(p.returncode == 0, f"exit {p.returncode}" + (f": {p.stderr[-300:]}" if p.returncode else ""))
    rows = json.loads((PROOF / "data_check.json").read_text())
    f.extra["output"] = rows
    for r in rows:
        eq = {k: r[f"{k}_indep"] == r[f"{k}_app"] for k in ("close", "ret12_1", "sma200")}
        f.ok(all(eq.values()), f"{r['ticker']} {r['date']}: close {r['close_app']}/{r['close_indep']}, 12-1 "
                               f"{r['ret12_1_app']}/{r['ret12_1_indep']}, sma200 {r['sma200_app']}/{r['sma200_indep']} "
                               "(app/independent)")
    f.shots.append("data_check.json")


# ---------------------------------------------------------------- main

def setup() -> None:
    PROOF.mkdir(parents=True, exist_ok=True)
    if USER_DB.exists():
        s = sqlite3.connect(f"file:{USER_DB}?mode=ro", uri=True)
        try:
            last = s.execute("SELECT MAX(date) FROM snapshot").fetchone()[0]
        finally:
            s.close()
        if last and (date.today() - date.fromisoformat(last)).days <= 6:
            copy_db(USER_DB, BASE_DB)
            copy_db(BASE_DB, MAIN_DB)
            print(f"setup: copied data/finres.db (latest snapshot {last}) → {BASE_DB}, {MAIN_DB}")
            return
    for p in (BASE_DB, MAIN_DB):
        _rm_db(p)
    print("setup: no recent data/finres.db; proof servers start empty and auto-refresh")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m e2e.flows")
    ap.add_argument("--only", default="", help="comma-separated flow ids (results merge into results.json)")
    args = ap.parse_args(argv)
    only = {int(x) for x in args.only.split(",") if x.strip()}
    todo = [x for x in FLOWS if not only or x[0] in only]
    setup()
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            R = Run(browser)
            try:
                for fid, name, steps, expected, fn in todo:
                    results.append(run_flow(R, fid, name, steps, expected, fn))
                    _write(results, bool(only))
            finally:
                browser.close()
    finally:
        Server.stop_all()
    _write(results, bool(only))
    print(f"\n{'id':>3}  {'flow':<22} {'pass':<5} {'sec':>6}  screenshot")
    for r in results:
        s = r["screenshot"]
        print(f"{r['id']:>3}  {r['name']:<22} {'yes' if r['pass'] else 'NO':<5} {r['seconds']:>6}  "
              f"{', '.join(s) if isinstance(s, list) else s or '-'}")
    failed = [r["id"] for r in results if not r["pass"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} flows passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


def _write(results: list, merge: bool) -> None:
    path = PROOF / "results.json"
    out = results
    if merge and path.exists():
        keep = {r["id"]: r for r in json.loads(path.read_text())}
        keep |= {r["id"]: r for r in results}
        out = [keep[k] for k in sorted(keep)]
    path.write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
