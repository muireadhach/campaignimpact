"""Generic web-page fetcher: archive an arbitrary URL with explicit citation metadata.

Use for sources without a dedicated fetcher (Miller Center essays, FDR Library,
Wikipedia, marxists.org, etc.). The caller supplies kind/author/date/citation
because generic pages rarely expose them reliably.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .. import archive

HEADERS = {"User-Agent": "presidential-research-tool/0.1 (personal research)"}
_client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=60)

# Per-site content selectors, tried in order; falls back to <article>, <main>, <body>.
SITE_SELECTORS = {
    "millercenter.org": ["article .field--name-body", "article"],
    "en.wikipedia.org": ["#mw-content-text .mw-parser-output"],
    "www.marxists.org": ["body"],
    "fdrlibrary.org": ["article", "main"],
    "www.fdrlibrary.org": ["article", "main"],
}
STRIP = ["script", "style", "nav", "header", "footer", "aside", ".breadcrumb", ".toc", ".mw-editsection",
         ".reference", ".reflist", ".navbox", "sup.reference", ".sidebar", ".infobox", "table.metadata"]


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:80]


def extract(html: bytes, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else url
    host = urlparse(url).netloc
    for sel in STRIP:
        for el in soup.select(sel):
            el.decompose()
    node = None
    for sel in SITE_SELECTORS.get(host, []) + ["article", "main", "body"]:
        node = soup.select_one(sel)
        if node:
            break
    parts = []
    for el in node.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "dd", "dt"]):
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) > 1:
            parts.append(txt)
    text = "\n\n".join(parts) if parts else node.get_text("\n\n", strip=True)
    return title, text


def extract_pdf(data: bytes, url: str) -> tuple[str, str]:
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    title = (reader.metadata or {}).get("/Title") or url.rsplit("/", 1)[-1]
    return str(title), "\n\n".join(pages)


def fetch(url: str, *, doc_id: str, kind: str, author: str | None = None, date: str | None = None,
          title: str | None = None, citation: str | None = None, notes: str | None = None,
          force: bool = False, sleep: float = 1.0) -> str:
    if archive.exists(doc_id) and not force:
        return doc_id
    r = _client.get(url)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        page_title, text = extract_pdf(r.content, url)
    else:
        page_title, text = extract(r.content, url)
    meta = archive.DocMeta(id=doc_id, source="web", kind=kind, title=title or page_title, date=date,
                           author=author, url=url, citation=citation, notes=notes)
    archive.save(meta, text, raw=r.content)
    time.sleep(sleep)
    return doc_id
