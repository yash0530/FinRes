"""Point-in-time EDGAR fact selection on synthetic companyfacts (offline)."""
from datetime import date

from finres import edgar


def item(start, end, val, filed, fy=None, fp=None, form="10-K"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "fy": fy, "fp": fp}
    if start:
        d["start"] = start
    return d


def facts(usgaap: dict, dei: dict | None = None) -> dict:
    """{concept: [items]} -> companyfacts-shaped dict (unit picked from concept)."""
    def wrap(concepts, unit_for):
        return {c: {"units": {unit_for(c): its}} for c, its in concepts.items()}
    out = {"facts": {"us-gaap": wrap(usgaap, lambda c: "shares" if "Shares" in c else "USD")}}
    if dei:
        out["facts"]["dei"] = wrap(dei, lambda c: "shares")
    return out


REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


# 1. strict filed < asof
def test_filed_on_or_after_asof_is_ignored():
    f = facts({REV: [item("2023-01-01", "2023-12-31", 100, "2024-02-15")]})
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 2, 10)) is None
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 2, 15)) is None
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 2, 16)) == (100, date(2023, 12, 31))


# 2. amendments
def test_amendment_latest_filed_before_asof_wins():
    f = facts({REV: [item("2023-01-01", "2023-12-31", 100, "2024-02-15"),
                     item("2023-01-01", "2023-12-31", 90, "2024-05-01", form="10-K/A")]})
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 4, 1))[0] == 100
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 5, 1))[0] == 100
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 5, 2))[0] == 90


# 3. TTM = FY + YTD - prior YTD
def _ttm_facts(fy="2024", fp="FY"):
    return facts({REV: [
        item("2023-01-01", "2023-12-31", 1000, "2024-02-15", fy, fp),
        item("2023-01-01", "2023-09-30", 700, "2023-11-01", fy, fp, "10-Q"),
        item("2023-07-01", "2023-09-30", 250, "2023-11-01", fy, fp, "10-Q"),
        item("2024-01-01", "2024-09-30", 900, "2024-11-01", fy, fp, "10-Q"),
        item("2024-07-01", "2024-09-30", 350, "2024-11-01", fy, fp, "10-Q"),  # standalone Q3: not a YTD
    ]})


def test_ttm_from_nine_month_ytd():
    assert edgar.ttm(_ttm_facts(), edgar.REVENUE, date(2024, 12, 1)) == (1000 + 900 - 700, date(2024, 9, 30))
    # before the 10-Q is known -> falls back to the fiscal year
    assert edgar.ttm(_ttm_facts(), edgar.REVENUE, date(2024, 10, 1)) == (1000, date(2023, 12, 31))


def test_ttm_falls_back_to_fy_without_prior_ytd():
    f = facts({REV: [item("2023-01-01", "2023-12-31", 1000, "2024-02-15"),
                     item("2024-01-01", "2024-03-31", 300, "2024-05-01", form="10-Q")]})
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 6, 1)) == (1000, date(2023, 12, 31))


# 4. 52/53-week years
def test_52_53_week_years_are_annual():
    assert edgar._classify(date(2022, 1, 31), date(2023, 1, 29)) == "annual"   # 363 days
    assert edgar._classify(date(2023, 1, 30), date(2024, 1, 28)) == "annual"   # 363 days
    assert edgar._classify(date(2023, 1, 1), date(2023, 12, 31)) == "annual"   # 364 days
    assert edgar._classify(date(2023, 1, 1), date(2024, 1, 7)) == "annual"     # 371 days
    f = facts({REV: [item("2023-01-01", "2024-01-07", 530, "2024-03-01")]})
    assert edgar.ttm(f, edgar.REVENUE, date(2024, 4, 1)) == (530, date(2024, 1, 7))
    assert edgar._classify(date(2023, 1, 1), date(2023, 4, 1)) == "quarter"
    assert edgar._classify(date(2023, 1, 1), date(2023, 6, 1)) is None


# 5. fy/fp are not period identity
def test_misleading_fy_fp_do_not_matter():
    asof = date(2024, 12, 1)
    base = edgar.ttm(_ttm_facts(), edgar.REVENUE, asof)
    assert edgar.ttm(_ttm_facts(fy=1999, fp="Q1"), edgar.REVENUE, asof) == base
    assert edgar.fundamentals(_ttm_facts(fy=1999, fp="Q1"), asof) == edgar.fundamentals(_ttm_facts(), asof)


