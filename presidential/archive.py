"""Local source-document archive.

Every citation in the corpus must resolve to a document here. A document is a
directory under archive/docs/<doc_id>/ containing:
  meta.yaml  - provenance and citation metadata
  text.txt   - normalized plain text (what quotes are verified against)
  raw.html   - original bytes, if fetched (gitignored)
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import ARCHIVE


@dataclass
class DocMeta:
    id: str
    source: str            # app | wayback | manual | govinfo | ...
    kind: str              # document_kind enum
    title: str
    date: str | None       # YYYY-MM-DD or YYYY-MM or YYYY
    author: str | None = None      # person id, party id, or free text
    url: str | None = None
    citation: str | None = None    # formatted citation for non-web sources
    fetched_at: str | None = None
    sha256: str | None = None
    notes: str | None = None
    extra: dict = field(default_factory=dict)


def normalize_text(s: str) -> str:
    """Canonical form used for both storage and quote matching.

    Curly quotes/dashes -> ASCII, NFKC, collapse runs of whitespace to one
    space, but preserve paragraph breaks as a single newline.
    """
    s = unicodedata.normalize("NFKC", s)
    s = (s.replace("‘", "'").replace("’", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "--")
          .replace(" ", " "))
    paras = re.split(r"\n\s*\n|\r\n\s*\r\n", s)
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    return "\n".join(p for p in paras if p)


def match_key(s: str) -> str:
    """Looser key for quote matching: lowercase, drop all non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def doc_dir(doc_id: str) -> Path:
    return ARCHIVE / doc_id


def exists(doc_id: str) -> bool:
    return (doc_dir(doc_id) / "text.txt").exists()


def load_meta(doc_id: str) -> DocMeta:
    d = yaml.safe_load((doc_dir(doc_id) / "meta.yaml").read_text())
    return DocMeta(**d)


def load_text(doc_id: str) -> str:
    return (doc_dir(doc_id) / "text.txt").read_text()


def save(meta: DocMeta, text: str, raw: bytes | None = None) -> Path:
    d = doc_dir(meta.id)
    d.mkdir(parents=True, exist_ok=True)
    if meta.kind == "dataset":
        # Tabular text: keep one record per line; only NFKC-normalize and trim.
        text = "\n".join(unicodedata.normalize("NFKC", ln).rstrip() for ln in text.splitlines()).strip() + "\n"
    else:
        text = normalize_text(text)
    meta.sha256 = hashlib.sha256(text.encode()).hexdigest()
    if meta.fetched_at is None:
        meta.fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (d / "text.txt").write_text(text)
    (d / "meta.yaml").write_text(yaml.safe_dump(asdict(meta), sort_keys=False, allow_unicode=True))
    if raw is not None:
        (d / "raw.html").write_bytes(raw)
    return d


def all_ids() -> list[str]:
    if not ARCHIVE.exists():
        return []
    return sorted(p.name for p in ARCHIVE.iterdir() if (p / "meta.yaml").exists())


def find_quote(doc_id: str, quote: str) -> tuple[bool, str]:
    """Verify a quote appears in the document.

    A quote may contain ' ... ' to elide text; each segment must appear in
    order. Matching is whitespace/punctuation/case-insensitive but requires
    the exact word sequence. Returns (ok, reason).
    """
    if not exists(doc_id):
        return False, f"document {doc_id!r} not in archive"
    hay = match_key(load_text(doc_id))
    segments = [s for s in re.split(r"\s*\.\.\.\s*|\s*…\s*", quote) if s.strip()]
    if not segments:
        return False, "empty quote"
    pos = 0
    for seg in segments:
        key = match_key(seg)
        if len(key) < 12:
            return False, f"segment too short to verify unambiguously: {seg!r}"
        i = hay.find(key, pos)
        if i < 0:
            return False, f"segment not found in {doc_id}: {seg[:80]!r}"
        pos = i + len(key)
    return True, "ok"
