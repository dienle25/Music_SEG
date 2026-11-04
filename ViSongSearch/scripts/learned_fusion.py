"""Learning-to-rank fusion of the lexical lists (round-1 method).

    python3 scripts/learned_fusion.py work_full [--cache DIR] [--jobs 2] [--perms 10000] [--quick N]

Lists: the four of the paper's lexical fusion (Restore_all + syllable uni+bigrams, unmarked
uni+bigrams, character 3-grams, metadata field), rebuilt as `pipeline lexical` builds them
(visong/methods.py). No dense run exists in this environment (the run files are not in the repository
and there is no GPU to recompute them), so this fuses lexical lists only; the paper's hybrids also
have a dense list, which is where type-dependent weights paid off.

Baselines, made as `pipeline.fuse` makes them: RRF with equal weights, one weight vector over
{0, 0.5, 1, 2}^4 tuned on all dev queries pooled (global), one vector per predicted query type, kept
only where it beats the global vector in 5-fold CV on the dev queries of that type (adaptive), and per
true type (oracle).

LTR (visong/ltr.py): features of every candidate song from every list (presence, rank, RRF term, score,
two normalised scores) and of the query (type probabilities of the classifier, out of fold for dev
queries; length; share of marked syllables; top score and margin of every list). Models: pointwise
logistic regression, pairwise logistic regression and sklearn gradient-boosted trees, each over a small
grid, with and without type-balanced query weights (LightGBM lambdarank is not installed). Model and
hyperparameters are chosen by 5-fold cross-validation over dev clusters (the classifier's folds, so
all queries about one song stay in one fold); criterion: the out-of-fold dev macro MRR@10 over the
seven typed types. The chosen models are refitted on all dev queries and applied once to the test
queries. RRF global and adaptive are refitted per fold too, for an out-of-fold dev estimate comparable
with the LTR's; their test runs use the weights tuned on all dev queries, as in the paper.

Writes WORK/results/method_ltr.json (and per-query ranks in method_ltr_ranks.json.gz).
"""
from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import os
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import ltr as L                                                       # noqa: E402
from visong import methods as M                                                   # noqa: E402
from visong import pipeline as P                                                  # noqa: E402
from visong.fusion import WeightTuner, rrf, weight_grid                           # noqa: E402

COMPS = ["restoreall_bm25_syl12", "bm25_nodiac12", "bm25_char3", "bm25_meta"]
RRF_VALUES = (0, 0.5, 1, 2)
DEPTH = 100
GBT_GRID = [{"max_iter": 100, "learning_rate": 0.1, "max_leaf_nodes": 15},
            {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 15},
            {"max_iter": 200, "learning_rate": 0.1, "max_leaf_nodes": 31}]
C_GRID = (0.01, 0.1, 1.0)


def ltr_configs():
    out = []
    for kind in ("logreg", "pairwise"):
        for C in C_GRID:
            for bal in (False, True):
                out.append({"kind": kind, "C": C, "balance": bal})
    for i, g in enumerate(GBT_GRID):
        for bal in (False, True):
            out.append({"kind": "gbt", "gbt": g, "gbt_id": i, "balance": bal})
    return out


def cfg_name(c) -> str:
    tail = ("_noquery" if c.get("drop_query") else "")
    if c["kind"] == "gbt":
        g = c["gbt"]
        return f"gbt_it{g['max_iter']}_lr{g['learning_rate']:g}_lv{g['max_leaf_nodes']}_bal{int(c['balance'])}" + tail
    return f"{c['kind']}_C{c['C']:g}_bal{int(c['balance'])}" + tail


def make_model(c) -> L.LTRModel:
    return L.LTRModel(c["kind"], len(COMPS), C=c.get("C", 1.0), balance=c["balance"], gbt=c.get("gbt"),
                      drop_query=c.get("drop_query", False))


# ---------------------------------------------------------------- shared state for forked workers

_G: dict = {}


def _one_thread():
    from threadpoolctl import threadpool_limits
    _G["threads"] = threadpool_limits(1)


