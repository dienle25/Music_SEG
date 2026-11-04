"""How far conclusions drawn from the synthetic queries can be trusted, without human queries.

    python3 scripts/validity_analyses.py work_full [--split test] [--perms 2000]

Writes WORK/results/validity.json:
  type_agreement      Kendall's tau between the system orderings of every pair of query types
  leave_one_out       tau between each main type and the macro over the other main types
  family_agreement    tau between the macro over rule-based types (Q1-Q3) and over the
                      LLM-written types (Q4, Q5)
  noise_stability     tau between the ordering on clean fragments (N0) and each sweep level
  channel_agreement   tau between the orderings at the same noise rate under the hand-set
                      channel (N) and the channel estimated from real transcriptions (E)
  discriminative_power share of system pairs whose MRR@10 differs at alpha 0.05 (two-sided
                      randomization test) for each type (Sakai, SIGIR 2006)
  prompt agreement    falls out of type_agreement for the test-only variants: Q4s~Q4 and Q5g~Q5
                      (another prompt), Q3u~Q3 (the same noise typed without marks)
  mixture_sensitivity how the ordering by a weighted macro moves when the weights of the five main
                      types are drawn from a flat Dirichlet instead of being equal: mean tau with the
                      equal-weight ordering, and how often each system comes first
Only runs over every query are compared (sampled reranking runs are left out), and of the
NA-FT seeds only seed 1, so that repeated seeds do not count as different systems. Runs whose
ranks are identical on every test query (a fusion whose tuned weights reduce it to one of its
inputs) count once; the duplicates are listed.
"""
import argparse
import itertools
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import pipeline as P                                                  # noqa: E402
from visong.evaluate import by_type, kendall_tau, per_query, randomization_test, read_trec, summarize  # noqa: E402

RULE, LLM = ["Q1", "Q2", "Q3"], ["Q4", "Q5"]


def tau(a: dict, b: dict) -> dict:
    common = [s for s in a if s in b]
    t, p = kendall_tau({s: a[s] for s in common}, {s: b[s] for s in common})
    return {"tau": round(float(t), 3), "p": float(p), "n_systems": len(common)}


def discriminative_power(pq: dict, qids: list, alpha: float = 0.05, perms: int = 2000) -> dict:
    """Share of system pairs with a significant difference in reciprocal rank on these queries."""
    names = sorted(pq)
    vec = {s: np.array([pq[s][q]["rr10"] for q in qids]) for s in names}
    sig = pairs = 0
    for a, b in itertools.combinations(names, 2):
        pairs += 1
        sig += randomization_test(vec[a], vec[b], n=perms) < alpha
    return {"pairs": pairs, "significant": int(sig), "share": round(sig / pairs, 3) if pairs else None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--split", default="test")
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args(argv)
    _, cluster_of = P.load(a.work)
    queries = [q for q in P.load_queries(a.work) if q["split"] == a.split]
    sweep = P.C.read_jsonl(os.path.join(a.work, "queries", "noise_sweep.jsonl"))
    rd = os.path.join(a.work, "runs")
    names = sorted(f[:-5] for f in os.listdir(rd) if f.endswith(".trec"))
    names = [s for s in names if P.run_qids(a.work, s) is None and not re.search(r"_s[23]$", s)]
    types, swept = by_type(queries), by_type(sweep)
    main_types = [t for t in P.QTYPES if t in types]
    pq, score, curve = {}, {}, {}
    for s in names:
        run = read_trec(os.path.join(rd, s + ".trec"))
        if not any(q["qid"] in run for q in queries[:50]):
            continue
        pq[s] = per_query(run, queries, cluster_of)
        score[s] = {t: summarize(pq[s], [q for q in qids if q in pq[s]])["rr10"] for t, qids in types.items()}
        if any(q["qid"] in run for q in sweep[:20]):
            sp = per_query(run, sweep, cluster_of)
            curve[s] = {t: summarize(sp, qids)["rr10"] for t, qids in swept.items()}
    # identical runs count once (the first name in sorted order stays)
    duplicates, seen = {}, {}
    for s in sorted(pq):
        key = hash(tuple(pq[s][q["qid"]]["rank"] for q in queries))
        if key in seen:
            duplicates[s] = seen[key]
        else:
            seen[key] = s
    for s in duplicates:
        pq.pop(s)
        score.pop(s)
        curve.pop(s, None)
    col = lambda t: {s: score[s][t] for s in score}                              # noqa: E731
    macro = lambda ts: {s: float(np.mean([score[s][t] for t in ts])) for s in score}  # noqa: E731
    out = {"systems": sorted(score), "duplicates": duplicates, "scores": score,
           "type_agreement": {f"{x}~{y}": tau(col(x), col(y)) for x, y in itertools.combinations(sorted(types), 2)},
           "leave_one_out": {t: tau(col(t), macro([u for u in main_types if u != t])) for t in main_types},
           "family_agreement": tau(macro([t for t in RULE if t in types]), macro([t for t in LLM if t in types])),
           "noise_stability": {}, "discriminative_power": {}}
    if curve and "N0" in swept:
        base = {s: curve[s]["N0"] for s in curve}
        out["noise_stability"] = {t: tau(base, {s: curve[s][t] for s in curve}) for t in sorted(swept) if t != "N0"}
        out["noise_systems"] = sorted(curve)
        lv = lambda t: {s: curve[s][t] for s in curve}                               # noqa: E731
        out["channel_agreement"] = {f"{x}{r}~{y}{r}": tau(lv(f"{x}{r}"), lv(f"{y}{r}"))
                                    for x, y in (("N", "E"), ("N", "W"), ("E", "W")) for r in (10, 20, 30, 40, 50)
                                    if f"{x}{r}" in swept and f"{y}{r}" in swept}
    # type-mixture sensitivity
    rng = np.random.default_rng(0)
    names_m = sorted(score)
    mat = np.array([[score[s][t] for t in main_types] for s in names_m])
    equal = dict(zip(names_m, mat.mean(axis=1)))
    taus, first = [], {}
    for _ in range(1000):
        w = rng.dirichlet(np.ones(len(main_types)))
        weighted = dict(zip(names_m, mat @ w))
        taus.append(tau(equal, weighted)["tau"])
        top = max(weighted, key=weighted.get)
        first[top] = first.get(top, 0) + 1
    out["mixture_sensitivity"] = {"draws": 1000, "tau_mean": round(float(np.mean(taus)), 3),
                                  "tau_p05": round(float(np.percentile(taus, 5)), 3),
                                  "first_place": dict(sorted(first.items(), key=lambda x: -x[1])),
                                  "equal_weight_first": max(equal, key=equal.get)}
    common = [q["qid"] for q in queries if all(q["qid"] in pq[s] for s in pq)]
    for t, qids in sorted(types.items()):
        qs = [q for q in qids if q in set(common)]
        out["discriminative_power"][t] = dict(discriminative_power(pq, qs, a.alpha, a.perms), queries=len(qs))
    os.makedirs(os.path.join(a.work, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(a.work, "results", "validity.json"), "w"), indent=1)
    print(len(score), "systems;", "family tau", out["family_agreement"]["tau"])
    print("leave-one-out", {t: v["tau"] for t, v in out["leave_one_out"].items()})
    print("noise", {t: v["tau"] for t, v in out["noise_stability"].items()})
    print("disc. power", {t: v["share"] for t, v in out["discriminative_power"].items()})


if __name__ == "__main__":
    main()
