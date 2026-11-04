"""Further analyses of the test results; no new runs are made.

    python3 -I scripts/extra_analyses.py work_full [--perms 10000] [--reps 5]

Writes WORK/results/extra_analyses.json:
  planned       planned paired comparisons: difference in MRR@10 (B - A) per query type and at
                50% noise, two-sided randomization test, Holm correction over the comparisons
                within each column. Systems with several seeds count with their mean per query.
  dense_weight  MRR@10 per type of the selected hybrid for every weight of the dense
                list (lexical weights fixed at their global values), on dev and test, and the
                single weight that maximises the macro average on dev
  catalogue     MRR@10 per type when the index keeps a random share of the other clusters (the
                target is always kept). Rankings are those of the full index restricted to the
                kept songs, so BM25 keeps the term statistics of the full index; the hybrid is
                fused again from its restricted lists.
  errors        failures (target below rank 10) of the hybrid selected on dev, per type: predicted
                query type, overlap of the query with the lyrics, medleys ranked first, and
                songs of the target's artist ranked above it
"""
import argparse
import gzip
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import pipeline as P                                                  # noqa: E402
from visong.corpus import is_compilation_song                                     # noqa: E402
from visong.evaluate import holm, randomization_test, read_trec, target_rank      # noqa: E402
from visong.fusion import rrf                                                     # noqa: E402
from visong.text import remove_diacritics, syllables                              # noqa: E402

TYPES = ["Q1", "Q2", "Q3", "Q4", "Q5"]
ALL_TYPES = TYPES + ["Q4u", "Q5u"]
NAFT = [f"dense_naft_e5small_s{i}" for i in (1, 2, 3)]
# The hybrid selected on dev (it is also the first stage of the reranking) is read from the run
# manifest, with the rule of run_gpu_experiments.py; the other hybrid is its counterpart with the
# other dense model.
def _selected(work="work_full"):
    try:
        man = json.load(open(os.path.join(work, "results", "gpu_manifest.json")))["dev_macro_mrr10"]
        hyb = {k: v for k, v in man.items() if k.startswith("hybrid_")}
        return max(hyb, key=hyb.get) if hyb else None
    except (OSError, KeyError):
        return None


SEL = _selected(sys.argv[1] if len(sys.argv) > 1 else "work_full") or "hybrid_naft_e5small_s3_rest_adaptive"
OTHER = ("hybrid_e5base_rest_" if "naft" in SEL else "hybrid_naft_e5small_s3_rest_") + SEL.rsplit("_", 1)[1]
SEL_GLOBAL = SEL.rsplit("_", 1)[0] + "_global"
RERANK = "rerank_bgererank_" + SEL
PLANNED = [
    ("restore_lex", "Restored marked bigrams instead of unmarked bigrams", ["bm25_nodiac12"], ["restoreall_bm25_syl12"]),
    ("lexfusion", "Tuned lexical fusion instead of restored bigrams", ["restoreall_bm25_syl12"], ["lexall_global"]),
    ("ql", "Query likelihood instead of BM25 (syllables)", ["bm25_syl"], ["ql_syl"]),
    ("word", "Word segmentation instead of syllables", ["bm25_syl"], ["bm25_word"]),
    ("phon", "Phonetic keys instead of syllable bigrams", ["bm25_syl12"], ["bm25_phon12"]),
    ("charx", "Character 5-grams across syllables instead of unmarked bigrams", ["bm25_nodiac12"], ["bm25_char5x"]),
    ("adaptive", "Weights per predicted type instead of one weight vector (hybrid)", [SEL_GLOBAL], [SEL]),
    ("restore", "Restored instead of typed queries (mE5-base)", ["dense_e5base"], ["dense_e5base_rest"]),
    ("naft", "NA-FT instead of zero-shot (mE5-small, restored queries)", ["dense_e5small_rest"],
     [s + "_rest" for s in NAFT]),
    ("nonoise", "NA-FT without noise (seed 1)", NAFT[:1], ["dense_naft_e5small_nonoise_s1"]),
    ("randneg", "NA-FT without hard negatives (seed 1)", NAFT[:1], ["dense_naft_e5small_randneg_s1"]),
    ("hybrid", "Hybrid instead of lexical fusion", ["lexall_global"], [SEL]),
    ("oracle", "True instead of predicted query type (hybrid)", [SEL], [SEL.replace("_adaptive", "_oracle")]),
    ("otherdense", "mE5-base instead of NA-FT mE5-small in the hybrid", [SEL], [OTHER]),
    ("rerank", "Cross-encoder reranking (sampled queries)", [SEL], [RERANK]),
]
CATALOGUE_SYSTEMS = ["bm25_syl", "bm25_nodiac12", "restoreall_bm25_syl12", "lexall_global",
                     "dense_e5base_rest", "dense_naft_e5small_s1_rest", SEL]


