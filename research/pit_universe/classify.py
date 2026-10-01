"""ADR-010 Qwen-cleaned PIT universe. CLI: python -m research.pit_universe.classify {extract|validate|run|universe}"""
import argparse, gzip, json, re, time  # noqa: E401
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

from finres import config
from research.pit_universe.build import eligible
from research.pit_universe.scan import (DATA, GROUPS, HERE, NOT_SYM, WORKERS, fetch, guard, html_to_text, log_error,
                                        parse_index_page, read_csv, write_csv)

BIZ = DATA / "business"
WORDS, TIMEOUT, PROMPT_VERSION = 1500, 300, "v1"
MAN_COLS = ["acc", "cik", "filed", "words", "method"]
CLASS_COLS = ["acc", "cik", "filed", "sells_into", "role", "confidence", "seconds", "evidence"]  # evidence: rows from 2026-10
VAL_COLS = ["cik", "name", "year", "acc", "truth", "sells_into", "role", "confidence", "evidence", "seconds", "prompt"]
ROLES = "compute semis_equipment memory_storage networking datacenter_infra power_energy cloud ai_software none".split()
CONF = ["low", "medium", "high"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sells_into", "role", "confidence", "evidence"],
          "properties": {"sells_into": {"type": "boolean"}, "role": {"type": "string", "enum": ROLES},
                         "confidence": {"type": "string", "enum": CONF}, "evidence": {"type": "string", "maxLength": 200}}}
SYSTEM = """You classify a company's BUSINESS from an anonymized excerpt of its 10-K annual report (Item 1, Business). Answer one question: does the Company SELL products or services INTO AI, datacenters, GPUs/accelerators, datacenter networking/optical interconnect, data storage, or datacenter power/cooling?
- sells_into = true only if the Company's own products or services are sold to customers in those markets (for example chips, servers, accelerators, storage, networking or optical gear, datacenter space/colocation/cloud infrastructure, power or cooling equipment for datacenters, AI models or AI platforms sold to others). These do NOT count: using AI/ML internally or as a feature inside unrelated software; operating or leasing its own data center to host its own software or website; generic IT, cybersecurity or risk-factor language; a product merely named "data center".
- role: the Company's main role in those markets (compute, semis_equipment, memory_storage, networking, datacenter_infra, power_energy, cloud, ai_software), or none if sells_into is false. confidence: low, medium or high. evidence: a short phrase (at most 200 characters) copied from the text that supports your answer.
Judge only from the text. The company is anonymized as "the Company" - do not guess its identity. Reply with ONLY the JSON object."""


# ---------- extract ----------

XREF = r"(?<![\"'\u201c\u2018])(?<![\"'\u201c\u2018] )(?<!see )(?<!in )(?<!under )(?<!Part I, )"  # not a cross-reference
ITEM1 = re.compile(XREF + r"\bItems?\s*1\s*(?:and\s*2\s*)?[.:\-\u2013\u2014]*\s*Business", re.I)
ITEM_END = re.compile(XREF + r"\bItem\s*(?:1A|1B|2)\b", re.I)
COVER = re.compile(r"Indicate by check mark|DOCUMENTS INCORPORATED BY REFERENCE", re.I)
SUFFIXES = r"(?:inc|incorporated|corp|corporation|ltd|limited|holdings|co|company|plc|n\.?v|s\.?a|l\.?p|llc)\b\.?"
SUFFIX = re.compile(r"[\s,]+" + SUFFIXES + "$", re.I)


def item1(text: str) -> tuple[str, str]:
    """(first WORDS words of Item 1, method): the heading with the most text before Item 1A/1B/2 (skips the TOC), else post-cover."""
    best = None
    for m in ITEM1.finditer(text):
        nxt = ITEM_END.search(text, m.end())
        n = (nxt.start() if nxt else len(text)) - m.end()
        best = (n, m.end(), m.end() + n) if best is None or n > best[0] else best
    if best and len(text[best[1]:best[2]].split()) >= 100:
        return " ".join(text[best[1]:best[2]].split()[:WORDS]), "item1"
    covers = [m.end() for m in COVER.finditer(text[:80_000])]
    return " ".join(text[covers[-1] if covers else 0:].split()[:WORDS]), "cover"


