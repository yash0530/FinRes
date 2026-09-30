"""Offline tests for the ADR-007 PIT universe build (research/pit_universe/build.py)."""
import csv

import pandas as pd

from research.pit_universe import build, scan


def _score(cik, acc, filed, elig="1", in_group="1", symbols="", sic="3674"):
    return {"cik": str(cik), "acc": acc, "filed": filed, "sic_filing": sic, "in_group": in_group,
            "eligible_text": elig, "symbols": symbols}


def test_universe_year_uses_latest_10k_filed_in_prior_year():
    scores = [_score(1, "a1", "2014-03-01", elig="1"), _score(1, "a2", "2014-11-20", elig="0"),  # latest wins: no
              _score(2, "b1", "2014-02-01", elig="0"), _score(2, "b2", "2014-12-30", elig="1"),  # latest wins: yes
              _score(3, "c1", "2015-01-05", elig="1"),  # filed in 2015 -> counts for 2016, not 2015
              _score(4, "d1", "2014-05-05", in_group="0"),
              _score(5, "e1", "2008-03-01"), _score(5, "e2", "2026-02-01")]  # 2009 and 2027: outside 2010-2026
    rows = build.eligible(scores, {"2": "BETA"})
    assert [(r["year"], r["cik"]) for r in rows] == [(2015, 2), (2016, 3)]
    assert rows[0]["name"] == "BETA" and rows[0]["filed"] == "2014-12-30"


def test_group_from_sic():
    assert [build.group_of(s) for s in ("3674", "3572", "3576", "3663", "7372", "4911", "6798", "3812", "3585", "x")] \
        == ["semis", "semis", "network", "network", "cloud", "infra", "infra", "infra", "infra", "other"]


TIINGO = """ticker,exchange,assetType,priceCurrency,startDate,endDate
SNDK,NASDAQ,Stock,USD,1995-11-08,2016-05-20
SNDK,NASDAQ,Stock,USD,2025-02-24,2026-09-29
OLDQ,NASDAQ,Stock,USD,2001-01-02,2013-06-30
OLDX,PINK,Stock,USD,2001-01-02,2026-09-29
ETFZ,NYSE ARCA,ETF,USD,2001-01-02,2026-09-29
ALTR,NASDAQ,Stock,USD,1988-04-04,2017-10-31
ALTR,NASDAQ,Stock,USD,2017-11-01,2025-03-26
"""


def test_recycled_ticker_resolved_by_filing_dates_and_unmapped_kept():
    tiingo = build.load_tiingo(TIINGO)
    assert set(tiingo) == {"SNDK", "OLDQ", "ALTR"}  # PINK and ETF rows dropped
    scores = [_score(10, "x1", "2015-02-10", symbols="SNDK"), _score(20, "y1", "2025-08-01", symbols="SNDK"),
              _score(30, "z1", "2012-02-01", symbols="OLD"), _score(40, "w1", "2012-02-01", symbols="NOPE"),
              _score(50, "a1", "2013-02-01", symbols="ALTR"), _score(50, "a2", "2016-02-01", symbols="ALTR"),
              _score(60, "b1", "2016-12-01", symbols="ALTR"), _score(60, "b2", "2024-02-01", symbols="ALTR")]
    elig = build.eligible(scores)
    rows, review = build.map_rows(elig, scores, {"20": "SNDK"}, tiingo)
    by = {(r["cik"], r["year"]): r for r in rows}
    by |= {r["cik"]: r for r in rows}
    assert (by["10"]["tiingo_start"], by["10"]["status"]) == ("1995-11-08", "delisted")  # old SanDisk
    assert (by["20"]["tiingo_start"], by["20"]["status"]) == ("2025-02-24", "listed")  # new SanDisk
    assert by[("50", 2017)]["tiingo_start"] == "1988-04-04"  # both ALTR rows overlap 2017: filing dates decide
    assert by[("60", 2017)]["tiingo_start"] == "2017-11-01"
    assert by["30"]["ticker"] == "OLDQ" and by["30"]["exchange"] == "NASDAQ"  # bankrupt-suffix fallback
    assert by["40"]["status"] == "unmapped" and by["40"]["ticker"] == ""
    assert [(r["cik"], r["reason"]) for r in review] == [("40", "unmapped")]


