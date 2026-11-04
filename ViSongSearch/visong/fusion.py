"""Combining ranked lists (SEG 12: combining evidence).

Reciprocal Rank Fusion (Cormack et al., SIGIR 2009) only uses ranks, so BM25
scores and cosine similarities never need to be put on the same scale:
    RRF(d) = sum_r w_r / (k + rank_r(d)),  k = 60
"""
from __future__ import annotations

from collections import defaultdict


def rrf(runs: list[list[tuple[str, float]]], k: int = 60, weights: list[float] | None = None,
        depth: int = 100) -> list[tuple[str, float]]:
    weights = weights or [1.0] * len(runs)
    score = defaultdict(float)
    for w, run in zip(weights, runs):
        if w == 0:
            continue
        for rank, (doc, _) in enumerate(run[:depth], start=1):
            score[doc] += w / (k + rank)
    return sorted(score.items(), key=lambda x: (-x[1], x[0]))


def combsum(runs: list[list[tuple[str, float]]], weights: list[float] | None = None,
            depth: int = 100) -> list[tuple[str, float]]:
    """Min-max normalised score sum (the 'linear' baseline)."""
    weights = weights or [1.0] * len(runs)
    score = defaultdict(float)
    for w, run in zip(weights, runs):
        run = run[:depth]
        if not run:
            continue
        hi, lo = run[0][1], run[-1][1]
        span = (hi - lo) or 1.0
        for doc, s in run:
            score[doc] += w * (s - lo) / span
    return sorted(score.items(), key=lambda x: (-x[1], x[0]))


def fuse_runs(named_runs: dict[str, dict[str, list[tuple[str, float]]]], names: list[str],
              weights: list[float] | None = None, method: str = "rrf", k: int = 60,
              depth: int = 100) -> dict[str, list[tuple[str, float]]]:
    """Fuse whole run files: named_runs[system][qid] -> ranking."""
    qids = set().union(*(named_runs[n].keys() for n in names))
    out = {}
    for q in qids:
        lists = [named_runs[n].get(q, []) for n in names]
        out[q] = rrf(lists, k, weights, depth) if method == "rrf" else combsum(lists, weights, depth)
    return out


class WeightTuner:
    """Scores many RRF weight vectors at once on a fixed set of queries.

    For each query we keep a (documents x lists) matrix of 1 / (k + rank). Fused
    scores for every weight vector are one matrix product; the rank of the target
    song cluster is counted over clusters, as in evaluate.target_rank. Ties are
    counted against the target, so the result is a slight lower bound; `best`
    re-checks the shortlist with the exact fusion.
    """

    def __init__(self, lists_by_query: dict[str, list[list[tuple[str, float]]]], target: dict[str, str],
                 cluster_of: dict[str, str], k: int = 60, depth: int = 100):
        import numpy as np
        self.k, self.depth = k, depth
        self.lists, self.target, self.cluster_of = lists_by_query, target, cluster_of
        self.items = []
        for qid, lists in lists_by_query.items():
            docs = sorted({d for run in lists for d, _ in run[:depth]}, key=lambda d: (cluster_of.get(d, d), d))
            if not docs:
                self.items.append(None)
                continue
            pos = {d: i for i, d in enumerate(docs)}
            m = np.zeros((len(docs), len(lists)), np.float32)
            for j, run in enumerate(lists):
                for r, (d, _) in enumerate(run[:depth], start=1):
                    m[pos[d], j] = 1.0 / (k + r)
            cl = [cluster_of.get(d, d) for d in docs]
            starts = np.flatnonzero(np.r_[True, np.array(cl[1:]) != np.array(cl[:-1])])
            clusters = [cl[i] for i in starts]
            t = clusters.index(target[qid]) if target[qid] in clusters else -1
            self.items.append((m, starts, t))

    def mrr(self, weights) -> "np.ndarray":
        import numpy as np
        w = np.asarray(weights, np.float32)                     # (G, lists)
        total = np.zeros(len(w))
        for item in self.items:
            if item is None or item[2] < 0:
                continue
            m, starts, t = item
            s = m @ w.T                                         # (docs, G)
            best = np.maximum.reduceat(s, starts, axis=0)       # (clusters, G)
            ts = best[t]
            ahead = (best >= ts).sum(axis=0) - 1                # clusters at or above the target
            rank = ahead + 1
            ok = (ts > 0) & (rank <= 10)
            total += np.where(ok, 1.0 / rank, 0.0)
        return total / max(1, len(self.items))

    def exact_mrr(self, weights) -> float:
        from .evaluate import target_rank
        tot = 0.0
        for qid, lists in self.lists.items():
            r = target_rank(rrf(lists, self.k, list(weights), self.depth), self.target[qid], self.cluster_of)
            tot += 1.0 / r if r is not None and r <= 10 else 0.0
        return tot / max(1, len(self.lists))

    def best(self, grid, shortlist: int = 25):
        """Weight vector with the best exact MRR@10; ties go to the most uniform vector."""
        import numpy as np
        grid = [tuple(g) for g in grid]
        approx = self.mrr(grid)
        order = np.argsort(-approx, kind="stable")[:shortlist]
        n = len(grid[0])
        scored = [(self.exact_mrr(grid[i]), -sum(abs(x - 1) for x in grid[i]), grid[i]) for i in order]
        top = max(scored, key=lambda x: (round(x[0], 10), x[1]))
        return top[2], top[0]


def weight_grid(n_lists: int, values=(0, 0.5, 1, 2)):
    from itertools import product
    return [w for w in product(values, repeat=n_lists) if any(w)]
