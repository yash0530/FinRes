"""Offline tests for the ADR-010 Qwen classification (research/pit_universe/classify.py)."""
import gzip
import json

import pytest

from research.pit_universe import classify as c
from research.pit_universe import scan


def test_item1_skips_table_of_contents():
    body = " ".join(["We design and sell datacenter switches."] * 60)
    text = ("TABLE OF CONTENTS PART I Item 1. Business 3 Item 1A. Risk Factors 12 Item 2. Properties 30 "
            f"PART I ITEM 1. BUSINESS Overview {body} see \u201cItem 1A. Risk Factors,\u201d {body} ITEM 1A. RISK FACTORS "
            "our stock may fall. MD&A: for our strategy see Item 1. Business. " + " ".join(["More MD&A text."] * 900))
    out, method = c.item1(text)
    assert method == "item1" and out.startswith("Overview We design") and "RISK FACTORS" not in out
    assert "Properties 30" not in out and "Risk Factors,\u201d We design" in out  # cross-references are skipped


def test_item1_caps_words_and_falls_back_after_cover():
    text = "Item 1. Business " + " ".join(f"w{i}" for i in range(3000)) + " Item 1A. Risk Factors"
    out, _ = c.item1(text)
    assert len(out.split()) == c.WORDS and out.startswith("w0 ")
    out, method = c.item1("FORM 10-K Indicate by check mark whether yes No. We sell optical modules to hyperscalers.")
    assert method == "cover" and out.startswith("whether yes No. We sell")


def test_anonymize_name_variants_and_tickers():
    names = ["NVIDIA CORP/CA", "Mellanox Technologies, Ltd.", "ACTIVE POWER INC (2000-07-03..2016-11-29)"]
    text = ("NVIDIA Corporation and Nvidia sell GPUs. Mellanox Technologies, Ltd. and Mellanox Technologies merged. "
            "Active Power, Inc. sells UPS. Shares trade as NVDA and MLNX. Invidia and nvidias stay; now is fine; "
            "powerful active power supplies.")
    out = c.anonymize(text, names, ["NVDA", "MLNX", "NOW", "A", ""])
    assert "NVIDIA" not in out and "Nvidia " not in out and "Mellanox" not in out and "NVDA" not in out
    assert "MLNX" not in out and out.startswith("the Company and the Company sell GPUs.")
    assert "Invidia and nvidias stay; now is fine" in out  # unrelated words and lower-case "now" untouched
    assert "powerful active power supplies." in out and "Inc" not in out and "Corporation" not in out
    assert "the Company merged." in out


def test_variants_strip_suffixes():
    v = c.variants(["APPLIED MATERIALS INC /DE", "Corporate Office Properties, L.P.", "Salesforce, Inc."])
    assert {"APPLIED MATERIALS INC", "APPLIED MATERIALS", "Corporate Office Properties", "Salesforce"} <= set(v)
    assert v == sorted(v, key=len, reverse=True)


def test_parse_validates_schema():
    good = {"sells_into": True, "role": "networking", "confidence": "high", "evidence": "x" * 300}
    assert c.parse("```json\n" + json.dumps(good) + "\n```")["evidence"] == "x" * 200
    for bad in ["not json", json.dumps(good | {"role": "banking"}), json.dumps(good | {"sells_into": "yes"}),
                json.dumps({k: v for k, v in good.items() if k != "confidence"}), json.dumps(good | {"extra": 1})]:
        with pytest.raises(ValueError):
            c.parse(bad)


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return self

    def json(self):
        return {"choices": [{"message": {"content": self.content, "reasoning": "ignored"}}]}


def test_classify_request_and_retry_once():
    good = json.dumps({"sells_into": False, "role": "none", "confidence": "medium", "evidence": "hosts its own SaaS"})
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Resp("oops" if len(calls) == 1 else good)
    res = c.classify("text", post=post)
    assert res["sells_into"] is False and res["role"] == "none" and "seconds" in res and len(calls) == 2
    body = calls[0][1]
    assert calls[0][0].endswith("/chat/completions") and calls[0][2] == 300
    assert (body["model"], body["temperature"], body["reasoning_effort"], body["max_tokens"]) == ("qwen-local", 0.2, "low", 1500)
    assert body["response_format"]["json_schema"]["strict"] is True
    assert calls[1][1]["messages"][-1]["role"] == "user" and "invalid" in calls[1][1]["messages"][-1]["content"]
    with pytest.raises(ValueError):
        c.classify("text", post=lambda url, json, timeout: _Resp("still bad"))