# 6. concept fallback across the ASC 606 switch
def test_revenue_concept_fallback():
    f = facts({
        "SalesRevenueNet": [item("2016-01-01", "2016-12-31", 80, "2017-02-15"),
                            item("2017-01-01", "2017-12-31", 100, "2018-02-15")],
        REV: [item("2018-01-01", "2018-12-31", 130, "2019-02-15")],
    })
    assert edgar.ttm(f, edgar.REVENUE, date(2018, 6, 1)) == (100, date(2017, 12, 31))
    assert edgar.ttm(f, edgar.REVENUE, date(2019, 6, 1)) == (130, date(2018, 12, 31))
    g = edgar.fundamentals(f, date(2019, 6, 1))
    assert g["revenue_ttm_prev"] == 100 and abs(g["revenue_growth"] - 0.30) < 1e-12


def test_preferred_concept_filed_later_does_not_hide_earlier_fallback():
    # The 2018 10-K restates FY2017 under the ASC 606 tag; in mid-2018 only SalesRevenueNet was known.
    f = facts({
        "SalesRevenueNet": [item("2017-01-01", "2017-12-31", 100, "2018-02-15")],
        REV: [item("2017-01-01", "2017-12-31", 101, "2019-02-15"),
              item("2018-01-01", "2018-12-31", 130, "2019-02-15")],
    })
    assert edgar.ttm(f, edgar.REVENUE, date(2018, 6, 1))[0] == 100
    assert edgar.fundamentals(f, date(2019, 6, 1))["revenue_ttm_prev"] == 101  # preferred concept wins


# 7. fundamentals()
def test_fundamentals_ratios():
    f = facts({
        REV: [item("2022-01-01", "2022-12-31", 800, "2023-02-15"),
              item("2023-01-01", "2023-12-31", 1000, "2024-02-15")],
        "GrossProfit": [item("2023-01-01", "2023-12-31", 600, "2024-02-15")],
        "Assets": [item(None, "2023-12-31", 2000, "2024-02-15")],
        "NetIncomeLoss": [item("2023-01-01", "2023-12-31", 150, "2024-02-15")],
    }, dei={"EntityCommonStockSharesOutstanding": [item(None, "2024-02-01", 50, "2024-02-15")]})
    g = edgar.fundamentals(f, date(2024, 3, 1))
    assert g["revenue_ttm"] == 1000 and g["revenue_ttm_prev"] == 800
    assert abs(g["revenue_growth"] - 0.25) < 1e-12
    assert g["gross_profit_ttm"] == 600 and g["assets"] == 2000 and g["gp_assets"] == 0.3
    assert g["net_income_ttm"] == 150 and g["shares"] == 50
    assert g["fund_end"] == "2023-12-31" and g["pit"] is True


def test_fundamentals_gross_from_cost_and_missing_concepts():
    f = facts({REV: [item("2023-01-01", "2023-12-31", 1000, "2024-02-15")],
               "CostOfRevenue": [item("2023-01-01", "2023-12-31", 700, "2024-02-15")]})
    g = edgar.fundamentals(f, date(2024, 3, 1))
    assert g["gross_profit_ttm"] == 300
    assert g["assets"] is None and g["gp_assets"] is None and g["revenue_growth"] is None
    assert g["net_income_ttm"] is None and g["shares"] is None
    empty = edgar.fundamentals({"facts": {}}, date(2024, 3, 1))
    assert all(v is None for k, v in empty.items() if k != "pit")
    assert edgar.fundamentals(None, date(2024, 3, 1))["revenue_ttm"] is None


def test_shares_fallback_to_us_gaap():
    f = facts({"CommonStockSharesOutstanding": [item(None, "2023-12-31", 42, "2024-02-15")]})
    assert edgar.fundamentals(f, date(2024, 3, 1))["shares"] == 42


def test_stale_values_are_dropped():
    """A company that stopped using a tag must not feed years-old values into today's factors."""
    f = facts({
        "SalesRevenueNet": [item("2016-01-01", "2016-12-31", 500, "2017-02-15")],
        "Assets": [item(None, "2016-12-31", 900, "2017-02-15")],
    })
    assert edgar.fundamentals(f, date(2017, 6, 1))["revenue_ttm"] == 500
    g = edgar.fundamentals(f, date(2019, 6, 1))  # 2.4 years after period end
    assert g["revenue_ttm"] is None and g["assets"] is None and g["gp_assets"] is None
