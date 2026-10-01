"""Read-only web UI over build/presidential.db."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from .. import DB_PATH, archive

app = FastAPI(title="Presidential")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else []


def db() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def q(sql: str, *args) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(sql, args).fetchall()


def q1(sql: str, *args):
    r = q(sql, *args)
    return r[0] if r else None


def enums() -> dict:
    import yaml
    from .. import DATA
    return yaml.safe_load((DATA / "schema" / "enums.yaml").read_text())


def render(request, name, **ctx):
    ctx.setdefault("enums", enums())
    return templates.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cycles = q("""SELECT c.*, p.name AS winner_name,
                  (SELECT COUNT(*) FROM candidates k WHERE k.cycle=c.year) AS n_candidates,
                  (SELECT COUNT(*) FROM positions x WHERE x.cycle=c.year) AS n_positions
                  FROM cycles c LEFT JOIN people p ON p.id=c.winner ORDER BY c.year""")
    stats = q1("""SELECT (SELECT COUNT(*) FROM positions) AS positions, (SELECT COUNT(*) FROM statements) AS statements,
                  (SELECT COUNT(*) FROM documents) AS documents, (SELECT COUNT(*) FROM followthrough) AS followthrough,
                  (SELECT COUNT(*) FROM reception) AS reception,
                  (SELECT COUNT(*) FROM positions WHERE review_status='unreviewed') AS unreviewed,
                  (SELECT COUNT(*) FROM statements WHERE verified=0) AS unverified""")
    problems = q("SELECT * FROM problems ORDER BY level, path")
    return render(request, "home.html", cycles=cycles, stats=stats, problems=problems)


@app.get("/cycle/{year}", response_class=HTMLResponse)
def cycle(request: Request, year: int):
    cy = q1("SELECT c.*, p.name AS winner_name FROM cycles c LEFT JOIN people p ON p.id=c.winner WHERE year=?", year)
    cands = q("""SELECT k.*, p.name, (SELECT COUNT(*) FROM positions x WHERE x.cycle=k.cycle AND x.candidate=k.person) AS n_positions
                 FROM candidates k JOIN people p ON p.id=k.person WHERE k.cycle=?
                 ORDER BY CASE k.tier WHEN 'nominee' THEN 0 WHEN 'contender' THEN 1 WHEN 'third_party' THEN 2 ELSE 3 END,
                          k.popular_vote DESC""", year)
    events = q("SELECT * FROM events WHERE id IN (SELECT value FROM json_each(?)) ORDER BY start", cy["context_events"] or "[]")
    return render(request, "cycle.html", cy=cy, cands=cands, events=events)


@app.get("/candidate/{year}/{person}", response_class=HTMLResponse)
def candidate(request: Request, year: int, person: str):
    k = q1("SELECT k.*, p.* , p.summary AS person_summary, k.summary AS summary FROM candidates k JOIN people p ON p.id=k.person WHERE k.cycle=? AND k.person=?", year, person)
    positions = q("""SELECT x.*, i.name AS issue_name, f.verdict, f.contested,
                     (SELECT group_concat(category) FROM position_categories pc WHERE pc.position=x.id) AS cats
                     FROM positions x JOIN issues i ON i.id=x.issue LEFT JOIN followthrough f ON f.position=x.id
                     WHERE x.cycle=? AND x.candidate=? ORDER BY i.category, x.first_date""", year, person)
    reception = q("SELECT * FROM reception WHERE cycle=? AND candidate=? ORDER BY date", year, person)
    return render(request, "candidate.html", k=k, positions=positions, reception=reception, year=year)


@app.get("/position/{pid}", response_class=HTMLResponse)
def position(request: Request, pid: str):
    x = q1("""SELECT x.*, i.name AS issue_name, i.category AS issue_category, p.name AS candidate_name, k.party
              FROM positions x JOIN issues i ON i.id=x.issue JOIN people p ON p.id=x.candidate
              JOIN candidates k ON k.cycle=x.cycle AND k.person=x.candidate WHERE x.id=?""", pid)
    if not x:
        return HTMLResponse("not found", 404)
    stmts = q("SELECT s.*, d.title AS doc_title, d.date AS doc_date FROM statements s JOIN documents d ON d.id=s.doc WHERE position=? ORDER BY date, seq", pid)
    cats = q("SELECT c.* FROM position_categories pc JOIN categories c ON c.id=pc.category WHERE pc.position=?", pid)
    events = q("SELECT e.* FROM position_events pe JOIN events e ON e.id=pe.event WHERE pe.position=?", pid)
    ft = q1("SELECT * FROM followthrough WHERE position=?", pid)
    mechs = q("SELECT m.*, d.title AS doc_title FROM mechanisms m LEFT JOIN documents d ON d.id=m.doc WHERE position=? ORDER BY date, seq", pid)
    mech_inds = {}
    for r in q("""SELECT mi.mechanism, i.id, i.name FROM mechanism_indicators mi JOIN mechanisms m ON m.id=mi.mechanism
                  JOIN indicators i ON i.id=mi.indicator WHERE m.position=? ORDER BY i.name""", pid):
        mech_inds.setdefault(r["mechanism"], []).append(r)
    assess = q("SELECT a.*, d.title AS doc_title, d.author AS doc_author FROM assessments a JOIN documents d ON d.id=a.doc WHERE position=? ORDER BY seq", pid)
    others = q("SELECT e.*, p.name AS by_name FROM enacted_by_other e JOIN people p ON p.id=e.by_person WHERE position=? ORDER BY date", pid)
    reception = q("SELECT r.*, d.title AS doc_title FROM reception r JOIN documents d ON d.id=r.doc WHERE position=? ORDER BY date", pid)
    same_issue = q("""SELECT x.id, x.title, x.candidate, p.name AS candidate_name, x.cycle, x.commitment_strength
                      FROM positions x JOIN people p ON p.id=x.candidate WHERE x.issue=? AND x.id<>? ORDER BY x.cycle, p.name""", x["issue"], pid)
    chain = q("SELECT id, title, candidate, cycle FROM positions WHERE supersedes=? OR id=?", pid, x["supersedes"] or "")
    return render(request, "position.html", x=x, stmts=stmts, cats=cats, events=events, ft=ft, mechs=mechs, mech_inds=mech_inds,
                  assess=assess, others=others, reception=reception, same_issue=same_issue, chain=chain)


@app.get("/positions", response_class=HTMLResponse)
def positions(request: Request, cycle: int | None = None, candidate: str | None = None, category: str | None = None,
              issue: str | None = None, attribution: str | None = None, strength: str | None = None,
              kind: str | None = None, verdict: str | None = None, event: str | None = None, review: str | None = None,
              text: str | None = None):
    where, args = [], []
    if cycle: where.append("x.cycle=?"); args.append(cycle)
    if candidate: where.append("x.candidate=?"); args.append(candidate)
    if issue: where.append("x.issue=?"); args.append(issue)
    if attribution: where.append("x.attribution=?"); args.append(attribution)
    if strength: where.append("x.commitment_strength=?"); args.append(strength)
    if kind: where.append("x.kind=?"); args.append(kind)
    if review: where.append("x.review_status=?"); args.append(review)
    if verdict: where.append("f.verdict=?"); args.append(verdict)
    if category:
        where.append("x.id IN (SELECT position FROM position_categories WHERE category=? OR category IN (SELECT id FROM categories WHERE parent=?))")
        args += [category, category]
    if event: where.append("x.id IN (SELECT position FROM position_events WHERE event=?)"); args.append(event)
    if text: where.append("x.id IN (SELECT id FROM positions_fts WHERE positions_fts MATCH ?)"); args.append(text)
    sql = f"""SELECT x.*, i.name AS issue_name, p.name AS candidate_name, f.verdict, f.contested,
              (SELECT group_concat(category) FROM position_categories pc WHERE pc.position=x.id) AS cats
              FROM positions x JOIN issues i ON i.id=x.issue JOIN people p ON p.id=x.candidate
              LEFT JOIN followthrough f ON f.position=x.id
              {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY x.cycle, p.name, i.category, x.first_date"""
    rows = q(sql, *args)
    filters = dict(cycle=cycle, candidate=candidate, category=category, issue=issue, attribution=attribution,
                   strength=strength, kind=kind, verdict=verdict, event=event, review=review, text=text)
    return render(request, "positions.html", rows=rows, filters=filters,
                  cycles=q("SELECT year FROM cycles ORDER BY year"), people=q("SELECT DISTINCT p.id, p.name FROM candidates k JOIN people p ON p.id=k.person ORDER BY p.name"),
                  categories=q("SELECT * FROM categories ORDER BY COALESCE(parent,id), parent IS NOT NULL, name"),
                  issues=q("SELECT * FROM issues ORDER BY name"), events=q("SELECT * FROM events ORDER BY start"))


@app.get("/issues", response_class=HTMLResponse)
def issues(request: Request):
    rows = q("""SELECT i.*, c.name AS category_name, COUNT(x.id) AS n,
                group_concat(DISTINCT x.cycle) AS cycles FROM issues i JOIN categories c ON c.id=i.category
                LEFT JOIN positions x ON x.issue=i.id GROUP BY i.id ORDER BY c.name, i.name""")
    return render(request, "issues.html", rows=rows)


@app.get("/issue/{iid}", response_class=HTMLResponse)
def issue(request: Request, iid: str):
    i = q1("SELECT i.*, c.name AS category_name FROM issues i JOIN categories c ON c.id=i.category WHERE i.id=?", iid)
    rows = q("""SELECT x.*, p.name AS candidate_name, k.party, f.verdict, f.contested FROM positions x JOIN people p ON p.id=x.candidate
                JOIN candidates k ON k.cycle=x.cycle AND k.person=x.candidate LEFT JOIN followthrough f ON f.position=x.id
                WHERE x.issue=? ORDER BY x.cycle, p.name, x.first_date""", iid)
    return render(request, "issue.html", i=i, rows=rows)


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request, year: int, a: str, b: str):
    def side(person):
        return q("""SELECT x.*, i.name AS issue_name, i.category, f.verdict FROM positions x JOIN issues i ON i.id=x.issue
                    LEFT JOIN followthrough f ON f.position=x.id WHERE x.cycle=? AND x.candidate=?""", year, person)
    A, B = side(a), side(b)
    issues_ = sorted({r["issue"] for r in A} | {r["issue"] for r in B})
    names = {r["issue"]: r["issue_name"] for r in A + B}
    grid = [(iid, names[iid], [r for r in A if r["issue"] == iid], [r for r in B if r["issue"] == iid]) for iid in issues_]
    pa, pb = q1("SELECT name FROM people WHERE id=?", a), q1("SELECT name FROM people WHERE id=?", b)
    return render(request, "compare.html", year=year, a=a, b=b, pa=pa, pb=pb, grid=grid)


@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, text: str | None = None, source: str | None = None):
    if text:
        rows = q("""SELECT d.*, snippet(documents_fts, 2, '<mark>', '</mark>', '…', 24) AS snip,
                    (SELECT COUNT(*) FROM statements s WHERE s.doc=d.id) AS n_cites
                    FROM documents_fts f JOIN documents d ON d.id=f.id WHERE documents_fts MATCH ? ORDER BY d.date""", text)
    else:
        rows = q("""SELECT d.*, NULL AS snip, (SELECT COUNT(*) FROM statements s WHERE s.doc=d.id)
                    + (SELECT COUNT(*) FROM assessments a WHERE a.doc=d.id) + (SELECT COUNT(*) FROM reception r WHERE r.doc=d.id) AS n_cites
                    FROM documents d {} ORDER BY d.date""".format("WHERE source=?" if source else ""), *([source] if source else []))
    return render(request, "documents.html", rows=rows, text=text, source=source,
                  sources=q("SELECT source, COUNT(*) n FROM documents GROUP BY source"))


@app.get("/document/{did}", response_class=HTMLResponse)
def document(request: Request, did: str, hl: str | None = None):
    d = q1("SELECT * FROM documents WHERE id=?", did)
    if not d:
        return HTMLResponse("not found", 404)
    text = archive.load_text(did)
    cites = q("""SELECT 'statement' AS kind, s.position AS ref, x.title, s.quote FROM statements s JOIN positions x ON x.id=s.position WHERE s.doc=?
                 UNION ALL SELECT 'assessment', a.position, x.title, a.quote FROM assessments a JOIN positions x ON x.id=a.position WHERE a.doc=?
                 UNION ALL SELECT 'reception', r.id, r.perceived, r.quote FROM reception r WHERE r.doc=?
                 UNION ALL SELECT 'mechanism', m.position, m.name, m.quote FROM mechanisms m WHERE m.doc=?""", did, did, did, did)
    paras = text.split("\n")
    return render(request, "document.html", d=d, paras=paras, cites=cites, hl=hl)


@app.get("/document/{did}/text", response_class=PlainTextResponse)
def document_text(did: str):
    return archive.load_text(did)


@app.get("/reception", response_class=HTMLResponse)
def reception(request: Request, cycle: int | None = None, candidate: str | None = None, fidelity: str | None = None,
              source_type: str | None = None):
    where, args = [], []
    if source_type: where.append("r.source_type=?"); args.append(source_type)
    if cycle: where.append("r.cycle=?"); args.append(cycle)
    if candidate: where.append("r.candidate=?"); args.append(candidate)
    if fidelity: where.append("r.fidelity=?"); args.append(fidelity)
    rows = q(f"""SELECT r.*, p.name AS candidate_name, x.title AS position_title, d.title AS doc_title
                 FROM reception r JOIN people p ON p.id=r.candidate LEFT JOIN positions x ON x.id=r.position JOIN documents d ON d.id=r.doc
                 {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY r.cycle, r.candidate, r.date""", *args)
    return render(request, "reception.html", rows=rows, cycle=cycle, candidate=candidate, fidelity=fidelity,
                  cycles=q("SELECT year FROM cycles ORDER BY year"),
                  people=q("SELECT DISTINCT p.id, p.name FROM reception r JOIN people p ON p.id=r.candidate ORDER BY p.name"))


@app.get("/similar/{pid}", response_class=HTMLResponse)
def similar(request: Request, pid: str, field: str = "solution"):
    from .. import embed
    x = q1("SELECT x.*, p.name AS candidate_name FROM positions x JOIN people p ON p.id=x.candidate WHERE x.id=?", pid)
    try:
        hits = embed.nearest(pid, field=field, k=25)
    except FileNotFoundError:
        hits = None
    rows = []
    if hits:
        for other, score in hits:
            r = q1("SELECT x.*, p.name AS candidate_name, i.name AS issue_name FROM positions x JOIN people p ON p.id=x.candidate JOIN issues i ON i.id=x.issue WHERE x.id=?", other)
            if r:
                rows.append((r, score))
    return render(request, "similar.html", x=x, rows=rows, field=field, available=hits is not None)


@app.get("/timeline", response_class=HTMLResponse)
def timeline(request: Request, start: int = 1925, end: int = 1945, indicators: str | None = None):
    """Small-multiple SVG charts of indicator series with an event lane and presidential-action markers."""
    all_ind = q("SELECT * FROM indicators ORDER BY category, name")
    default = ["unemployment-rate-1929-42", "unemployment-rate", "industrial-production", "gdp-nominal", "bank-suspensions", "farm-prices-received",
               "ppi-all-commodities", "federal-debt", "federal-surplus-deficit", "hs-graduation-rate"]
    chosen = [i for i in (indicators or "").split(",") if i] or [r["id"] for r in all_ind if r["id"] in default]
    W, H, L, R_, T, B = 900, 150, 70, 20, 16, 26
    span = max(1, end - start)
    sx = lambda yr: L + (yr - start) / span * (W - L - R_)
    year_ticks = [(yr, sx(yr)) for yr in range(start, end + 1) if yr % (1 if span <= 25 else 5 if span <= 120 else 20) == 0]
    panels = []
    for ind in all_ind:
        if ind["id"] not in chosen:
            continue
        vals = q("SELECT year, value FROM indicator_values WHERE indicator=? AND year BETWEEN ? AND ? ORDER BY year", ind["id"], start, end)
        if not vals:
            continue
        ys = [v["value"] for v in vals]; lo, hi = min(ys), max(ys)
        if lo > 0 and lo / hi > 0.6:
            lo *= 0.98  # zoom in on flat series
        if lo == hi:
            hi = lo + 1
        sy = lambda v, lo=lo, hi=hi: T + (hi - v) / (hi - lo) * (H - T - B)
        pts = [(sx(v["year"]), sy(v["value"]), v["year"], v["value"]) for v in vals]
        acts = q("""SELECT m.id, m.name, m.date, m.type, m.position, x.title, p.name AS candidate_name, f.verdict
                    FROM mechanism_indicators mi JOIN mechanisms m ON m.id=mi.mechanism JOIN positions x ON x.id=m.position
                    JOIN people p ON p.id=x.candidate LEFT JOIN followthrough f ON f.position=m.position
                    WHERE mi.indicator=? AND CAST(substr(m.date,1,4) AS INTEGER) BETWEEN ? AND ? ORDER BY m.date""", ind["id"], start, end)
        byyear = {v["year"]: v["value"] for v in vals}
        groups: dict[int, list] = {}
        for a in acts:
            groups.setdefault(int(a["date"][:4]), []).append(a)
        markers = [{"year": y, "x": sx(y + 0.5), "n": len(g), "acts": g, "v0": byyear.get(y), "after": {k: byyear.get(y + k) for k in (1, 2, 4)}}
                   for y, g in sorted(groups.items())]
        panels.append({"ind": ind, "pts": pts, "markers": markers, "ticks": [(v, sy(v)) for v in (lo, (lo + hi) / 2, hi)], "n_acts": len(acts)})
    events = q("SELECT * FROM events WHERE CAST(substr(start,1,4) AS INTEGER) <= ? AND CAST(substr(COALESCE(NULLIF(end,''),start),1,4) AS INTEGER) >= ? ORDER BY start", end, start)
    lanes = []  # assign each event a row so overlapping events don't stack on top of each other
    for e in events:
        s0 = max(start, int(e["start"][:4])); e0 = min(end + 1, int((e["end"] or e["start"])[:4]) + 1)
        row = next((i for i, last in enumerate(lanes) if last <= s0), None)
        if row is None:
            lanes.append(e0); row = len(lanes) - 1
        else:
            lanes[row] = e0
        e = dict(e); e.update(x0=sx(s0), w=max(2, sx(e0) - sx(s0)), row=row)
        events[events.index(next(x for x in events if x["id"] == e["id"]))] = e
    cycles = [(c["year"], sx(c["year"] + 0.85)) for c in q("SELECT year FROM cycles WHERE year BETWEEN ? AND ?", start, end)]
    return render(request, "timeline.html", panels=panels, events=events, n_lanes=len(lanes), all_ind=all_ind, chosen=chosen,
                  start=start, end=end, W=W, H=H, L=L, year_ticks=year_ticks, cycles=cycles)
