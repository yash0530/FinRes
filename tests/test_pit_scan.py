"""Offline tests for the ADR-007 PIT 10-K scan (research/pit_universe/scan.py)."""
import csv
import json
from pathlib import Path

import pytest

from research.pit_universe import scan

ROOT = Path(__file__).resolve().parent.parent

FORM_IDX = """Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    March 31, 2024

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K             ACME CHIPS INC                                                1000001     2024-02-06  edgar/data/1000001/0001000001-24-000001.txt
10-K/A           ACME CHIPS INC                                                1000001     2024-03-01  edgar/data/1000001/0001000001-24-000002.txt
10-K405          OLD  POWER  CO                                                1000002     2024-01-30  edgar/data/1000002/0001000002-24-000003.txt
10-KSB           TINY CORP                                                     1000003     2024-01-15  edgar/data/1000003/0001000003-24-000004.txt
10-KT            TRANSITION CORP                                               1000004     2024-01-16  edgar/data/1000004/0001000004-24-000005.txt
SC 13D           SOME HOLDER                                                   1000005     2024-01-17  edgar/data/1000005/0001000005-24-000006.txt
"""

INDEX_HTM = """<html><body>
<div class="companyInfo"><span class="companyName">Parent Holdings (Filer) CIK: <a href="/cgi-bin/browse-edgar?CIK=0000999999&amp;action=getcompany">0000999999</a></span>
<p class="identInfo"><acronym title="Standard Industrial Code">SIC</acronym>: <b><a href="/cgi-bin/browse-edgar?action=getcompany&amp;SIC=6770">6770</a></b></p></div>
<div class="companyInfo"><span class="companyName">ACME CHIPS INC (Filer) CIK: <a href="/cgi-bin/browse-edgar?CIK=0001000001&amp;action=getcompany">0001000001</a></span>
<p class="identInfo"><acronym title="Standard Industrial Code">SIC</acronym>: <b><a href="/cgi-bin/browse-edgar?action=getcompany&amp;SIC=3674&amp;owner=include">3674</a></b> Semiconductors</p></div>
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>10-K</td><td><a href="/ix?doc=/Archives/edgar/data/1000001/000100000124000001/acme-10k.htm">acme-10k.htm</a></td><td>10-K</td><td>1503780</td></tr>
<tr><td>2</td><td>EX-21</td><td><a href="/Archives/edgar/data/1000001/000100000124000001/ex21.htm">ex21.htm</a></td><td>EX-21</td><td>9999999</td></tr>
<tr><td>3</td><td>XBRL</td><td><a href="/Archives/edgar/data/1000001/000100000124000001/acme.xml">acme.xml</a></td><td>EX-101.INS</td><td>8888888</td></tr>
<tr><td>&nbsp;</td><td>Complete submission text file</td><td><a href="/Archives/edgar/data/1000001/000100000124000001/0001000001-24-000001.txt">0001000001-24-000001.txt</a></td><td>&nbsp;</td><td>99999999</td></tr>
</table></body></html>"""


def test_parse_form_idx_keeps_only_10k_and_10k405():
    rows = scan.parse_form_idx(FORM_IDX)
    assert [(r["form"], r["cik"]) for r in rows] == [("10-K", 1000001), ("10-K405", 1000002)]
    r = rows[0]
    assert r["filed"] == "2024-02-06" and r["acc"] == "0001000001-24-000001"
    assert r["path"] == "edgar/data/1000001/0001000001-24-000001.txt" and r["company"] == "ACME CHIPS INC"
    assert rows[1]["company"] == "OLD  POWER  CO"


def test_parse_index_page_sic_and_primary():
    sic, primary = scan.parse_index_page(INDEX_HTM, 1000001)
    assert sic == 3674  # the filer's own block, not the co-registrant listed first
    assert primary == "https://www.sec.gov/Archives/edgar/data/1000001/000100000124000001/acme-10k.htm"
    assert scan.parse_index_page(INDEX_HTM, 999999)[0] == 6770


def test_parse_index_page_fallback_largest_non_exhibit():
    page = INDEX_HTM.replace("<td>10-K</td><td>1503780", "<td>10-K/A</td><td>1503780").replace(
        '<tr><td>1</td><td>10-K</td>', '<tr><td>1</td><td>main</td>')
    page = page.replace("</table>", '<tr><td>4</td><td>x</td><td><a href="/Archives/small.htm">small.htm</a></td>'
                                    "<td>GRAPHIC-ISH</td><td>10</td></tr></table>")
    _, primary = scan.parse_index_page(page, 1000001)
    assert primary.endswith("acme-10k.htm")  # exhibits, XML and the full submission .txt are excluded


def test_in_group():
    assert all(scan.in_group(s) for s in (3570, 3579, 3674, 3812, 3585, 7372, 6798, 4911, 1094, "3674"))
    assert not any(scan.in_group(s) for s in (3580, 3811, 6770, 4900, None, "", "abc"))


