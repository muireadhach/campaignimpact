"""Statistical indicator series, archived like any other source.

Each fetch stores the publisher's raw file as an archive document (kind
'dataset', id 'data-<source>-<series>') and writes data/indicators/<id>.yaml
holding annualized values plus the doc reference and the reduction method.
The build refuses indicator YAML whose doc is missing, so a chart can always
be traced to the file it was computed from.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
from collections import defaultdict

import httpx
import yaml

from .. import DATA, archive

INDICATORS = DATA / "indicators"
# No custom User-Agent: fred.stlouisfed.org silently stalls requests carrying a non-browser/non-curl UA.
_client = httpx.Client(follow_redirects=True, timeout=120)


def _write_indicator(iid: str, *, name: str, unit: str, category: str, doc: str, series: str,
                     method: str, values: dict[int, float], notes: str | None = None, source_name: str = "") -> None:
    INDICATORS.mkdir(exist_ok=True)
    d = {"id": iid, "name": name, "unit": unit, "category": category, "source_name": source_name,
         "doc": doc, "series": series, "method": method}
    if notes:
        d["notes"] = notes
    d["values"] = {int(y): round(float(v), 4) for y, v in sorted(values.items())}
    (INDICATORS / f"{iid}.yaml").write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))


def fred(series: str, iid: str, *, name: str, unit: str, category: str, reduce: str = "mean",
         scale: float = 1.0, notes: str | None = None, force: bool = False) -> str:
    """Fetch a FRED series (keyless CSV export) and annualize it. reduce: mean | last | sum."""
    doc = f"data-fred-{series.lower()}"
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    if not archive.exists(doc) or force:
        r = _client.get(url); r.raise_for_status()
        meta = archive.DocMeta(id=doc, source="fred", kind="dataset", title=f"FRED series {series}", date=None,
                               author="Federal Reserve Bank of St. Louis (FRED)", url=url,
                               citation=f"Federal Reserve Bank of St. Louis, FRED series {series}, {url} (retrieved {time.strftime('%Y-%m-%d')})")
        archive.save(meta, r.text, raw=r.content)
        time.sleep(0.5)
    by_year: dict[int, list[float]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(archive.load_text(doc))):
        v = row.get(series, "")
        if v in ("", ".", None):
            continue
        by_year[int(row["observation_date"][:4])].append(float(v) * scale)
    fn = {"mean": statistics.fmean, "last": lambda xs: xs[-1], "sum": sum}[reduce]
    values = {y: fn(xs) for y, xs in by_year.items()}
    _write_indicator(iid, name=name, unit=unit, category=category, doc=doc, series=series,
                     method=f"annual {reduce} of published observations", values=values, notes=notes,
                     source_name="FRED (St. Louis Fed)")
    return iid


def treasury_debt(iid: str = "federal-debt", *, force: bool = False) -> str:
    """Total public debt outstanding at fiscal year end, 1790-present (TreasuryDirect via fiscaldata)."""
    doc = "data-treasury-debt-outstanding"
    url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_outstanding"
    if not archive.exists(doc) or force:
        rows, page = [], 1
        while True:
            r = _client.get(url, params={"page[size]": 500, "page[number]": page, "sort": "record_date"})
            r.raise_for_status(); j = r.json(); rows += j["data"]
            if page >= j["meta"]["total-pages"]:
                break
            page += 1
        text = "record_date,debt_outstanding_amt\n" + "\n".join(f"{x['record_date']},{x['debt_outstanding_amt']}" for x in rows)
        meta = archive.DocMeta(id=doc, source="treasury", kind="dataset", title="Historical Debt Outstanding (fiscal year end)", date=None,
                               author="U.S. Department of the Treasury, Fiscal Data", url=url,
                               citation=f"U.S. Treasury Fiscal Data, Historical Debt Outstanding, {url} (retrieved {time.strftime('%Y-%m-%d')})")
        archive.save(meta, text, raw=json.dumps(rows).encode())
    values = {}
    for row in csv.DictReader(io.StringIO(archive.load_text(doc))):
        values[int(row["record_date"][:4])] = float(row["debt_outstanding_amt"]) / 1e9
    _write_indicator(iid, name="Federal debt outstanding", unit="billions of dollars (nominal)", category="fiscal",
                     doc=doc, series="debt_outstanding_amt", method="value at fiscal year end", values=values,
                     source_name="U.S. Treasury Fiscal Data",
                     notes="Fiscal years ended June 30 through 1976, September 30 thereafter. Nominal dollars; see federal-debt-pct-gdp for the ratio.")
    return iid


def nces_hs_graduates(iid: str = "hs-graduation-rate", *, force: bool = False) -> str:
    """High school graduates as a percent of 17-year-olds (NCES Digest table 219.10)."""
    import xlrd
    doc = "data-nces-tabn219-10"
    url = "https://nces.ed.gov/programs/digest/d22/tables/xls/tabn219.10.xls"
    if not archive.exists(doc) or force:
        r = _client.get(url); r.raise_for_status()
        b = xlrd.open_workbook(file_contents=r.content); s = b.sheet_by_index(0)
        lines = ["\t".join(str(s.cell_value(ri, ci)) for ci in range(s.ncols)) for ri in range(s.nrows)]
        meta = archive.DocMeta(id=doc, source="nces", kind="dataset", title="NCES Digest of Education Statistics, Table 219.10: High school graduates, by sex and control of school", date="2022",
                               author="National Center for Education Statistics", url=url,
                               citation=f"NCES, Digest of Education Statistics 2022, Table 219.10, {url}")
        archive.save(meta, "\n".join(lines), raw=r.content)
    values = {}
    for line in archive.load_text(doc).split("\n"):
        cells = line.split("\t")
        if len(cells) < 15:
            continue
        yr = cells[0].strip()
        m = yr[:4]
        if not (m.isdigit() and yr[4:5] == "-"):
            continue
        try:
            pct = float(cells[14])  # last column: 'Total graduates as a ratio of 17-year-old population'
        except ValueError:
            continue
        values[int(m) + 1] = pct
    _write_indicator(iid, name="High school graduates as percent of 17-year-olds", unit="percent", category="education",
                     doc=doc, series="Graduates as a ratio of 17-year-old population", method="school year ending in the given year; published years only",
                     values=values, source_name="NCES Digest of Education Statistics")
    return iid


def load_all() -> dict[str, dict]:
    out = {}
    if INDICATORS.exists():
        for p in sorted(INDICATORS.glob("*.yaml")):
            out[p.stem] = yaml.safe_load(p.read_text())
    return out


def fetch_default_set(force: bool = False) -> list[str]:
    done = []
    done.append(fred("M0892AUSM156SNBR", "unemployment-rate-1929-42", name="Unemployment rate (NBER series, 1929–1942)", unit="percent", category="labor",
                     notes="NBER Macrohistory series M0892; monthly, averaged to annual. Use with unemployment-rate (BLS, 1948–).", force=force))
    done.append(fred("UNRATE", "unemployment-rate", name="Unemployment rate (BLS, 1948–)", unit="percent", category="labor", force=force))
    done.append(fred("GDPA", "gdp-nominal", name="Gross domestic product (nominal, annual)", unit="billions of dollars", category="economy", force=force))
    done.append(fred("CPIAUCNS", "cpi", name="Consumer price index (all urban consumers, 1982–84=100)", unit="index", category="prices", force=force))
    done.append(fred("FYFSD", "federal-surplus-deficit", name="Federal surplus or deficit (fiscal year)", unit="millions of dollars", category="fiscal", reduce="last", force=force))
    done.append(fred("FYONGDA188S", "federal-outlays-pct-gdp", name="Federal outlays as percent of GDP", unit="percent of GDP", category="fiscal", reduce="last", force=force))
    done.append(fred("TOTALSL", "consumer-credit", name="Consumer credit outstanding (1943–)", unit="billions of dollars", category="household", reduce="last", scale=1e-3,
                     notes="Proxy for household debt; earlier decades are not in FRED. Monthly, year-end value.", force=force))
    # Narrower series for per-mechanism links (data/cycles/*/followthrough/*.yaml `indicators:`)
    done.append(fred("X14AICBS", "bank-suspensions", name="Bank suspensions (all incorporated commercial banks)", unit="banks per year", category="banking", force=force,
                     notes="Fed Banking and Monetary Statistics 1914-1941; annual count. Ends 1941."))
    done.append(fred("M0460AUSM319NNBR", "farm-prices-received", name="Index of prices received by farmers (1910–14=100)", unit="index", category="agriculture", force=force,
                     notes="NBER Macrohistory M0460A; monthly, averaged to annual. 1910–1967."))
    done.append(fred("A2001C1A027NBEA", "farm-cash-receipts", name="Cash receipts from farm marketings", unit="billions of dollars", category="agriculture", force=force,
                     notes="BEA farm output, annual, 1929–."))
    done.append(fred("INDPRO", "industrial-production", name="Industrial production index (2017=100)", unit="index", category="industry", force=force,
                     notes="Federal Reserve G.17; monthly, averaged to annual. 1919–."))
    done.append(fred("PPIACO", "ppi-all-commodities", name="Producer (wholesale) price index, all commodities (1982=100)", unit="index", category="prices", force=force,
                     notes="BLS; monthly, averaged to annual. 1913–. The 1930s 'wholesale price' target of reflation policy."))
    done.append(fred("M09075USM476NNBR", "nonfarm-foreclosures", name="Nonfarm real estate foreclosures", unit="foreclosures per year", category="household", reduce="sum", force=force,
                     notes="NBER Macrohistory M09075; monthly counts summed to annual. 1934–1963 only, so it shows the HOLC era but not the pre-1934 peak."))
    done.append(fred("M1125AUSM343NNBR", "stock-prices", name="Index of all common stock prices (Cowles / S&P)", unit="index", category="finance", force=force,
                     notes="NBER Macrohistory M1125A; monthly, averaged to annual. 1871–1956."))
    done.append(fred("M1476BUSM027NNBR", "gold-stock", name="Monetary gold stock", unit="billions of dollars", category="monetary", reduce="last", force=force,
                     notes="NBER Macrohistory M1476B; December value. 1914–1946. Jumps in 1934 reflect the dollar's revaluation to $35/oz."))
    done.append(fred("M0829AUSM065NNBR", "mfg-weekly-hours", name="Average hours worked per week, manufacturing", unit="hours", category="labor", force=force,
                     notes="NBER Macrohistory M0829A; monthly, averaged to annual. 1920–1948."))
    done.append(fred("M08142USM055NNBR", "mfg-hourly-earnings", name="Average hourly earnings, 25 manufacturing industries", unit="dollars per hour (nominal)", category="labor", force=force,
                     notes="NBER Macrohistory M08142; monthly, averaged to annual. 1920–1948."))
    done.append(fred("M02108USM324NNBR", "construction-contracts", name="Index of construction contracts awarded (cost-adjusted)", unit="index", category="infrastructure", force=force,
                     notes="NBER Macrohistory M02108 (Engineering News-Record); monthly, averaged to annual. 1914–1943. Public works show up here."))
    done.append(treasury_debt(force=force))
    done.append(nces_hs_graduates(force=force))
    return done
