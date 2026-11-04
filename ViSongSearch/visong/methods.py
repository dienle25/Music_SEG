"""Shared machinery of the round-1 method experiments (scripts/nbest_restore.py and
scripts/learned_fusion.py).

The run files of `pipeline lexical` (work/runs/*.trec) are not part of the repository, so the lexical
lists the methods need are rebuilt here the way that stage builds them: one index per analyzer over the
lyrics (or over the metadata field), BM25 k1 and b tuned on the development queries as typed over
GRID_K1 x GRID_B, and every query of the work folder (test, dev, noise sweeps) searched to depth 100.
Runs are kept as arrays of document positions and scores and cached on disk under a fingerprint of the
corpus and query files, so a changed collection never reuses stale runs.

The query-type classifier is refitted as `pipeline lexical` fits it (logistic regression, class
weighting chosen by 5-fold CV on the training queries), and its class probabilities are kept too: every
dev query is classified by a model fitted without the dev queries of its fold (5 folds of dev
clusters), every other query by the model fitted on all examples.

Statistics, all on RR@10:
  - per query type: two-sided paired randomization (sign-flip) test and a percentile bootstrap interval
    of the mean difference. Within one type every query is about a different song (one query per
    cluster), so resampling queries is resampling songs.
  - macro (mean over types of the type means): the types share songs (Q1-Q3 are cut from the same
    fragment of a song, Q4/Q5 and their unmarked forms are about the same songs), so the test flips the
    sign of all queries of one song together and the interval resamples songs (song-level bootstrap).
  - Holm's correction over a declared family of tests.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import time
from collections import Counter

import numpy as np

from . import pipeline as P
from .corpus import read_jsonl
from .evaluate import bootstrap_ci, holm, randomization_test, target_rank
from .lexical import LexicalIndex
from .queries import UNMARKED_TYPES
from .text import clean, remove_diacritics

TYPED = list(P.TYPED)            # Q1-Q5, Q4u, Q5u: every type of the dev split
MAIN = list(P.QTYPES)            # Q1-Q5: the paper's macro average
DEPTH = 100
N_BOOT = 2000                    # bootstrap draws, as in the paper's intervals
DATA_FILES = ("corpus.jsonl", "clusters.json") + tuple(
    os.path.join("queries", f) for f in P.QUERY_FILES + ("noise_sweep.jsonl", "restored.json"))


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def fingerprint(work: str) -> str:
    """Hash of the corpus and query files: cached runs belong to exactly these inputs."""
    h = hashlib.md5()
    for f in DATA_FILES:
        path = os.path.join(work, f)
        if not os.path.exists(path):
            continue
        h.update(f.encode())
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()[:12]


def rr10(rank) -> float:
    return 0.0 if rank is None or rank > 10 else 1.0 / rank


class Work:
    """Songs, clusters and queries of a work folder, with the conventions of `pipeline lexical`."""

    def __init__(self, work: str, cache: str | None = None):
        self.work = work
        self.songs, self.cluster_of = P.load(work)
        self.ids = [s.doc_id for s in self.songs]
        self.queries = P.load_queries(work)
        self.sweep = read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
        self.dev = [q for q in self.queries if q["split"] == "dev"]
        self.test = [q for q in self.queries if q["split"] == "test"]
        self.allq = self.test + self.dev + self.sweep          # what `pipeline lexical` searches
        self.by_qid = {q["qid"]: q for q in self.allq}
        self.restored = json.load(open(os.path.join(work, "queries", "restored.json")))
        self.fingerprint = fingerprint(work)
        self.cache = None
        if cache:
            self.cache = os.path.join(cache, self.fingerprint)
            os.makedirs(self.cache, exist_ok=True)

    # ------------------------------------------------------------ cache
    def cached(self, name: str, make):
        """Value of make() kept in the cache folder (gzip pickle)."""
        if not self.cache:
            return make()
        path = os.path.join(self.cache, f"{name}.pkl.gz")
        if os.path.exists(path):
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        value = make()
        tmp = path + ".tmp"
        with gzip.open(tmp, "wb", compresslevel=3) as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        return value

    # ------------------------------------------------------------ indexes and runs
    def index(self, name: str) -> LexicalIndex:
        """bm25_<analyzer> over the lyrics, or bm25_meta (unmarked uni+bigrams over title, artist,
        singers and genre), with k1 and b tuned on the dev queries as typed (`pipeline.tune_bm25`)."""
        if name == "bm25_meta":
            ix = LexicalIndex([P.meta_text(s) for s in self.songs], self.ids, "nodiac12")
        else:
            ix = LexicalIndex([s.lyrics for s in self.songs], self.ids, name[len("bm25_"):])

        def tune():
            t0 = time.time()
            m, (k1, b) = P.tune_bm25(ix, self.dev, self.cluster_of)
            log(f"{name}: k1={k1} b={b} dev MRR@10 {m:.4f} ({time.time() - t0:.0f}s)")
            return {"k1": k1, "b": b, "dev_mrr10": m}
        prm = self.cached(f"params_{name}", tune)
        ix.set_params(prm["k1"], prm["b"])
        ix.tuned = prm
        return ix

    def params(self, run_name: str) -> dict:
        """Tuned BM25 parameters of the index a run uses (restore*_bm25_x uses bm25_x)."""
        name = run_name.split("_", 1)[1] if run_name.startswith("restore") else run_name
        return self.cached(f"params_{name}", lambda: self.index(name).tuned)

    def text_of(self, restore: str | None):
        if restore is None:
            return lambda q: q["text"]
        table = self.restored[restore]
        return lambda q: table[q["qid"]]

    def run(self, name: str, index: LexicalIndex | None = None) -> dict:
        """Top-100 of every query of allq for a run of `pipeline lexical`: bm25_<analyzer>, bm25_meta,
        or restore{all,train}_bm25_<analyzer> (the restored text of queries/restored.json)."""
        def make():
            restore, ixname = None, name
            if name.startswith("restore"):
                restore, ixname = name[len("restore"):].split("_", 1)
            ix = index or self.index(ixname)
            t0 = time.time()
            text = self.text_of(restore)
            out = {q["qid"]: topk(ix.bm25_scores(ix.query_vector(text(q)))) for q in self.allq}
            log(f"run {name}: {len(out)} queries ({time.time() - t0:.0f}s)")
            return out
        return self.cached(f"run_{name}", make)

    def ranking(self, run: dict, qid: str) -> list[tuple[str, float]]:
        e = run.get(qid)
        if e is None:
            return []
        return [(self.ids[i], float(s)) for i, s in zip(e[0], e[1])]

    def rank(self, ranking, q) -> int | None:
        return target_rank(ranking, q["cluster"], self.cluster_of)

    # ------------------------------------------------------------ query-type classifier
    def qtype(self) -> dict:
        """Predicted type and class probabilities of every query of allq (see module docstring)."""
        return self.cached("qtype_logreg", self._qtype)

    def _qtype(self) -> dict:
        from .qtype import QueryTypeClassifier, cv_macro_f1
        t0 = time.time()
        vm = {t for s in self.songs for t in clean(s.lyrics).split()}
        vp = {remove_diacritics(t) for t in vm}
        train_q = [q for q in self.queries if q["split"] == "train" and q["type"] in TYPED]
        dev_llm = [q for q in self.dev if q["type"] in ("Q4", "Q5") + UNMARKED_TYPES]
        fit_q = train_q + dev_llm
        cv = {b: cv_macro_f1([q["text"] for q in fit_q], [q["type"] for q in fit_q], [q["cluster"] for q in fit_q],
                             vm, vp, "logreg", b) for b in (False, True)}
        balanced = cv[True] > cv[False]
        log(f"qtype: CV macro-F1 plain {cv[False]:.3f} balanced {cv[True]:.3f} ({time.time() - t0:.0f}s)")

        def fit(rows):
            return QueryTypeClassifier(vm, vp, "logreg", balanced).fit([q["text"] for q in rows],
                                                                       [q["type"] for q in rows])
        pred, proba = {}, {}

        def predict(clf, rows):
            if not rows:
                return
            X = clf.features.transform([q["text"] for q in rows])
            pr = clf.model.predict_proba(X)
            order = [list(clf.model.classes_).index(t) for t in TYPED]
            for q, lab, p in zip(rows, clf.model.predict(X), pr):
                pred[q["qid"]] = str(lab)
                proba[q["qid"]] = p[order].astype(np.float32)
        predict(fit(fit_q), [q for q in self.allq if q["split"] != "dev"])
        dev_clusters = sorted({q["cluster"] for q in self.dev})
        fold_of = {c: i % 5 for i, c in enumerate(sorted(dev_clusters, key=P.hash_key))}
        for f in range(5):
            held = [q for q in self.dev if fold_of[q["cluster"]] == f]
            predict(fit(train_q + [q for q in dev_llm if fold_of[q["cluster"]] != f]), held)
        typed_dev = [q for q in self.dev if q["type"] in TYPED]
        typed_test = [q for q in self.test if q["type"] in TYPED]
        acc = {s: float(np.mean([pred[q["qid"]] == q["type"] for q in rows]))
               for s, rows in (("dev_oof", typed_dev), ("test", typed_test))}
        log(f"qtype: accuracy {acc} ({time.time() - t0:.0f}s)")
        return {"pred": pred, "proba": proba, "classes": TYPED, "balanced": balanced,
                "cv_macro_f1": {"plain": cv[False], "balanced": cv[True]}, "accuracy": acc,
                "fold_of_dev_cluster": fold_of}


def topk(scores: np.ndarray, k: int = DEPTH, positive_only: bool = True):
    """Positions and scores of the k best documents, in the order of `lexical.top_k`."""
    k = min(k, len(scores))
    if k <= 0:
        return np.zeros(0, np.int32), np.zeros(0)
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    if positive_only:
        idx = idx[scores[idx] > 0]
    return idx.astype(np.int32), scores[idx].astype(np.float64)


# ---------------------------------------------------------------- evaluation and statistics

def by_type_scores(rr: dict[str, float], queries, types) -> dict:
    """Mean RR@10 per type, query counts, the macro over MAIN and over the typed dev types."""
    per = {}
    for q in queries:
        if q["type"] in types and q["qid"] in rr:
            per.setdefault(q["type"], []).append(rr[q["qid"]])
    out = {t: float(np.mean(v)) for t, v in per.items()}
    out["n"] = {t: len(v) for t, v in per.items()}
    main = [t for t in MAIN if t in per]
    out["macro"] = float(np.mean([out[t] for t in main])) if len(main) == len(MAIN) else None
    typed = [t for t in TYPED if t in per]
    out["macro7"] = float(np.mean([out[t] for t in typed])) if len(typed) == len(TYPED) else None
    return out


def _macro_parts(diffs: dict, clusters: dict | None):
    """Per-query weights 1 / (types x queries of the type) and, if `clusters` is given, the index of
    every query's song among the songs of all types."""
    parts = {t: np.asarray(v, float) for t, v in diffs.items() if len(v)}
    if not parts:
        return None, None
    x = np.concatenate([v / (len(parts) * len(v)) for v in parts.values()])
    if clusters is None:
        return x, None
    labels = np.concatenate([np.asarray(clusters[t], dtype=object) for t in parts])
    _, idx = np.unique(labels.astype(str), return_inverse=True)
    return x, idx


