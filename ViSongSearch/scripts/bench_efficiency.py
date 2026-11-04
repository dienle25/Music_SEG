"""Time per passage and per query of every model on this machine (efficiency section).

Each model encodes the same random sample of passages and of test queries; dense search time
is measured against the model's full index, in batches and one query at a time; lexical search
is timed on the same queries. Run it on an otherwise idle machine, after the experiments.

    python scripts/bench_efficiency.py --work work_full --models e5small=models/multilingual-e5-small,...
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from visong import pipeline as P                     # noqa: E402
from visong.corpus import read_jsonl                 # noqa: E402
from visong.dense import DenseEncoder, load_index    # noqa: E402
from visong.lexical import LexicalIndex              # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--models", required=True, help="name=path,...; the index work/dense/<name> is searched")
    ap.add_argument("--n-passages", type=int, default=2000)
    ap.add_argument("--n-queries", type=int, default=500)
    a = ap.parse_args(argv)
    rng = random.Random(0)
    texts = [p["text"] for p in read_jsonl(os.path.join(a.work, "passages.jsonl"))]
    sample = rng.sample(texts, min(a.n_passages, len(texts)))
    test = [q for q in P.load_queries(a.work) if q["split"] == "test"]
    queries = rng.sample(test, min(a.n_queries, len(test)))
    qtexts = [q["text"] for q in queries]
    out = {"cpu_count": os.cpu_count(), "passages_timed": len(sample), "queries_timed": len(queries), "dense": {}}
    for spec in a.models.split(","):
        name, path = spec.split("=", 1)
        enc = DenseEncoder(path)
        enc.encode_passages(sample[:64])                      # warm-up
        t0 = time.time()
        enc.encode_passages(sample)
        tp = time.time() - t0
        t0 = time.time()
        qv = enc.encode_queries(qtexts)
        tq = time.time() - t0
        row = {"passages_per_s": len(sample) / tp, "ms_per_query_encode": 1000 * tq / len(qtexts)}
        if os.path.exists(os.path.join(a.work, "dense", name, "meta.json")):
            index = load_index(a.work, name)
            t0 = time.time()
            index.search_batch(qv, 100)
            row["ms_per_query_search_batch"] = 1000 * (time.time() - t0) / len(qv)
            t0 = time.time()
            for v in qv[:100]:
                index.search_batch(v[None, :], 100)
            row["ms_per_query_search_single"] = 1000 * (time.time() - t0) / min(100, len(qv))
            row["index_passages"] = len(index.pids)
            row["index_mb"] = index.vectors.nbytes / 1e6
            row["full_encode_minutes"] = len(index.pids) / row["passages_per_s"] / 60
        out["dense"][name] = row
        print(name, {k: round(v, 2) for k, v in row.items()}, flush=True)
    songs, _ = P.load(a.work)
    params = json.load(open(os.path.join(a.work, "results", "lexical_params.json")))
    out["lexical"] = {}
    for analyzer in ("nodiac12", "syl12"):
        p = params.get(f"bm25_{analyzer}", {})
        t0 = time.time()
        index = LexicalIndex([s.lyrics for s in songs], [s.doc_id for s in songs], analyzer,
                             p.get("k1", 0.9), p.get("b", 0.4))
        t_index = time.time() - t0
        t0 = time.time()
        for t in qtexts:
            index.search(t, 100)
        out["lexical"][analyzer] = {"index_s": t_index, "ms_per_query": 1000 * (time.time() - t0) / len(qtexts),
                                    "vocab": len(index.vocab)}
        print(analyzer, out["lexical"][analyzer], flush=True)
    json.dump(out, open(os.path.join(a.work, "results", "efficiency.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
