"""Re-tune query likelihood and RM3 over the extended grids of visong.pipeline and rewrite their runs.

    python3 -I scripts/retune_lm.py work_full

The first full run tuned mu over {30, ..., 2000} and RM3 over 5-10 feedback songs and 10-20 terms;
the best values lay at the edges of those grids. This script repeats step 3 of `pipeline lexical`
with the extended grids (GRID_MU, GRID_RM3), using the BM25 parameters already tuned for each
index, and rewrites ql_syl, rm3_syl, rm3_nodiac and rm3_syl12 and their entries in
results/lexical_params.json. Nothing else changes.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import corpus as C                                                    # noqa: E402
from visong import pipeline as P                                                  # noqa: E402
from visong.evaluate import write_trec                                            # noqa: E402
from visong.lexical import LexicalIndex                                           # noqa: E402


def main(argv=None):
    work = (argv or sys.argv[1:])[0]
    songs, cluster_of = P.load(work)
    queries = P.load_queries(work)
    sweep = C.read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    dev = [q for q in queries if q["split"] == "dev"]
    test = [q for q in queries if q["split"] == "test"]
    allq = test + dev + sweep
    path = os.path.join(work, "results", "lexical_params.json")
    params = json.load(open(path))
    texts, ids = [s.lyrics for s in songs], [s.doc_id for s in songs]
    idx = {}
    for a in ("syl", "nodiac", "syl12"):
        idx[a] = LexicalIndex(texts, ids, a)
        bp = params[f"bm25_{a}"]
        idx[a].set_params(bp["k1"], bp["b"])
    t0 = time.time()
    ql_dev = {mu: P.mrr(P.run_index(idx["syl"], dev, model="ql", mu=mu), dev, cluster_of) for mu in P.GRID_MU}
    best_mu = max(ql_dev, key=ql_dev.get)
    params["ql_syl"] = {"mu": best_mu, "dev_mrr10_by_mu": ql_dev}
    print("ql", {k: round(v, 4) for k, v in ql_dev.items()}, "->", best_mu, f"{time.time() - t0:.0f}s", flush=True)
    rm3_dev = {i: P.mrr(P.run_index(idx["syl"], dev, model="rm3", mu=best_mu, **g), dev, cluster_of)
               for i, g in enumerate(P.GRID_RM3)}
    bi = max(rm3_dev, key=rm3_dev.get)
    rm3 = P.GRID_RM3[bi]
    params["rm3"] = {**rm3, "mu": best_mu, "dev_mrr10": rm3_dev[bi]}
    print("rm3", params["rm3"], f"{time.time() - t0:.0f}s", flush=True)
    runs = {"ql_syl": P.run_index(idx["syl"], allq, model="ql", mu=best_mu),
            "rm3_syl": P.run_index(idx["syl"], allq, model="rm3", mu=best_mu, **rm3),
            "rm3_nodiac": P.run_index(idx["nodiac"], allq, model="rm3", mu=best_mu, **rm3),
            "rm3_syl12": P.run_index(idx["syl12"], allq, model="rm3", mu=best_mu, **rm3)}
    for name, run in runs.items():
        write_trec(os.path.join(work, "runs", f"{name}.trec"), run, name)
    json.dump(params, open(path, "w"), indent=1, ensure_ascii=False)
    print("wrote", sorted(runs), f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