def _setup(tmp_path, monkeypatch, accs):
    monkeypatch.setattr(c, "DATA", tmp_path)
    monkeypatch.setattr(c, "BIZ", tmp_path / "business")
    monkeypatch.setattr(scan, "DATA", tmp_path)  # log_error
    (tmp_path / "business").mkdir()
    for a in accs:
        (tmp_path / "business" / f"{a}.txt.gz").write_bytes(gzip.compress(f"text {a}".encode()))
    scan.write_csv(tmp_path / "business.csv", [{"acc": a, "cik": "1", "filed": "2014-03-01", "words": 2, "method": "item1"}
                                               for a in accs], c.MAN_COLS)


def test_run_resumes_and_skips_errors(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["a1", "a2", "a3"])
    seen = []

    def call(text):
        seen.append(text)
        if text == "text a2":
            raise RuntimeError("timeout")
        return {"sells_into": True, "role": "compute", "confidence": "high", "evidence": "e", "seconds": 1.0}
    c.step_run(call)
    assert [r["acc"] for r in scan.read_csv(tmp_path / "qwen_class.csv")] == ["a1", "a3"]
    assert list(scan.read_csv(tmp_path / "qwen_class.csv")[0]) == c.CLASS_COLS
    seen.clear()
    c.step_run(lambda t: seen.append(t) or {"sells_into": False, "role": "none", "confidence": "low", "evidence": "",
                                            "seconds": 2.0})
    assert seen == ["text a2"]  # only the failed one is retried
    assert [r["acc"] for r in scan.read_csv(tmp_path / "qwen_class.csv")] == ["a1", "a3", "a2"]
    assert "timeout" in (tmp_path / "errors.csv").read_text()


def test_universe_q_keeps_only_sells_into_filings():
    scores = [{"cik": "1", "acc": "x1", "filed": "2014-03-01"}, {"cik": "1", "acc": "x2", "filed": "2015-03-01"},
              {"cik": "2", "acc": "y0", "filed": "2014-05-01"}, {"cik": "2", "acc": "y1", "filed": "2014-05-01"},
              {"cik": "3", "acc": "z1", "filed": "2014-06-01"}]
    rows = [{"year": "2015", "cik": "1", "filed": "2014-03-01"}, {"year": "2016", "cik": "1", "filed": "2015-03-01"},
            {"year": "2015", "cik": "2", "filed": "2014-05-01"}, {"year": "2015", "cik": "3", "filed": "2014-06-01"}]
    cls = [{"acc": "x1", "sells_into": "True"}, {"acc": "x2", "sells_into": "False"}, {"acc": "y1", "sells_into": "True"},
           {"acc": "y0", "sells_into": "False"}]  # z1 unclassified -> dropped; cik 2 uses the later acc y1
    assert [(r["cik"], r["year"]) for r in c.filter_universe(rows, scores, cls)] == [("1", "2015"), ("2", "2015")]


def test_spot_filings_match_cik_and_filing_year():
    md = ("| 50863 | INTEL CORP | 2015 | **REAL** | Data Center Group |\n"
          "| 796343 | ADOBE INC. | 2023 | **BOILERPLATE** | Sensei |\n")
    scores = [{"cik": "50863", "acc": "i14", "filed": "2014-02-14", "eligible_text": "1"},
              {"cik": "50863", "acc": "i15", "filed": "2015-02-13", "eligible_text": "1"},
              {"cik": "796343", "acc": "a23", "filed": "2023-01-20", "eligible_text": "1"},
              {"cik": "796343", "acc": "a23b", "filed": "2023-12-20", "eligible_text": "0"}]
    out = c.spot_filings(md, scores)
    assert [(s["acc"], s["truth"]) for s in out] == [("i15", True), ("a23", False)]


def test_report_counts_agreement():
    rows = [{"cik": "1", "name": "A", "year": "2015", "truth": "True", "sells_into": "True", "role": "compute",
             "confidence": "high", "evidence": "sells GPUs", "seconds": "10"},
            {"cik": "2", "name": "B", "year": "2016", "truth": "False", "sells_into": "True", "role": "cloud",
             "confidence": "low", "evidence": "a|b", "seconds": "20"}]
    md = c.report(rows)
    assert "1/2 = 50%" in md and "15.0 s per call" in md and "| truth false | 1 | 0 |" in md and "a/b" in md
