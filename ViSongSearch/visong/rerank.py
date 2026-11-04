"""Cross-encoder reranking of the top candidates (TMG 07-08, SEG 7 learning to rank).

For each candidate song we score its two passages most similar to the query
(chosen with the dense index) and keep the higher score. With restored="all" the query is
used in its diacritic-restored form throughout, as by the dense run dense_<name>_rest.

    python -m visong.rerank --work work --run rrf_hybrid --model models/bge-reranker-v2-m3 \
        --dense e5base --dense-model models/multilingual-e5-base --depth 20 --max-queries 1000
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time

import numpy as np

from .corpus import read_jsonl
from .dense import DenseEncoder, load_index, query_texts
from .evaluate import read_trec, write_trec
from .queries import QUERY_FILES
from .text import clean


def rerank(work, run_name, model_path, dense_name, dense_model, depth=20, max_queries=None,
           splits=("test",), batch_size=32, seed=0, out_name=None, restored=None, n_passages=2,
           per_type: dict | None = None, types=None):
    """per_type={"dev": 40, "test": 100} samples that many queries of each type in each split
    (instead of max_queries spread over the types); types limits the query types."""
    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    run = read_trec(os.path.join(work, "runs", f"{run_name}.trec"))
    queries = []
    for f in QUERY_FILES:
        path = os.path.join(work, "queries", f)
        if os.path.exists(path):
            queries += [q for q in read_jsonl(path) if q["split"] in splits and q["qid"] in run
                        and (types is None or q["type"] in types)]
    if per_type:
        rng = random.Random(seed)
        by_st = {}
        for q in queries:
            by_st.setdefault((q["split"], q["type"]), []).append(q)
        queries = [q for k in sorted(by_st) for q in (by_st[k] if len(by_st[k]) <= per_type.get(k[0], 0)
                                                       else rng.sample(by_st[k], per_type.get(k[0], 0)))]
    elif max_queries and len(queries) > max_queries:
        # stratified sample: same number per query type
        rng = random.Random(seed)
        by_t = {}
        for q in queries:
            by_t.setdefault(q["type"], []).append(q)
        per = max_queries // len(by_t)
        queries = [q for t in sorted(by_t) for q in (by_t[t] if len(by_t[t]) <= per else rng.sample(by_t[t], per))]
    passages = {p["pid"]: p["text"] for p in read_jsonl(os.path.join(work, "passages.jsonl"))}
    index = load_index(work, dense_name)
    texts = query_texts(work, queries, restored)
    enc = DenseEncoder(dense_model)
    qv = enc.encode_queries(texts)
    ce = CrossEncoder(model_path, max_length=256, device="cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        ce.model.half()                              # inference only
    out, t0, pairs_scored = {}, time.time(), 0
    for q, text, vec in zip(queries, texts, qv):
        cands = [d for d, _ in run[q["qid"]][:depth]]
        best = index.best_passages(vec, cands, n=n_passages)
        pairs, owners = [], []
        for d in cands:
            for i in best.get(d, []):
                pairs.append((clean(text), clean(passages[index.pids[i]])))
                owners.append(d)
        scores = ce.predict(pairs, batch_size=batch_size, show_progress_bar=False) if pairs else []
        pairs_scored += len(pairs)
        doc_score = {}
        for d, s in zip(owners, scores):
            doc_score[d] = max(doc_score.get(d, -1e9), float(s))
        reranked = sorted(doc_score.items(), key=lambda x: -x[1])
        tail = [(d, -1e6 - r) for r, (d, _) in enumerate(run[q["qid"]][depth:])]
        out[q["qid"]] = reranked + tail
    secs = time.time() - t0
    name = out_name or f"rerank_{os.path.basename(model_path.rstrip('/'))}_{run_name}"
    write_trec(os.path.join(work, "runs", f"{name}.trec"), out, name)
    # the report evaluates a sampled run only on the queries it covers
    json.dump(sorted(out), open(os.path.join(work, "runs", f"{name}.qids"), "w"))
    stats = {"queries": len(queries), "pairs": pairs_scored, "seconds": secs, "depth": depth,
             "passages_per_song": n_passages,
             "ms_per_query": 1000 * secs / max(1, len(queries)), "restored": restored}
    json.dump(stats, open(os.path.join(work, "results", f"{name}_stats.json"), "w"), indent=1)
    print(name, stats, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work")
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--dense", required=True, help="name of the encoded dense index")
    ap.add_argument("--dense-model", required=True)
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--max-queries", type=int)
    ap.add_argument("--out")
    ap.add_argument("--restored", choices=["all", "train"], help="rerank with diacritic-restored queries")
    ap.add_argument("--passages", type=int, default=2, help="passages scored per candidate song")
    a = ap.parse_args(argv)
    rerank(a.work, a.run, a.model, a.dense, a.dense_model, a.depth, a.max_queries, out_name=a.out,
           restored=a.restored, n_passages=a.passages)


if __name__ == "__main__":
    main()