def blocks_for(queries):
    """Candidates, feature rows and labels of the given queries."""
    W, runs, proba = _G["W"], _G["runs"], _G["proba"]
    qids, cands, Xs, ys, tbs = [], [], [], [], []
    for q in queries:
        lists = [runs[c].get(q["qid"], (np.zeros(0, np.int32), np.zeros(0))) for c in COMPS]
        qf = L.query_features(q["text"], proba[q["qid"]], lists)
        cand, X = L.block(lists, qf, DEPTH)
        cl = [W.cluster_of.get(W.ids[i], W.ids[i]) for i in cand]
        qids.append(q["qid"])
        cands.append(cand)
        Xs.append(X)
        ys.append(np.array([c == q["cluster"] for c in cl], np.int8))
    return L.Dataset(qids, cands, Xs, ys)


def ranks_from_scores(ds: L.Dataset, idx, scores, queries) -> dict:
    W = _G["W"]
    out, at = {}, 0
    for i in idx:
        lo, hi = ds.offsets[i], ds.offsets[i + 1]
        s = scores[at:at + hi - lo]
        at += hi - lo
        tb = L.rrf_terms(ds.X[lo:hi], len(COMPS))
        ranking = L.rank_candidates(s, ds.cand[lo:hi], W.ids, tiebreak=tb)
        out[ds.qids[i]] = W.rank(ranking, queries[i])
    return out


def _cv_task(args):
    """Fit config c without fold f of the dev set and rank the queries of fold f."""
    c, f = args
    ds, dev, fold = _G["dev_ds"], _G["dev"], _G["fold"]
    tr = [i for i, q in enumerate(dev) if fold[i] != f]
    te = [i for i, q in enumerate(dev) if fold[i] == f]
    t0 = time.time()
    model = make_model(c).fit(ds, tr, [dev[i]["type"] for i in tr])
    scores = model.score(ds.X[ds.rows(te)])
    return cfg_name(c), f, ranks_from_scores(ds, te, scores, dev), time.time() - t0


def _rrf_fold_task(f):
    """RRF global and adaptive weights tuned without fold f; ranks of the queries of fold f."""
    dev, fold = _G["dev"], _G["fold"]
    train = [q for i, q in enumerate(dev) if fold[i] != f]
    held = [q for i, q in enumerate(dev) if fold[i] == f]
    w_g, per = fit_rrf(train)
    pred = _G["pred"]
    g = {q["qid"]: rank_rrf(q, w_g) for q in held}
    a = {q["qid"]: rank_rrf(q, per.get(pred[q["qid"]], {}).get("weights", w_g)) for q in held}
    return f, g, a, w_g, {t: v["weights"] for t, v in per.items()}


# ---------------------------------------------------------------- RRF baselines (pipeline.fuse)

def lists_of(q):
    W, runs = _G["W"], _G["runs"]
    return [W.ranking(runs[c], q["qid"]) for c in COMPS]


def tuner(qs):
    return WeightTuner({q["qid"]: lists_of(q) for q in qs}, {q["qid"]: q["cluster"] for q in qs}, _G["W"].cluster_of)


def fit_rrf(qs):
    """Global weights on all of qs; per type, the type's own weights where they win 5-fold CV."""
    grid = weight_grid(len(COMPS), RRF_VALUES)
    w_g, m_g = tuner(qs).best(grid)
    per = {}
    for t in sorted({q["type"] for q in qs}):
        dq = [q for q in qs if q["type"] == t]
        w_t, m_t = tuner(dq).best(grid)
        cv = P.cv_select(tuner, qs, dq, grid, lambda w: grid)
        per[t] = {"weights": w_t if cv["use_type"] else w_g, "own_weights": w_t, "dev_mrr10_own": m_t, **cv}
    return w_g, per


def rank_rrf(q, weights):
    return _G["W"].rank(rrf(lists_of(q), weights=list(weights)), q)


# ---------------------------------------------------------------- reporting

def summarize(ranks_by_sys, groups):
    out = {}
    for name, ranks in ranks_by_sys.items():
        row = {}
        for g, qids in groups.items():
            qs = [q for q in qids if q in ranks]
            if qs:
                r = [ranks[q] for q in qs]
                row[g] = {"n": len(qs), "mrr10": float(np.mean([M.rr10(x) for x in r])),
                          "s1": float(np.mean([x == 1 for x in r])),
                          "s10": float(np.mean([x is not None and x <= 10 for x in r])),
                          "r100": float(np.mean([x is not None and x <= 100 for x in r]))}
        if all(t in row for t in M.MAIN):
            row["macro"] = float(np.mean([row[t]["mrr10"] for t in M.MAIN]))
        if all(t in row for t in M.TYPED):
            row["macro7"] = float(np.mean([row[t]["mrr10"] for t in M.TYPED]))
        out[name] = row
    return out


