"""Independent recomputation (no finres.signals) vs the app's state, for docs/proof."""
import sqlite3, json
import yfinance as yf, pandas as pd
from datetime import date
from finres import db, state, edgar
conn = db.connect(__import__("os").environ.get("FINRES_DB", "data/finres.db"))
fr = state.frame(conn, None)
sc, t_app = fr["scored"], fr["t"]
rows = []
for t in ["NVDA", "VST", "TSM"]:
    h = yf.Ticker(t).history(period="2y", auto_adjust=True)["Close"].dropna()
    h.index = h.index.tz_localize(None).normalize()
    h = h[h.index <= t_app]  # same as-of date as the app
    info = yf.Ticker(t).info
    c = h.iloc[-1]; r121 = h.iloc[-22] / h.iloc[-253] - 1; sma200 = h.iloc[-200:].mean()
    app = sc.loc[t]
    f = edgar.companyfacts(t, max_age_days=36500); fu = edgar.fundamentals(f, date.today()) if f else {}
    gm_edgar = (fu.get("gross_profit_ttm") or 0) / fu["revenue_ttm"] if fu and fu.get("revenue_ttm") and fu.get("gross_profit_ttm") else None
    rows.append({"ticker": t, "date": str(h.index[-1].date()),
        "close_indep": round(float(c), 2), "close_app": round(float(app["close"]), 2),
        "ret12_1_indep": round(float(r121), 4), "ret12_1_app": round(float(app["ret_12_1"]), 4),
        "sma200_indep": round(float(sma200), 2), "sma200_app": round(float(app["sma200"]), 2),
        "sma200_yahoo_info": info.get("twoHundredDayAverage"),
        "gross_margin_edgar": None if gm_edgar is None else round(gm_edgar, 4), "gross_margin_yahoo": info.get("grossMargins"),
        "rev_growth_edgar": fu.get("revenue_growth") and round(fu["revenue_growth"], 4), "rev_growth_yahoo_quarterly": info.get("revenueGrowth")})
print(json.dumps(rows, indent=1))
json.dump(rows, open("docs/proof/data_check.json", "w"), indent=1)