def macro_randomization_test(diffs: dict[str, np.ndarray], n: int = 10000, seed: int = 0,
                             chunk: int = 500, clusters: dict[str, list] | None = None) -> float:
    """Two-sided paired sign-flip test of a macro difference: the statistic is the mean over types of
    the mean per-query difference, so every query carries weight 1 / (types x queries of its type).
    With `clusters` (type -> song cluster of each query, aligned with `diffs`), the signs of all queries
    about one song are flipped together (a song-level test): the types share songs, so their
    differences are not independent."""
    x, idx = _macro_parts(diffs, clusters)
    if x is None or not np.any(x):
        return 1.0
    if idx is not None:
        x = np.bincount(idx, weights=x)                      # one summed difference per song
    obs = abs(x.sum())
    rng = np.random.default_rng(seed)
    count, done = 0, 0
    while done < n:
        m = min(chunk, n - done)
        signs = rng.choice([-1.0, 1.0], size=(m, len(x)))
        count += int(np.sum(np.abs(signs @ x) >= obs - 1e-12))
        done += m
    return float((count + 1) / (n + 1))


def macro_bootstrap(values: dict[str, np.ndarray], clusters: dict[str, list], n: int = N_BOOT, seed: int = 0,
                    alpha: float = 0.05) -> tuple[float, float]:
    """Percentile interval of the macro (mean over types of the type means) of per-query `values`
    (a system's RR@10, or a paired difference), resampling songs with replacement (song-level
    bootstrap): a drawn song brings all of its queries, of every type."""
    types = [t for t, v in values.items() if len(v)]
    if not types:
        return (float("nan"), float("nan"))
    labels = np.concatenate([np.asarray(clusters[t], dtype=object) for t in types]).astype(str)
    uniq, inv = np.unique(labels, return_inverse=True)
    pos, at = {}, 0
    for t in types:
        pos[t] = inv[at:at + len(values[t])]
        at += len(values[t])
    rng = np.random.default_rng(seed)
    u = len(uniq)
    out = np.empty(n)
    done = 0
    while done < n:
        m = min(1000, n - done)
        draws = rng.integers(0, u, size=(m, u)) + (np.arange(m) * u)[:, None]
        counts = np.bincount(draws.ravel(), minlength=m * u).reshape(m, u).astype(float)
        means = []
        for t in types:
            c = counts[:, pos[t]]                                  # multiplicity of each query
            tot = c.sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                means.append((c @ np.asarray(values[t], float)) / tot)
        out[done:done + m] = np.nanmean(np.vstack(means), axis=0)
        done += m
    return float(np.quantile(out, alpha / 2)), float(np.quantile(out, 1 - alpha / 2))