def dev_macro7(ranks, dev):
    s = M.by_type_scores({q["qid"]: M.rr10(ranks[q["qid"]]) for q in dev}, dev, M.TYPED)
    return s["macro7"], {t: s[t] for t in M.TYPED}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work", nargs="?", default="work_full")
    ap.add_argument("--cache", help="cache folder for tuned parameters and runs (default WORK/runs/method_cache)")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    ap.add_argument("--perms", type=int, default=10000)
    ap.add_argument("--quick", type=int, default=0, help="smoke test: at most this many queries per type")
    ap.add_argument("--no-rrf-oof", action="store_true", help="skip the out-of-fold dev estimate of RRF")
    a = ap.parse_args(argv)
    t_start = time.time()
    W = M.Work(a.work, a.cache or os.path.join(a.work, "runs", "method_cache"))
    M.log(f"work {a.work} fingerprint {W.fingerprint}; cache {W.cache}")
    W.index("bm25_syl12")          # tuned parameters of the restored bigram list (cached)
    runs = {c: W.run(c) for c in COMPS}
    qt = W.qtype()
    dev = [q for q in W.dev if q["type"] in M.TYPED]
    test = W.test + W.sweep
    if a.quick:
        def cap(rows):
            seen, keep = Counter(), []
            for q in rows:
                if seen[q["type"]] < a.quick:
                    keep.append(q)
                    seen[q["type"]] += 1
            return keep
        dev, test = cap(dev), cap(test)
    fold_of = qt["fold_of_dev_cluster"]
    fold = [fold_of[q["cluster"]] for q in dev]
    _G.update(W=W, runs=runs, proba=qt["proba"], pred=qt["pred"], dev=dev, fold=fold)
    groups = {}
    for q in test:
        groups.setdefault(q["type"], []).append(q["qid"])
    out = {"setup": {"work": a.work, "fingerprint": W.fingerprint, "components": COMPS, "depth": DEPTH,
                     "dense_runs": "not available locally: lexical lists only",
                     "params": {c: W.params(c) for c in COMPS},
                     "qtype": {k: qt[k] for k in ("balanced", "cv_macro_f1", "accuracy")},
                     "dev_queries": M.type_counts(dev), "test_queries": M.type_counts(test),
                     "folds": "5 folds of dev clusters (the classifier's out-of-fold folds)",
                     "selection": "out-of-fold dev macro MRR@10 over Q1-Q5, Q4u, Q5u", "quick": a.quick,
                     "features": L.feature_names(COMPS)}}

    # 1. RRF baselines, tuned on all dev queries
    t0 = time.time()
    w_g, per = fit_rrf(dev)
    pred = qt["pred"]
    systems = {"lexall_equal": {}, "lexall_global": {}, "lexall_adaptive": {}, "lexall_oracle": {}}
    for q in test + dev:
        systems["lexall_equal"][q["qid"]] = rank_rrf(q, [1] * len(COMPS))
        systems["lexall_global"][q["qid"]] = rank_rrf(q, w_g)
        systems["lexall_adaptive"][q["qid"]] = rank_rrf(q, per.get(pred[q["qid"]], {}).get("weights", w_g))
        systems["lexall_oracle"][q["qid"]] = rank_rrf(q, per.get(P.oracle_type(q["type"]), {}).get("weights", w_g))
    systems["restoreall_bm25_syl12"] = {q["qid"]: W.rank(W.ranking(runs["restoreall_bm25_syl12"], q["qid"]), q)
                                        for q in test + dev}
    out["rrf"] = {"global_weights": w_g, "per_type": per, "seconds": round(time.time() - t0, 1)}
    M.log(f"RRF global {w_g}; per type " + ", ".join(f"{t} {v['weights']}{'' if v['use_type'] else ' (global)'}"
                                                     for t, v in per.items()) + f" ({time.time() - t0:.0f}s)")

    # 2. LTR: dev dataset, 5-fold CV over the grid
    t0 = time.time()
    dev_ds = blocks_for(dev)
    _G["dev_ds"] = dev_ds
    has_pos = [bool(dev_ds.y[dev_ds.offsets[i]:dev_ds.offsets[i + 1]].any()) for i in range(len(dev))]
    out["setup"]["dev_rows"] = int(len(dev_ds.y))
    out["setup"]["dev_candidates_mean"] = float(np.mean(np.diff(dev_ds.offsets)))
    out["setup"]["dev_target_in_pool"] = float(np.mean(has_pos))
    M.log(f"dev dataset: {len(dev_ds.y)} rows, {np.mean(np.diff(dev_ds.offsets)):.0f} candidates per query, "
          f"target in pool {np.mean(has_pos):.3f} ({time.time() - t0:.0f}s)")
    configs = ltr_configs()
    tasks = [(c, f) for c in configs for f in range(5)]
    oof = {cfg_name(c): {} for c in configs}
    fit_s = Counter()
    ctx = mp.get_context("fork")

    def run_tasks(task_list, fn):
        if a.jobs > 1:
            # one BLAS/OpenMP thread per worker: forked workers that each start an OpenMP pool
            # oversubscribe the cores, and the trees then run a hundred times slower
            with ctx.Pool(a.jobs, initializer=_one_thread) as pool:
                yield from pool.imap_unordered(fn, task_list)
        else:
            yield from map(fn, task_list)
    for name, f, ranks, sec in run_tasks(tasks, _cv_task):
        oof[name].update(ranks)
        fit_s[name] += sec
    cv_table = {}
    for c in configs:
        n = cfg_name(c)
        m7, per_t = dev_macro7(oof[n], dev)
        cv_table[n] = {"config": c, "oof_dev_macro7": m7, "oof_dev": per_t, "fit_seconds_5_folds": round(fit_s[n], 1)}
    M.log("CV: " + "; ".join(f"{n} {v['oof_dev_macro7']:.4f}" for n, v in cv_table.items())
          + f" ({time.time() - t0:.0f}s)")

    def best(cands):
        return max(cands, key=lambda c: (round(cv_table[cfg_name(c)]["oof_dev_macro7"], 10), -configs.index(c)))
    chosen = {"ltr": best(configs)}
    for kind in ("logreg", "pairwise", "gbt"):
        chosen[f"ltr_{kind}"] = best([c for c in configs if c["kind"] == kind])
    # ablation: the chosen model without the query-level features (type probabilities, length, ...)
    noq = {**chosen["ltr"], "drop_query": True}
    for name, f, ranks, sec in run_tasks([(noq, f) for f in range(5)], _cv_task):
        oof.setdefault(name, {}).update(ranks)
    m7, per_t = dev_macro7(oof[cfg_name(noq)], dev)
    cv_table[cfg_name(noq)] = {"config": noq, "oof_dev_macro7": m7, "oof_dev": per_t}
    chosen["ltr_noquery"] = noq
    out["ltr_cv"] = cv_table
    out["ltr_selected"] = {k: {"config": cfg_name(c), **cv_table[cfg_name(c)]} for k, c in chosen.items()}
    M.log("selected: " + ", ".join(f"{k} {cfg_name(c)}" for k, c in chosen.items()))

    # 3. out-of-fold dev estimate of RRF global and adaptive
    if not a.no_rrf_oof:
        t0 = time.time()
        g_oof, a_oof, fold_w = {}, {}, {}
        for f, g, ad, wg, wt in run_tasks(list(range(5)), _rrf_fold_task):
            g_oof.update(g)
            a_oof.update(ad)
            fold_w[f] = {"global": wg, "per_type": wt}
        out["rrf_oof"] = {"global": dev_macro7(g_oof, dev), "adaptive": dev_macro7(a_oof, dev),
                          "fold_weights": fold_w, "seconds": round(time.time() - t0, 1)}
        M.log(f"RRF out-of-fold dev macro7: global {out['rrf_oof']['global'][0]:.4f}, "
              f"adaptive {out['rrf_oof']['adaptive'][0]:.4f} ({time.time() - t0:.0f}s)")

    # 4. final models on all dev queries, applied once to test (and the noise sweeps)
    t0 = time.time()
    finals = {}
    for k, c in chosen.items():
        n = cfg_name(c)
        if n not in finals:
            finals[n] = make_model(c).fit(dev_ds, list(range(len(dev))), [q["type"] for q in dev])
    coef = {}
    for k, c in chosen.items():
        mdl = finals[cfg_name(c)]
        if c["kind"] in ("logreg", "pairwise") and not c.get("drop_query"):
            names = L.feature_names(COMPS)
            w = (mdl.model.coef_.ravel())[:len(names)]
            top = np.argsort(-np.abs(w))[:15]
            coef[k] = [(names[j], float(w[j])) for j in top]
    out["ltr_top_coefficients_standardised"] = coef
    for n in finals:
        systems[f"ltr:{n}"] = {}
    batch = 1500
    for s in range(0, len(test), batch):
        part = test[s:s + batch]
        ds = blocks_for(part)
        idx = list(range(len(part)))
        for n, mdl in finals.items():
            scores = mdl.score(ds.X)
            systems[f"ltr:{n}"].update(ranks_from_scores(ds, idx, scores, part))
    for k, c in chosen.items():
        systems[k] = systems[f"ltr:{cfg_name(c)}"]
        systems[k + "_dev_oof"] = oof[cfg_name(c)]
    for n in list(finals):
        del systems[f"ltr:{n}"]
    M.log(f"test predictions ({time.time() - t0:.0f}s)")

    # 5. tables and paired tests (test split; dev rows are reported separately)
    test_sys = {k: v for k, v in systems.items() if not k.endswith("_dev_oof")}
    out["test"] = summarize(test_sys, groups)
    out["dev_in_sample_rrf"] = {k: dev_macro7(systems[k], dev) for k in
                                ("lexall_equal", "lexall_global", "lexall_adaptive", "lexall_oracle",
                                 "restoreall_bm25_syl12")}
    out["dev_oof_ltr"] = {k: dev_macro7(oof[cfg_name(c)], dev) for k, c in chosen.items()}
    rr = {s: {q: M.rr10(r) for q, r in ranks.items()} for s, ranks in test_sys.items()}
    typed = {t: groups[t] for t in M.TYPED if t in groups}
    other = {t: groups[t] for t in sorted(groups) if t not in M.TYPED}
    prim = [("ltr vs lexall_global", "lexall_global", "ltr"), ("ltr vs lexall_adaptive", "lexall_adaptive", "ltr")]
    sig = {"primary": M.comparison_family(rr, prim, typed, a.perms, macro_types=[t for t in M.MAIN if t in typed]),
           "secondary_variants_and_sweeps": M.comparison_family(rr, prim, other, a.perms),
           "secondary_model_families": M.comparison_family(
               rr, [(f"{k} vs lexall_global", "lexall_global", k) for k in ("ltr_logreg", "ltr_pairwise", "ltr_gbt",
                                                                             "ltr_noquery")]
               + [("ltr vs restoreall_bm25_syl12", "restoreall_bm25_syl12", "ltr"),
                  ("ltr vs ltr_noquery", "ltr_noquery", "ltr")],
               typed, a.perms, macro_types=[t for t in M.MAIN if t in typed])}
    out["significance"] = sig
    out["seconds"] = round(time.time() - t_start, 1)
    res_dir = W.cache if a.quick else os.path.join(a.work, "results")
    os.makedirs(res_dir, exist_ok=True)
    name = "method_ltr" + ("_quick" if a.quick else "")
    json.dump(out, open(os.path.join(res_dir, f"{name}.json"), "w"), indent=1, ensure_ascii=False,
              default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    with gzip.open(os.path.join(res_dir, f"{name}_ranks.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(systems, f)
    for s in ("lexall_global", "lexall_adaptive", "ltr", "ltr_logreg", "ltr_pairwise", "ltr_gbt", "ltr_noquery"):
        row = out["test"].get(s, {})
        M.log(f"test {s}: macro {row.get('macro', float('nan')):.4f} " +
              " ".join(f"{t} {row[t]['mrr10']:.3f}" for t in M.TYPED if t in row))
    M.log(f"wrote {os.path.join(res_dir, name + '.json')} ({out['seconds']}s)")


if __name__ == "__main__":
    main()
