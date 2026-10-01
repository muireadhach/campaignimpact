"""Similarity embeddings over position solution and rationale text.

Backend: model2vec static embeddings (small, CPU-only, no torch). Falls back to
a hashed TF-IDF if the model cannot be loaded (e.g. offline before first
download). Vectors are stored in build/embeddings.npz keyed by position id.
"""
from __future__ import annotations

import re
import sqlite3

import numpy as np

from . import BUILD, DB_PATH

EMB_PATH = BUILD / "embeddings.npz"
FIELDS = ("solution", "rationale")
MODEL_NAME = "minishlab/potion-base-8M"


def _texts() -> dict[str, dict[str, str]]:
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = db.execute("SELECT id, title, solution, rationale FROM positions").fetchall()
    return {r[0]: {"solution": f"{r[1]}. {r[2]}", "rationale": r[3] or ""} for r in rows}


def _encoder():
    try:
        from model2vec import StaticModel
        m = StaticModel.from_pretrained(MODEL_NAME)
        return "model2vec", lambda texts: m.encode(texts)
    except Exception as e:  # noqa: BLE001
        print(f"model2vec unavailable ({e}); using hashed TF-IDF")

        def tfidf(texts):
            dim = 4096
            X = np.zeros((len(texts), dim), dtype=np.float32)
            df = np.zeros(dim)
            toks = []
            for t in texts:
                ws = re.findall(r"[a-z]{3,}", t.lower())
                grams = ws + [f"{a}_{b}" for a, b in zip(ws, ws[1:])]
                toks.append(grams)
                for g in set(grams):
                    df[hash(g) % dim] += 1
            idf = np.log((1 + len(texts)) / (1 + df)) + 1
            for i, grams in enumerate(toks):
                for g in grams:
                    X[i, hash(g) % dim] += 1
                X[i] *= idf
            return X
        return "tfidf", tfidf


def build_embeddings(force: bool = False) -> None:
    texts = _texts()
    ids = sorted(texts)
    backend, enc = _encoder()
    out = {"ids": np.array(ids), "backend": np.array(backend)}
    for f in FIELDS:
        X = np.asarray(enc([texts[i][f] or " " for i in ids]), dtype=np.float32)
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        out[f] = X / np.where(norms == 0, 1, norms)
    np.savez(EMB_PATH, **out)
    print(f"embedded {len(ids)} positions x {FIELDS} with {backend} -> {EMB_PATH.name}")


def nearest(pid: str, field: str = "solution", k: int = 20) -> list[tuple[str, float]]:
    if not EMB_PATH.exists():
        raise FileNotFoundError(EMB_PATH)
    z = np.load(EMB_PATH)
    ids = list(z["ids"])
    if pid not in ids:
        return []
    X = z[field]
    i = ids.index(pid)
    sims = X @ X[i]
    order = np.argsort(-sims)
    return [(ids[j], float(sims[j])) for j in order if j != i][:k]