def variants(names: list[str]) -> list[str]:
    """Each name, minus a /STATE tag, then with Inc/Corp/Ltd/Holdings/... suffixes stripped one at a time."""
    out = []
    for n in names:
        n = re.sub(r"\s*\(\d{4}-[\d-]*\.\.[\d-]*\)$", "", n or "").strip()  # companies.csv former_names date span
        n = re.sub(r"\s*/[A-Z]{2,3}/?$", "", n).strip(" ,.")
        while len(n) >= 2 and n not in out:
            out.append(n)
            n = SUFFIX.sub("", n).strip(" ,.")
    return sorted(set(out), key=len, reverse=True)


def anonymize(text: str, names: list[str], tickers: list[str]) -> str:
    """Name variants (case-insensitive but capitalized, plus any trailing Inc/Corp...) and tickers (case-sensitive, so
    "NOW" never eats "now") -> "the Company"."""
    vs = [r"\s+".join(map(re.escape, v.split())) for v in variants(names)]
    if vs:
        rx = r"(?<!\w)(?-i:(?=[A-Z0-9]))(?:" + "|".join(vs) + r")(?:,?\s+" + SUFFIXES + r")*(?!\w)"
        text = re.sub(rx, "the Company", text, flags=re.I)
    ts = sorted({t for t in tickers if len(t) >= 2 and t.upper() not in NOT_SYM}, key=len, reverse=True)
    return re.sub(r"\b(?:" + "|".join(map(re.escape, ts)) + r")\b", "the Company", text) if ts else text


def acc_of(scores: list[dict]) -> dict[tuple, str]:
    """{(cik, filed): acc} with build.eligible's tie-break: the latest (filed, acc)."""
    acc = {}
    for r in scores:
        acc[(r["cik"], r["filed"])] = max(acc.get((r["cik"], r["filed"]), ""), r["acc"])
    return acc


def targets(scores: list[dict]) -> list[dict]:
    """Filings behind every base or R-A (threshold 2) eligible company-year: [{acc, cik, filed}], one per acc."""
    acc = acc_of(scores)
    rows = eligible(scores) + eligible(scores, rule=(2.0, GROUPS))
    return list({acc[(str(e["cik"]), e["filed"])]: {"acc": acc[(str(e["cik"]), e["filed"])], "cik": str(e["cik"]),
                                                     "filed": e["filed"]} for e in rows}.values())


def identity() -> dict[str, tuple[list, list]]:
    """{cik: (names, tickers)} from companies.csv (name + former names) and every filing-stated symbol."""
    ids = {r["cik"]: ([r["name"]] + r["former_names"].split("|"), r["tickers"].split("|"))
           for r in read_csv(DATA / "companies.csv")}
    for r in read_csv(DATA / "scores.csv"):
        ids.setdefault(r["cik"], ([], []))[1].extend((r.get("symbols") or "").split("|"))
    return ids


def extract_one(f: dict, ids: dict) -> dict:
    path = BIZ / f"{f['acc']}.txt.gz"
    if not path.exists():
        cik, acc = int(f["cik"]), f["acc"]
        r = fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm")
        _, primary = parse_index_page(r.raise_for_status().text, cik)
        if primary is None:
            raise ValueError("no primary document")
        excerpt, method = item1(html_to_text(fetch(primary).raise_for_status().content.decode("utf-8", "replace")))
        path.write_bytes(gzip.compress(anonymize(excerpt, *ids.get(f["cik"], ([], []))).encode()))
        return {**f, "words": len(excerpt.split()), "method": method}
    return {**f, "words": len(gzip.decompress(path.read_bytes()).split()), "method": "cached"}


def step_extract(todo: list[dict] | None = None) -> list[dict]:
    BIZ.mkdir(parents=True, exist_ok=True)
    man = {r["acc"]: r for r in read_csv(DATA / "business.csv")}
    todo = targets(read_csv(DATA / "scores.csv")) if todo is None else todo
    ids, t0, out = identity(), time.time(), []
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (f, (row, err)) in enumerate(zip(todo, ex.map(lambda f: guard(extract_one, f, ids), todo)), 1):
            log_error("extract", f["acc"], err) if err else out.append(man.setdefault(f["acc"], row))
            if i % 100 == 0 or i == len(todo):
                write_csv(DATA / "business.csv", list(man.values()), MAN_COLS)
                print(f"extract {i}/{len(todo)} {i / (time.time() - t0) * 60:.0f}/min", flush=True)
    print(f"{len(out)}/{len(todo)} extracted;", {m: sum(r["method"] == m for r in out) for m in ("item1", "cover")})
    return out


