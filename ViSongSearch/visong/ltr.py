"""Learning-to-rank fusion of ranked lists (round-1 method).

The candidates of a query are the songs in the top `depth` (100) of any of its lists, so a query has at
most depth x lists candidates. A (query, song) pair is described by
  - for every list: whether the song is in it, log of its rank (depth + 1 if absent), its RRF term
    1 / (60 + rank) (0 if absent), its score, the score divided by the list's top score, and the score
    min-max normalised over the list (0 if absent);
  - for the query: the predicted probabilities of the seven query types, its length in syllables, the
    share of its syllables that carry marks, and for every list the top score and the relative margin
    (s1 - s2) / s1 between its first two songs.
Linear models also see the products of the list terms (presence, RRF term, both normalised scores)
with the type probabilities, so that they can weight the lists differently for every predicted type,
as type-adaptive RRF does; trees find such interactions themselves.

A candidate is relevant when it belongs to the target cluster. LightGBM, whose lambdarank objective
would be the first choice, is not installed in our environment, and scikit-learn has no listwise or
pairwise tree learner, so the models are:
  logreg    pointwise logistic regression on standardised features
  pairwise  logistic regression without intercept on differences between a relevant and a
            non-relevant candidate of the same query (RankNet with a linear scorer); the negatives
            are the `n_hard` best non-relevant candidates by equal-weight RRF and `n_rand` random ones
  gbt       sklearn HistGradientBoostingClassifier (pointwise)
Training uses only queries whose target is among the candidates (the others carry no ranking signal).
Pointwise models are fitted on every relevant candidate and at most `neg_cap` non-relevant ones per
query (`training_rows`: the hardest by their best rank in any list, plus random ones); every candidate
is scored at test time. With `balance`, every query is weighted by 1 / (queries of its type), so that
each type counts equally, as in the macro average; types are known for training queries only, never
used at test time.

`CachedRRF` reproduces the RRF weight tuning of `pipeline.fuse` (fusion.WeightTuner) from per-query
results computed once, so that tuning on many subsets of the queries (cross-validation) is cheap.
"""
from __future__ import annotations

import numpy as np

from .text import clean, has_diacritics

RUN_FEATS = ("in", "logrank", "rr60", "score", "ntop", "minmax")
INTERACT = ("in", "rr60", "ntop", "minmax")
N_TYPES = 7


def query_features(text: str, proba, lists) -> np.ndarray:
    """Type probabilities, length, share of marked syllables, top score and margin of every list."""
    toks = clean(text).split()
    n = len(toks)
    marked = sum(has_diacritics(t) for t in toks) / max(1, n)
    tops, margins = [], []
    for _, sc in lists:
        top = float(sc[0]) if len(sc) else 0.0
        second = float(sc[1]) if len(sc) > 1 else 0.0
        tops.append(top)
        margins.append((top - second) / top if top > 0 else 0.0)
    return np.concatenate([np.asarray(proba, float), [float(n), marked], tops, margins])


def candidate_block(lists, depth: int = 100):
    """Candidate positions (sorted) and their per-list features, len(lists) * len(RUN_FEATS) columns.
    `lists` holds (positions, scores) arrays in rank order, one pair per list."""
    pools = [np.asarray(pos[:depth]) for pos, _ in lists]
    cand = np.unique(np.concatenate(pools)) if any(len(p) for p in pools) else np.zeros(0, np.int64)
    m = len(cand)
    cols = []
    for pos, sc in lists:
        pos, sc = np.asarray(pos[:depth]), np.asarray(sc[:depth], float)
        rank = np.full(m, depth + 1.0)
        score = np.zeros(m)
        if len(pos):
            at = np.searchsorted(cand, pos)
            rank[at] = np.arange(1, len(pos) + 1)
            score[at] = sc
        inn = (rank <= depth).astype(float)
        top = float(sc[0]) if len(sc) else 0.0
        lo = float(sc[-1]) if len(sc) else 0.0
        ntop = score / top if top > 0 else np.zeros(m)
        span = top - lo
        mm = np.where(inn > 0, (score - lo) / span if span > 0 else 1.0, 0.0)
        cols += [inn, np.log(rank), np.where(inn > 0, 1.0 / (60.0 + rank), 0.0), score, ntop, mm]
    X = np.column_stack(cols) if m else np.zeros((0, len(lists) * len(RUN_FEATS)))
    return cand, X


def block(lists, qfeat, depth: int = 100):
    """Candidates and full feature rows (list features, then the query features repeated)."""
    cand, X = candidate_block(lists, depth)
    Q = np.repeat(np.asarray(qfeat, float)[None, :], len(cand), axis=0)
    return cand, np.hstack([X, Q])