def paired(a, b, perms: int = 10000, seed: int = 0) -> dict:
    """B against A on the same queries: means, difference B - A, 95% bootstrap interval of the
    difference, two-sided paired randomization test."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    lo, hi = bootstrap_ci(b - a, n=N_BOOT, seed=seed)
    return {"n": int(len(a)), "mean_a": float(a.mean()), "mean_b": float(b.mean()), "delta": float(b.mean() - a.mean()),
            "ci95": [lo, hi], "wins": int(np.sum(b > a)), "losses": int(np.sum(b < a)),
            "p": randomization_test(b, a, perms, seed)}


def comparison_family(rr: dict[str, dict[str, float]], comparisons, groups: dict[str, list[str]],
                      perms: int = 10000, macro_types=None, cluster_of_qid: dict[str, str] | None = None,
                      macro_name: str = "macro") -> dict:
    """Paired tests of comparisons [(label, system_a, system_b)] in every column of `groups`
    (column -> qids), and optionally of the macro over `macro_types` (a song-level test and interval
    when `cluster_of_qid` is given); Holm's correction over all tests of the family."""
    tests = {}
    for label, a, b in comparisons:
        for col, qids in groups.items():
            qs = [q for q in qids if q in rr[a] and q in rr[b]]
            if len(qs) < 2:
                continue
            tests[f"{label}|{col}"] = {"comparison": label, "system_a": a, "system_b": b, "column": col,
                                       **paired([rr[a][q] for q in qs], [rr[b][q] for q in qs], perms)}
        if macro_types:
            common = {t: [q for q in groups[t] if q in rr[a] and q in rr[b]] for t in macro_types}
            diffs = {t: np.array([rr[b][q] - rr[a][q] for q in qs]) for t, qs in common.items()}
            cl = ({t: [cluster_of_qid[q] for q in qs] for t, qs in common.items()} if cluster_of_qid else None)
            ma = float(np.mean([np.mean([rr[a][q] for q in qs]) for qs in common.values()]))
            mb = float(np.mean([np.mean([rr[b][q] for q in qs]) for qs in common.values()]))
            entry = {"comparison": label, "system_a": a, "system_b": b, "column": macro_name,
                     "types": list(macro_types), "n": int(sum(len(v) for v in diffs.values())),
                     "mean_a": ma, "mean_b": mb, "delta": mb - ma,
                     "p": macro_randomization_test(diffs, perms, clusters=cl),
                     "unit": "song" if cl else "query"}
            if cl:
                entry["n_songs"] = int(len({c for v in cl.values() for c in v}))
                entry["ci95"] = list(macro_bootstrap(diffs, cl))
            tests[f"{label}|{macro_name}"] = entry
    adj = holm({k: v["p"] for k, v in tests.items()})
    for k, v in tests.items():
        v["p_holm"] = adj[k]
        v["significant"] = bool(adj[k] < 0.05)
    return {"tests": tests, "family_size": len(tests), "perms": perms,
            "note": "two-sided paired randomization test on RR@10 (song-level sign flips for the macro), "
                    "95% percentile bootstrap interval of the difference (songs resampled for the macro), "
                    "Holm correction over this family"}