def test_counting_word_boundaries_case_and_whitespace():
    h = scan.count_hits("GPUs and a gpu. Data  centers\nand a data\ncenter; DataCenter; hyperscalers")
    assert h["compute"] == 6  # GPUs, gpu, data centers, data center, DataCenter, hyperscalers -> each once
    assert scan.count_hits("GPUs")["compute"] == 1 and scan.count_hits("GPUsX")["compute"] == 0
    assert scan.count_hits("our datacentre")["compute"] == 0
    assert scan.count_hits("Generative AI and machine-learning")["ai"] == 1


def test_counting_gpus_not_double_counted():
    pats = dict(zip(scan.DICT["compute"], scan.PATTERNS["compute"]))
    assert len(pats["GPU"].findall("GPUs")) == 0 and len(pats["GPUs"].findall("GPUs")) == 1
    assert len(pats["data center"].findall("data centers")) == 0
    assert len(pats["data centers"].findall("data centers")) == 1


def test_html_stripping_entities_and_ixbrl_header():
    raw = ("<html><head><title>data center</title></head><body><ix:header><ix:hidden>liquid cooling</ix:hidden>"
           "</ix:header><p>Our <b>data</b>&nbsp;center &amp; liquid<br/>cooling</p><script>GPU GPU</script></body></html>")
    text = scan.html_to_text(raw)
    assert "<" not in text and "&nbsp;" not in text
    h = scan.count_hits(text)
    assert h["compute"] == 1 and h["cooling"] == 1


def test_eligibility_thresholds_utility_vs_other():
    words = " ".join(["filler"] * 9990)
    five = words + " data center" * 5  # ~5 hits per 10k words
    assert scan.score_text(five, 3674)["eligible_text"] == 1
    assert scan.score_text(five, 4911)["eligible_text"] == 0  # utilities need the power group
    power = words + " colocation" * 2
    assert scan.score_text(power, 4911)["eligible_text"] == 1
    assert scan.score_text(power, 3674)["eligible_text"] == 0
    r = scan.score_text(power, 4911)
    assert r["hits_power"] == 2 and r["per10k_power"] == pytest.approx(2.0, abs=0.01)


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.content, self.status_code = text, text.encode(), status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)
        return self


def test_score_is_resumable_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "DATA", tmp_path)
    cands = [{"cik": "1000001", "company": "ACME", "form": "10-K", "filed": "2024-02-06",
              "path": "x", "acc": "0001000001-24-000001"},
             {"cik": "1000002", "company": "OTHER", "form": "10-K", "filed": "2023-02-06",
              "path": "y", "acc": "0001000002-23-000001"}]
    scan.write_csv(tmp_path / "candidates.csv", cands, list(cands[0].keys()))
    scan.write_csv(tmp_path / "companies.csv", [{"cik": "1000001", "sic": "3674"}, {"cik": "1000002", "sic": "6770"}],
                   ["cik", "sic"])
    calls = []

    def fake(url):
        calls.append(url)
        if url.endswith("-index.htm"):
            return _Resp(INDEX_HTM if "1000001" in url else INDEX_HTM.replace("SIC=3674", "SIC=6770"))
        return _Resp("<p>We sell GPUs to data centers.</p>")

    monkeypatch.setattr(scan, "fetch", fake)
    scan.step_score()
    n = len(calls)
    scan.step_score()
    assert len(calls) == n  # second run does no work
    rows = list(csv.DictReader(open(tmp_path / "scores.csv")))
    assert [r["acc"] for r in rows] == ["0001000002-23-000001", "0001000001-24-000001"]  # oldest first
    assert rows[0]["in_group"] == "0" and rows[0]["words"] == ""
    assert rows[1]["in_group"] == "1" and rows[1]["hits_compute"] == "2" and rows[1]["eligible_text"] == "1"
    assert n == 3  # out-of-group filing skips the document download


def test_dictionary_frozen_and_research_budget():
    d = json.loads((ROOT / "research/pit_universe/dictionary.json").read_text())
    assert "FROZEN" in d["_note"] and set(scan.GROUPS) <= set(d)
    n = sum(1 for p in (ROOT / "research").rglob("*.py")
            for line in p.read_text().splitlines() if line.strip() and not line.strip().startswith("#"))
    assert n <= 450


def test_app_never_imports_research():
    for p in (ROOT / "finres").rglob("*.py"):
        assert "research" not in "\n".join(l for l in p.read_text().splitlines() if "import" in l), p


def test_symbols_from_cover_page_and_item5():
    from research.pit_universe import scan
    cover = ("Title of each class Trading Symbol(s) Name of each exchange on which registered "
             "Common Stock, $0.001 par value per share NVDA The Nasdaq Global Select Market")
    assert scan.symbols(cover) == "NVDA"
    old = "Our common stock is traded on the Nasdaq Global Select Market under the symbol “XLNX”. A holder"
    assert scan.symbols(old) == "XLNX"
    assert scan.symbols("Class A trades under the symbols GOOGL and GOOG on NASDAQ") .startswith("GOOGL")
    assert scan.symbols("no symbols here") == ""
