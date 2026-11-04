"""Analyses added in the first revision. Fusions are recomputed from stored runs; no new search.

    python3 -I scripts/round1_analyses.py work_full

Writes WORK/results/round1_analyses.json:
  selected        the hybrid with the best dev macro (the rule of run_gpu_experiments.py) and its lists
  fusion_alt      other ways to add the dense list to the lexical fusion, each tuned on dev:
                  - rrf2 / convex: RRF or min-max score fusion (Bruch et al., TOIS 2023) of the lexical
                    fusion and the dense list, one weight for all queries (global) or one per
                    predicted type (adaptive, kept per type only if it wins 5-fold CV on dev);
                  - route: for each predicted type, the best of lexical fusion, dense list and the
                    global hybrid on dev queries of that type;
                  - margin: no labels; the dense list joins the hybrid with weight w only when the
                    BM25 top-1 margin (s1 - s2) / s1 of the restored-bigram run is below theta
  qtype_ablation  the classifier without its length feature, and the effect of a leading "bài" on
                  the predictions for Q5/Q5u; with those predictions, the hybrid's test MRR@10
  nonpilot        main systems on test queries about clusters outside the pilot catalogue
  rerank          the sampled reranking: as run, interpolated with the first-stage score (alpha
                  tuned on the dev sample) and selective (only the predicted types for which
                  reranking helped on the dev sample)
  restore_train   the selected hybrid with Restore_train in place of Restore_all (lexical list, and
                  the dense list when dense_<model>_resttrain exists), with the same weights
  ambiguity       fragments drawn and rejected by the ambiguity filter when the rule queries were
                  generated (regenerated with the same seed; checked against rule.jsonl)
  restorer_cap    share of syllable tokens whose marked form is among the restorer's candidates
  cluster_check   lyric similarity of the version pairs inside each multi-song cluster
  short_fragments share of random test fragments of n syllables that also occur in another cluster
Every tuned value is chosen on dev; "dev macro" is the mean MRR@10 over the dev query types.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import itertools
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import corpus as C                                                    # noqa: E402
from visong import pipeline as P                                                  # noqa: E402
from visong.evaluate import target_rank                                           # noqa: E402
from visong.fusion import combsum, rrf                                            # noqa: E402
from visong.text import clean, remove_diacritics, syllables                       # noqa: E402

MAIN = ["Q1", "Q2", "Q3", "Q4", "Q5"]
TYPED = MAIN + ["Q4u", "Q5u"]
VARIANTS = ["Q3u", "Q4s", "Q5g"]
SWEEP_QID = re.compile(r"-[rew]\d+$")


def rr(rank):
    return 0.0 if rank is None or rank > 10 else 1.0 / rank


def read_run(path, keep=None):
    """Like evaluate.read_trec, without the noise-sweep queries (or only the qids in `keep`)."""
    run = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            q, _, d, r, s, _ = line.split()
            if (keep is not None and q not in keep) or (keep is None and SWEEP_QID.search(q)):
                continue
            run[q].append((int(r), d, float(s)))
    return {q: [(d, s) for _, d, s in sorted(v)] for q, v in run.items()}


def dev_macro_of(work, name, dev, cluster_of):
    run = read_run(os.path.join(work, "runs", f"{name}.trec"), {q["qid"] for q in dev})
    per = defaultdict(list)
    for q in dev:
        per[q["type"]].append(rr(target_rank(run.get(q["qid"], []), q["cluster"], cluster_of)))
    return float(np.mean([np.mean(v) for v in per.values()]))


def selected(work, dev=None, cluster_of=None):
    """The hybrid with the best dev macro, as recorded by run_gpu_experiments.py (or recomputed)."""
    man = json.load(open(os.path.join(work, "results", "gpu_manifest.json")))
    scores = {k: v for k, v in man["dev_macro_mrr10"].items() if k.startswith("hybrid_")}
    if not scores:
        names = [f[:-5] for f in os.listdir(os.path.join(work, "runs"))
                 if f.startswith("hybrid_") and f.endswith(("_global.trec", "_adaptive.trec"))]
        scores = {n: dev_macro_of(work, n, dev, cluster_of) for n in names}
    sel = max(scores, key=scores.get)
    base = sel.rsplit("_", 1)[0]
    fp = json.load(open(os.path.join(work, "results", "fusion_params.json")))[base]
    return {"system": sel, "base": base, "dev_macro": scores[sel], "components": fp["components"],
            "global_weights": fp["global"]["weights"],
            "per_type": {t: v["weights"] for t, v in fp["per_type"].items()}, "dev_scores": scores}


class Evaluator:
    def __init__(self, queries, cluster_of):
        self.queries, self.cluster_of = queries, cluster_of

    def scores(self, rank_of, split, types):
        per = defaultdict(list)
        for q in self.queries:
            if q["split"] == split and q["type"] in types:
                per[q["type"]].append(rr(rank_of(q)))
        out = {t: float(np.mean(v)) for t, v in per.items()}
        out["n"] = {t: len(v) for t, v in per.items()}
        main = [t for t in MAIN if t in out]
        out["macro"] = float(np.mean([out[t] for t in main])) if main else None
        dev_types = [t for t in TYPED if t in out]
        out["macro7"] = float(np.mean([out[t] for t in dev_types])) if dev_types else None
        return out


def fold(qid, k=5):
    return int(hashlib.md5(f"cv:{qid}".encode()).hexdigest()[:6], 16) % k


def tune_param(values, rr_of, dev, pred_dev):
    """rr_of[qid][value] -> RR@10. Global value by pooled dev MRR (as the RRF tuner); per true type,
    a value kept only if it beats the global one in 5-fold CV on that type's dev queries."""
    def mean(qs, v):
        return float(np.mean([rr_of[q["qid"]][v] for q in qs])) if qs else 0.0
    g = max(values, key=lambda v: (round(mean(dev, v), 10), -abs(v - values[len(values) // 2])))
    per_type = {}
    for t in sorted({q["type"] for q in dev}):
        dq = [q for q in dev if q["type"] == t]
        cv_t = cv_g = 0.0
        for f in range(5):
            held = [q for q in dq if fold(q["qid"]) == f]
            rest = [q for q in dq if fold(q["qid"]) != f]
            rest_all = [q for q in dev if fold(q["qid"]) != f]
            if not held or not rest:
                continue
            vt = max(values, key=lambda v: mean(rest, v))
            vg = max(values, key=lambda v: mean(rest_all, v))
            cv_t += mean(held, vt) * len(held)
            cv_g += mean(held, vg) * len(held)
        per_type[t] = max(values, key=lambda v: mean(dq, v)) if cv_t > cv_g else g
    return g, per_type


def fusion_alternatives(ev, queries, cluster_of, runs, sel, pred):
    lex, dense = runs["lexall_global"], runs[sel["components"][-1]]
    comps = sel["components"]
    dev = [q for q in queries if q["split"] == "dev" and q["type"] in TYPED]
    test = [q for q in queries if q["split"] == "test"]
    targets = dev + test
    out = {}
    tr = lambda ranking, q: target_rank(ranking, q["cluster"], cluster_of)          # noqa: E731

    # two-list fusions: RRF (weights 1, w) and convex (1 - a, a)
    specs = {"rrf2": (list(P.ADAPT_VALUES), lambda q, w: rrf([lex.get(q["qid"], []), dense.get(q["qid"], [])],
                                                              weights=[1, w])),
             "convex": ([round(x, 2) for x in np.arange(0, 1.01, 0.05)],
                        lambda q, a: combsum([lex.get(q["qid"], []), dense.get(q["qid"], [])], weights=[1 - a, a]))}
    for name, (values, fuse) in specs.items():
        rr_of = {q["qid"]: {v: rr(tr(fuse(q, v), q)) for v in values} for q in targets}
        g, per_type = tune_param(values, rr_of, dev, pred)
        out[name] = {"values": values, "global_value": g, "per_type_value": per_type,
                     "global": {s: ev.scores(lambda q: None if q["qid"] not in rr_of else
                                             (1 / rr_of[q["qid"]][g] if rr_of[q["qid"]][g] else None), s, TYPED + VARIANTS)
                                for s in ("dev", "test")},
                     "adaptive": {s: ev.scores(lambda q: (lambda v: 1 / v if v else None)(
                         rr_of[q["qid"]][per_type.get(pred.get(q["qid"]), g)]), s, TYPED + VARIANTS)
                                  for s in ("dev", "test")}}

    # routing by predicted type over three systems
    sysruns = {"lexical": lex, "dense": dense, "hybrid_global": runs[sel["base"] + "_global"]}
    rank = {s: {q["qid"]: tr(r.get(q["qid"], []), q) for q in targets} for s, r in sysruns.items()}
    choice = {}
    for t in sorted({q["type"] for q in dev}):
        dq = [q for q in dev if q["type"] == t]
        choice[t] = max(sysruns, key=lambda s: np.mean([rr(rank[s][q["qid"]]) for q in dq]))
    out["route"] = {"choice": choice,
                    **{s: ev.scores(lambda q: rank[choice.get(pred.get(q["qid"]), "hybrid_global")][q["qid"]], s,
                                    TYPED + VARIANTS) for s in ("dev", "test")}}

    # label-free margin router on top of the hybrid's global lexical weights
    margin_run = runs["restoreall_bm25_syl12"]

    def margin(q):
        r = margin_run.get(q["qid"], [])
        if len(r) < 2 or r[0][1] <= 0:
            return 0.0
        return (r[0][1] - r[1][1]) / r[0][1]
    m = {q["qid"]: margin(q) for q in targets}
    head = list(sel["global_weights"][:-1])
    lists = {q["qid"]: [runs[n].get(q["qid"], []) for n in comps] for q in targets}
    wvals = [w for w in P.ADAPT_VALUES if w > 0]
    rr_w = {q["qid"]: {w: rr(tr(rrf(lists[q["qid"]], weights=head + [w]), q)) for w in [0] + wvals} for q in targets}
    thetas = sorted(set(round(float(x), 4) for x in np.quantile([m[q["qid"]] for q in dev],
                                                                 [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])))

    def dev_macro(th, w):
        per = defaultdict(list)
        for q in dev:
            per[q["type"]].append(rr_w[q["qid"]][w if m[q["qid"]] < th else 0])
        return float(np.mean([np.mean(v) for v in per.values()]))
    th, w = max(itertools.product(thetas, wvals), key=lambda x: dev_macro(*x))
    out["margin"] = {"theta": th, "weight": w, "thetas": thetas,
                     **{s: ev.scores(lambda q: (lambda v: 1 / v if v else None)(
                         rr_w[q["qid"]][w if m[q["qid"]] < th else 0]), s, TYPED + VARIANTS) for s in ("dev", "test")},
                     "share_routed_test": {t: float(np.mean([m[q["qid"]] < th for q in test if q["type"] == t]))
                                           for t in TYPED + VARIANTS}}
    out["reference"] = {s: ev.scores(lambda q: tr(runs[sel["system"]].get(q["qid"], []), q), s, TYPED + VARIANTS)
                        for s in ("dev", "test")}
    return out


def hybrid_rank_with(runs, sel, pred_of, q, cluster_of, swap=None):
    comps = [swap.get(n, n) if swap else n for n in sel["components"]]
    w = sel["per_type"].get(pred_of(q), sel["global_weights"])
    return target_rank(rrf([runs[n].get(q["qid"], []) for n in comps], weights=list(w)), q["cluster"], cluster_of)


def qtype_ablation(work, ev, queries, songs, cluster_of, runs, sel):
    from visong.qtype import QueryTypeClassifier
    vm = {s for song in songs for s in syllables(song.lyrics)}
    vp = {remove_diacritics(s) for s in vm}
    train_q = [q for q in queries if q["split"] == "train" and q["type"] in TYPED]
    dev_llm = [q for q in queries if q["split"] == "dev" and q["type"] in ("Q4", "Q5", "Q4u", "Q5u")]
    test = [q for q in queries if q["split"] == "test"]
    fit_q = train_q + dev_llm
    params = json.load(open(os.path.join(work, "results", "lexical_params.json")))["qtype_logreg"]
    balanced = params["balanced"]
    out = {}
    for name, drop in (("full", ()), ("no_length", ("length",))):
        clf = QueryTypeClassifier(vm, vp, "logreg", balanced, drop=drop).fit([q["text"] for q in fit_q],
                                                                            [q["type"] for q in fit_q])
        pred = dict(zip([q["qid"] for q in test], clf.predict([q["text"] for q in test])))
        typed = [q for q in test if q["type"] in TYPED]
        acc = {t: float(np.mean([pred[q["qid"]] == q["type"] for q in typed if q["type"] == t])) for t in TYPED}
        acc["all"] = float(np.mean([pred[q["qid"]] == q["type"] for q in typed]))
        res = {"accuracy": acc,
               "hybrid": ev.scores(lambda q: hybrid_rank_with(runs, sel, lambda x: pred.get(x["qid"]), q, cluster_of),
                                   "test", TYPED + VARIANTS)}
        if name == "full":
            lead = re.compile(r"^\s*(bài hát|bài|bai hat|bai)\s+", re.I)
            q5 = [q for q in typed if q["type"] in ("Q5", "Q5u")]
            with_lead = [q for q in q5 if lead.match(q["text"])]
            stripped = clf.predict([lead.sub("", q["text"]) for q in with_lead]) if with_lead else []
            res["leading_bai"] = {"queries": len(q5), "with_lead": len(with_lead),
                                  "accuracy_as_written": float(np.mean([pred[q["qid"]] == q["type"] for q in with_lead]))
                                  if with_lead else None,
                                  "accuracy_without_lead": float(np.mean([p == q["type"] for p, q in zip(stripped, with_lead)]))
                                  if with_lead else None,
                                  "variants_predicted": {t: dict(Counter(pred[q["qid"]] for q in test if q["type"] == t))
                                                         for t in VARIANTS}}
        out[name] = res
    return out


def nonpilot(work, ev, queries, cluster_of, systems):
    # Ids only (no lyrics) in <work>/pilot_doc_ids.json; else the pilot corpus next to <work>.
    ids_path = os.path.join(work, "pilot_doc_ids.json")
    if os.path.exists(ids_path):
        pilot_docs = set(json.load(open(ids_path)))
    else:
        pilot_docs = {r["doc_id"] for r in C.read_jsonl(os.path.join(os.path.dirname(work.rstrip("/")) or ".",
                                                                     "work_pilot", "corpus.jsonl"))}
    pilot_clusters = {cluster_of[d] for d in pilot_docs if d in cluster_of}
    test = [q for q in queries if q["split"] == "test"]
    keep = [q for q in test if q["cluster"] not in pilot_clusters]
    sub = Evaluator(keep, cluster_of)
    res = {"pilot_clusters": len(pilot_clusters), "test_queries": len(test), "nonpilot_queries": len(keep),
           "by_type": dict(Counter(q["type"] for q in keep)), "systems": {}}
    for name, run in systems.items():
        res["systems"][name] = {"nonpilot": sub.scores(lambda q: target_rank(run.get(q["qid"], []), q["cluster"],
                                                                              cluster_of), "test", TYPED + VARIANTS),
                                "all": ev.scores(lambda q: target_rank(run.get(q["qid"], []), q["cluster"], cluster_of),
                                                 "test", TYPED + VARIANTS)}
    return res


def rerank_analysis(work, ev, queries, cluster_of, sel, pred):
    name = f"rerank_bgererank_{sel['system']}"
    path = os.path.join(work, "runs", f"{name}.trec")
    if not os.path.exists(path):
        return None
    sampled = set(json.load(open(os.path.join(work, "runs", f"{name}.qids"))))
    R = read_run(path, sampled)
    F = read_run(os.path.join(work, "runs", f"{sel['system']}.trec"), sampled)
    qs = [q for q in queries if q["qid"] in sampled]
    depth = 20

    def interp(q, a):
        first = F.get(q["qid"], [])[:depth]
        ce = {d: s for d, s in R.get(q["qid"], [])[:depth]}
        if not first:
            return []
        fs = np.array([s for _, s in first])
        cs = np.array([ce.get(d, -1e9) for d, _ in first])
        nf = (fs - fs.min()) / ((fs.max() - fs.min()) or 1)
        valid = cs > -1e8
        nc = np.zeros_like(cs)
        if valid.any():
            lo, hi = cs[valid].min(), cs[valid].max()
            nc[valid] = (cs[valid] - lo) / ((hi - lo) or 1)
        s = a * nc + (1 - a) * nf
        order = np.argsort(-s, kind="stable")
        return [(first[i][0], float(s[i])) for i in order] + F[q["qid"]][depth:]
    alphas = [round(x, 1) for x in np.arange(0, 1.01, 0.1)]
    rank = {a: {q["qid"]: target_rank(interp(q, a), q["cluster"], cluster_of) for q in qs} for a in alphas}
    sub = Evaluator(qs, cluster_of)

    def dev_macro(a):
        per = defaultdict(list)
        for q in qs:
            if q["split"] == "dev":
                per[q["type"]].append(rr(rank[a][q["qid"]]))
        return float(np.mean([np.mean(v) for v in per.values()])) if per else 0.0
    best_a = max(alphas, key=lambda a: (round(dev_macro(a), 10), -a))
    # selective: rerank only the predicted types where reranking (alpha=1) beat the first stage on dev
    helps = {}
    for t in TYPED:
        dq = [q for q in qs if q["split"] == "dev" and pred.get(q["qid"]) == t]
        if dq:
            helps[t] = float(np.mean([rr(rank[1.0][q["qid"]]) - rr(rank[0.0][q["qid"]]) for q in dq])) > 0
    sel_rank = lambda q: rank[1.0 if helps.get(pred.get(q["qid"])) else 0.0][q["qid"]]  # noqa: E731
    return {"run": name, "sampled": {s: dict(Counter(q["type"] for q in qs if q["split"] == s)) for s in ("dev", "test")},
            "first_stage": {s: sub.scores(lambda q: rank[0.0][q["qid"]], s, TYPED) for s in ("dev", "test")},
            "reranked": {s: sub.scores(lambda q: rank[1.0][q["qid"]], s, TYPED) for s in ("dev", "test")},
            "alpha": best_a, "dev_macro_by_alpha": {str(a): dev_macro(a) for a in alphas},
            "interpolated": {s: sub.scores(lambda q: rank[best_a][q["qid"]], s, TYPED) for s in ("dev", "test")},
            "selective_types": helps,
            "selective": {s: sub.scores(sel_rank, s, TYPED) for s in ("dev", "test")}}


def ambiguity_counts(work):
    """Regenerate the rule queries with the same seed and a checker that counts its verdicts."""
    from visong.queries import AmbiguityChecker, generate_rule_queries
    songs, cluster_of = P.load(work)
    checker = AmbiguityChecker(songs, cluster_of)
    calls = Counter()
    orig = checker.ambiguous

    def counted(frag, doc_id):
        a = orig(frag, doc_id)
        calls["rejected" if a else "accepted"] += 1
        return a
    checker.ambiguous = counted
    counts = P.FULL_COUNTS if len(songs) >= 3000 else P.PILOT_COUNTS
    qs = generate_rule_queries(songs, cluster_of, counts, seed=13, checker=checker,
                               split_of_doc={s.doc_id: s.extra["split"] for s in songs},
                               one_per_cluster=("dev", "test") if len(songs) >= 3000 else ())
    stored = C.read_jsonl(os.path.join(work, "queries", "rule.jsonl"))
    regen = {q["qid"]: q["text"] for q in qs}
    # rule.jsonl may lack the few queries dropped later for overlapping the NA-FT training data
    same = all(regen.get(q["qid"]) == q["text"] for q in stored)
    return {"fragments_checked": sum(calls.values()), **calls,
            "rejected_share": round(calls["rejected"] / max(1, sum(calls.values())), 4),
            "stored_queries_reproduced": same, "dropped_after_generation": len(regen) - len(stored)}


def short_fragments(songs, cluster_of, lengths=(3, 4, 5, 6, 8, 10, 12), per_song=2, seed=29):
    """How often a fragment of n syllables of a test song also occurs, marks ignored, in another
    cluster: the reason the rule queries have at least five syllables."""
    import random
    from visong.queries import AmbiguityChecker, representatives
    checker = AmbiguityChecker(songs, cluster_of)
    by_id = {s.doc_id: s for s in songs}
    reps = [by_id[d] for c, d in sorted(representatives(songs, cluster_of).items())
            if by_id[d].extra.get("split") == "test"]
    rng = random.Random(seed)
    out = {}
    for n in lengths:
        drawn = shared = 0
        for s in reps:
            syl = remove_diacritics(" ".join(clean(line) for line in s.lines())).split()
            if len(syl) < n:
                continue
            for _ in range(per_song):
                i = rng.randrange(len(syl) - n + 1)
                drawn += 1
                shared += checker.ambiguous(" ".join(syl[i:i + n]), s.doc_id)
        out[str(n)] = {"fragments": drawn, "shared": shared, "share": round(shared / max(1, drawn), 4)}
    return out


def restorer_cap(songs, cap=8):
    forms = defaultdict(Counter)
    for s in songs:
        for t in syllables(s.lyrics):
            forms[remove_diacritics(t)][t] += 1
    tokens = covered = 0
    n_forms_tok = 0
    for k, c in forms.items():
        top = {f for f, _ in c.most_common(cap)}
        n = sum(c.values())
        tokens += n
        covered += sum(v for f, v in c.items() if f in top)
        n_forms_tok += n * len(c)
    return {"cap": cap, "unmarked_types": len(forms), "token_coverage": round(covered / tokens, 5),
            "types_over_cap": sum(len(c) > cap for c in forms.values()),
            "forms_per_type": round(float(np.mean([len(c) for c in forms.values()])), 2),
            "forms_per_token": round(n_forms_tok / tokens, 2)}


def cluster_check(songs, cluster_of):
    by = defaultdict(list)
    for s in songs:
        by[cluster_of[s.doc_id]].append(s)
    ratios = []
    for c, ss in by.items():
        if len(ss) < 2:
            continue
        for a, b in itertools.combinations(ss, 2):
            sa, sb = syllables(a.lyrics), syllables(b.lyrics)
            ratios.append(difflib.SequenceMatcher(None, sa, sb, autojunk=False).ratio())
    r = np.array(ratios)
    return {"pairs": len(r), "ratio_quartiles": [round(float(x), 3) for x in np.percentile(r, [25, 50, 75])],
            "share_below_0.3": round(float((r < 0.3).mean()), 3), "share_below_0.5": round(float((r < 0.5).mean()), 3)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--only", default="fusion_alt,qtype_ablation,nonpilot,rerank,restore_train,ambiguity,"
                                       "restorer_cap,cluster_check,short_fragments")
    a = ap.parse_args(argv)
    work, todo = a.work, a.only.split(",")
    songs, cluster_of = P.load(work)
    queries = P.load_queries(work)
    ev = Evaluator(queries, cluster_of)
    pred = json.load(open(os.path.join(work, "results", "qtype_pred.json")))["logreg"]
    sel = selected(work, [q for q in queries if q["split"] == "dev"], cluster_of)
    path = os.path.join(work, "results", "round1_analyses.json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    out["selected"] = sel
    need = set(sel["components"]) | {"lexall_global", "lexall_adaptive", sel["system"], sel["base"] + "_global",
                                     "restoreall_bm25_syl12", "bm25_syl", "restoretrain_bm25_syl12"}
    runs = {}
    if set(todo) & {"fusion_alt", "qtype_ablation", "nonpilot", "restore_train"}:
        for n in sorted(need):
            p = os.path.join(work, "runs", f"{n}.trec")
            if os.path.exists(p):
                runs[n] = read_run(p)
        print("runs loaded", sorted(runs), flush=True)
    if "fusion_alt" in todo:
        out["fusion_alt"] = fusion_alternatives(ev, queries, cluster_of, runs, sel, pred)
        print("fusion_alt", {k: round(v["test"]["macro"], 3) if "test" in v else
                             {kk: round(vv["test"]["macro"], 3) for kk, vv in v.items() if kk in ("global", "adaptive")}
                             for k, v in out["fusion_alt"].items()}, flush=True)
    if "qtype_ablation" in todo:
        out["qtype_ablation"] = qtype_ablation(work, ev, queries, songs, cluster_of, runs, sel)
        print("qtype_ablation", {k: (round(v["accuracy"]["all"], 3), round(v["hybrid"]["macro"], 3))
                                 for k, v in out["qtype_ablation"].items()}, flush=True)
    if "nonpilot" in todo:
        systems = {n: runs[n] for n in ("bm25_syl", "restoreall_bm25_syl12", "lexall_global", "lexall_adaptive",
                                        sel["components"][-1], sel["base"] + "_global", sel["system"]) if n in runs}
        out["nonpilot"] = nonpilot(work, ev, queries, cluster_of, systems)
        print("nonpilot", out["nonpilot"]["nonpilot_queries"], {n: round(v["nonpilot"]["macro"], 3)
                                                               for n, v in out["nonpilot"]["systems"].items()}, flush=True)
    if "rerank" in todo:
        out["rerank"] = rerank_analysis(work, ev, queries, cluster_of, sel, pred)
        if out["rerank"]:
            print("rerank alpha", out["rerank"]["alpha"], {k: round(out["rerank"][k]["test"]["macro7"], 3)
                                                          for k in ("first_stage", "reranked", "interpolated", "selective")},
                  flush=True)
    if "restore_train" in todo and "restoretrain_bm25_syl12" in runs:
        dense = sel["components"][-1]
        swap = {"restoreall_bm25_syl12": "restoretrain_bm25_syl12"}
        rt = dense.replace("_rest", "_resttrain") if dense.endswith("_rest") else None
        if rt and os.path.exists(os.path.join(work, "runs", f"{rt}.trec")):
            runs[rt] = read_run(os.path.join(work, "runs", f"{rt}.trec"))
            swap[dense] = rt
        out["restore_train"] = {"swap": swap, "test": ev.scores(
            lambda q: hybrid_rank_with(runs, sel, lambda x: pred.get(x["qid"]), q, cluster_of, swap), "test",
            TYPED + VARIANTS)}
        print("restore_train", round(out["restore_train"]["test"]["macro"], 3), flush=True)
    if "ambiguity" in todo:
        out["ambiguity"] = ambiguity_counts(work)
        print("ambiguity", out["ambiguity"], flush=True)
    if "short_fragments" in todo:
        out["short_fragments"] = short_fragments(songs, cluster_of)
        print("short_fragments", {k: v["share"] for k, v in out["short_fragments"].items()}, flush=True)
    if "restorer_cap" in todo:
        out["restorer_cap"] = restorer_cap(songs)
        print("restorer_cap", out["restorer_cap"], flush=True)
    if "cluster_check" in todo:
        out["cluster_check"] = cluster_check(songs, cluster_of)
        print("cluster_check", out["cluster_check"], flush=True)
    json.dump(out, open(path, "w"), indent=1, ensure_ascii=False)
    print("wrote", path)


if __name__ == "__main__":
    main()
