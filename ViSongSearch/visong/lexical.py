"""Lexical retrieval over an inverted index (SEG 5 indexes, SEG 7 retrieval models).

The index is a sparse document-term matrix (scipy CSC), which plays the role of
the posting lists: column t holds the documents that contain term t.

Models
- BM25 (Robertson; Lucene idf), with any analyzer (syllable, word, no-diacritic, char n-gram)
- Query likelihood with Dirichlet smoothing (Zhai & Lafferty)
- RM3 pseudo-relevance feedback on top of BM25 (Lavrenko & Croft; Abdul-Jaleel et al.)
- Multi-field BM25: weighted sum of field scores (title/artist vs lyrics)
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy import sparse

from .text import analyzer as get_analyzer


class LexicalIndex:
    def __init__(self, texts: list[str], doc_ids: list[str], analyzer: str = "syl",
                 k1: float = 0.9, b: float = 0.4):
        self.analyzer_name = analyzer
        self.analyze = get_analyzer(analyzer)
        self.doc_ids = list(doc_ids)
        self.pos = {d: i for i, d in enumerate(self.doc_ids)}
        vocab: dict[str, int] = {}
        rows, cols, vals = [], [], []
        lengths = np.zeros(len(texts), dtype=np.float64)
        for i, t in enumerate(texts):
            counts = Counter(self.analyze(t))
            lengths[i] = sum(counts.values())
            for term, c in counts.items():
                j = vocab.setdefault(term, len(vocab))
                rows.append(i)
                cols.append(j)
                vals.append(c)
        self.vocab = vocab
        self.tf = sparse.csc_matrix((np.array(vals, dtype=np.float64), (rows, cols)),
                                    shape=(len(texts), len(vocab)))
        self.dl = lengths
        self.avgdl = float(lengths.mean()) if len(lengths) else 0.0
        self.N = len(texts)
        self.df = np.diff(self.tf.indptr).astype(np.float64)
        self.cf = np.asarray(self.tf.sum(axis=0)).ravel()
        self.total_terms = float(self.cf.sum())
        self.idf = np.log(1.0 + (self.N - self.df + 0.5) / (self.df + 0.5))
        self.set_params(k1, b)

    # ------------------------------------------------------------ BM25
    def set_params(self, k1: float, b: float) -> None:
        self.k1, self.b = k1, b
        tf = self.tf.tocoo()
        norm = k1 * (1.0 - b + b * self.dl[tf.row] / max(self.avgdl, 1e-9))
        w = self.idf[tf.col] * tf.data * (k1 + 1.0) / (tf.data + norm)
        self.w = sparse.csc_matrix((w, (tf.row, tf.col)), shape=self.tf.shape)

    def query_vector(self, query: str) -> dict[int, float]:
        counts = Counter(self.analyze(query))
        return {self.vocab[t]: float(c) for t, c in counts.items() if t in self.vocab}

    def bm25_scores(self, qvec: dict[int, float]) -> np.ndarray:
        if not qvec:
            return np.zeros(self.N)
        cols = np.fromiter(qvec.keys(), dtype=np.int64)
        weights = np.fromiter(qvec.values(), dtype=np.float64)
        return np.asarray(self.w[:, cols] @ weights).ravel()

    # ------------------------------------------------------------ QL
    def ql_scores(self, qvec: dict[int, float], mu: float = 1000.0) -> np.ndarray:
        if not qvec:
            return np.zeros(self.N)
        cols = np.fromiter(qvec.keys(), dtype=np.int64)
        qtf = np.fromiter(qvec.values(), dtype=np.float64)
        p = self.cf[cols] / self.total_terms
        base = float((qtf * np.log(mu * p)).sum()) - qtf.sum() * np.log(self.dl + mu)
        sub = self.tf[:, cols].tocoo()
        extra = np.zeros(self.N)
        np.add.at(extra, sub.row, qtf[sub.col] * np.log1p(sub.data / (mu * p[sub.col])))
        return base + extra

    # ------------------------------------------------------------ RM3
    def rm3_vector(self, qvec: dict[int, float], fb_docs: int = 10, fb_terms: int = 20,
                   orig_weight: float = 0.5, mu: float = 1000.0) -> dict[int, float]:
        if not qvec:
            return qvec
        ql = self.ql_scores(qvec, mu)
        top = np.argsort(-ql)[:fb_docs]
        doc_w = np.exp(ql[top] - ql[top].max())
        doc_w /= doc_w.sum()
        tf_rows = self.tf.tocsr()[top]
        rel = np.zeros(self.tf.shape[1])
        for i in range(len(top)):
            row = tf_rows.getrow(i)
            rel[row.indices] += doc_w[i] * row.data / max(self.dl[top[i]], 1.0)
        best = np.argsort(-rel)[:fb_terms]
        rel_sum = rel[best].sum() or 1.0
        qsum = sum(qvec.values())
        new: dict[int, float] = {t: orig_weight * c / qsum for t, c in qvec.items()}
        for t in best:
            if rel[t] > 0:
                new[int(t)] = new.get(int(t), 0.0) + (1 - orig_weight) * rel[t] / rel_sum
        return new

    # ------------------------------------------------------------ search
    def scores(self, query: str, model: str = "bm25", **kw) -> np.ndarray:
        qvec = self.query_vector(query)
        if model == "bm25":
            return self.bm25_scores(qvec)
        if model == "ql":
            return self.ql_scores(qvec, kw.get("mu", 1000.0))
        if model == "rm3":
            return self.bm25_scores(self.rm3_vector(qvec, **{k: v for k, v in kw.items()
                                                            if k in ("fb_docs", "fb_terms", "orig_weight", "mu")}))
        raise ValueError(model)

    def search(self, query: str, k: int = 100, model: str = "bm25", **kw) -> list[tuple[str, float]]:
        # BM25 gives 0 to documents that share no term with the query: they are not retrieved.
        positive_only = model in ("bm25", "rm3")
        return top_k(self.scores(query, model, **kw), self.doc_ids, k, positive_only)


def top_k(scores: np.ndarray, doc_ids: list[str], k: int, positive_only: bool = False) -> list[tuple[str, float]]:
    if len(scores) == 0:
        return []
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    if positive_only:
        return [(doc_ids[i], float(scores[i])) for i in idx if scores[i] > 0]
    return [(doc_ids[i], float(scores[i])) for i in idx if scores[i] > -math.inf]


class MultiFieldBM25:
    """Score = sum over fields of weight * BM25(field). Fields share doc order."""

    def __init__(self, fields: dict[str, list[str]], doc_ids: list[str], weights: dict[str, float],
                 analyzer: str = "nodiac", params: dict[str, tuple[float, float]] | None = None):
        params = params or {}
        self.indexes = {f: LexicalIndex(t, doc_ids, analyzer, *params.get(f, (0.9, 0.4)))
                        for f, t in fields.items()}
        self.weights = weights
        self.doc_ids = list(doc_ids)

    def scores(self, query: str) -> np.ndarray:
        return sum(self.weights[f] * ix.scores(query) for f, ix in self.indexes.items())

    def search(self, query: str, k: int = 100) -> list[tuple[str, float]]:
        return top_k(self.scores(query), self.doc_ids, k, positive_only=True)
