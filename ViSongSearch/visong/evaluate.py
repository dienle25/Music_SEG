"""Evaluation for known-item search (SEG 9).

Each query has exactly one relevant cluster. A ranking of documents is turned
into a ranking of clusters (first copy counts), then
  RR@10     1/rank if rank <= 10 else 0   (mean = MRR@10, the main metric)
  S@k       1 if rank <= k                 (Success@1, @10)
  R@100     same as S@100 here, for first-stage recall
Significance: paired randomization (sign-flip) test and paired t-test per
query, Holm correction across comparisons, bootstrap 95% CI.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

import numpy as np
from scipy import stats


# ------------------------------------------------------------- run files

def write_trec(path: str, run: dict[str, list[tuple[str, float]]], tag: str, depth: int = 100) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for q in sorted(run):
            for r, (d, s) in enumerate(run[q][:depth], 1):
                f.write(f"{q} Q0 {d} {r} {s:.6f} {tag}\n")


def read_trec(path: str) -> dict[str, list[tuple[str, float]]]:
    run = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            q, _, d, r, s, _ = line.split()
            run[q].append((int(r), d, float(s)))
    return {q: [(d, s) for _, d, s in sorted(v)] for q, v in run.items()}


# ------------------------------------------------------------- metrics

def target_rank(ranking: list[tuple[str, float]], target_cluster: str, cluster_of: dict[str, str],
                depth: int = 100) -> int | None:
    seen = set()
    for doc, _ in ranking:
        c = cluster_of.get(doc, doc)
        if c in seen:
            continue
        seen.add(c)
        if c == target_cluster:
            return len(seen)
        if len(seen) >= depth:
            break
    return None


def per_query(run, queries, cluster_of, depth: int = 100) -> dict[str, dict[str, float]]:
    out = {}
    for q in queries:
        r = target_rank(run.get(q["qid"], []), q["cluster"], cluster_of, depth)
        out[q["qid"]] = {
            "rank": r if r is not None else math.inf,
            "rr10": (1.0 / r) if r is not None and r <= 10 else 0.0,
            "s1": 1.0 if r == 1 else 0.0,
            "s10": 1.0 if r is not None and r <= 10 else 0.0,
            "r100": 1.0 if r is not None and r <= 100 else 0.0,
        }
    return out


def summarize(pq: dict[str, dict[str, float]], qids=None) -> dict[str, float]:
    qids = list(qids if qids is not None else pq)
    if not qids:
        return {"n": 0}
    res = {"n": len(qids)}
    for m in ("rr10", "s1", "s10", "r100"):
        res[m] = float(np.mean([pq[q][m] for q in qids]))
    return res


# ------------------------------------------------------------- statistics

def randomization_test(a: np.ndarray, b: np.ndarray, n: int = 10000, seed: int = 0) -> float:
    """Two-sided paired sign-flip test on per-query differences."""
    d = np.asarray(a, float) - np.asarray(b, float)
    if not np.any(d):
        return 1.0
    obs = abs(d.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n, len(d)))
    perm = np.abs((signs * d).mean(axis=1))
    return float((np.sum(perm >= obs - 1e-12) + 1) / (n + 1))


def paired_t(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if np.allclose(a, b):
        return 1.0
    return float(stats.ttest_rel(a, b).pvalue)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values."""
    items = sorted(pvalues.items(), key=lambda x: x[1])
    m = len(items)
    adj, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adj[k] = running
    return adj


def bootstrap_ci(x, n: int = 10000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    x = np.asarray(x, float)
    if len(x) == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def kendall_tau(scores_a: dict[str, float], scores_b: dict[str, float]) -> tuple[float, float]:
    """Agreement between two orderings of the same systems (e.g. synthetic vs human queries)."""
    systems = sorted(set(scores_a) & set(scores_b))
    tau, p = stats.kendalltau([scores_a[s] for s in systems], [scores_b[s] for s in systems])
    return float(tau), float(p)


def compare(pq_a, pq_b, qids, metric: str = "rr10", n: int = 10000) -> dict[str, float]:
    a = np.array([pq_a[q][metric] for q in qids])
    b = np.array([pq_b[q][metric] for q in qids])
    return {"delta": float(a.mean() - b.mean()), "p_rand": randomization_test(a, b, n),
            "p_t": paired_t(a, b)}


def by_type(queries) -> dict[str, list[str]]:
    groups = defaultdict(list)
    for q in queries:
        groups[q["type"]].append(q["qid"])
    return dict(groups)


def sample_by_type(queries, max_per_type: int, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    groups = defaultdict(list)
    for q in queries:
        groups[q["type"]].append(q)
    out = []
    for t, qs in sorted(groups.items()):
        out.extend(qs if len(qs) <= max_per_type else rng.sample(qs, max_per_type))
    return out