def summarize_ranks(ranks_by_sys: dict[str, dict], groups: dict[str, list[str]], cluster_of_qid: dict[str, str],
                    macros: dict[str, list[str]] | None = None, ci: bool = True) -> dict:
    """Per system and column: n, MRR@10 with its 95% bootstrap interval, S@1, S@10, R@100; and each
    macro of `macros` (name -> types) with a song-level bootstrap interval."""
    macros = macros or {}
    out = {}
    for name, ranks in ranks_by_sys.items():
        row = {}
        rr = {}
        for g, qids in groups.items():
            qs = [q for q in qids if q in ranks]
            if not qs:
                continue
            r = [ranks[q] for q in qs]
            v = np.array([rr10(x) for x in r])
            rr[g] = (qs, v)
            row[g] = {"n": len(qs), "mrr10": float(v.mean()),
                      "s1": float(np.mean([x == 1 for x in r])),
                      "s10": float(np.mean([x is not None and x <= 10 for x in r])),
                      "r100": float(np.mean([x is not None and x <= 100 for x in r]))}
            if ci:
                row[g]["ci95"] = list(bootstrap_ci(v, n=N_BOOT))
        for mname, types in macros.items():
            if all(t in rr for t in types):
                row[mname] = float(np.mean([row[t]["mrr10"] for t in types]))
                if ci:
                    row[mname + "_ci95"] = list(macro_bootstrap({t: rr[t][1] for t in types},
                                                                {t: [cluster_of_qid[q] for q in rr[t][0]] for t in types}))
        out[name] = row
    return out


def type_counts(queries) -> dict:
    return dict(sorted(Counter(q["type"] for q in queries).items()))