def n_columns(n_runs: int) -> int:
    return n_runs * len(RUN_FEATS) + N_TYPES + 2 + 2 * n_runs


def query_columns(n_runs: int) -> list[int]:
    """Columns of the query-level features (for the ablation without them)."""
    return list(range(n_runs * len(RUN_FEATS), n_columns(n_runs)))


def feature_names(run_names) -> list[str]:
    names = [f"{r}:{f}" for r in run_names for f in RUN_FEATS]
    names += [f"p_{t}" for t in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q4u", "Q5u")] + ["length", "marked_share"]
    names += [f"{r}:top" for r in run_names] + [f"{r}:margin" for r in run_names]
    return names


def interaction_names(run_names) -> list[str]:
    types = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q4u", "Q5u")
    return [f"{r}:{f}*p_{t}" for r in run_names for f in INTERACT for t in types]


def with_interactions(X: np.ndarray, n_runs: int) -> np.ndarray:
    """Append the products of the list terms in INTERACT with the type probabilities."""
    per = len(RUN_FEATS)
    sel = [r * per + RUN_FEATS.index(f) for r in range(n_runs) for f in INTERACT]
    P = X[:, n_runs * per: n_runs * per + N_TYPES]
    inter = (X[:, sel][:, :, None] * P[:, None, :]).reshape(len(X), -1)
    return np.hstack([X, inter])


class Dataset:
    """Feature rows of many queries, stacked; `offsets[i]:offsets[i + 1]` are the rows of query i."""

    def __init__(self, qids, cands, Xs, ys):
        self.qids = list(qids)
        self.offsets = np.cumsum([0] + [len(c) for c in cands])
        self.cand = np.concatenate(cands) if cands else np.zeros(0, np.int64)
        width = Xs[0].shape[1] if Xs else 0
        self.X = np.vstack(Xs).astype(np.float32) if Xs else np.zeros((0, width), np.float32)
        self.y = np.concatenate(ys).astype(np.int8) if ys else np.zeros(0, np.int8)

    def rows(self, qidx) -> np.ndarray:
        parts = [np.arange(self.offsets[i], self.offsets[i + 1]) for i in qidx]
        return np.concatenate(parts) if parts else np.zeros(0, np.int64)

    def has_target(self, i: int) -> bool:
        return bool(self.y[self.offsets[i]:self.offsets[i + 1]].any())

    def query_of_row(self) -> np.ndarray:
        return np.repeat(np.arange(len(self.qids)), np.diff(self.offsets))


def rrf_terms(X: np.ndarray, n_runs: int) -> np.ndarray:
    """Equal-weight RRF score of every row (sum of the lists' 1 / (60 + rank) terms)."""
    cols = [r * len(RUN_FEATS) + RUN_FEATS.index("rr60") for r in range(n_runs)]
    return np.asarray(X)[:, cols].sum(axis=1)


def best_rank_terms(X: np.ndarray, n_runs: int) -> np.ndarray:
    """Largest 1 / (60 + rank) of every row over the lists, i.e. its best rank in any list."""
    cols = [r * len(RUN_FEATS) + RUN_FEATS.index("rr60") for r in range(n_runs)]
    return np.asarray(X)[:, cols].max(axis=1)


def training_rows(ds: Dataset, qidx, n_runs: int, neg_cap: int | None = None, n_rand: int = 20, seed: int = 0):
    """Rows of the queries `qidx` that a pointwise model is fitted on, and their query (position in
    `qidx`). Queries whose target is not among the candidates are left out. With `neg_cap`, a query
    keeps its relevant candidates, its `neg_cap - n_rand` hardest non-relevant ones (best rank in any
    list, then equal-weight RRF) and `n_rand` random other ones."""
    rng = np.random.default_rng(seed)
    rows, owner = [], []
    for j, i in enumerate(qidx):
        lo, hi = int(ds.offsets[i]), int(ds.offsets[i + 1])
        y = ds.y[lo:hi]
        if not y.any():
            continue
        if neg_cap is None or (hi - lo) - int(y.sum()) <= neg_cap:
            r = np.arange(lo, hi)
        else:
            Xq = ds.X[lo:hi]
            hard = best_rank_terms(Xq, n_runs) + 1e-3 * rrf_terms(Xq, n_runs)
            neg = np.flatnonzero(y == 0)
            order = neg[np.argsort(-hard[neg], kind="stable")]
            n_hard = max(0, neg_cap - n_rand)
            rest = order[n_hard:]
            rand = rng.choice(rest, size=min(n_rand, len(rest)), replace=False) if len(rest) else rest
            r = lo + np.sort(np.concatenate([np.flatnonzero(y == 1), order[:n_hard], rand]))
        rows.append(r)
        owner.append(np.full(len(r), j))
    if not rows:
        raise ValueError("no training query has its target among the candidates")
    return np.concatenate(rows), np.concatenate(owner)


class LTRModel:
    """One of the models of the module docstring; `score` gives larger values to better candidates."""

    def __init__(self, kind: str, n_runs: int, C: float = 1.0, balance: bool = False, gbt: dict | None = None,
                 n_hard: int = 30, n_rand: int = 20, neg_cap: int | None = None, drop_query: bool = False,
                 seed: int = 0):
        self.kind, self.n_runs, self.C, self.balance = kind, n_runs, C, balance
        self.gbt = gbt or {}
        self.n_hard, self.n_rand, self.seed = n_hard, n_rand, seed
        self.neg_cap = neg_cap
        self.drop_query = drop_query

    # ------------------------------------------------------------ features
    def design(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, np.float32)               # sklearn's lbfgs and the trees accept float32
        if self.drop_query:
            drop = set(query_columns(self.n_runs))
            return X[:, [j for j in range(X.shape[1]) if j not in drop]]
        if self.kind in ("logreg", "pairwise"):
            return with_interactions(X, self.n_runs)
        return X

    def query_weights(self, types) -> np.ndarray:
        if not self.balance:
            return np.ones(len(types))
        from collections import Counter
        n = Counter(types)
        w = np.array([1.0 / n[t] for t in types])
        return w * len(types) / w.sum()

    # ------------------------------------------------------------ fit
    def fit(self, ds: Dataset, qidx, types):
        """Fit on the queries `qidx` of `ds`, whose true types are `types`."""
        qidx = list(qidx)
        qw = self.query_weights(types)
        if self.kind == "pairwise":
            keep = [j for j, i in enumerate(qidx) if ds.has_target(i)]
            rows = ds.rows([qidx[j] for j in keep])
            X = self.design(ds.X[rows])
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            self.scaler = StandardScaler().fit(X)
            Z = self.scaler.transform(X)
            lens = [int(ds.offsets[qidx[j] + 1] - ds.offsets[qidx[j]]) for j in keep]
            D, lab, w = pairs(Z, ds.y[rows], lens, qw[keep], rrf_terms(ds.X[rows], self.n_runs),
                              self.n_hard, self.n_rand, self.seed)
            self.model = LogisticRegression(C=self.C, fit_intercept=False, max_iter=3000)
            self.model.fit(D, lab, sample_weight=w)
            return self
        rows, owner = training_rows(ds, qidx, self.n_runs, self.neg_cap, self.n_rand, self.seed)
        X = self.design(ds.X[rows])
        y = ds.y[rows]
        roww = qw[owner]
        if self.kind == "gbt":
            from sklearn.ensemble import HistGradientBoostingClassifier
            self.model = HistGradientBoostingClassifier(
                learning_rate=self.gbt.get("learning_rate", 0.1), max_iter=self.gbt.get("max_iter", 200),
                max_leaf_nodes=self.gbt.get("max_leaf_nodes", 31), min_samples_leaf=self.gbt.get("min_samples_leaf", 40),
                l2_regularization=self.gbt.get("l2_regularization", 1.0), early_stopping=False, random_state=self.seed)
            self.model.fit(X, y, sample_weight=roww)
            return self
        if self.kind != "logreg":
            raise ValueError(self.kind)
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        self.scaler = StandardScaler().fit(X)
        self.model = LogisticRegression(C=self.C, max_iter=3000)
        self.model.fit(self.scaler.transform(X), y, sample_weight=roww)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        Z = self.design(X)
        if self.kind == "gbt":
            return self.model.decision_function(Z)
        Z = self.scaler.transform(Z)
        if self.kind == "pairwise":
            return Z @ self.model.coef_.ravel()
        return self.model.decision_function(Z)


def pairs(Z, y, lens, qw, hardness, n_hard: int = 30, n_rand: int = 20, seed: int = 0):
    """Difference vectors between the relevant and the non-relevant candidates of each query (labelled
    1; half of them reversed and labelled 0). The rows of the queries are stacked in Z, `lens[i]` rows
    for query i; the negatives are the `n_hard` with the largest `hardness` (equal-weight RRF score)
    and `n_rand` random others. The pairs of a query share its weight qw[i]."""
    rng = np.random.default_rng(seed)
    D, lab, w = [], [], []
    at = 0
    for i, n in enumerate(lens):
        z, yy, hd = Z[at:at + n], y[at:at + n], hardness[at:at + n]
        at += n
        pos = np.flatnonzero(yy == 1)
        neg = np.flatnonzero(yy == 0)
        if not len(pos) or not len(neg):
            continue
        order = neg[np.argsort(-hd[neg], kind="stable")]
        hard = order[:n_hard]
        rest = order[n_hard:]
        rand = rng.choice(rest, size=min(n_rand, len(rest)), replace=False) if len(rest) else rest
        chosen = np.concatenate([hard, rand])
        for p in pos:
            diff = z[p][None, :] - z[chosen]
            flip = rng.random(len(chosen)) < 0.5
            diff[flip] *= -1
            D.append(diff)
            lab.append(np.where(flip, 0, 1))
            w.append(np.full(len(chosen), qw[i] / (len(chosen) * len(pos))))
    if not D:
        raise ValueError("no training pairs")
    return np.vstack(D), np.concatenate(lab), np.concatenate(w) * len(qw)


def rank_candidates(scores, cand, ids, tiebreak=None) -> list[tuple[str, float]]:
    """Ranking of a query's candidates by model score. Trees give many candidates the same score, so
    ties are broken by `tiebreak` (larger first; the equal-weight RRF score), then by document id."""
    if len(cand) == 0:
        return []
    doc = np.array([ids[i] for i in cand])
    scores = np.asarray(scores, float)
    tb = np.zeros(len(cand)) if tiebreak is None else np.asarray(tiebreak, float)
    by_id = np.argsort(np.argsort(doc, kind="stable"), kind="stable")     # rank of each document id
    order = np.lexsort((by_id, -tb, -scores))                            # last key sorts first
    return [(str(doc[j]), float(scores[j])) for j in order]


# ---------------------------------------------------------------- RRF baselines

class CachedRRF:
    """RRF weight tuning of `pipeline.fuse` from per-query results computed once.

    For every query and every weight vector of `grid`, keeps the query's term of WeightTuner.mrr (the
    approximate MRR@10, ties counted against the target) and of WeightTuner.exact_mrr (rrf, then
    target_rank). `subset(qids)` then behaves like WeightTuner(lists of qids): its mrr, exact_mrr and
    best sum these terms in the order of `qids`, as WeightTuner does, so the selected weights are
    identical (checked in the tests and at run time). Only the vectors of `grid` can be scored."""

    def __init__(self, lists_by_qid: dict, target: dict, cluster_of: dict, grid, k: int = 60, depth: int = 100):
        from .evaluate import target_rank
        from .fusion import WeightTuner, rrf
        self.grid = [tuple(g) for g in grid]
        self.col = {g: j for j, g in enumerate(self.grid)}
        qids = list(lists_by_qid)
        self.row = {q: i for i, q in enumerate(qids)}
        tuner = WeightTuner(lists_by_qid, target, cluster_of, k, depth)
        w = np.asarray(self.grid, np.float32)
        G = len(self.grid)
        self.A = np.zeros((len(qids), G))              # approximate RR@10 (WeightTuner.mrr's terms)
        self.E = np.zeros((len(qids), G))              # exact RR@10 (WeightTuner.exact_mrr's terms)
        for i, (qid, item) in enumerate(zip(qids, tuner.items)):
            if item is not None and item[2] >= 0:
                m, starts, t = item
                s = m @ w.T
                best = np.maximum.reduceat(s, starts, axis=0)
                ts = best[t]
                rank = (best >= ts).sum(axis=0)
                self.A[i] = np.where((ts > 0) & (rank <= 10), 1.0 / rank, 0.0)
            lists = lists_by_qid[qid]
            for j, g in enumerate(self.grid):
                r = target_rank(rrf(lists, k, list(g), depth), target[qid], cluster_of)
                self.E[i, j] = 1.0 / r if r is not None and r <= 10 else 0.0

    def subset(self, qids) -> "_RRFSubset":
        return _RRFSubset(self, [self.row[q] for q in qids])


class _RRFSubset:
    def __init__(self, cache: CachedRRF, rows):
        self.c, self.rows = cache, rows

    def mrr(self, weights) -> np.ndarray:
        total = np.zeros(len(self.c.grid))
        for i in self.rows:
            total += self.c.A[i]
        total = total / max(1, len(self.rows))
        return total[[self.c.col[tuple(g)] for g in weights]]

    def exact_mrr(self, weights) -> float:
        j = self.c.col[tuple(weights)]
        tot = 0.0
        for i in self.rows:
            tot += float(self.c.E[i, j])
        return tot / max(1, len(self.rows))

    def best(self, grid, shortlist: int = 25):
        """As fusion.WeightTuner.best."""
        grid = [tuple(g) for g in grid]
        approx = self.mrr(grid)
        order = np.argsort(-approx, kind="stable")[:shortlist]
        scored = [(self.exact_mrr(grid[i]), -sum(abs(x - 1) for x in grid[i]), grid[i]) for i in order]
        top = max(scored, key=lambda x: (round(x[0], 10), x[1]))
        return top[2], top[0]
