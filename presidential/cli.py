"""Command-line entry point: `uv run presidential <command>`."""
from __future__ import annotations

import argparse
import sys

from . import archive


def cmd_build(a):
    from . import build
    build.build(strict=not a.lenient)


def cmd_check(a):
    from . import models
    c = models.load()
    for p in c.problems:
        print(p)
    print(f"{len(c.errors)} errors, {len(c.problems) - len(c.errors)} warnings")
    sys.exit(1 if c.errors else 0)


def cmd_fetch(a):
    from .sources import app
    for slug in a.slugs:
        slug = slug.rstrip("/").split("/documents/")[-1]
        did = app.fetch(slug, force=a.force)
        m = archive.load_meta(did)
        print(f"{did}\t{m.date}\t{m.kind}\t{m.title}")


def cmd_verify(a):
    ok, why = archive.find_quote(a.doc, a.quote)
    print("OK" if ok else f"FAIL: {why}")
    sys.exit(0 if ok else 1)


def cmd_grep(a):
    """Search archived documents for a phrase; prints doc id + context. Use it to find exact wording before writing a quote."""
    import re
    pat = re.compile(re.escape(a.phrase) if not a.regex else a.phrase, re.I)
    for did in archive.all_ids():
        if a.doc and a.doc not in did:
            continue
        text = archive.load_text(did)
        for m in pat.finditer(text):
            s, e = max(0, m.start() - a.context), min(len(text), m.end() + a.context)
            print(f"--- {did}\n{text[s:e].replace(chr(10), ' / ')}\n")


def cmd_docs(a):
    for did in archive.all_ids():
        m = archive.load_meta(did)
        print(f"{did}\t{m.date}\t{m.kind}\t{m.title[:70]}")


def cmd_serve(a):
    import uvicorn
    uvicorn.run("presidential.web.app:app", host="127.0.0.1", port=a.port, reload=a.reload)


def cmd_export(a):
    from pathlib import Path
    from .web import export as ex
    r = ex.export(Path(a.out), base=a.base, note=a.note, site_url=a.site_url)
    print(f"{r['pages']} pages, {r['bytes']/1e6:.1f} MB -> {a.out}")
    for u in r["misses"][:20]:
        print("  skipped:", u)


def cmd_embed(a):
    from . import embed
    embed.build_embeddings(force=a.force)


def main(argv=None):
    p = argparse.ArgumentParser(prog="presidential")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build", help="validate data/ and compile build/presidential.db")
    s.add_argument("--lenient", action="store_true", help="build even with errors")
    s.set_defaults(fn=cmd_build)
    s = sub.add_parser("check", help="validate only"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("fetch", help="fetch APP documents by slug or URL into the archive")
    s.add_argument("slugs", nargs="+"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("verify", help="check a quote against an archived doc")
    s.add_argument("doc"); s.add_argument("quote"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("grep", help="search archived text")
    s.add_argument("phrase"); s.add_argument("--doc"); s.add_argument("--regex", action="store_true")
    s.add_argument("-C", "--context", type=int, default=200); s.set_defaults(fn=cmd_grep)
    s = sub.add_parser("docs", help="list archived documents"); s.set_defaults(fn=cmd_docs)
    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--port", type=int, default=8321); s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("export", help="freeze the web UI into a static site")
    s.add_argument("--out", default="site", help="output directory (default: site)")
    s.add_argument("--base", default="", help="path prefix when the site is served from a subfolder, e.g. /promises")
    s.add_argument("--note", default=None, help="banner shown at the top of every page")
    s.add_argument("--site-url", default=None, help="public origin, e.g. https://example.com — writes sitemap.xml and robots.txt")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("indicators", help="fetch/refresh statistical indicator series into data/indicators")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_indicators)
    s = sub.add_parser("embed", help="compute similarity embeddings")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_embed)
    a = p.parse_args(argv)
    a.fn(a)


def cmd_indicators(a):
    from .sources import data
    ids = data.fetch_default_set(force=a.force)
    print("indicators:", ", ".join(ids))


def _add_indicators_parser():
    pass