# ---------- classify ----------

def parse(content: str) -> dict:
    """Validated {sells_into, role, confidence, evidence} from the reply text; ValueError if it is not."""
    d = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip()))
    if not isinstance(d, dict) or set(d) != set(SCHEMA["required"]) or not isinstance(d["sells_into"], bool) \
            or d["role"] not in ROLES or d["confidence"] not in CONF or not isinstance(d["evidence"], str):
        raise ValueError(f"reply does not match the schema: {str(d)[:200]}")
    return d | {"evidence": " ".join(d["evidence"].split())[:200]}  # one line: the watchdog counts rows with wc -l


def classify(text: str, post=httpx.post) -> dict:
    """One Qwen call (ADR-010); retries once on invalid JSON. Adds 'seconds'."""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "10-K EXCERPT\n" + text}]
    body = {"model": config.LLM_MODEL, "messages": msgs, "temperature": 0.2, "reasoning_effort": "low", "max_tokens": 1500,
            "response_format": {"type": "json_schema", "json_schema": {"name": "exposure", "strict": True, "schema": SCHEMA}}}
    t0 = time.monotonic()
    for attempt in range(2):
        r = post(f"{config.LLM_URL}/chat/completions", json=body, timeout=TIMEOUT)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content") or ""  # never the reasoning field
        try:
            return parse(content) | {"seconds": round(time.monotonic() - t0, 1)}
        except ValueError as e:
            if attempt:
                raise
            body["messages"] = msgs + [{"role": "assistant", "content": content[:2000]},
                                       {"role": "user", "content": f"That reply was invalid: {str(e)[:500]}\n"
                                                                   "Reply again with ONLY the corrected JSON object."}]


def excerpt(acc: str) -> str:
    return gzip.decompress((BIZ / f"{acc}.txt.gz").read_bytes()).decode()


def step_run(call=classify) -> None:
    """Classify every extracted filing, one request at a time, resumable (appends to qwen_class.csv)."""
    out = DATA / "qwen_class.csv"
    rows = read_csv(out)
    done = {r["acc"] for r in rows}
    todo = [f for f in read_csv(DATA / "business.csv") if f["acc"] not in done and (BIZ / f"{f['acc']}.txt.gz").exists()]
    t0 = time.time()
    for i, f in enumerate(todo, 1):
        res, err = guard(call, excerpt(f["acc"]))
        if err:
            log_error("classify", f["acc"], err)
            continue
        rows.append({k: f[k] for k in ("acc", "cik", "filed")} | {k: res[k] for k in CLASS_COLS[3:]})
        write_csv(out, rows, CLASS_COLS)
        if i % 20 == 0 or i == len(todo):
            print(f"run {i}/{len(todo)} ETA {(len(todo) - i) * (time.time() - t0) / i / 3600:.1f} h", flush=True)


# ---------- validate ----------

SPOT = re.compile(r"^\| (\d+) \| (.+?) \| (\d{4}) \| \*\*(REAL|PARTIAL|BOILERPLATE)\*\* \|", re.M)


def spot_filings(md: str, scores: list[dict]) -> list[dict]:
    """The spot-check rows with their filing: the CIK's latest keyword-eligible 10-K filed in that year (else latest)."""
    out = []
    for cik, name, year, verdict in SPOT.findall(md):
        fs = sorted((r for r in scores if r["cik"] == cik and r["filed"][:4] == year),
                    key=lambda r: (r["eligible_text"] == "1", r["filed"], r["acc"]))
        out.append({"cik": cik, "name": name, "year": year, "truth": verdict != "BOILERPLATE", "verdict": verdict,
                    "acc": fs[-1]["acc"] if fs else "", "filed": fs[-1]["filed"] if fs else ""})
    return out


