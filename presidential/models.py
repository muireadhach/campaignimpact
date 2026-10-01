"""Load and validate the YAML corpus under data/.

Layout:
  data/schema/{enums,categories,issues,events}.yaml
  data/people/<person>.yaml
  data/cycles/<year>/cycle.yaml
  data/cycles/<year>/candidates/<person>.yaml
  data/cycles/<year>/positions/<position-id>.yaml
  data/cycles/<year>/followthrough/<position-id>.yaml
  data/cycles/<year>/reception/<reception-id>.yaml

Validation is deliberately strict: unknown enum values, dangling references,
and unverifiable quotes are all hard errors. The corpus should never build in
a state where a claim cannot be traced to archived text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import DATA, archive

DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Problem:
    level: str      # error | warn
    path: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.path}: {self.msg}"


@dataclass
class Corpus:
    enums: dict[str, dict[str, str]]
    categories: dict[str, dict]
    issues: dict[str, dict]
    events: dict[str, dict]
    people: dict[str, dict]
    cycles: dict[str, dict]
    candidates: dict[str, dict]       # key "<cycle>/<person>"
    positions: dict[str, dict]
    followthrough: dict[str, dict]    # key = position id
    reception: dict[str, dict]
    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]


def _load_yaml(p: Path) -> Any:
    with p.open() as f:
        return yaml.safe_load(f)


def _load_dir(d: Path) -> dict[str, tuple[Path, dict]]:
    out = {}
    if not d.exists():
        return out
    for p in sorted(d.glob("*.yaml")):
        rec = _load_yaml(p) or {}
        rec.setdefault("id", p.stem)
        out[rec["id"]] = (p, rec)
    return out


class Validator:
    def __init__(self, corpus: Corpus):
        self.c = corpus

    def err(self, path, msg):
        self.c.problems.append(Problem("error", str(path), msg))

    def warn(self, path, msg):
        self.c.problems.append(Problem("warn", str(path), msg))

    def enum(self, path, name, value, *, required=True):
        if value is None:
            if required:
                self.err(path, f"missing required enum field {name}")
            return
        if value not in self.c.enums.get(name, {}):
            self.err(path, f"{name}={value!r} not in enums.yaml")

    def require(self, path, rec, *keys):
        for k in keys:
            if k not in rec or rec[k] in (None, "", []):
                self.err(path, f"missing required field {k!r}")

    def date(self, path, value, label="date", *, required=False):
        if value is None:
            if required:
                self.err(path, f"missing {label}")
            return
        if not DATE_RE.match(str(value)):
            self.err(path, f"{label}={value!r} must be YYYY, YYYY-MM, or YYYY-MM-DD")

    def ref(self, path, table: dict, value, label, *, required=True):
        if value is None:
            if required:
                self.err(path, f"missing reference {label}")
            return
        if value not in table:
            self.err(path, f"{label}={value!r} does not exist")

    def doc(self, path, doc_id, *, required=True):
        if doc_id is None:
            if required:
                self.err(path, "missing doc reference")
            return
        if not archive.exists(doc_id):
            self.err(path, f"doc={doc_id!r} not in archive")

    def quote(self, path, doc_id, quote):
        if not doc_id or not quote:
            return
        if not archive.exists(doc_id):
            return  # already reported
        ok, why = archive.find_quote(doc_id, quote)
        if not ok:
            self.err(path, f"quote not verified: {why}")

    def provenance(self, path, rec):
        prov = rec.get("provenance")
        if not isinstance(prov, dict):
            self.err(path, "missing provenance block")
            return
        self.require(path, prov, "created_by", "created_at")
        self.date(path, prov.get("created_at"), "provenance.created_at")
        self.enum(path, "review_status", prov.get("review_status"))

    # --- record validators -------------------------------------------------

    def person(self, path, rec):
        self.require(path, rec, "name")
        self.date(path, rec.get("born"), "born")
        self.date(path, rec.get("died"), "died")

    def cycle(self, path, rec):
        self.require(path, rec, "year", "election_date")
        self.date(path, rec.get("election_date"), "election_date", required=True)
        for e in rec.get("context_events", []) or []:
            self.ref(path, self.c.events, e, "context_events[]")
        if rec.get("winner"):
            self.ref(path, self.c.people, rec["winner"], "winner")

    def candidate(self, path, rec):
        self.require(path, rec, "person", "cycle", "party", "tier")
        self.ref(path, self.c.people, rec.get("person"), "person")
        self.ref(path, self.c.cycles, str(rec.get("cycle")), "cycle")
        self.enum(path, "candidate_tier", rec.get("tier"))
        self.provenance(path, rec)
        for s in rec.get("sources", []) or []:
            self.doc(path, s)

    def position(self, path, rec):
        self.require(path, rec, "cycle", "candidate", "issue", "title", "solution", "kind",
                     "commitment_strength", "attribution", "evidence_quality")
        ck = f"{rec.get('cycle')}/{rec.get('candidate')}"
        if ck not in self.c.candidates:
            self.err(path, f"candidate {ck!r} has no candidate record")
        self.ref(path, self.c.issues, rec.get("issue"), "issue")
        for cat in rec.get("categories", []) or []:
            self.ref(path, self.c.categories, cat, "categories[]")
        if not rec.get("categories"):
            self.warn(path, "no categories; will inherit from issue")
        for e in rec.get("triggered_by", []) or []:
            self.ref(path, self.c.events, e, "triggered_by[]")
        self.enum(path, "position_kind", rec.get("kind"))
        self.enum(path, "commitment_strength", rec.get("commitment_strength"))
        self.enum(path, "attribution", rec.get("attribution"))
        self.enum(path, "evidence_quality", rec.get("evidence_quality"))
        self.provenance(path, rec)
        stmts = rec.get("statements") or []
        if not stmts:
            self.err(path, "position has no statements; every position needs at least one evidenced statement")
        for i, s in enumerate(stmts):
            sp = f"{path}#statements[{i}]"
            self.require(sp, s, "date", "doc")
            self.date(sp, s.get("date"), "date", required=True)
            self.enum(sp, "campaign_phase", s.get("phase"))
            self.enum(sp, "attribution", s.get("attribution"), required=False)
            self.doc(sp, s.get("doc"))
            if rec.get("attribution") in ("candidate_direct", "surrogate", "party_platform", "reported"):
                if not s.get("quote"):
                    self.err(sp, f"attribution={rec['attribution']} requires a verbatim quote")
            self.quote(sp, s.get("doc"), s.get("quote"))
        if rec.get("supersedes"):
            self.ref(path, self.c.positions, rec["supersedes"], "supersedes")

    def followthrough(self, path, rec):
        self.require(path, rec, "position", "verdict")
        self.ref(path, self.c.positions, rec.get("position"), "position")
        self.enum(path, "verdict", rec.get("verdict"))
        self.provenance(path, rec)
        for i, m in enumerate(rec.get("mechanisms") or []):
            mp = f"{path}#mechanisms[{i}]"
            self.require(mp, m, "type", "name", "date")
            self.enum(mp, "mechanism_type", m.get("type"))
            self.date(mp, m.get("date"), "date", required=True)
            self.doc(mp, m.get("doc"), required=False)
            self.quote(mp, m.get("doc"), m.get("quote"))
            for iid in m.get("indicators") or []:
                if not (DATA / "indicators" / f"{iid}.yaml").exists():
                    self.err(mp, f"indicators[]={iid!r} has no data/indicators file")
        assess = rec.get("assessments") or []
        for i, a in enumerate(assess):
            ap = f"{path}#assessments[{i}]"
            self.require(ap, a, "doc", "verdict", "reasoning")
            self.enum(ap, "verdict", a.get("verdict"))
            self.doc(ap, a.get("doc"))
            self.quote(ap, a.get("doc"), a.get("quote"))
        if rec.get("verdict") != "not_applicable" and not assess:
            self.err(path, "a verdict other than not_applicable needs at least one sourced assessment")
        verdicts = {a.get("verdict") for a in assess}
        if len(verdicts) > 1 and not rec.get("contested"):
            self.warn(path, f"assessments disagree {sorted(verdicts)} but contested is not set")
        obs = rec.get("obstruction")
        if obs:
            for r in obs.get("reasons", []) or []:
                self.enum(path, "obstruction_reason", r)
            self.doc(path, obs.get("doc"), required=False)
        for i, e in enumerate(rec.get("enacted_by_other") or []):
            ep = f"{path}#enacted_by_other[{i}]"
            self.require(ep, e, "by", "type", "name", "date")
            self.ref(ep, self.c.people, e.get("by"), "by")
            self.enum(ep, "mechanism_type", e.get("type"))
            self.date(ep, e.get("date"), "date", required=True)
            self.doc(ep, e.get("doc"), required=False)

    def reception(self, path, rec):
        self.require(path, rec, "cycle", "candidate", "source_type", "perceived", "fidelity", "salience", "doc",
                     "evidence_quality")
        ck = f"{rec.get('cycle')}/{rec.get('candidate')}"
        if ck not in self.c.candidates:
            self.err(path, f"candidate {ck!r} has no candidate record")
        if rec.get("position"):
            self.ref(path, self.c.positions, rec["position"], "position")
        self.enum(path, "reception_source_type", rec.get("source_type"))
        self.enum(path, "reception_fidelity", rec.get("fidelity"))
        self.enum(path, "salience", rec.get("salience"))
        self.enum(path, "evidence_quality", rec.get("evidence_quality"))
        self.date(path, rec.get("date"), "date")
        self.doc(path, rec.get("doc"))
        self.quote(path, rec.get("doc"), rec.get("quote"))
        self.provenance(path, rec)


def load() -> Corpus:
    schema = DATA / "schema"
    enums = _load_yaml(schema / "enums.yaml")
    cats = {c["id"]: c for c in _load_yaml(schema / "categories.yaml")}
    issues = {i["id"]: i for i in _load_yaml(schema / "issues.yaml")}
    events = {e["id"]: e for e in _load_yaml(schema / "events.yaml")}

    people_raw = _load_dir(DATA / "people")
    cycles_raw, cands_raw, pos_raw, ft_raw, rec_raw = {}, {}, {}, {}, {}
    cycles_dir = DATA / "cycles"
    if cycles_dir.exists():
        for cd in sorted(cycles_dir.iterdir()):
            if not cd.is_dir():
                continue
            cy = _load_yaml(cd / "cycle.yaml") if (cd / "cycle.yaml").exists() else {}
            cy.setdefault("year", int(cd.name))
            cycles_raw[cd.name] = (cd / "cycle.yaml", cy)
            for pid, (p, r) in _load_dir(cd / "candidates").items():
                r.setdefault("cycle", int(cd.name)); r.setdefault("person", pid)
                cands_raw[f"{cd.name}/{pid}"] = (p, r)
            for pid, (p, r) in _load_dir(cd / "positions").items():
                r.setdefault("cycle", int(cd.name))
                pos_raw[pid] = (p, r)
            for pid, (p, r) in _load_dir(cd / "followthrough").items():
                r.setdefault("position", pid)
                ft_raw[pid] = (p, r)
            for rid, (p, r) in _load_dir(cd / "reception").items():
                r.setdefault("cycle", int(cd.name))
                rec_raw[rid] = (p, r)

    c = Corpus(
        enums=enums, categories=cats, issues=issues, events=events,
        people={k: v[1] for k, v in people_raw.items()},
        cycles={k: v[1] for k, v in cycles_raw.items()},
        candidates={k: v[1] for k, v in cands_raw.items()},
        positions={k: v[1] for k, v in pos_raw.items()},
        followthrough={k: v[1] for k, v in ft_raw.items()},
        reception={k: v[1] for k, v in rec_raw.items()},
    )
    v = Validator(c)
    for iid, i in issues.items():
        if i.get("category") not in cats:
            v.err(f"issues.yaml#{iid}", f"category {i.get('category')!r} not in categories.yaml")
    for table, fn in ((people_raw, v.person), (cycles_raw, v.cycle), (cands_raw, v.candidate),
                      (pos_raw, v.position), (ft_raw, v.followthrough), (rec_raw, v.reception)):
        for key, (path, rec) in table.items():
            rel = path.relative_to(DATA)
            if "id" in rec and not ID_RE.match(str(rec["id"])):
                v.err(rel, f"id {rec['id']!r} must be lowercase kebab-case")
            fn(rel, rec)
    return c
