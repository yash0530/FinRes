"""SEC 8-K severe-item flags: point-in-time window, item parsing, cache/no-CIK behaviour (offline)."""
import gzip
import json
from datetime import date

import httpx

from finres import edgar

ASOF = date(2026, 9, 27)
SUB = {"filings": {"recent": {
    "form":       ["8-K",        "8-K",        "8-K/A",      "8-K",        "8-K",        "10-Q",       "8-K",        "8-K"],
    "filingDate": ["2026-09-27", "2026-09-20", "2026-09-12", "2026-09-01", "2026-08-13", "2026-08-10", "2026-08-12", "2026-07-01"],
    "items":      ["4.01",       "2.02,9.01",  "4.01,9.01",  "5.02",       "1.03",       "",           "3.01",       "4.02"],
}}}


def test_window_forms_and_items():
    got = edgar.severe_8k(SUB, ASOF)
    # 09-27 is ON asof (excluded, PIT); 09-20 2.02/9.01 and 09-01 5.02 not severe; 10-Q ignored;
    # asof - 45d = 08-13 is the open lower edge, so the 08-13 bankruptcy and older filings are outside.
    assert got == [{"date": "2026-09-12", "items": ["4.01"], "labels": ["auditor change"]}]


def test_window_edges_and_multi_item():
    got = edgar.severe_8k(SUB, ASOF, days=46)  # lo = 2026-08-12 (strict) -> 08-13 bankruptcy now inside
    assert [f["date"] for f in got] == ["2026-09-12", "2026-08-13"]
    assert got[1]["labels"] == ["bankruptcy/receivership"]
    got = edgar.severe_8k(SUB, date(2026, 9, 28))  # the asof-day filing becomes visible the next day
    assert got[0] == {"date": "2026-09-27", "items": ["4.01"], "labels": ["auditor change"]}
    sub = {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-09-01"], "items": ["1.02, 4.02,9.01"]}}}
    assert edgar.severe_8k(sub, ASOF)[0]["items"] == ["1.02", "4.02"]


def test_non_severe_and_empty():
    assert edgar.severe_8k(SUB, date(2026, 9, 5), days=10) == []  # only the 5.02 in (08-26, 09-05)
    assert edgar.severe_8k(None, ASOF) == [] and edgar.severe_8k({}, ASOF) == []


def test_recent_8k_no_cik_cache_only_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "EDGAR_DIR", tmp_path)
    monkeypatch.setattr(edgar, "cik_map", lambda: {"AAA": 1})

    def no_net(url):
        raise AssertionError("network")

    monkeypatch.setattr(edgar, "_get", no_net)
    assert edgar.recent_8k("ZZZ", ASOF) == []  # no CIK
    assert edgar.recent_8k("AAA", ASOF, max_age_days=None) == []  # cache-only, nothing cached
    (tmp_path / "AAA.sub.json.gz").write_bytes(gzip.compress(json.dumps(SUB).encode()))
    assert edgar.recent_8k("AAA", ASOF)[0]["date"] == "2026-09-12"  # fresh cache: no fetch
    assert edgar.recent_8k("AAA", ASOF, max_age_days=None)[0]["items"] == ["4.01"]


def test_recent_8k_fetch_and_404(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "EDGAR_DIR", tmp_path)
    monkeypatch.setattr(edgar, "cik_map", lambda: {"AAA": 1, "BBB": 2})
    urls = []

    def get(url):
        urls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(404, request=req) if "CIK0000000002" in url else httpx.Response(200, json=SUB, request=req)

    monkeypatch.setattr(edgar, "_get", get)
    assert edgar.recent_8k("AAA", ASOF)[0]["labels"] == ["auditor change"]
    assert urls == ["https://data.sec.gov/submissions/CIK0000000001.json"]
    assert (tmp_path / "AAA.sub.json.gz").exists()
    assert edgar.recent_8k("BBB", ASOF) == []
