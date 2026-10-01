"""Internet Archive (archive.org) fetcher: archives an item's OCR text (_djvu.txt).

OCR text from scanned periodicals is noisy; quotes drawn from it should be
short and the position's evidence_quality should reflect OCR uncertainty.
"""
from __future__ import annotations

import time

import httpx

from .. import archive

_client = httpx.Client(follow_redirects=True, timeout=120,
                       headers={"User-Agent": "presidential-research-tool/0.1 (personal research)"})


def item_meta(identifier: str) -> dict:
    r = _client.get(f"https://archive.org/metadata/{identifier}")
    r.raise_for_status()
    return r.json()


def fetch(identifier: str, *, kind: str, author: str | None = None, date: str | None = None,
          title: str | None = None, citation: str | None = None, notes: str | None = None,
          force: bool = False, sleep: float = 1.0) -> str:
    doc_id = f"ia-{identifier.lower().replace('_', '-')}"
    if archive.exists(doc_id) and not force:
        return doc_id
    m = item_meta(identifier).get("metadata", {})
    txt_name = next((f["name"] for f in item_meta(identifier).get("files", []) if f["name"].endswith("_djvu.txt")), None)
    if not txt_name:
        raise RuntimeError(f"no _djvu.txt for {identifier}")
    r = _client.get(f"https://archive.org/download/{identifier}/{txt_name}")
    r.raise_for_status()
    url = f"https://archive.org/details/{identifier}"
    meta = archive.DocMeta(id=doc_id, source="ia", kind=kind, title=title or m.get("title", identifier),
                           date=date or (m.get("date") or "")[:10] or None, author=author or m.get("creator"),
                           url=url, citation=citation or f"{title or m.get('title', identifier)}. Internet Archive, {url} (OCR text).",
                           notes=notes, extra={"identifier": identifier})
    archive.save(meta, r.text)
    time.sleep(sleep)
    return doc_id
