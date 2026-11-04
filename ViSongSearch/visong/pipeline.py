"""End-to-end pipeline: corpus -> clusters -> queries -> lexical runs -> evaluation.

    python -m visong.pipeline build   --work work --dbs ../Music_SEG [--jsonl data/hac.jsonl]
    python -m visong.pipeline queries --work work
    python -m visong.pipeline lexical --work work
    python -m visong.pipeline report  --work work
Dense, reranking and fine-tuning runs (visong/dense.py, rerank.py, finetune.py)
write TREC files into work/runs/, and `report` evaluates every file found there.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import time
from collections import Counter, defaultdict

import numpy as np

from . import corpus as C
from .dedup import cluster_songs, find_compilations, norm_title
from .diacritics import DiacriticRestorer, syllable_accuracy
from .evaluate import (bootstrap_ci, by_type, compare, holm, kendall_tau, per_query, read_trec,
                       summarize, write_trec)
from .fusion import WeightTuner, rrf, weight_grid
from .lexical import LexicalIndex, MultiFieldBM25
from .qtype import QueryTypeClassifier, cv_macro_f1
from .queries import (MAIN_TYPES, QUERY_FILES, UNMARKED_TYPES, AmbiguityChecker, generate_rule_queries,
                      noise_sweep, split_of, write_unmarked_variants)
from .text import clean, remove_diacritics

PILOT_COUNTS = {"train": {"Q1": 3, "Q2": 3, "Q3": 3}, "dev": {"Q1": 2, "Q2": 2, "Q3": 2},
                "test": {"Q1": 3, "Q2": 3, "Q3": 3}}
FULL_COUNTS = {"train": {"Q1": 2, "Q2": 2, "Q3": 2}, "dev": {"Q1": 1, "Q2": 1, "Q3": 1},
               "test": {"Q1": 1, "Q2": 1, "Q3": 1}}


def p(work, *parts):
    path = os.path.join(work, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# ------------------------------------------------------------------ build

def build(work: str, dbs_root: str | None, jsonl: list[str] | None, min_vi_ratio: float = 0.25):
    songs = []
    # crawl files first: when a Hợp Âm Chuẩn page is also in a group database, the crawled copy
    # (with singers, views and the update date) is kept
    for path in jsonl or []:
        songs += C.load_jsonl_songs(path)
    if dbs_root:
        songs += C.load_sqlite_songs(C.find_group_dbs(dbs_root))
    songs, stats = C.prepare(songs, min_vi_ratio=min_vi_ratio)
    log(f"corpus {stats}")
    cluster_of, cstats = cluster_songs(songs)
    # Medleys whose title lacks "mashup"/"liên khúc" ("A x B"): kept in the index, never targets.
    medleys = find_compilations(songs, cluster_of)
    for s in songs:
        if s.doc_id in medleys:
            s.extra["compilation"] = True
            s.extra["medley_of"] = medleys[s.doc_id]
    cstats["compilations_by_title"] = sum(C.is_compilation(s.title) for s in songs)
    cstats["compilations_by_lyrics"] = sum(s.doc_id in medleys and not C.is_compilation(s.title) for s in songs)
    log(f"clusters {cstats}")
    # The split is decided by the cluster's most common normalised title, not by the
    # cluster id, so a song keeps its split when the corpus grows (new versions can
    # change which doc id names the cluster, but rarely the majority title).
    members = defaultdict(list)
    for s in songs:
        members[cluster_of[s.doc_id]].append(s)
    key_of_cluster = {}
    for c, ms in members.items():
        titles = Counter(norm_title(m.title) or m.doc_id for m in ms)
        key_of_cluster[c] = sorted(titles.items(), key=lambda x: (-x[1], x[0]))[0][0]
    for s in songs:
        s.extra["cluster"] = cluster_of[s.doc_id]
        s.extra["cluster_key"] = key_of_cluster[cluster_of[s.doc_id]]
        s.extra["split"] = split_of(s.extra["cluster_key"])
    C.write_jsonl(p(work, "corpus.jsonl"), songs)
    passages = [ps for s in songs for ps in C.passages_of(s)]
    C.write_jsonl(p(work, "passages.jsonl"), passages)
    json.dump(cluster_of, open(p(work, "clusters.json"), "w"), ensure_ascii=False)
    summary = {"corpus": stats, "clusters": cstats, "passages": len(passages),
               "sources": Counter(s.source for s in songs),
               "splits": Counter(s.extra["split"] for s in songs),
               "median_syllables": float(np.median([len(clean(s.lyrics).split()) for s in songs]))}
    json.dump(summary, open(p(work, "results", "corpus_stats.json"), "w"), ensure_ascii=False, indent=1)
    log(f"passages {len(passages)}")


def load(work):
    songs = C.load_corpus(os.path.join(work, "corpus.jsonl"))
    cluster_of = json.load(open(os.path.join(work, "clusters.json")))
    return songs, cluster_of


# ------------------------------------------------------------------ queries

def make_queries(work: str, counts=None, seed: int = 13):
    songs, cluster_of = load(work)
    pilot = len(songs) < 3000
    counts = counts or (PILOT_COUNTS if pilot else FULL_COUNTS)
    checker = AmbiguityChecker(songs, cluster_of)
    # The full collection asks about one version per cluster in dev and test, so every test
    # query is about a different song; the pilot asked about every version.
    qs = generate_rule_queries(songs, cluster_of, counts, seed=seed, checker=checker,
                               split_of_doc={s.doc_id: s.extra["split"] for s in songs},
                               one_per_cluster=() if pilot else ("dev", "test"))
    sweep = noise_sweep(qs)
    C.write_jsonl(p(work, "queries", "rule.jsonl"), qs)
    C.write_jsonl(p(work, "queries", "noise_sweep.jsonl"), sweep)
    log(f"queries {Counter((q['split'], q['type']) for q in qs)}; sweep {len(sweep)}")


def load_queries(work, names=QUERY_FILES):
    out = []
    for n in names:
        path = os.path.join(work, "queries", n)
        if os.path.exists(path):
            out += C.read_jsonl(path)
    return out


# ------------------------------------------------------------------ lexical

ANALYZERS = ["syl", "syl12", "word", "nodiac", "nodiac12", "char3", "char4x", "char5x", "phon12"]
GRID_K1 = [0.1, 0.2, 0.3, 0.6, 0.9, 1.2, 1.5, 2.0]
GRID_B = [0.3, 0.5, 0.75, 0.9, 1.0]
GRID_MU = [5, 10, 20, 30, 50, 100, 300, 1000, 2000]
GRID_META_W = [0.02, 0.05, 0.1, 0.3, 0.5, 1.0]
GRID_RM3 = [{"fb_docs": d, "fb_terms": t, "orig_weight": w} for d in (5, 10, 20) for t in (10, 20, 40)
            for w in (0.5, 0.7, 0.9)]
QTYPES = list(MAIN_TYPES)                       # the macro average is over these
TYPED = QTYPES + list(UNMARKED_TYPES)           # types the classifier predicts


def meta_text(s) -> str:
    """Searchable metadata: title, artist (composer or performers), singers and genre."""
    return " ".join(x for x in (s.title, s.artist, s.extra.get("singers", ""), s.genre) if x)


def run_index(index, queries, k=100, model="bm25", text_of=None, **kw):
    run = {}
    for q in queries:
        text = text_of(q) if text_of else q["text"]
        run[q["qid"]] = index.search(text, k=k, model=model, **kw)
    return run


def mrr(run, queries, cluster_of):
    return summarize(per_query(run, queries, cluster_of))["rr10"]


def tune_bm25(index, dev, cluster_of):
    best = (-1, None)
    for k1 in GRID_K1:
        for b in GRID_B:
            index.set_params(k1, b)
            m = mrr(run_index(index, dev), dev, cluster_of)
            if m > best[0]:
                best = (m, (k1, b))
    index.set_params(*best[1])
    return best


def write_runs(work, runs):
    for name, run in runs.items():
        write_trec(p(work, "runs", f"{name}.trec"), run, name)


def update_json(path, key, value):
    data = json.load(open(path)) if os.path.exists(path) else {}
    data[key] = value
    json.dump(data, open(path, "w"), indent=1, ensure_ascii=False)


def lexical(work: str, eval_split: str = "test"):
    songs, cluster_of = load(work)
    write_unmarked_variants(work)
    queries = load_queries(work)
    sweep = C.read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    dev = [q for q in queries if q["split"] == "dev"]
    test = [q for q in queries if q["split"] == eval_split]
    texts = [s.lyrics for s in songs]
    ids = [s.doc_id for s in songs]
    meta_texts = [meta_text(s) for s in songs]
    params = {}
    runs: dict[str, dict] = {}
    allq = test + dev + sweep

    # 1. BM25 over lyrics with every analyzer, k1 and b tuned on dev
    indexes = {}
    for a in ANALYZERS:
        t0 = time.time()
        indexes[a] = LexicalIndex(texts, ids, a)
        best = tune_bm25(indexes[a], dev, cluster_of)
        params[f"bm25_{a}"] = {"k1": best[1][0], "b": best[1][1], "dev_mrr10": best[0],
                               "vocab": len(indexes[a].vocab), "index_s": round(time.time() - t0, 2)}
        log(f"bm25_{a} {params[f'bm25_{a}']}")
        runs[f"bm25_{a}"] = run_index(indexes[a], allq)
    syl, syl12, nod = indexes["syl"], indexes["syl12"], indexes["nodiac"]

    # 2. metadata field alone, and lyrics + metadata (weighted sum of per-field BM25)
    meta = LexicalIndex(meta_texts, ids, "nodiac12")
    best = tune_bm25(meta, dev, cluster_of)
    params["bm25_meta"] = {"k1": best[1][0], "b": best[1][1], "dev_mrr10": best[0], "vocab": len(meta.vocab)}
    runs["bm25_meta"] = run_index(meta, allq)
    n12, mt = params["bm25_nodiac12"], params["bm25_meta"]
    mf = MultiFieldBM25({"lyrics": texts, "meta": meta_texts}, ids, {"lyrics": 1.0, "meta": 0.3}, "nodiac12",
                        {"lyrics": (n12["k1"], n12["b"]), "meta": (mt["k1"], mt["b"])})

    def mf_run(qs, w):
        mf.weights["meta"] = w
        return {q["qid"]: mf.search(q["text"]) for q in qs}
    best_w = max(GRID_META_W, key=lambda w: mrr(mf_run(dev, w), dev, cluster_of))
    params["bm25_lyrmeta_nodiac12"] = {"meta_weight": best_w}
    runs["bm25_lyrmeta_nodiac12"] = mf_run(allq, best_w)
    log(f"metadata {params['bm25_meta']} lyrics+meta weight {best_w}")

    # 3. language-model retrieval and pseudo-relevance feedback
    best_mu = max(GRID_MU, key=lambda mu: mrr(run_index(syl, dev, model="ql", mu=mu), dev, cluster_of))
    params["ql_syl"] = {"mu": best_mu}
    runs["ql_syl"] = run_index(syl, allq, model="ql", mu=best_mu)
    # RM3 feedback documents, terms and the weight of the original query are tuned on dev with
    # the syllable index and reused for the other indexes.
    rm3_dev = {i: mrr(run_index(syl, dev, model="rm3", mu=best_mu, **g), dev, cluster_of) for i, g in enumerate(GRID_RM3)}
    rm3 = GRID_RM3[max(rm3_dev, key=rm3_dev.get)]
    params["rm3"] = {**rm3, "mu": best_mu, "dev_mrr10": rm3_dev[max(rm3_dev, key=rm3_dev.get)]}
    log(f"ql mu {best_mu}; rm3 {params['rm3']}")
    runs["rm3_syl"] = run_index(syl, allq, model="rm3", mu=best_mu, **rm3)
    runs["rm3_nodiac"] = run_index(nod, allq, model="rm3", mu=best_mu, **rm3)
    runs["rm3_syl12"] = run_index(syl12, allq, model="rm3", mu=best_mu, **rm3)

    # 4. Diacritic restoration. "all": LM trained on every indexed song (what a deployed
    # engine would do, since it indexes those lyrics anyway). "train": LM trained on
    # training songs only, i.e. the test songs are new to the LM (a lower bound).
    restored = {}
    test_ids = {q["qid"] for q in test}
    text_of_qid = {q["qid"]: q["text"] for q in allq}
    # Gold marked text: the source fragment for Q2, the LLM's marked query for Q4u/Q5u.
    gold_of = {"Q2": lambda q: q["meta"]["source_fragment"],
               **{t: (lambda q: text_of_qid.get(q["meta"].get("from"))) for t in UNMARKED_TYPES}}
    for name, train_texts in (("all", texts),
                              ("train", [s.lyrics for s in songs if s.extra.get("split") == "train"])):
        t0 = time.time()
        rest = DiacriticRestorer(train_texts)
        acc, rq = defaultdict(lambda: [0, 0]), {}
        for q in allq:
            rq[q["qid"]] = rest.restore(q["text"])
            if q["type"] in gold_of and q["qid"] in test_ids:
                gold = gold_of[q["type"]](q)
                if gold:
                    a, n = syllable_accuracy(rq[q["qid"]], gold)
                    acc[q["type"]][0] += a
                    acc[q["type"]][1] += n
        restored[name] = rq
        params[f"restore_{name}"] = {**{f"{t.lower()}_syllable_acc": a / max(1, n) for t, (a, n) in sorted(acc.items())},
                                     "ms_per_query": round(1000 * (time.time() - t0) / max(1, len(allq)), 2)}
        log(f"restore_{name} {params[f'restore_{name}']}")
        runs[f"restore{name}_bm25_syl"] = run_index(syl, allq, text_of=lambda q: rq[q["qid"]])
        runs[f"restore{name}_bm25_syl12"] = run_index(syl12, allq, text_of=lambda q: rq[q["qid"]])
    json.dump(restored, open(p(work, "queries", "restored.json"), "w"), ensure_ascii=False)

    # 5. Query-type classifier (NB vs logistic regression). Rule types are learned from
    # training songs; Q4/Q5 (and their unmarked variants) exist only for dev/test songs, so
    # their examples come from dev.
    train_q = [q for q in queries if q["split"] == "train" and q["type"] in TYPED]
    dev_llm = [q for q in dev if q["type"] in ("Q4", "Q5") + UNMARKED_TYPES]
    fit_q = train_q + dev_llm
    typed_test = [q for q in test if q["type"] in TYPED]
    preds = {}
    # The dev LLM queries are training examples, so their own predictions would be in-sample.
    # Every dev query is therefore predicted by a model fitted without its cluster's fold
    # (5 folds over dev clusters); test and sweep queries by the model fitted on everything.
    dev_clusters = sorted({q["cluster"] for q in dev})
    fold_of = {c: i % 5 for i, c in enumerate(sorted(dev_clusters, key=hash_key))}
    for model in ("nb", "logreg"):
        # Class weighting (or a uniform prior for NB) is kept if it wins 5-fold CV on the
        # training queries; the test queries play no part in the choice.
        cv = {b: cv_macro_f1([q["text"] for q in fit_q], [q["type"] for q in fit_q], [q["cluster"] for q in fit_q],
                             set(syl.vocab), set(nod.vocab), model, b) for b in (False, True)}
        balanced = cv[True] > cv[False]

        def fit(rows):
            return QueryTypeClassifier(set(syl.vocab), set(nod.vocab), model, balanced).fit(
                [q["text"] for q in rows], [q["type"] for q in rows])
        clf = fit(fit_q)
        others = [q for q in allq if q["split"] != "dev"]
        pred = dict(zip([q["qid"] for q in others], clf.predict([q["text"] for q in others])))
        for f in range(5):
            held = [q for q in dev if fold_of[q["cluster"]] == f]
            if held:
                clf_f = fit(train_q + [q for q in dev_llm if fold_of[q["cluster"]] != f])
                pred.update(zip([q["qid"] for q in held], clf_f.predict([q["text"] for q in held])))
        preds[model] = pred
        conf = defaultdict(Counter)
        for q in typed_test:
            conf[q["type"]][pred[q["qid"]]] += 1
        acc = float(np.mean([pred[q["qid"]] == q["type"] for q in typed_test])) if typed_test else None
        typed_dev = [q for q in dev if q["type"] in TYPED]
        dev_acc = float(np.mean([pred[q["qid"]] == q["type"] for q in typed_dev])) if typed_dev else None
        params[f"qtype_{model}"] = {"test_accuracy": acc, "dev_accuracy_out_of_fold": dev_acc,
                                    "train_examples": dict(Counter(q["type"] for q in fit_q)),
                                    "balanced": balanced, "cv_macro_f1": {"plain": cv[False], "balanced": cv[True]},
                                    "confusion": {t: dict(c) for t, c in sorted(conf.items())}}
        log(f"qtype_{model} acc={acc:.3f} confusion={params[f'qtype_{model}']['confusion']}")
    json.dump(preds, open(p(work, "results", "qtype_pred.json"), "w"))

    write_runs(work, runs)
    json.dump(params, open(p(work, "results", "lexical_params.json"), "w"), indent=1, ensure_ascii=False)
    log(f"wrote {len(runs)} runs")

    # 6. Fusion of lexical lists: equal weights, one tuned weight vector, weights per
    # predicted query type (adaptive) and per true type (oracle).
    for lm in ("all", "train"):
        fuse(work, [f"restore{lm}_bm25_syl12", "bm25_nodiac12", "bm25_char3", "bm25_meta"], f"lex{lm}",
             runs=runs, eval_split=eval_split)
    fuse(work, ["restoreall_bm25_syl12", "bm25_nodiac12", "bm25_char3"], "lexlyrics", runs=runs,
         eval_split=eval_split)


# ------------------------------------------------------------------ fusion

def hash_key(x: str) -> str:
    import hashlib
    return hashlib.md5(f"fold:{x}".encode()).hexdigest()


def oracle_type(t: str) -> str:
    """Noise-sweep queries come from exact fragments: N0 is exact, the rest are noisy (N with
    the hand-set channel, E with its weights estimated from real transcriptions, W with the
    observed channel). Variants seen only in test (Q3u, Q4s, Q5g) count as their source types."""
    if t[:1] in ("N", "E", "W") and t[1:].isdigit():
        return "Q1" if t[1:] == "0" else "Q3"
    return {"Q3u": "Q3", "Q4s": "Q4", "Q5g": "Q5"}.get(t, t)


ADAPT_VALUES = (0, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16)


def fuse(work: str, comps: list[str], name: str, runs=None, eval_split: str = "test",
         values=(0, 0.5, 1, 2), adapt_last: int | None = None) -> dict:
    """RRF over the given runs with weights tuned on dev; writes {name}_{equal,global,adaptive,oracle}.

    adapt_last=None tunes every weight per query type. adapt_last=1 keeps the global
    weights of the first lists and tunes only the last list's weight per type (for a
    hybrid: how much to trust the dense list), which needs far fewer dev queries."""
    _, cluster_of = load(work)
    queries = load_queries(work)
    sweep = C.read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    runs = runs or {}
    runs = {n: runs[n] if n in runs else read_trec(os.path.join(work, "runs", f"{n}.trec")) for n in comps}
    # A query missing from a lexical run simply had no matching song, so it counts as an
    # empty list. Sweep queries are fused only if every component was run on them.
    dev = [q for q in queries if q["split"] == "dev"]
    targets = dev + [q for q in queries if q["split"] == eval_split]
    sweep_ids = {q["qid"] for q in sweep}
    if sweep and all(sweep_ids & set(runs[n]) for n in comps):
        targets += sweep
    pred = json.load(open(os.path.join(work, "results", "qtype_pred.json")))["logreg"]
    grid = weight_grid(len(comps), values)

    def tuner(qs):
        return WeightTuner({q["qid"]: [runs[n].get(q["qid"], []) for n in comps] for q in qs},
                           {q["qid"]: q["cluster"] for q in qs}, cluster_of)
    t0 = time.time()
    w_global, m_global = tuner(dev).best(grid)

    def type_grid(w_g):
        if not adapt_last:
            return grid
        from itertools import product
        head = tuple(w_g[:-adapt_last])
        return [head + tail for tail in product(ADAPT_VALUES, repeat=adapt_last) if any(head + tail)]
    w_type, m_type, cv = {}, {}, {}
    for t in sorted({q["type"] for q in dev}):
        dq = [q for q in dev if q["type"] == t]
        w_t, m_t = tuner(dq).best(type_grid(w_global))
        # Per-type weights are tuned on few queries and can overfit, so a type keeps them
        # only if they beat the global weights in 5-fold cross-validation on dev.
        cv[t] = cv_select(tuner, dev, dq, grid, type_grid)
        w_type[t], m_type[t] = (w_t, m_t) if cv[t]["use_type"] else (w_global, tuner(dq).exact_mrr(w_global))

    def run_with(weights_of):
        return {q["qid"]: rrf([runs[n].get(q["qid"], []) for n in comps], weights=list(weights_of(q)))
                for q in targets}
    out = {f"{name}_equal": run_with(lambda q: [1] * len(comps)),
           f"{name}_global": run_with(lambda q: w_global),
           f"{name}_adaptive": run_with(lambda q: w_type.get(pred.get(q["qid"]), w_global)),
           f"{name}_oracle": run_with(lambda q: w_type.get(oracle_type(q["type"]), w_global))}
    write_runs(work, out)
    info = {"components": comps, "grid": list(values), "adapt_last": adapt_last,
            "global": {"weights": w_global, "dev_mrr10": m_global},
            "per_type": {t: {"weights": w_type[t], "dev_mrr10": m_type[t], **cv[t]} for t in w_type},
            "dev_queries": len(dev), "seconds": round(time.time() - t0, 1)}
    update_json(p(work, "results", "fusion_params.json"), name, info)
    log(f"fusion {name}: global {w_global} ({m_global:.3f}); per type "
        + ", ".join(f"{t} {w_type[t]} ({m_type[t]:.3f}{'' if cv[t]['use_type'] else ', global'})" for t in w_type))
    return out


def cv_select(tuner, dev, dq, grid, type_grid, folds: int = 5, seed: int = 0) -> dict:
    """5-fold CV on one type's dev queries: per-type weights vs global weights, both
    tuned without the held-out fold."""
    import random
    ids = sorted(q["qid"] for q in dq)
    random.Random(seed).shuffle(ids)
    fold = {qid: i % folds for i, qid in enumerate(ids)}
    cv_type = cv_global = 0.0
    for f in range(min(folds, len(ids))):
        held = [q for q in dq if fold[q["qid"]] == f]
        w_g, _ = tuner([q for q in dev if fold.get(q["qid"], -1) != f]).best(grid)
        w_t, _ = tuner([q for q in dq if fold[q["qid"]] != f]).best(type_grid(w_g))
        th = tuner(held)
        cv_type += th.exact_mrr(w_t) * len(held)
        cv_global += th.exact_mrr(w_g) * len(held)
    n = max(1, len(ids))
    return {"use_type": cv_type > cv_global, "cv_type": cv_type / n, "cv_global": cv_global / n}


# ------------------------------------------------------------------ report

def run_qids(work, name):
    """Queries a run was evaluated on, when it covers only a sample (e.g. reranking)."""
    path = os.path.join(work, "runs", f"{name}.qids")
    return set(json.load(open(path))) if os.path.exists(path) else None


def report(work: str, baseline: str = "bm25_syl", split: str = "test", metric: str = "rr10",
           systems: list[str] | None = None, name: str | None = None):
    songs, cluster_of = load(work)
    queries = [q for q in load_queries(work) if q["split"] == split]
    sweep = C.read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    files = sorted(f[:-5] for f in os.listdir(os.path.join(work, "runs")) if f.endswith(".trec"))
    if systems:
        files = [f for f in files if f in systems or f == baseline]
    types = by_type(queries)
    synthetic = [t for t in QTYPES if t in types]
    table, pq_all, subset = {}, {}, {}
    curve = defaultdict(dict)                      # noise sweep curve
    sweep_types = by_type(sweep)
    # one run in memory at a time: 50 runs over ~20k queries at depth 100 do not fit in RAM together
    for sysname in files:
        run = read_trec(os.path.join(work, "runs", f"{sysname}.trec"))
        if any(q["qid"] in run for q in sweep[:20]):
            pqs = per_query(run, sweep, cluster_of)
            for t, qids in sweep_types.items():
                curve[sysname][t] = summarize(pqs, qids)[metric]
        only = run_qids(work, sysname)
        qs = [q for q in queries if only is None or q["qid"] in only]
        subset[sysname] = None if only is None else len(qs)
        pq = per_query(run, qs, cluster_of)
        pq_all[sysname] = pq
        row = {"ALL": summarize(pq)}
        for t, qids in types.items():
            row[t] = summarize(pq, [q for q in qids if q in pq])
        row["MACRO"] = {m: float(np.mean([row[t][m] for t in synthetic if row[t]["n"]]))
                        for m in ("rr10", "s1", "s10", "r100")} if synthetic else {}
        table[sysname] = row
        del run
        gc.collect()
    runs = dict.fromkeys(pq_all)
    # significance of every system against the baseline, per type, Holm-corrected per type
    sig = {}
    for t, qids in list(types.items()) + [("ALL", [q["qid"] for q in queries])]:
        pvals, deltas = {}, {}
        for sysname in runs:
            if sysname == baseline or baseline not in pq_all:
                continue
            common = [q for q in qids if q in pq_all[sysname] and q in pq_all[baseline]]
            if len(common) < 2:
                continue
            c = compare(pq_all[sysname], pq_all[baseline], common, metric, n=5000)
            pvals[sysname], deltas[sysname] = c["p_rand"], c["delta"]
        adj = holm(pvals)
        sig[t] = {n: {"delta": deltas[n], "p": pvals[n], "p_holm": adj[n]} for n in pvals}
    ci = {sysname: {t: bootstrap_ci([pq_all[sysname][q][metric] for q in qids if q in pq_all[sysname]], n=2000)
                    for t, qids in types.items()} for sysname in runs}
    # agreement between system orderings on synthetic and human queries
    agreement = {}
    if "H" in types:
        h = {s: table[s]["H"][metric] for s in runs if table[s]["H"]["n"] and subset[s] is None}
        for t in synthetic + ["MACRO"]:
            other = {s: (table[s][t][metric] if t != "MACRO" else table[s]["MACRO"][metric]) for s in h}
            if len(h) >= 3:
                agreement[t] = dict(zip(("tau", "p"), kendall_tau(other, h)))
    out = {"table": table, "significance_vs_" + baseline: sig, "ci95": ci, "noise_curve": curve,
           "subset_sizes": subset, "kendall_tau_vs_human": agreement}
    tag = name or split
    json.dump(out, open(p(work, "results", f"report_{tag}.json"), "w"), indent=1)
    write_markdown(work, table, sig, ci, curve, types, baseline, metric, tag, subset, agreement)
    return out


def write_markdown(work, table, sig, ci, curve, types, baseline, metric, tag, subset, agreement):
    tnames = [t for t in QTYPES if t in types] + sorted(t for t in types if t not in QTYPES)
    lines = [f"# Results ({tag}, metric {metric} unless noted)", "",
             "| system | " + " | ".join(tnames) + " | MACRO | ALL | S@1 | S@10 | R@100 |",
             "|---|" + "---|" * (len(tnames) + 5)]
    best = {t: max((row[t][metric] for row in table.values() if row[t].get("n")), default=None) for t in tnames}
    for sysname, row in sorted(table.items(), key=lambda x: -x[1]["MACRO"].get(metric, x[1]["ALL"].get(metric, 0))):
        cells = []
        for t in tnames:
            if not row[t].get("n"):
                cells.append("–")
                continue
            v = row[t][metric]
            s = sig.get(t, {}).get(sysname)
            mark = "†" if s and s["p_holm"] < 0.05 and s["delta"] > 0 else ("‡" if s and s["p_holm"] < 0.05 else "")
            cell = f"{v:.3f}{mark}"
            cells.append(f"**{cell}**" if best[t] is not None and abs(v - best[t]) < 1e-9 else cell)
        a, mac = row["ALL"], row["MACRO"].get(metric, float("nan"))
        label = sysname + (f" (n={subset[sysname]})" if subset.get(sysname) else "")
        lines.append(f"| {label} | " + " | ".join(cells) +
                     f" | {mac:.3f} | {a[metric]:.3f} | {a['s1']:.3f} | {a['s10']:.3f} | {a['r100']:.3f} |")
    lines += ["", f"† significantly better / ‡ significantly worse than {baseline} "
                  "(paired randomization test, Holm-corrected within each column, p < 0.05). "
                  "MACRO = mean over the synthetic query types; bold = best in column.",
              "Queries per type: " + ", ".join(f"{t}={len(types[t])}" for t in tnames)]
    if curve:
        rates = sorted({r for c in curve.values() for r in c}, key=lambda x: int(x[1:]))
        lines += ["", "## Noise sweep (MRR@10 by share of corrupted syllables)", "",
                  "| system | " + " | ".join(r[1:] + "%" for r in rates) + " |",
                  "|---|" + "---|" * len(rates)]
        for sysname, c in sorted(curve.items(), key=lambda x: -x[1].get(rates[-1], 0)):
            lines.append(f"| {sysname} | " + " | ".join(f"{c.get(r, float('nan')):.3f}" for r in rates) + " |")
    if agreement:
        lines += ["", "## Agreement with human queries (Kendall tau of system orderings)", ""]
        lines += [f"- {t}: tau = {v['tau']:.3f} (p = {v['p']:.3g})" for t, v in agreement.items()]
    open(p(work, "results", f"report_{tag}.md"), "w").write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["build", "queries", "lexical", "fuse", "report", "all"])
    ap.add_argument("--work", default="work")
    ap.add_argument("--dbs", help="root folder of the group's SQLite databases")
    ap.add_argument("--jsonl", nargs="*", help="crawler JSONL exports")
    ap.add_argument("--split", default="test")
    ap.add_argument("--components", help="fuse: comma-separated run names")
    ap.add_argument("--adapt-last", type=int, help="fuse: tune only the last N weights per query type")
    ap.add_argument("--name", help="fuse: output prefix; report: output tag")
    ap.add_argument("--systems", help="report: comma-separated run names (default: every run)")
    ap.add_argument("--baseline", default="bm25_syl")
    a = ap.parse_args(argv)
    if a.stage in ("build", "all"):
        build(a.work, a.dbs, a.jsonl)
    if a.stage in ("queries", "all"):
        make_queries(a.work)
        write_unmarked_variants(a.work)
    if a.stage in ("lexical", "all"):
        lexical(a.work, a.split)
    if a.stage == "fuse":
        fuse(a.work, a.components.split(","), a.name, eval_split=a.split, adapt_last=a.adapt_last)
    if a.stage in ("report", "all"):
        report(a.work, a.baseline, split=a.split, systems=a.systems.split(",") if a.systems else None,
               name=a.name if a.stage == "report" else None)


if __name__ == "__main__":
    main()