def hybrid_params(work, name):
    """Fusion parameters of an adaptive hybrid run: hybrid_<dense run>_adaptive."""
    fp = json.load(open(os.path.join(work, "results", "fusion_params.json")))
    return fp[name[:-len("_adaptive")]]


def rr(rank):
    return 0.0 if rank is None or rank > 10 else 1.0 / rank


def load_ranks(work):
    with gzip.open(os.path.join(work, "results", "perquery_ranks.json.gz"), "rt") as fh:
        ranks = json.load(fh)
    for name in list(ranks):
        qp = os.path.join(work, "runs", f"{name}.qids")
        if os.path.exists(qp):
            keep = set(json.load(open(qp)))
            ranks[name] = {q: r for q, r in ranks[name].items() if q in keep}
    return ranks


def planned(ranks, groups, perms):
    cols = TYPES + ["Q4u", "Q5u", "Q3u", "Q4s", "Q5g", "N50", "W50"]
    out, pvals = {}, defaultdict(dict)
    for key, label, a, b in PLANNED:
        if not all(s in ranks for s in a + b):
            continue
        row = {"label": label, "a": a, "b": b, "cols": {}}
        for c in cols:
            qids = [q for q in groups.get(c, []) if all(q in ranks[s] for s in a + b)]
            if not qids:
                continue
            va = np.array([np.mean([rr(ranks[s][q]) for s in a]) for q in qids])
            vb = np.array([np.mean([rr(ranks[s][q]) for s in b]) for q in qids])
            row["cols"][c] = {"n": len(qids), "a": float(va.mean()), "b": float(vb.mean()),
                              "delta": float(vb.mean() - va.mean())}
            pvals[c][key] = randomization_test(vb, va, perms)
        if all(t in row["cols"] for t in TYPES):
            row["macro_delta"] = float(np.mean([row["cols"][t]["delta"] for t in TYPES]))
            # 95% bootstrap interval of the macro difference (queries resampled within each type)
            rng = np.random.default_rng(0)
            diffs = {t: np.array([np.mean([rr(ranks[s][q]) for s in b]) - np.mean([rr(ranks[s][q]) for s in a])
                                  for q in groups[t] if all(q in ranks[s] for s in a + b)]) for t in TYPES}
            boots = [np.mean([rng.choice(d, len(d)).mean() for d in diffs.values()]) for _ in range(2000)]
            row["macro_delta_ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
        out[key] = row
    for c, pv in pvals.items():
        for key, p_adj in holm(pv).items():
            out[key]["cols"][c]["p_raw"] = pv[key]
            out[key]["cols"][c]["p_holm"] = p_adj
    return out


def fuse_rank(lists, weights, cluster, cluster_of):
    return target_rank(rrf(lists, weights=list(weights)), cluster, cluster_of)


def dense_weight(work, queries, cluster_of):
    fp = hybrid_params(work, SEL)
    comps, head = fp["components"], list(fp["global"]["weights"][:-1])
    runs = {n: read_trec(os.path.join(work, "runs", f"{n}.trec")) for n in comps}
    res = {}
    for w in P.ADAPT_VALUES:
        res[str(w)] = {}
        for split in ("dev", "test"):
            per = defaultdict(list)
            for q in queries:
                if q["split"] != split or q["type"] not in ALL_TYPES:
                    continue
                lists = [runs[n].get(q["qid"], []) for n in comps]
                per[q["type"]].append(rr(fuse_rank(lists, head + [w], q["cluster"], cluster_of)))
            d = {t: float(np.mean(v)) for t, v in per.items()}
            d["macro"] = float(np.mean([d[t] for t in TYPES]))
            res[str(w)][split] = d
    best_w = max(P.ADAPT_VALUES, key=lambda w: (round(res[str(w)]["dev"]["macro"], 10), -w))
    return {"system": SEL, "components": comps, "head": head, "by_weight": res, "macro_tuned_weight": best_w}


def restricted(ranking, kept, cluster_of):
    return [(d, s) for d, s in ranking if cluster_of.get(d, d) in kept]


def catalogue(work, queries, cluster_of, fractions=(0.25, 0.5, 1.0), reps=5):
    test = [q for q in queries if q["split"] == "test" and q["type"] in ALL_TYPES]
    clusters = sorted(set(cluster_of.values()))
    fp = hybrid_params(work, SEL)
    pred = json.load(open(os.path.join(work, "results", "qtype_pred.json")))["logreg"]
    w_of = {t: v["weights"] for t, v in fp["per_type"].items()}
    out = {}
    for s in CATALOGUE_SYSTEMS:
        if s == SEL:
            comp_runs = {n: read_trec(os.path.join(work, "runs", f"{n}.trec")) for n in fp["components"]}
        else:
            path = os.path.join(work, "runs", f"{s}.trec")
            if not os.path.exists(path):            # a run that failed: leave it out, keep the rest
                out[s] = {"missing": True}
                continue
            run = read_trec(path)
        res = {}
        for f in fractions:
            per = defaultdict(list)
            for rep in range(reps if f < 1 else 1):
                rng = random.Random(1000 * rep + int(100 * f))
                kept = {c for c in clusters if f >= 1 or rng.random() < f}
                for q in test:
                    k = kept | {q["cluster"]}
                    if s == SEL:
                        lists = [restricted(comp_runs[n].get(q["qid"], []), k, cluster_of) for n in fp["components"]]
                        w = w_of.get(pred.get(q["qid"]), fp["global"]["weights"])
                        rank = fuse_rank(lists, w, q["cluster"], cluster_of)
                    else:
                        rank = target_rank(restricted(run.get(q["qid"], []), k, cluster_of), q["cluster"], cluster_of)
                    per[q["type"]].append(rr(rank))
            d = {t: float(np.mean(v)) for t, v in per.items()}
            d["macro"] = float(np.mean([d[t] for t in TYPES]))
            res[str(f)] = d
        out[s] = res
        print("catalogue", s, {f: round(v["macro"], 3) for f, v in res.items()}, flush=True)
    return {"fractions": list(fractions), "reps": reps, "clusters": len(clusters), "systems": out}


def unmarked(text):
    return [remove_diacritics(x) for x in syllables(text or "")]


def longest_run(q, lyr):
    best, prev = 0, [0] * (len(lyr) + 1)
    for x in q:
        cur = [0] * (len(lyr) + 1)
        for j, y in enumerate(lyr, start=1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def artists(song):
    parts = re.split(r"\s*(?:,|;|/|&|\bft\.?|\bfeat\.?|\bx\b)\s*", song.artist or "", flags=re.I)
    return {" ".join(unmarked(p)) for p in parts if len(unmarked(p)) >= 1 and len(" ".join(unmarked(p))) >= 2}


def names_artist(qsyl, song):
    """True when the query contains two consecutive syllables of one of the song's artist names
    (or the whole name, when it has one syllable of four letters or more)."""
    qs = " " + " ".join(qsyl) + " "
    for name in artists(song):
        toks = name.split()
        if len(toks) == 1:
            if len(toks[0]) >= 4 and f" {toks[0]} " in qs:
                return True
            continue
        if any(f" {toks[i]} {toks[i + 1]} " in qs for i in range(len(toks) - 1)):
            return True
    return False


def errors(work, queries, songs, cluster_of):
    by_id = {s.doc_id: s for s in songs}
    pred = json.load(open(os.path.join(work, "results", "qtype_pred.json")))["logreg"]
    run = read_trec(os.path.join(work, "runs", f"{SEL}.trec"))
    out = {}
    for t in ALL_TYPES:
        qs = [q for q in queries if q["split"] == "test" and q["type"] == t]
        rows = []
        for q in qs:
            ranking = run.get(q["qid"], [])
            rank = target_rank(ranking, q["cluster"], cluster_of)
            target = by_id[q["doc_id"]]
            qsyl, lyr = unmarked(q["text"]), unmarked(target.lyrics)
            lyr_set = set(lyr)
            first = by_id.get(ranking[0][0]) if ranking else None
            above, seen = [], set()
            for d, _ in ranking:
                c = cluster_of.get(d, d)
                if c == q["cluster"]:
                    break
                if c not in seen:
                    seen.add(c)
                    above.append(by_id[d])
                if len(above) >= 10:
                    break
            rows.append({"fail": rank is None or rank > 10, "pred": pred.get(q["qid"]),
                         "overlap": sum(x in lyr_set for x in qsyl) / max(1, len(qsyl)),
                         "run": longest_run(qsyl, lyr), "len": len(qsyl),
                         "medley_first": bool(first and first.doc_id not in {target.doc_id}
                                              and is_compilation_song(first)),
                         "names_artist": names_artist(qsyl, target),
                         "same_artist_above": bool(artists(target) & set().union(*(artists(s) for s in above)))
                         if above else False})
        fails = [r for r in rows if r["fail"]]
        oks = [r for r in rows if not r["fail"]]
        mean = lambda rs, k: float(np.mean([r[k] for r in rs])) if rs else None    # noqa: E731
        out[t] = {"n": len(rows), "failures": len(fails),
                  "failures_by_predicted_type": dict(Counter(r["pred"] for r in fails)),
                  "fail": {k: mean(fails, k) for k in ("overlap", "run", "len", "medley_first", "names_artist",
                                                      "same_artist_above")},
                  "ok": {k: mean(oks, k) for k in ("overlap", "run", "len", "medley_first", "names_artist",
                                                  "same_artist_above")}}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--perms", type=int, default=10000)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--only", default="planned,dense_weight,catalogue,errors")
    a = ap.parse_args(argv)
    songs, cluster_of = P.load(a.work)
    queries = P.load_queries(a.work)
    sweep = P.C.read_jsonl(os.path.join(a.work, "queries", "noise_sweep.jsonl"))
    groups = defaultdict(list)
    for q in queries:
        if q["split"] == "test":
            groups[q["type"]].append(q["qid"])
    for q in sweep:
        groups[q["type"]].append(q["qid"])
    path = os.path.join(a.work, "results", "extra_analyses.json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    todo = a.only.split(",")
    if "planned" in todo:
        out["planned"] = planned(load_ranks(a.work), groups, a.perms)
        print("planned", {k: {c: round(v["delta"], 3) for c, v in r["cols"].items()} for k, r in out["planned"].items()})
    if "dense_weight" in todo:
        out["dense_weight"] = dense_weight(a.work, queries, cluster_of)
        print("dense weight", {w: round(v["test"]["macro"], 3) for w, v in out["dense_weight"]["by_weight"].items()},
              "macro-tuned", out["dense_weight"]["macro_tuned_weight"])
    if "errors" in todo:
        out["errors"] = errors(a.work, queries, songs, cluster_of)
        print("errors", {t: (v["failures"], v["n"]) for t, v in out["errors"].items()})
    if "catalogue" in todo:
        out["catalogue"] = catalogue(a.work, queries, cluster_of, reps=a.reps)
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()