def report(rows: list[dict]) -> str:
    t, q = (lambda r: str(r["truth"]) == "True"), (lambda r: str(r["sells_into"]) == "True")
    cm = {(a, b): sum(t(r) == a and q(r) == b for r in rows) for a in (True, False) for b in (True, False)}
    agree, secs = sum(t(r) == q(r) for r in rows), [float(r["seconds"]) for r in rows]
    lines = ["# ADR-010 validation: Qwen vs the 30 hand-judged filings", "",
             f"Prompt {PROMPT_VERSION}. Truth: REAL/PARTIAL = true, BOILERPLATE = false. "
             f"**Agreement: {agree}/{len(rows)} = {agree / max(len(rows), 1):.0%}** (ADR-010 gate: >= 80%). "
             f"Average {sum(secs) / max(len(secs), 1):.1f} s per call.", "",
             "| | Qwen true | Qwen false |", "|---|---|---|",
             f"| truth true | {cm[True, True]} | {cm[True, False]} |", f"| truth false | {cm[False, True]} | {cm[False, False]} |",
             "", "| cik | name | year | truth | qwen sells_into | role | confidence | agree | evidence |",
             "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['cik']} | {r['name']} | {r['year']} | {r['truth']} | {r['sells_into']} | {r['role']} | "
              f"{r['confidence']} | {'yes' if t(r) == q(r) else '**no**'} | {r['evidence'].replace('|', '/')} |" for r in rows]
    return "\n".join(lines) + "\n"


def step_validate(call=classify) -> None:
    out = DATA / "qwen_validation.csv"
    spots = spot_filings((HERE / "spot_check.md").read_text(), read_csv(DATA / "scores.csv"))
    assert all(s["acc"] for s in spots), f"no filing for {[s for s in spots if not s['acc']]}"
    step_extract([{k: s[k] for k in ("acc", "cik", "filed")} for s in spots])
    done = {r["acc"]: r for r in read_csv(out) if r["prompt"] == PROMPT_VERSION}
    for s in spots:
        if s["acc"] not in done:
            res = call(excerpt(s["acc"]))
            done[s["acc"]] = {k: s[k] for k in ("cik", "name", "year", "acc", "truth")} | res | {"prompt": PROMPT_VERSION}
            write_csv(out, list(done.values()), VAL_COLS)
            print(f"{s['name']} {s['year']}: truth {s['truth']} qwen {res['sells_into']} ({res['seconds']} s)", flush=True)
    (HERE / "classify_validation.md").write_text(md := report([done[s["acc"]] for s in spots]))
    print(md)


# ---------- universe ----------

def filter_universe(rows: list[dict], scores: list[dict], cls: list[dict]) -> list[dict]:
    """Universe rows whose filing (the year's latest 10-K, as build.eligible picks it) Qwen says sells_into."""
    acc, yes = acc_of(scores), {r["acc"] for r in cls if str(r["sells_into"]) == "True"}
    return [r for r in rows if acc.get((str(r["cik"]), r["filed"])) in yes]


def step_universe() -> None:
    scores, cls = read_csv(DATA / "scores.csv"), read_csv(DATA / "qwen_class.csv")
    n, done = len(read_csv(DATA / "business.csv")), (DATA / "classify.done").exists()
    print(f"classified {len(cls)}/{n} filings" + ("" if done else " - PARTIAL: classify run not finished"))
    for tag in ("", "_ra"):
        rows = read_csv(HERE / f"universe{tag}.csv")
        keep = filter_universe(rows, scores, cls)
        write_csv(HERE / f"universe{tag}_q.csv", keep, list(rows[0]) if rows else [])
        print(f"universe{tag}_q.csv: {len(keep)}/{len(rows)} company-years, {len({r['cik'] for r in keep})} CIKs")
        before, after = Counter(r["year"] for r in rows), Counter(r["year"] for r in keep)
        print("  year: before -> after Qwen |", " ".join(f"{y}: {before[y]}->{after[y]}" for y in sorted(before)))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m research.pit_universe.classify")
    ap.add_argument("step", choices=["extract", "validate", "run", "universe"])
    {"extract": step_extract, "validate": step_validate, "run": step_run, "universe": step_universe}[ap.parse_args(argv).step]()


if __name__ == "__main__":
    main()
