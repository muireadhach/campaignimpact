"""Compile the YAML corpus into build/presidential.db.

The DB is a disposable index; data/ and archive/ are the source of truth.
"""
from __future__ import annotations

import json
import sqlite3
import sys

import yaml

from . import BUILD, DATA, DB_PATH, archive, models

SCHEMA = """
CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT, parent TEXT);
CREATE TABLE issues (id TEXT PRIMARY KEY, name TEXT, category TEXT, aliases TEXT);
CREATE TABLE events (id TEXT PRIMARY KEY, name TEXT, start TEXT, end TEXT, summary TEXT, kind TEXT);
CREATE TABLE indicators (id TEXT PRIMARY KEY, name TEXT, unit TEXT, category TEXT, source_name TEXT, doc TEXT, series TEXT, method TEXT, notes TEXT);
CREATE TABLE indicator_values (indicator TEXT, year INTEGER, value REAL, PRIMARY KEY (indicator, year));
CREATE TABLE mechanism_indicators (mechanism INTEGER, indicator TEXT, PRIMARY KEY (mechanism, indicator));
CREATE TABLE documents (id TEXT PRIMARY KEY, source TEXT, kind TEXT, title TEXT, date TEXT, author TEXT,
    url TEXT, citation TEXT, sha256 TEXT, chars INTEGER);
CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT, born TEXT, died TEXT, parties TEXT, offices TEXT, summary TEXT);
CREATE TABLE cycles (year INTEGER PRIMARY KEY, election_date TEXT, winner TEXT, summary TEXT, context_events TEXT);
CREATE TABLE candidates (cycle INTEGER, person TEXT, party TEXT, tier TEXT, result TEXT, popular_vote REAL,
    electoral_vote INTEGER, summary TEXT, review_status TEXT, PRIMARY KEY (cycle, person));
CREATE TABLE positions (id TEXT PRIMARY KEY, cycle INTEGER, candidate TEXT, issue TEXT, title TEXT,
    solution TEXT, rationale TEXT, kind TEXT, commitment_strength TEXT, attribution TEXT,
    evidence_quality TEXT, supersedes TEXT, notes TEXT, review_status TEXT, created_by TEXT,
    n_statements INTEGER, first_date TEXT, last_date TEXT);
CREATE TABLE position_categories (position TEXT, category TEXT, PRIMARY KEY (position, category));
CREATE TABLE position_events (position TEXT, event TEXT, PRIMARY KEY (position, event));
CREATE TABLE statements (id INTEGER PRIMARY KEY, position TEXT, seq INTEGER, date TEXT, venue TEXT, phase TEXT,
    audience TEXT, attribution TEXT, doc TEXT, quote TEXT, note TEXT, verified INTEGER);
CREATE TABLE followthrough (position TEXT PRIMARY KEY, verdict TEXT, contested INTEGER, summary TEXT,
    obstruction_reasons TEXT, obstruction_explanation TEXT, obstruction_doc TEXT, review_status TEXT);
CREATE TABLE mechanisms (id INTEGER PRIMARY KEY, position TEXT, seq INTEGER, type TEXT, name TEXT, date TEXT,
    doc TEXT, quote TEXT, effect TEXT, consistent TEXT);
CREATE TABLE assessments (id INTEGER PRIMARY KEY, position TEXT, seq INTEGER, doc TEXT, verdict TEXT,
    reasoning TEXT, quote TEXT);
CREATE TABLE enacted_by_other (id INTEGER PRIMARY KEY, position TEXT, by_person TEXT, type TEXT, name TEXT,
    date TEXT, doc TEXT, note TEXT);
CREATE TABLE reception (id TEXT PRIMARY KEY, cycle INTEGER, candidate TEXT, position TEXT, source_type TEXT,
    source_name TEXT, date TEXT, perceived TEXT, fidelity TEXT, salience TEXT, doc TEXT, quote TEXT,
    note TEXT, evidence_quality TEXT, review_status TEXT);
CREATE TABLE problems (level TEXT, path TEXT, msg TEXT);
CREATE VIRTUAL TABLE positions_fts USING fts5(id UNINDEXED, title, solution, rationale, quotes);
CREATE VIRTUAL TABLE documents_fts USING fts5(id UNINDEXED, title, text);
"""


