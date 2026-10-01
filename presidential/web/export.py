"""Freeze the read-only web UI into a static site (`./pres export`).

Every route is a GET over the compiled database, so the whole UI can be crawled in-process and written
out as plain files. The parts that need a server — the filter forms and the search boxes — are handled in
the browser instead: the templates tag each row with its filter keys, and `static/site.js` reads the query
string and hides the rows that do not match. Full-text search over document *text* stays server-side
(run `./pres serve` locally for that); the published search covers metadata and position text.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from .. import DB_PATH

ATTR = re.compile(r'\b(href|action|src)="(/[^"#][^"]*|/)"')
DROP = {"hl"}                                    # applied client-side, so one page serves every quote link
SEEDS = ["/", "/positions", "/issues", "/reception", "/documents", "/timeline"]
LISTINGS = {"/positions", "/documents", "/reception"}
STATIC = Path(__file__).parent / "static"

Key = tuple[str, str]                            # (path, canonical query)


def key_of(url: str) -> Key:
    u = urlsplit(url)
    qs = sorted((k, v) for k, v in parse_qsl(u.query) if k not in DROP and v != "")
    return (u.path.rstrip("/") or "/"), urlencode(qs)


def _slug(query: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", query).strip("-").lower()
    return s if len(s) <= 80 else s[:71] + "-" + hashlib.sha1(query.encode()).hexdigest()[:8]


def dest(key: Key, plain: bool) -> str:
    """Site-relative file path for a crawled URL."""
    path, query = key
    parts = [p for p in path.split("/") if p]
    if plain:
        return "/".join(parts) + ".txt"
    if query:
        parts += ["f", _slug(query)]
    return "/".join(parts + ["index.html"])


def link(key: Key, plain: bool, base: str) -> str:
    d = dest(key, plain)
    if d.endswith("index.html"):
        d = d[: -len("index.html")]
    return (base.rstrip("/") + "/" + d) or "/"


def _rewrite(body: str, base: str, known: dict[Key, bool]) -> str:
    def one(m: re.Match) -> str:
        attr, url = m.group(1), m.group(2)
        k = key_of(url)
        if k not in known:
            return m.group(0)
        keep = [(a, b) for a, b in parse_qsl(urlsplit(url).query) if a in DROP]
        tgt = link(k, known[k], base) + ("?" + urlencode(keep) if keep else "")
        return f'{attr}="{tgt}"'
    return ATTR.sub(one, body)


def _inject(body: str, key: Key, base: str, note: str | None) -> str:
    path, query = key
    body = body.replace("<body>", f'<body data-page="{path.strip("/") or "home"}">', 1)
    if path in LISTINGS:                         # filter forms submit to the unfiltered page; JS does the rest
        body = body.replace('<form class="filters"', f'<form class="filters" action="{link((path, ""), False, base)}"', 1)
    if note:
        body = body.replace("<main>", f'<main><p class="prov" style="border:1px solid var(--line);'
                                      f'border-radius:6px;padding:.4rem .7rem;background:var(--soft)">{note}</p>', 1)
    tail = ""
    if path == "/timeline" and not query:
        tail += "<script>window.TL_CANONICAL=1</script>"
    tail += f'<script src="{base.rstrip("/")}/static/site.js"></script>'
    return body.replace("</body>", tail + "</body>", 1)


def export(out: Path, base: str = "", note: str | None = None, site_url: str | None = None,
           limit: int = 20000, quiet: bool = False) -> dict:
    from fastapi.testclient import TestClient
    from .app import app

    if not DB_PATH.exists():
        raise SystemExit("no build/presidential.db — run `./pres build` first")
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
        all_inds = [r[0] for r in c.execute("SELECT id FROM indicators ORDER BY id")]
    # the canonical /timeline renders every series; site.js shows the default set and toggles the rest
    override = {("/timeline", ""): "/timeline?indicators=" + ",".join(all_inds)}

    client = TestClient(app)
    pages: dict[Key, tuple[str, bool]] = {}
    queue: deque[tuple[Key, str]] = deque((key_of(u), u) for u in SEEDS)
    misses: list[str] = []
    while queue and len(pages) < limit:
        key, url = queue.popleft()
        if key in pages:
            continue
        r = client.get(override.get(key, url))
        if r.status_code != 200 or r.text == "not found":
            misses.append(url)
            continue
        plain = r.headers.get("content-type", "").startswith("text/plain")
        pages[key] = (r.text, plain)
        if not quiet and len(pages) % 100 == 0:
            print(f"  {len(pages)} pages…", flush=True)
        if plain:
            continue
        for m in ATTR.finditer(r.text):
            k = key_of(m.group(2))
            if k not in pages:
                queue.append((k, m.group(2)))

    known = {k: v[1] for k, v in pages.items()}
    if out.exists():
        shutil.rmtree(out)
    nbytes = 0
    for key, (body, plain) in pages.items():
        if not plain:
            body = _inject(_rewrite(body, base, known), key, base, note)
        f = out / dest(key, plain)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
        nbytes += len(body.encode())

    (out / "static").mkdir(parents=True, exist_ok=True)
    shutil.copy(STATIC / "site.js", out / "static" / "site.js")
    (out / ".nojekyll").write_text("")           # keep GitHub Pages from filtering the build
    (out / "404.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Not found</title>'
        '<body style="font:16px/1.5 Georgia,serif;max-width:40rem;margin:4rem auto;padding:0 1rem">'
        f'<h1>Not found</h1><p><a href="{base.rstrip("/") or ""}/">Back to the index</a>.</p>')
    if site_url:
        root = site_url.rstrip("/")
        urls = "".join(f"<url><loc>{root}{link(k, False, base)}</loc></url>"
                       for k, v in sorted(pages.items()) if not v[1])
        (out / "sitemap.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {root}/sitemap.xml\n")
    return {"pages": len(pages), "bytes": nbytes, "misses": misses}