def test_map_falls_back_to_other_filings_then_submissions_and_drops_duplicates():
    tiingo = build.load_tiingo("ticker,exchange,assetType,priceCurrency,startDate,endDate\n"
                               "META,NASDAQ,Stock,USD,2012-05-18,2026-09-29\nSO,NYSE,Stock,USD,1990-01-02,2026-09-29\n")
    scores = [_score(1, "f1", "2014-02-01", symbols="FB"), _score(1, "f2", "2023-02-01", symbols="META"),
              _score(2, "p1", "2014-02-01", symbols="", sic="4911"), _score(3, "s1", "2014-02-01", symbols="SO",
                                                                            sic="4911")]
    rows, review = build.map_rows(build.eligible(scores), scores, {"2": "SO", "3": ""}, tiingo)
    got = {(r["year"], r["cik"]): r["ticker"] for r in rows}
    assert got[(2015, "1")] == "META"  # FB has no Stock row; the company's later filing says META
    assert got[(2015, "2")] == "SO" and (2015, "3") not in got  # subsidiary on the parent's ticker dropped
    assert [(r["cik"], r["reason"]) for r in review] == [("3", "duplicate ticker")]


def test_price_plan_prioritizes_delisted_by_eligible_years():
    rows = [{"year": str(y), "cik": c, "ticker": t, "status": s, "tiingo_start": "2001-01-01", "tiingo_end": "2015-01-01"}
            for c, t, s, ys in (("1", "AAA", "delisted", range(2010, 2012)), ("2", "BBB", "delisted", range(2010, 2015)),
                                ("3", "CCC", "listed", range(2010, 2013)), ("4", "", "unmapped", range(2010, 2020)))
            for y in ys]
    listed, todo = build.price_plan(rows)
    assert list(listed) == ["3"] and listed["3"]["year"] == "2012"
    assert [u["cik"] for u in todo] == ["2", "1"]


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body
        self.text = body if isinstance(body, str) else "[]"

    def json(self):
        return self._body


def test_tiingo_resume_rate_limit_and_monthly_quota_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DATA", tmp_path)
    t = [1_000_000.0]
    clock = lambda: t[0]  # noqa: E731
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    # 50 requests already made in the last hour (from an earlier run) -> must wait before the next one
    scan.write_csv(tmp_path / "tiingo_done.csv", [{"cik": f"9{i}", "ticker": "X", "status": 200, "rows": 1,
                                                   "at": t[0] - 100 + i} for i in range(50)],
                   ["cik", "ticker", "status", "rows", "at"])
    calls, stored = [], {}
    replies = iter([_Resp(200, [{"date": "2012-01-03T00:00:00.000Z", "adjClose": 5.0}]),
                    _Resp(429, "You have run over your hourly request allocation"),
                    _Resp(200, []),
                    _Resp(429, "Error: You have exceeded your monthly unique symbol request allocation")])

    def get(url, params, timeout):
        calls.append(url)
        return next(replies)

    todo = [{"cik": c, "ticker": c, "start": "2010-01-01", "end": "2013-01-01"} for c in ("90", "A", "B", "C", "D")]
    left = build.tiingo_fetch(todo, "k", lambda c, s: stored.update({c: s}), get, sleep, clock)
    assert calls[0].endswith("/A/prices") and "90" not in "".join(calls)  # already done -> skipped
    assert slept[0] > 3000 and 3600 in slept  # waited for the hourly window, then slept after the 429
    assert left == 2 and list(stored) == ["A"] and stored["A"].index[0] == pd.Timestamp("2012-01-03")
    with open(tmp_path / "tiingo_done.csv") as f:
        done = [r["cik"] for r in csv.DictReader(f)]
    assert done[-2:] == ["A", "B"]  # C hit the monthly quota: not marked done, resumes next run
    replies = iter([_Resp(200, []), _Resp(200, [])])
    assert build.tiingo_fetch(todo, "k", lambda c, s: None, get, sleep, clock) == 0
    assert calls[-2:] == [build.TIINGO_PX.format("C"), build.TIINGO_PX.format("D")]


def test_coverage_counts_priced_share():
    rows = [{"year": "2012", "cik": "1", "status": "listed"}, {"year": "2012", "cik": "2", "status": "unmapped"}]
    md = build.coverage(rows, [{"year": "2012", "reason": "duplicate ticker"}], {"1": ("2011-05-01", "2026-09-01")})
    assert "| 2012 | 3 | 2 | 1 | 0 | 1 | 1 | 1 | 1 | 50% |" in md
