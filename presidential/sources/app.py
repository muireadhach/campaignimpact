"""Fetcher for The American Presidency Project (presidency.ucsb.edu).

Documents are addressed by URL slug: https://www.presidency.ucsb.edu/documents/<slug>
Archived as doc_id 'app-<slug>'.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .. import archive

BASE = "https://www.presidency.ucsb.edu"
HEADERS = {"User-Agent": "presidential-research-tool/0.1 (personal research; contact via github)"}
_client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)


def doc_id_for(slug: str) -> str:
    return f"app-{slug}"


def _parse_date(s: str) -> str | None:
    s = s.strip()
    for fmt in ("%B %d, %Y", "%B %Y", "%Y"):
        try:
            d = datetime.strptime(s, fmt)
            return d.strftime("%Y-%m-%d" if fmt == "%B %d, %Y" else "%Y-%m" if fmt == "%B %Y" else "%Y")
        except ValueError:
            pass
    return None


def _guess_kind(title: str, person: str) -> str:
    t = title.lower()
    if re.search(r"party platform", t):
        return "platform"
    if "radio" in t or "fireside" in t:
        return "radio_address"
    if "debate" in t:
        return "debate"
    if "press conference" in t or "news conference" in t:
        return "press_conference"
    if "message to the congress" in t or "message the congress" in t or "state of the union" in t:
        return "message_to_congress"
    if "executive order" in t or "proclamation" in t:
        return "executive_order"
    if t.startswith("letter") or t.startswith("telegram"):
        return "letter"
    if t.startswith("statement") or "statement" in t:
        return "statement"
    if any(w in t for w in ("address", "remarks", "speech")):
        return "speech"
    return "other"


def fetch(slug: str, *, force: bool = False, kind: str | None = None, author: str | None = None,
          sleep: float = 1.0) -> str:
    """Fetch one APP document into the archive. Returns doc_id."""
    doc_id = doc_id_for(slug)
    if archive.exists(doc_id) and not force:
        return doc_id
    url = f"{BASE}/documents/{slug}"
    r = _client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "lxml")
    h1 = soup.select_one("h1")
    title = h1.get_text(" ", strip=True) if h1 else slug
    person_el = soup.select_one(".field-title")
    person = person_el.get_text(" ", strip=True) if person_el else ""
    date_el = soup.select_one(".date-display-single")
    date = _parse_date(date_el.get_text(strip=True)) if date_el else None
    body = soup.select_one(".field-docs-content")
    if body is None:
        raise RuntimeError(f"no .field-docs-content on {url}")
    # Preserve paragraph structure: one paragraph per block-level element.
    parts = []
    for el in body.find_all(["p", "h2", "h3", "h4", "li", "blockquote", "pre"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)
    text = "\n\n".join(parts) if parts else body.get_text("\n\n", strip=True)
    meta = archive.DocMeta(
        id=doc_id, source="app", kind=kind or _guess_kind(title, person), title=title, date=date,
        author=author or person or None, url=url,
        citation=f"{person + ', ' if person else ''}\"{title},\" {date or 'n.d.'}. The American Presidency Project, {url}",
    )
    archive.save(meta, text, raw=r.content)
    time.sleep(sleep)
    return doc_id


def fetch_many(slugs: list[str], **kw) -> list[str]:
    return [fetch(s, **kw) for s in slugs]


def search(*, from_date: str, to_date: str, person: str = "", keywords: str = "", per_page: int = 100,
           sleep: float = 1.0) -> list[dict]:
    """List APP documents in a date range (MM-DD-YYYY). Returns [{slug, date, person, title}]."""
    out, page = [], 0
    while True:
        r = _client.get(f"{BASE}/advanced-search", params={
            "field-keywords": keywords, "field-keywords2": "", "field-keywords3": "",
            "from[date]": from_date, "to[date]": to_date, "person2": person,
            "items_per_page": per_page, "page": page})
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "lxml")
        rows = soup.select("table tbody tr")
        if not rows:
            break
        for tr in rows:
            tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
            a = tr.select_one('a[href^="/documents/"]')
            if a and len(tds) >= 3:
                out.append({"slug": a["href"].split("/documents/")[-1], "date": _parse_date(tds[0]) or tds[0],
                            "person": tds[1], "title": tds[2]})
        if not soup.select_one(".pager-next a, li.next a"):
            break
        page += 1
        time.sleep(sleep)
    return out