def _j(x) -> str | None:
    return json.dumps(x, default=str) if x not in (None, [], {}) else None


def build(*, strict: bool = True) -> models.Corpus:
    c = models.load()
    for p in c.problems:
        print(p, file=sys.stderr)
    if c.errors and strict:
        print(f"\nBUILD FAILED: {len(c.errors)} error(s)", file=sys.stderr)
        sys.exit(1)

    BUILD.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    parent_of = {}
    for cat in c.categories.values():
        for ch in cat.get("children", []) or []:
            parent_of[ch] = cat["id"]
    db.executemany("INSERT INTO categories VALUES (?,?,?)",
                   [(k, v["name"], parent_of.get(k)) for k, v in c.categories.items()])
    db.executemany("INSERT INTO issues VALUES (?,?,?,?)",
                   [(k, v["name"], v.get("category"), _j(v.get("aliases"))) for k, v in c.issues.items()])
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)",
                   [(k, v["name"], str(v.get("start") or ""), str(v.get("end") or ""), v.get("summary"), v.get("kind", "political"))
                    for k, v in c.events.items()])
    from .sources import data as _data
    links = yaml.safe_load((DATA / "schema" / "indicator_links.yaml").read_text()) or {}
    indicators = _data.load_all()
    for iid, ind in indicators.items():
        if not archive.exists(ind["doc"]):
            print(f"[error] indicators/{iid}: doc {ind['doc']!r} not in archive", file=sys.stderr); sys.exit(1)
        db.execute("INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?,?)",
                   (iid, ind["name"], ind.get("unit"), ind.get("category"), ind.get("source_name"), ind["doc"], ind.get("series"), ind.get("method"), ind.get("notes")))
        db.executemany("INSERT INTO indicator_values VALUES (?,?,?)", [(iid, int(y), float(v)) for y, v in ind["values"].items()])

    for did in archive.all_ids():
        m = archive.load_meta(did)
        text = archive.load_text(did)
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (m.id, m.source, m.kind, m.title, m.date, m.author, m.url, m.citation, m.sha256, len(text)))
        db.execute("INSERT INTO documents_fts VALUES (?,?,?)", (m.id, m.title, text))

    db.executemany("INSERT INTO people VALUES (?,?,?,?,?,?,?)",
                   [(k, v["name"], str(v.get("born") or ""), str(v.get("died") or ""), _j(v.get("parties")),
                     _j(v.get("offices")), v.get("summary")) for k, v in c.people.items()])
    db.executemany("INSERT INTO cycles VALUES (?,?,?,?,?)",
                   [(int(v["year"]), str(v["election_date"]), v.get("winner"), v.get("summary"),
                     _j(v.get("context_events"))) for v in c.cycles.values()])
    db.executemany("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?)",
                   [(int(v["cycle"]), v["person"], v["party"], v["tier"], v.get("result"), v.get("popular_vote_pct"),
                     v.get("electoral_vote"), v.get("summary"), v["provenance"]["review_status"])
                    for v in c.candidates.values()])

    for pid, p in c.positions.items():
        stmts = p.get("statements") or []
        dates = sorted(str(s["date"]) for s in stmts)
        cats = p.get("categories") or [c.issues[p["issue"]]["category"]]
        db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, int(p["cycle"]), p["candidate"], p["issue"], p["title"], p["solution"], p.get("rationale"),
                    p["kind"], p["commitment_strength"], p["attribution"], p["evidence_quality"],
                    p.get("supersedes"), p.get("notes"), p["provenance"]["review_status"],
                    p["provenance"]["created_by"], len(stmts), dates[0] if dates else None,
                    dates[-1] if dates else None))
        db.executemany("INSERT INTO position_categories VALUES (?,?)", [(pid, cat) for cat in cats])
        db.executemany("INSERT INTO position_events VALUES (?,?)",
                       [(pid, e) for e in p.get("triggered_by") or []])
        quotes = []
        for i, s in enumerate(stmts):
            ok = archive.find_quote(s["doc"], s["quote"])[0] if s.get("quote") else None
            db.execute("INSERT INTO statements (position,seq,date,venue,phase,audience,attribution,doc,quote,note,verified)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, i, str(s["date"]), s.get("venue"), s.get("phase"), s.get("audience"),
                        s.get("attribution") or p["attribution"], s["doc"], s.get("quote"), s.get("note"),
                        None if ok is None else int(ok)))
            if s.get("quote"):
                quotes.append(s["quote"])
        db.execute("INSERT INTO positions_fts VALUES (?,?,?,?,?)",
                   (pid, p["title"], p["solution"], p.get("rationale") or "", "\n".join(quotes)))

    for pid, f in c.followthrough.items():
        obs = f.get("obstruction") or {}
        db.execute("INSERT INTO followthrough VALUES (?,?,?,?,?,?,?,?)",
                   (pid, f["verdict"], int(bool(f.get("contested"))), f.get("summary"), _j(obs.get("reasons")),
                    obs.get("explanation"), obs.get("doc"), f["provenance"]["review_status"]))
        pos_cats = c.positions[pid].get("categories") or [c.issues[c.positions[pid]["issue"]]["category"]]
        for i, m in enumerate(f.get("mechanisms") or []):
            cur = db.execute("INSERT INTO mechanisms (position,seq,type,name,date,doc,quote,effect,consistent)"
                       " VALUES (?,?,?,?,?,?,?,?,?)",
                       (pid, i, m["type"], m["name"], str(m["date"]), m.get("doc"), m.get("quote"), m.get("effect"),
                        m.get("consistent")))
            linked = m.get("indicators")
            if linked is None:
                linked = [x for cat in pos_cats for x in links.get(cat, [])]
            for iid in dict.fromkeys(linked):
                if iid in indicators:
                    db.execute("INSERT OR IGNORE INTO mechanism_indicators VALUES (?,?)", (cur.lastrowid, iid))
        for i, a in enumerate(f.get("assessments") or []):
            db.execute("INSERT INTO assessments (position,seq,doc,verdict,reasoning,quote) VALUES (?,?,?,?,?,?)",
                       (pid, i, a["doc"], a["verdict"], a["reasoning"], a.get("quote")))
        for e in f.get("enacted_by_other") or []:
            db.execute("INSERT INTO enacted_by_other (position,by_person,type,name,date,doc,note)"
                       " VALUES (?,?,?,?,?,?,?)",
                       (pid, e["by"], e["type"], e["name"], str(e["date"]), e.get("doc"), e.get("note")))

    for rid, r in c.reception.items():
        db.execute("INSERT INTO reception VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, int(r["cycle"]), r["candidate"], r.get("position"), r["source_type"], r.get("source_name"),
                    str(r.get("date") or ""), r["perceived"], r["fidelity"], r["salience"], r["doc"], r.get("quote"),
                    r.get("note"), r["evidence_quality"], r["provenance"]["review_status"]))

    db.executemany("INSERT INTO problems VALUES (?,?,?)", [(p.level, p.path, p.msg) for p in c.problems])
    db.commit()
    db.close()

    n = lambda t: len(getattr(c, t))
    print(f"built {DB_PATH.relative_to(BUILD.parent)}: {len(archive.all_ids())} documents, {n('people')} people, "
          f"{n('cycles')} cycles, {n('candidates')} candidates, {n('positions')} positions, "
          f"{n('followthrough')} followthrough, {n('reception')} reception; "
          f"{len(c.errors)} errors, {len(c.problems) - len(c.errors)} warnings")
    return c
