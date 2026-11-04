"""All neural experiments in one resumable run (meant for a Kaggle GPU notebook).

Steps, each skipped when its output already exists:
  1. lexical runs (CPU) if missing;
  2. zero-shot dense retrieval with every bi-encoder in MODELS, with the queries as typed and
     with their diacritics restored first (run names ending in _rest);
  3. NA-FT: noise-aware fine-tuning of the bases in NAFT_BASES with several seeds (also searched
     with restored queries), plus two ablations (no noise, random negatives) for the first seed;
  4. hybrid fusion of the lexical lists with the best zero-shot and the best NA-FT run
     (chosen on dev, never on test);
  5. cross-encoder reranking of the best hybrid run (chosen on dev), with passages chosen by
     the best dense run on dev and the query in the form that run used;
  6. the final report, per-query results and a manifest of versions and timings.

    python scripts/run_gpu_experiments.py --work work_pilot
    python scripts/run_gpu_experiments.py --work work_pilot --quick      # small smoke run
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from visong import pipeline as P                               # noqa: E402
from visong.corpus import read_jsonl                           # noqa: E402
from visong.dense import REST_SUFFIX                           # noqa: E402
from visong.evaluate import per_query, read_trec, summarize    # noqa: E402

MODELS = {   # name -> Hugging Face id (zero-shot bi-encoders)
    "e5small": "intfloat/multilingual-e5-small",
    "e5base": "intfloat/multilingual-e5-base",
    "viemb": "AITeamVN/Vietnamese_Embedding",
    "bgem3": "BAAI/bge-m3",
    "bkai": "bkai-foundation-models/vietnamese-bi-encoder",
}
NAFT_BASES = {"e5small": "intfloat/multilingual-e5-small", "e5base": "intfloat/multilingual-e5-base"}
SEEDS = (1, 2, 3)
RERANKERS = {"bgererank": "BAAI/bge-reranker-v2-m3", "vierank": "AITeamVN/Vietnamese_Reranker"}
LEX = ["restoreall_bm25_syl12", "bm25_nodiac12", "bm25_char3", "bm25_meta"]


FAILED = {}


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def guarded(step, fn, *args, **kw):
    """Run one step; a failure (e.g. a model that cannot be downloaded) is logged and skipped."""
    try:
        return fn(*args, **kw)
    except Exception as e:                      # noqa: BLE001 - an unattended run must go on
        import traceback
        traceback.print_exc()
        FAILED[step] = f"{type(e).__name__}: {e}"[:500]
        log(f"FAILED {step}: {FAILED[step]}")
        free_gpu()
        return None


def have(work, name):
    return os.path.exists(os.path.join(work, "runs", f"{name}.trec"))


def dev_score(work, name, cluster_of, dev):
    """Macro MRR@10 over query types on dev: used for every model choice."""
    run = read_trec(os.path.join(work, "runs", f"{name}.trec"))
    pq = per_query(run, dev, cluster_of)
    types = sorted({q["type"] for q in dev})
    return sum(summarize(pq, [q["qid"] for q in dev if q["type"] == t])["rr10"] for t in types) / len(types)


def dense(work, name, model, timings, batch_size=64, cache=None):
    from visong.dense import encode_corpus, search_queries
    if have(work, f"dense_{name}"):
        return
    t0 = time.time()
    encode_corpus(work, model, name, batch_size=batch_size, cache_root=cache)
    search_queries(work, model, name)
    timings[f"dense_{name}"] = round(time.time() - t0, 1)
    free_gpu()


def restored_search(work, name, model, timings):
    """Search the index `name` again with every query's diacritics restored first (the
    restorer of the lexical stage, trained on all indexed songs). Only the queries are encoded."""
    from visong.dense import search_queries
    if have(work, f"dense_{name}{REST_SUFFIX}"):
        return
    t0 = time.time()
    search_queries(work, model, name, restored="all")
    timings[f"dense_{name}{REST_SUFFIX}"] = round(time.time() - t0, 1)
    free_gpu()


def naft(work, base_name, base, seed, timings, noise=True, hard=True, epochs=1, batch_size=32, max_examples=None,
         cache=None):
    from visong.dense import encode_corpus, search_queries
    from visong.finetune import train
    tag = "" if noise and hard else ("_nonoise" if not noise else "_randneg")
    out = f"naft_{base_name}{tag}_s{seed}"
    if have(work, f"dense_{out}"):
        return out
    t0 = time.time()
    path = os.path.join(work, "models", out)
    if not os.path.exists(os.path.join(path, "naft_info.json")):
        train(work, base, out, seed=seed, epochs=epochs, batch_size=batch_size, max_examples=max_examples,
              noise=noise, hard_negatives=hard)
    free_gpu()
    encode_corpus(work, path, out, cache_root=cache)
    search_queries(work, path, out)
    timings[f"naft_{out}"] = round(time.time() - t0, 1)
    free_gpu()
    return out


def free_gpu():
    try:
        import gc

        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def versions():
    out = {"python": platform.python_version()}
    for mod in ("torch", "transformers", "sentence_transformers", "numpy", "scipy", "sklearn"):
        try:
            out[mod] = __import__(mod).__version__
        except ImportError:
            pass
    try:
        import torch
        out["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        pass
    try:
        out["git"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
    except OSError:
        pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work_pilot")
    ap.add_argument("--quick", action="store_true", help="two small models, one seed, 300 training pairs")
    ap.add_argument("--models", help="override MODELS: name=path,name=path")
    ap.add_argument("--naft-bases", help="override NAFT_BASES: name=path,...")
    ap.add_argument("--rerankers", help="override RERANKERS: name=path,...")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--rerank-queries", type=int, default=3000, help="max test queries to rerank (stratified)")
    ap.add_argument("--rerank-depth", type=int, default=20)
    ap.add_argument("--rerank-passages", type=int, default=2, help="passages scored per candidate song")
    ap.add_argument("--rerank-per-type", help="queries per type in each split, e.g. dev=40,test=100 "
                                              "(overrides --rerank-queries; the main types only)")
    ap.add_argument("--skip", default="", help="comma list of steps to skip: dense,naft,ablation,fuse,rerank")
    ap.add_argument("--max-examples", type=int, help="cap the NA-FT training examples (CPU runs)")
    ap.add_argument("--cache-dir", help="reuse passage vectors encoded before by the same weights")
    a = ap.parse_args(argv)

    def parse(s, default):
        return dict(x.split("=", 1) for x in s.split(",")) if s else dict(default)
    models = parse(a.models, MODELS)
    bases = parse(a.naft_bases, NAFT_BASES)
    rerankers = parse(a.rerankers, RERANKERS)
    seeds = [int(x) for x in a.seeds.split(",")]
    max_examples, cache = a.max_examples, a.cache_dir
    if a.quick:
        models = dict(list(models.items())[:2])
        bases = dict(list(bases.items())[:1])
        rerankers = dict(list(rerankers.items())[:1])
        seeds, max_examples = seeds[:1], 300
    skip = set(filter(None, a.skip.split(",")))
    work, timings = a.work, {}
    t_all = time.time()

    if not have(work, "bm25_syl") or not os.path.exists(os.path.join(work, "results", "qtype_pred.json")):
        log("lexical runs")
        P.lexical(work)
    _, cluster_of = P.load(work)
    dev = [q for q in P.load_queries(work) if q["split"] == "dev"]

    if "dense" not in skip:
        for name, model in models.items():
            log(f"dense {name} ({model})")
            guarded(f"dense_{name}", dense, work, name, model, timings, cache=cache)
            if have(work, f"dense_{name}"):
                guarded(f"dense_{name}{REST_SUFFIX}", restored_search, work, name, model, timings)
    naft_runs = []
    if "naft" not in skip:
        for bname, base in bases.items():
            for seed in seeds:
                log(f"NA-FT {bname} seed {seed}")
                out = guarded(f"naft_{bname}_s{seed}", naft, work, bname, base, seed, timings,
                              max_examples=max_examples, cache=cache)
                if out:
                    naft_runs.append(out)
                    guarded(f"dense_{out}{REST_SUFFIX}", restored_search, work, out,
                            os.path.join(work, "models", out), timings)
            if "ablation" not in skip:
                for noise, hard in ((False, True), (True, False)):
                    log(f"NA-FT ablation {bname} noise={noise} hard_negatives={hard}")
                    guarded(f"naft_{bname}_ablation_{noise}_{hard}", naft, work, bname, base, seeds[0], timings,
                            noise=noise, hard=hard, max_examples=max_examples, cache=cache)

    scores = {}
    if "fuse" not in skip:
        # each model with the queries as typed and restored; the dev score picks one of them
        zero = [r for n in models for r in (f"dense_{n}", f"dense_{n}{REST_SUFFIX}") if have(work, r)]
        tuned = [r for n in naft_runs for r in (f"dense_{n}", f"dense_{n}{REST_SUFFIX}") if have(work, r)]
        for group in (zero, tuned):
            if not group:
                continue
            for r in group:
                scores[r] = dev_score(work, r, cluster_of, dev)
            best = max(group, key=scores.get)
            name = "hybrid_" + best[len("dense_"):]
            log(f"fusion {name} (best on dev: {best} {scores[best]:.3f})")
            P.fuse(work, LEX + [best], name, adapt_last=1)
            for variant in ("global", "adaptive"):
                scores[f"{name}_{variant}"] = dev_score(work, f"{name}_{variant}", cluster_of, dev)
    if "rerank" not in skip and rerankers:
        from visong.rerank import rerank
        hybrids = [n for n in scores if n.startswith("hybrid_")]
        first = max(hybrids, key=scores.get) if hybrids else "lexall_global"
        dense_for_passages = [n for n in scores if n.startswith("dense_")]
        dname, restored = None, None
        if dense_for_passages:
            dname = max(dense_for_passages, key=scores.get)[len("dense_"):]
            if dname.endswith(REST_SUFFIX):           # same index and model, restored queries
                dname, restored = dname[:-len(REST_SUFFIX)], "all"
        dmodel = models.get(dname) or os.path.join(work, "models", dname or "")
        for rname, rmodel in rerankers.items():
            out = f"rerank_{rname}_{first}"
            if have(work, out) or dname is None:
                continue
            log(f"rerank {first} with {rname}; passages chosen with {dname}"
                + (" on restored queries" if restored else ""))
            t0 = time.time()
            per_type = {k: int(v) for k, v in (x.split("=") for x in a.rerank_per_type.split(","))} \
                if a.rerank_per_type else None
            guarded(out, rerank, work, first, rmodel, dname, dmodel, depth=a.rerank_depth,
                    max_queries=a.rerank_queries, out_name=out, restored=restored, n_passages=a.rerank_passages,
                    per_type=per_type, splits=tuple(per_type) if per_type else ("test",),
                    types=P.TYPED if per_type else None)
            timings[out] = round(time.time() - t0, 1)
            free_gpu()

    log("report")
    P.report(work)
    # compact per-query ranks of every run, enough for any later significance test
    queries = [q for q in P.load_queries(work) if q["split"] in ("dev", "test")]
    queries += read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    ranks = {}
    for f in sorted(os.listdir(os.path.join(work, "runs"))):
        if f.endswith(".trec"):
            pq = per_query(read_trec(os.path.join(work, "runs", f)), queries, cluster_of)
            ranks[f[:-5]] = {q: (None if v["rank"] == float("inf") else int(v["rank"])) for q, v in pq.items()}
    with gzip.open(os.path.join(work, "results", "perquery_ranks.json.gz"), "wt") as fh:
        json.dump(ranks, fh)
    manifest = {"versions": versions(), "timings_s": timings, "dev_macro_mrr10": scores, "failed": FAILED,
                "total_minutes": round((time.time() - t_all) / 60, 1), "models": models, "naft_bases": bases,
                "seeds": seeds, "rerankers": rerankers, "quick": a.quick}
    json.dump(manifest, open(os.path.join(work, "results", "gpu_manifest.json"), "w"), indent=1)
    log(f"done in {manifest['total_minutes']} min")


if __name__ == "__main__":
    main()
