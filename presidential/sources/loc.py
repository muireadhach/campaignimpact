"""Library of Congress Chronicling America fetcher (loc.gov JSON API).

search() finds newspaper pages by keyword/date; fetch_page() archives one
page's OCR text. Public-domain US newspapers through 1963 - the primary source
for the reception layer (editorials, coverage) in pre-polling eras.
"""
from __future__ import annotations

import re
import time

import httpx

from .. import archive

_client = httpx.Client(follow_redirects=True, timeout=120,
                       headers={"User-Agent": "presidential-research-tool/0.1 (personal research)"})


def search(query: str, *, date1: str, date2: str, count: int = 25, page: int = 1, sleep: float = 2.0) -> list[dict]:
    """Search pages. Dates are YYYY or YYYY-MM-DD. Returns [{url, date, paper, page, snippet}]."""
    r = _client.get("https://www.loc.gov/collections/chronicling-america/", params={
        "q": query, "dates": f"{date1}/{date2}", "fo": "json", "c": count, "sp": page})
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        out.append({
            "url": it["id"], "date": it.get("date"),
            "paper": (it.get("partof_title") or [""])[0],
            "page": (it.get("number_page") or [""])[0].lstrip("0"),
            "snippet": (it.get("description") or [""])[0][:300],
            "state": (it.get("location_state") or [""])[0] if it.get("location_state") else "",
        })
    time.sleep(sleep)
    return out


def _get_json(url: str, tries: int = 4) -> dict:
    """loc.gov intermittently drops connections mid-body; retry with backoff."""
    last = None
    for i in range(tries):
        try:
            r = _client.get(url)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"loc.gov fetch failed after {tries} tries: {url}") from last


def page_id(url: str) -> str:
    m = re.search(r"/resource/(sn\d+)/(\d{4}-\d{2}-\d{2})/ed-(\d+)/\?sp=(\d+)", url)
    if not m:
        raise ValueError(f"not a loc.gov page resource URL: {url}")
    lccn, date, ed, sp = m.groups()
    return f"loc-{lccn}-{date}-ed{ed}-p{sp}"


def fetch_page(url: str, *, kind: str = "news_article", title: str | None = None, notes: str | None = None,
               force: bool = False, sleep: float = 2.0) -> str:
    doc_id = page_id(url)
    if archive.exists(doc_id) and not force:
        return doc_id
    sep = "&" if "?" in url else "?"
    d = _get_json(f"{url}{sep}fo=json")
    item = d.get("item", {})
    ft_url = d.get("resource", {}).get("fulltext_file") or d.get("fulltext_service")
    if not ft_url:
        raise RuntimeError(f"no fulltext service for {url}")
    ft = _get_json(ft_url)
    text = next(iter(ft.values()))["full_text"]
    paper = (item.get("partof") or [{}])[0].get("title", "") if isinstance(item.get("partof"), list) else ""
    date = item.get("date")
    m = re.search(r"sp=(\d+)", url)
    pg = m.group(1) if m else "?"
    t = title or f"{item.get('title', paper)} (page {pg})"
    meta = archive.DocMeta(id=doc_id, source="loc", kind=kind, title=t, date=date, author=paper or None,
                           url=url.split("?")[0] + f"?sp={pg}",
                           citation=f"{item.get('title', paper)}, p. {pg}. Chronicling America, Library of Congress. {url} (OCR text).",
                           notes=notes)
    archive.save(meta, text)
    time.sleep(sleep)
    return doc_id
