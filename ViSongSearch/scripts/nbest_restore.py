"""Retrieval with the n best diacritic restorations instead of the best one (round-1 method).

    python3 scripts/nbest_restore.py work_full [--cache DIR] [--jobs 2] [--perms 10000] [--quick N]

Restorer: the Kneser-Ney trigram restorer of visong/diacritics.py trained on training songs only
(Restore_train, the main setting) and, as a secondary setting, on all lyrics (Restore_all).
visong/nbest.py returns its k best restorations (k <= KMAX); each is searched with BM25 on marked
syllable uni+bigrams (the index of restore*_bm25_syl12; k1 and b tuned on dev with the queries as
typed, as in `pipeline lexical`), and the rankings are fused by restoration probability:
  wrrf  weighted RRF        sum_i p_i / (60 + rank_i(d)) over the top 100 of each restoration
  mix   score mixture       sum_i p_i * BM25(r_i, d)
with p_i proportional to P_LM(r_i)^alpha over the n-best list. k = 1 is the 1-best restoration, i.e.
the run restore{train,all}_bm25_syl12 of the pipeline (checked query by query).

Tuning, on dev only: k in K_GRID, alpha in ALPHA_GRID and the fusion, by the dev macro MRR@10 over
the seven typed types (Q1-Q5, Q4u, Q5u), the rule run_gpu_experiments.py uses to select hybrids;
ties go to the smaller k. The method of the paper's revision plan fuses by restoration probability
alone (beta = 0); a retrieval-aware variant, reported as exploratory, also multiplies each weight by
(top BM25 score of the restoration / best top score)^beta, beta in BETA_GRID.

Test, each selected system once: Q1-Q5, Q4u, Q5u, the test-only variants Q3u, Q4s, Q5g, the noise
sweeps N (hand-set channel), E and W (channels from real transcriptions), and U: the N sweep with every
mark removed, derived here for this analysis (test only, not part of the released collection).
Baselines: the 1-best restoration and BM25 on unmarked uni+bigrams (bm25_nodiac12) and unigrams.

Writes WORK/results/method_nbest.json (and per-query ranks in method_nbest_ranks.json.gz).
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
from visong import methods as M                                                   # noqa: E402
from visong.diacritics import DiacriticRestorer, syllable_accuracy                # noqa: E402
from visong.fusion import rrf                                                     # noqa: E402
from visong.nbest import evidence_weights, nbest                                  # noqa: E402
from visong.text import clean, remove_diacritics                                  # noqa: E402

KMAX = 10
K_GRID = (1, 2, 3, 4, 5, 6, 8, 10)
ALPHA_GRID = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)
BETA_GRID = (0.0, 1.0, 2.0, 4.0)
METHODS = ("wrrf", "mix")
ONE = ("1best", 1, 1.0, 0.0)
TARGET_TYPES = ["Q2", "Q3", "Q4u", "Q5u"]          # the types whose queries lack some marks
SWEEP_PREFIXES = ("N", "E", "W", "U")


def dev_grid():
    return [ONE] + [(m, k, a, b) for m in METHODS for k in K_GRID[1:] for a in ALPHA_GRID for b in BETA_GRID]


def cfg_name(c) -> str:
    return "1best" if c[0] == "1best" else f"{c[0]}_k{c[1]}_a{c[2]:g}_b{c[3]:g}"


# ---------------------------------------------------------------- per-query evaluation (forked workers)

_G: dict = {}


def _evaluate(q):
    """Target rank of q under every configuration, plus restoration diagnostics."""
    rest, ix, ids, cluster_of = _G["rest"], _G["ix"], _G["ids"], _G["cluster_of"]
    t0 = time.perf_counter()
    nb = nbest(rest, q["text"], KMAX)
    t1 = time.perf_counter()
    texts = [t for t, _ in nb]
    lps = np.array([s for _, s in nb])
    S = np.vstack([ix.bm25_scores(ix.query_vector(t)) for t in texts])
    tops = S.max(axis=1)
    lists = []
    for i in range(len(texts)):
        pos, sc = M.topk(S[i])
        lists.append([(ids[j], float(s)) for j, s in zip(pos, sc)])
    t2 = time.perf_counter()

    def rank(ranking):
        return M.target_rank(ranking, q["cluster"], cluster_of)
    ranks = {}
    for c in _G["configs"]:
        method, k, alpha, beta = c
        kk = min(k, len(texts))
        if method == "1best" or kk == 1:
            ranks[c] = rank(lists[0])
            continue
        w = evidence_weights(lps[:kk], tops[:kk], alpha, beta)
        if method == "mix":
            pos, sc = M.topk(w @ S[:kk])
            fused = [(ids[j], float(s)) for j, s in zip(pos, sc)]
        else:
            fused = rrf(lists[:kk], weights=[float(x) for x in w])
        ranks[c] = rank(fused)
    t3 = time.perf_counter()
    single = [rank(lst) for lst in lists]
    oracle = {}
    for k in K_GRID:
        got = [r for r in single[:k] if r is not None]
        oracle[k] = min(got) if got else None
    gold = _G["gold"].get(q["qid"])
    diag = {"n": len(texts), "top1": texts[0], "single": single, "post1": float(evidence_weights(lps, tops, 1.0)[0])}
    if gold is not None:
        g = " ".join(clean(gold).split())
        diag["gold_pos"] = texts.index(g) if g in texts else None
        diag["acc"] = syllable_accuracy(texts[0], g)
        diag["gold_post"] = float(evidence_weights(lps, tops, 1.0)[texts.index(g)]) if g in texts else 0.0
    return q["qid"], ranks, oracle, diag, (t1 - t0, t2 - t1, t3 - t2)


def evaluate_all(queries, configs, jobs):
    _G["configs"] = configs
    out = {}
    times = np.zeros(3)
    if jobs > 1:
        with mp.get_context("fork").Pool(jobs) as pool:
            it = pool.imap_unordered(_evaluate, queries, chunksize=32)
            for qid, ranks, oracle, diag, t in it:
                out[qid] = (ranks, oracle, diag)
                times += t
    else:
        for q in queries:
            qid, ranks, oracle, diag, t = _evaluate(q)
            out[qid] = (ranks, oracle, diag)
            times += t
    return out, {"decode_ms": 1000 * times[0] / max(1, len(queries)),
                 "search_ms_kmax": 1000 * times[1] / max(1, len(queries)),
                 "fusion_ms_all_configs": 1000 * times[2] / max(1, len(queries))}


# ---------------------------------------------------------------- selection

def dev_table(res, dev, configs):
    table = {}
    for c in configs:
        rr = {q["qid"]: M.rr10(res[q["qid"]][0][c]) for q in dev}
        s = M.by_type_scores(rr, dev, M.TYPED)
        s["target4"] = float(np.mean([s[t] for t in TARGET_TYPES]))
        table[c] = s
    return table


def select(table, allowed):
    """Best dev macro7 among `allowed` configs; ties: smaller k, alpha nearer 1, smaller beta."""
    return max(allowed, key=lambda c: (round(table[c]["macro7"], 10), -c[1], -abs(c[2] - 1), -c[3]))


# ---------------------------------------------------------------- derived queries and baselines

def unmarked_sweep(sweep):
    """U<r>: the N<r> sweep queries with every mark removed (no Telex residue)."""
    out = []
    for q in sweep:
        if q["type"].startswith("N") and q["type"][1:].isdigit():
            out.append({**q, "qid": q["qid"] + "-u", "type": "U" + q["type"][1:], "text": remove_diacritics(q["text"]),
                        "meta": {**q.get("meta", {}), "from": q["qid"]}})
    return out


def gold_texts(W, queries):
    """Marked text a restoration should produce: the fragment for Q2, the marked LLM query for Q4u/Q5u,
    the marked noisy query for Q3u and the U sweep."""
    gold = {}
    for q in queries:
        t = q["type"]
        if t == "Q2":
            gold[q["qid"]] = q["meta"]["source_fragment"]
        elif t in ("Q4u", "Q5u", "Q3u") or t.startswith("U"):
            src = W.by_qid.get(q["meta"].get("from"))
            if src is not None:
                gold[q["qid"]] = src["text"]
    return gold


def baseline_ranks(W, name, ix, queries, restore=None):
    """Target rank of every query for a plain run (cached run over allq, derived queries searched here)."""
    run = W.run(name, ix)
    out = {}
    for q in queries:
        if q["qid"] in run:
            out[q["qid"]] = W.rank(W.ranking(run, q["qid"]), q)
        else:
            text = q["text"] if restore is None else restore(q)
            pos, sc = M.topk(ix.bm25_scores(ix.query_vector(text)))
            out[q["qid"]] = W.rank([(W.ids[j], float(s)) for j, s in zip(pos, sc)], q)
    return out


# ---------------------------------------------------------------- main

def summarize(ranks_by_sys, queries, groups):
    out = {}
    for name, ranks in ranks_by_sys.items():
        rr = {q: M.rr10(r) for q, r in ranks.items()}
        row = {}
        for g, qids in groups.items():
            qs = [q for q in qids if q in rr]
            if qs:
                row[g] = {"n": len(qs), "mrr10": float(np.mean([rr[q] for q in qs])),
                          "s1": float(np.mean([ranks[q] == 1 for q in qs])),
                          "s10": float(np.mean([ranks[q] is not None and ranks[q] <= 10 for q in qs])),
                          "r100": float(np.mean([ranks[q] is not None and ranks[q] <= 100 for q in qs]))}
        main = [t for t in M.MAIN if t in row]
        if len(main) == len(M.MAIN):
            row["macro"] = float(np.mean([row[t]["mrr10"] for t in main]))
        typed = [t for t in M.TYPED if t in row]
        if len(typed) == len(M.TYPED):
            row["macro7"] = float(np.mean([row[t]["mrr10"] for t in typed]))
        if all(t in row for t in TARGET_TYPES):
            row["target4"] = float(np.mean([row[t]["mrr10"] for t in TARGET_TYPES]))
        out[name] = row
    return out


def restoration_diagnostics(res, queries, by_type_groups):
    out = {}
    for t, qids in by_type_groups.items():
        rows = [res[q][2] for q in qids if q in res and "gold_pos" in res[q][2]]
        if not rows:
            continue
        acc = np.sum([r["acc"] for r in rows], axis=0)
        pos = [r["gold_pos"] for r in rows]
        out[t] = {"n": len(rows), "top1_syllable_acc": float(acc[0] / max(1, acc[1])),
                  "top1_exact": float(np.mean([p == 0 for p in pos])),
                  "gold_in_topk": {k: float(np.mean([p is not None and p < k for p in pos])) for k in K_GRID},
                  "mean_gold_posterior_alpha1": float(np.mean([r["gold_post"] for r in rows])),
                  "mean_top1_posterior_alpha1": float(np.mean([r["post1"] for r in rows]))}
    return out


def oracle_mrr(res, groups):
    """MRR@10 if one could pick, for each query, the best of its first k restorations (upper bound
    of any fusion of those k runs that keeps one of them)."""
    out = {}
    for g, qids in groups.items():
        qs = [q for q in qids if q in res]
        if qs:
            out[g] = {k: float(np.mean([M.rr10(res[q][1][k]) for q in qs])) for k in K_GRID}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work", nargs="?", default="work_full")
    ap.add_argument("--cache", help="cache folder for tuned parameters and runs (default WORK/runs/method_cache)")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    ap.add_argument("--perms", type=int, default=10000)
    ap.add_argument("--quick", type=int, default=0, help="smoke test: at most this many queries per type")
    a = ap.parse_args(argv)
    t_start = time.time()
    W = M.Work(a.work, a.cache or os.path.join(a.work, "runs", "method_cache"))
    M.log(f"work {a.work} fingerprint {W.fingerprint}; cache {W.cache}")
    usweep = unmarked_sweep(W.sweep)
    for q in usweep:
        W.by_qid.setdefault(q["qid"], q)
    dev = [q for q in W.dev if q["type"] in M.TYPED]
    test = W.test + W.sweep + usweep
    if a.quick:
        def cap(rows):
            seen = Counter()
            keep = []
            for q in rows:
                if seen[q["type"]] < a.quick:
                    keep.append(q)
                    seen[q["type"]] += 1
            return keep
        dev, test = cap(dev), cap(test)
    groups_test = {}
    for q in test:
        groups_test.setdefault(q["type"], []).append(q["qid"])
    groups_dev = {}
    for q in dev:
        groups_dev.setdefault(q["type"], []).append(q["qid"])

    ix = W.index("bm25_syl12")
    nod12 = W.index("bm25_nodiac12")
    nod = W.index("bm25_nodiac")
    M.log(f"indexes: syl12 {ix.tuned}, nodiac12 {nod12.tuned}, nodiac {nod.tuned}")
    _G.update(ix=ix, ids=W.ids, cluster_of=W.cluster_of, gold=gold_texts(W, dev + test))
    train_texts = [s.lyrics for s in W.songs if s.extra.get("split") == "train"]
    restorers = {"train": train_texts, "all": [s.lyrics for s in W.songs]}
    out = {"setup": {"work": a.work, "fingerprint": W.fingerprint, "kmax": KMAX, "k_grid": K_GRID,
                     "alpha_grid": ALPHA_GRID, "beta_grid": BETA_GRID, "methods": METHODS,
                     "index": {"analyzer": "syl12", **ix.tuned},
                     "baselines": {"bm25_nodiac12": nod12.tuned, "bm25_nodiac": nod.tuned},
                     "selection": "dev macro MRR@10 over Q1-Q5, Q4u, Q5u; ties: smaller k, alpha nearer 1, smaller beta",
                     "dev_queries": M.type_counts(dev), "test_queries": M.type_counts(test),
                     "u_sweep": "N sweep queries with every mark removed (derived here, test only)",
                     "quick": a.quick},
           "restorers": {}}
    systems = {}           # name -> {qid: rank} on test
    perquery = {}
    for lm, texts in restorers.items():
        t0 = time.time()
        rest = DiacriticRestorer(texts)
        _G["rest"] = rest
        M.log(f"restorer {lm}: {len(texts)} songs ({time.time() - t0:.0f}s)")
        # 1. dev: every configuration
        configs = dev_grid()
        res_dev, tm_dev = evaluate_all(dev, configs, a.jobs)
        table = dev_table(res_dev, dev, configs)
        base = [c for c in configs if c[3] == 0.0]
        chosen = {"nbest": select(table, base)}
        for m in METHODS:
            chosen[f"nbest_{m}"] = select(table, [ONE] + [c for c in base if c[0] == m])
            # the best configuration of this fusion with k >= 2, even when the 1-best wins on dev
            chosen[f"nbest_{m}_forced"] = select(table, [c for c in base if c[0] == m])
        chosen["nbest_ra"] = select(table, configs)
        M.log(f"restorer {lm}: dev 1best macro7 {table[ONE]['macro7']:.4f}; "
              + "; ".join(f"{n} {cfg_name(c)} {table[c]['macro7']:.4f}" for n, c in chosen.items()))
        # 2. test: the selected configurations and the 1-best
        test_cfgs = sorted({ONE, *chosen.values()}, key=cfg_name)
        res_test, tm_test = evaluate_all(test, test_cfgs, a.jobs)
        # the 1-best must be the pipeline's run restore{lm}_bm25_syl12
        mism = sum(res_test[q["qid"]][2]["top1"] != W.restored[lm][q["qid"]]
                   for q in test if q["qid"] in W.restored[lm])
        mism += sum(res_dev[q["qid"]][2]["top1"] != W.restored[lm][q["qid"]] for q in dev)
        systems[f"restore{lm}_1best"] = {q["qid"]: res_test[q["qid"]][0][ONE] for q in test}
        for n, c in chosen.items():
            systems[f"{n}_{lm}"] = {q["qid"]: res_test[q["qid"]][0][c] for q in test}
        perquery[lm] = {"dev": {q["qid"]: {cfg_name(c): res_dev[q["qid"]][0][c] for c in {ONE, *chosen.values()}}
                                for q in dev},
                        "test_single": {q["qid"]: res_test[q["qid"]][2]["single"] for q in test}}
        sens = {}
        for name in ("nbest", "nbest_wrrf_forced", "nbest_mix_forced"):
            c = chosen[name]
            if c[0] in METHODS:
                sens[name] = {"by_k": {k: table[(c[0], k, c[2], c[3])]["macro7"] if k > 1 else table[ONE]["macro7"]
                                       for k in K_GRID},
                              "by_alpha": {al: table[(c[0], c[1], al, c[3])]["macro7"] for al in ALPHA_GRID}}
        out["restorers"][lm] = {
            "lm_songs": len(texts),
            "top1_matches_restored_json": mism == 0, "top1_mismatches": int(mism),
            "selected": {n: {"config": cfg_name(c), "method": c[0], "k": c[1], "alpha": c[2], "beta": c[3],
                             "dev": table[c]} for n, c in chosen.items()},
            "dev_1best": table[ONE],
            "dev_grid": {cfg_name(c): {"macro7": table[c]["macro7"], "target4": table[c]["target4"],
                                       **{t: table[c][t] for t in M.TYPED}} for c in configs},
            "dev_sensitivity_of_selected": sens,
            "dev_restoration": restoration_diagnostics(res_dev, dev, groups_dev),
            "test_restoration": restoration_diagnostics(res_test, test, groups_test),
            "dev_oracle_best_of_k": oracle_mrr(res_dev, groups_dev),
            "test_oracle_best_of_k": oracle_mrr(res_test, groups_test),
            "nbest_size_test": dict(sorted(Counter(min(res_test[q["qid"]][2]["n"], KMAX) for q in test).items())),
            "timing_ms_per_query": {"dev_all_configs": tm_dev, "test_selected": tm_test},
        }
        M.log(f"restorer {lm}: done ({time.time() - t0:.0f}s); top-1 mismatches with restored.json: {mism}")

    # 3. baselines on the same test queries
    M.log("baselines")
    systems["bm25_nodiac12"] = baseline_ranks(W, "bm25_nodiac12", nod12, test)
    systems["bm25_nodiac"] = baseline_ranks(W, "bm25_nodiac", nod, test)
    systems["bm25_syl12"] = baseline_ranks(W, "bm25_syl12", ix, test)
    for lm in restorers:      # the pipeline's run, from restored.json (derived U queries: the decoder's top 1)
        name = f"restore{lm}_bm25_syl12"
        piped = baseline_ranks(W, name, ix, [q for q in test if q["qid"] in W.restored[lm]],
                               restore=lambda q, lm=lm: W.restored[lm][q["qid"]])
        same = all(piped[q] == systems[f"restore{lm}_1best"][q] for q in piped)
        out["restorers"][lm]["k1_equals_pipeline_run"] = bool(same)
    out["test"] = summarize(systems, test, groups_test)

    # 4. paired tests
    rr = {s: {q: M.rr10(r) for q, r in ranks.items()} for s, ranks in systems.items()}
    g = {t: groups_test[t] for t in groups_test}
    target = {t: g[t] for t in TARGET_TYPES if t in g}
    other_cols = {t: g[t] for t in sorted(g) if t not in TARGET_TYPES and t not in ("Q4s", "Q5g")}
    prim = [("nbest_train vs restoretrain_1best", "restoretrain_1best", "nbest_train"),
            ("nbest_train vs bm25_nodiac12", "bm25_nodiac12", "nbest_train")]
    sig = {"primary": M.comparison_family(rr, prim, target, a.perms, macro_types=list(target)),
           "secondary_other_columns": M.comparison_family(rr, prim, other_cols, a.perms),
           "secondary_all_lm": M.comparison_family(
               rr, [("nbest_all vs restoreall_1best", "restoreall_1best", "nbest_all"),
                    ("nbest_all vs bm25_nodiac12", "bm25_nodiac12", "nbest_all")],
               {**target, **({"Q3u": g["Q3u"]} if "Q3u" in g else {})}, a.perms, macro_types=list(target)),
           "exploratory_variants": M.comparison_family(
               rr, [(f"{n}_train vs restoretrain_1best", "restoretrain_1best", f"{n}_train")
                    for n in ("nbest_wrrf_forced", "nbest_mix_forced", "nbest_ra")],
               {**target, **({"Q3u": g["Q3u"]} if "Q3u" in g else {})}, a.perms, macro_types=list(target))}
    out["significance"] = sig
    out["seconds"] = round(time.time() - t_start, 1)
    res_dir = W.cache if a.quick else os.path.join(a.work, "results")      # smoke tests stay in the cache
    os.makedirs(res_dir, exist_ok=True)
    name = "method_nbest" + ("_quick" if a.quick else "")
    json.dump(out, open(os.path.join(res_dir, f"{name}.json"), "w"), indent=1, ensure_ascii=False,
              default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    with gzip.open(os.path.join(res_dir, f"{name}_ranks.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"test": systems, "perquery": perquery}, f)
    for s in ("restoretrain_1best", "nbest_train", "nbest_mix_forced_train", "nbest_wrrf_forced_train",
              "nbest_ra_train", "bm25_nodiac12", "restoreall_1best", "nbest_all"):
        row = out["test"].get(s, {})
        M.log(f"test {s}: " + " ".join(f"{t} {row[t]['mrr10']:.3f}" for t in TARGET_TYPES + ["Q3u", "N50", "U50"]
                                        if t in row))
    M.log(f"wrote {os.path.join(res_dir, name + '.json')} ({out['seconds']}s)")


if __name__ == "__main__":
    main()
