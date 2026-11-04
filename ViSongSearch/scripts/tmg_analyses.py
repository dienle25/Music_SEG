"""Text-mining analyses for the TMG301 report.

    python scripts/tmg_analyses.py work        ->  work/results/tmg_analyses.json

Run after `pipeline lexical` (it reads the queries, runs and lexical_params.json).
  text_stats     tokens, types, hapax share and Zipf exponent of lyric syllables, with and
                 without marks; how ambiguous a syllable becomes once its marks are gone
  restore_order  diacritic restoration with a 1-, 2- and 3-gram language model (TMG 02)
  qtype          per-class precision, recall and F1 of the query-type classifiers (TMG 03, 04),
                 without and with class balancing
  noise          edit operations and character edit distance of Q3 queries, MRR@10 by
                 distance (TMG 01)
  clustering     clusters at other Jaccard thresholds and without the title rule (TMG 06)
  pair_check     exact Jaccard of every pair of songs sharing a rare shingle, against the
                 clusters: how many near-duplicate pairs the LSH left apart (TMG 06, SEG 3.7)
  fragment_check test fragments that also occur verbatim in another cluster (exact check), and
                 MRR@10 on Q1-Q3 when every song containing the fragment counts as correct
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from visong import corpus as C                                     # noqa: E402
from visong.dedup import cluster_songs, norm_title, shingle_matrix # noqa: E402
from visong.diacritics import DiacriticRestorer, syllable_accuracy  # noqa: E402
from visong.evaluate import per_query, read_trec                   # noqa: E402
from visong.pipeline import TYPED, load, load_queries              # noqa: E402
from visong.qtype import QueryTypeClassifier                       # noqa: E402
from visong.queries import UNMARKED_TYPES, AmbiguityChecker        # noqa: E402
from visong.text import clean, remove_diacritics                   # noqa: E402


def zipf_fit(counts: Counter, min_count: int = 5) -> tuple[float, float, int]:
    """Slope s of log f = a - s log r, fitted on the ranks whose frequency is >= min_count,
    with the R^2 of the fit and the number of ranks used."""
    f = np.log(np.array(sorted((c for c in counts.values() if c >= min_count), reverse=True), dtype=float))
    r = np.log(np.arange(1, len(f) + 1, dtype=float))
    coef = np.polyfit(r, f, 1)
    r2 = 1 - float(((f - np.polyval(coef, r)) ** 2).sum() / ((f - f.mean()) ** 2).sum())
    return float(-coef[0]), r2, len(f)


def text_stats(songs) -> dict:
    marked = Counter()
    for s in songs:
        marked.update(clean(s.lyrics).split())
    plain, forms = Counter(), defaultdict(Counter)
    for w, c in marked.items():
        p = remove_diacritics(w)
        plain[p] += c
        forms[p][w] += c
    n = sum(marked.values())
    out = {"tokens": n}
    for name, cnt in (("marked", marked), ("unmarked", plain)):
        slope, r2, ranks = zipf_fit(cnt)
        out[name] = {"types": len(cnt), "hapax_share": sum(c == 1 for c in cnt.values()) / len(cnt),
                     "zipf_exponent": round(slope, 3), "zipf_r2": round(r2, 3), "zipf_ranks": ranks,
                     "top10_share": sum(c for _, c in cnt.most_common(10)) / n,
                     "top10": [w for w, _ in cnt.most_common(10)]}
    # Once marks are gone, a plain syllable can stand for several words. Picking each plain
    # syllable's most frequent marked form is the most-frequent-sense baseline of WSD.
    out["ambiguous_token_share"] = sum(plain[p] for p in forms if len(forms[p]) > 1) / n
    out["most_frequent_form_accuracy"] = sum(f.most_common(1)[0][1] for f in forms.values()) / n
    out["most_forms"] = sorted(((p, len(f)) for p, f in forms.items()), key=lambda x: -x[1])[:5]
    return out


def restore_order(songs, queries) -> dict:
    text_of = {q["qid"]: q["text"] for q in queries}
    test = [q for q in queries if q["split"] == "test" and q["type"] in ("Q2",) + UNMARKED_TYPES]
    gold = {q["qid"]: (q["meta"]["source_fragment"] if q["type"] == "Q2" else text_of.get(q["meta"].get("from")))
            for q in test}
    out = {}
    for name, texts in (("all", [s.lyrics for s in songs]),
                        ("train", [s.lyrics for s in songs if s.extra.get("split") == "train"])):
        rest = DiacriticRestorer(texts)
        for order in (1, 2, 3):
            rest.order = order
            acc = defaultdict(lambda: [0, 0, 0, 0])          # syllables right, syllables, queries right, queries
            for q in test:
                if not gold[q["qid"]]:
                    continue
                pred = rest.restore(q["text"])
                a, k = syllable_accuracy(pred, gold[q["qid"]])
                t = acc[q["type"]]
                t[0] += a
                t[1] += k
                t[2] += int(a == k)
                t[3] += 1
            out[f"{name}_{order}"] = {t: {"syllable_acc": v[0] / v[1], "query_acc": v[2] / v[3], "n": v[3]}
                                      for t, v in sorted(acc.items())}
    return out


def prf(confusion: dict[str, dict[str, int]]) -> dict:
    labels = sorted(set(confusion) | {p for row in confusion.values() for p in row})
    per = {}
    for c in labels:
        tp = confusion.get(c, {}).get(c, 0)
        fp = sum(row.get(c, 0) for t, row in confusion.items() if t != c)
        fn = sum(v for p, v in confusion.get(c, {}).items() if p != c)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        per[c] = {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0,
                  "support": tp + fn}
    gold = [c for c in labels if per[c]["support"]]
    total = sum(per[c]["support"] for c in gold)
    return {"per_class": per, "macro_f1": float(np.mean([per[c]["f1"] for c in gold])),
            "accuracy": sum(confusion.get(c, {}).get(c, 0) for c in gold) / total}


def qtype(work, songs, queries) -> dict:
    """Test precision, recall and F1 per class for both classifiers, without and with class
    balancing. The pipeline keeps the setting that wins cross-validation on the training queries."""
    params = json.load(open(os.path.join(work, "results", "lexical_params.json")))
    vocab_marked = {w for s in songs for w in clean(s.lyrics).split()}
    vocab_plain = {remove_diacritics(w) for w in vocab_marked}
    fit_q = ([q for q in queries if q["split"] == "train" and q["type"] in TYPED]
             + [q for q in queries if q["split"] == "dev" and q["type"] in ("Q4", "Q5") + UNMARKED_TYPES])
    test = [q for q in queries if q["split"] == "test" and q["type"] in TYPED]
    out = {"chosen": {m: params[f"qtype_{m}"].get("balanced") for m in ("nb", "logreg")},
           "cv_macro_f1": {m: params[f"qtype_{m}"].get("cv_macro_f1") for m in ("nb", "logreg")},
           "train_examples": params["qtype_logreg"]["train_examples"]}
    for model in ("nb", "logreg"):
        for balanced in (False, True):
            clf = QueryTypeClassifier(vocab_marked, vocab_plain, model, balanced)
            clf.fit([q["text"] for q in fit_q], [q["type"] for q in fit_q])
            conf = defaultdict(Counter)
            for q, p in zip(test, clf.predict([q["text"] for q in test])):
                conf[q["type"]][p] += 1
            conf = {t: dict(c) for t, c in sorted(conf.items())}
            out[f"{model}_{'balanced' if balanced else 'plain'}"] = {**prf(conf), "confusion": conf}
    return out


def osa_distance(a: str, b: str) -> int:
    """Edit distance with insertions, deletions, substitutions and adjacent transpositions
    (optimal string alignment), the operations a typing error makes."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = int(a[i - 1] != b[j - 1])
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


BUCKETS = [(0, 0), (1, 2), (3, 4), (5, 10 ** 6)]
NOISE_RUNS = ["bm25_syl", "bm25_syl12", "bm25_nodiac12", "bm25_char3", "restoreall_bm25_syl12", "lexall_global"]


def noise(work, queries, cluster_of) -> dict:
    sweep = C.read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    out = {}
    for name, qs in (("q3_test", [q for q in queries if q["split"] == "test" and q["type"] == "Q3"]),
                     ("sweep_test", [q for q in sweep if q["split"] == "test"])):
        ops = Counter()
        for q in qs:
            ops.update(q["meta"].get("ops", {}))
        dist = {q["qid"]: osa_distance(clean(q["text"]), clean(q["meta"]["source_fragment"])) for q in qs}
        res = {"n": len(qs), "ops": dict(ops), "char_distance_mean": float(np.mean(list(dist.values()))),
               "char_distance_median": float(np.median(list(dist.values()))),
               "changed_syllables_mean": float(np.mean([q["meta"].get("changed", 0) for q in qs])),
               "by_distance": {}}
        for run_name in NOISE_RUNS:
            path = os.path.join(work, "runs", f"{run_name}.trec")
            if not os.path.exists(path):
                continue
            pq = per_query(read_trec(path), qs, cluster_of)
            for lo, hi in BUCKETS:
                ids = [q["qid"] for q in qs if lo <= dist[q["qid"]] <= hi]
                if ids:
                    key = f"{lo}-{hi}" if hi < 10 ** 6 else f"{lo}+"
                    b = res["by_distance"].setdefault(key, {"n": len(ids)})
                    b[run_name] = float(np.mean([pq[i]["rr10"] for i in ids]))
        out[name] = res
    return out


def clustering(songs, cluster_of) -> dict:
    out = {}
    settings = [(t, 0.05) for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)] + [(0.5, 2.0)]
    title_of = {s.doc_id: norm_title(s.title) for s in songs}
    for threshold, title_threshold in settings:
        cl, stats = cluster_songs(songs, threshold=threshold, title_threshold=title_threshold)
        members = defaultdict(list)
        for d, c in cl.items():
            members[c].append(d)
        multi = [m for m in members.values() if len(m) > 1]
        key = f"jaccard_{threshold}" + ("" if title_threshold <= 1 else "_no_title_rule")
        out[key] = {"clusters": stats["clusters"], "multi_clusters": len(multi),
                    "songs_in_multi": stats["songs_in_multi_clusters"], "largest": stats["largest_cluster"],
                    # clusters whose songs do not all share a normalised title: medleys, renamed
                    # uploads, or wrong merges
                    "mixed_title_clusters": sum(len({title_of[d] for d in m}) > 1 for m in multi)}
        if (threshold, title_threshold) == (0.5, 0.05):
            out[key]["same_as_collection"] = cl == cluster_of
    return out


def pair_check(songs, cluster_of, thresholds=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8), max_df: int = 50) -> dict:
    """Every pair of songs that shares at least one shingle found in at most `max_df` songs gets
    its exact Jaccard (a sparse product of the song x shingle matrix). A pair at Jaccard >= 0.3
    always shares many shingles, so leaving out the common ones (refrains) loses no such pair."""
    X, n_sh = shingle_matrix(songs)
    df = np.diff(X.tocsc().indptr)
    from scipy import sparse
    R = X @ sparse.diags((df <= max_df).astype(np.float32))
    cand = (R @ R.T).tocoo()
    keep = cand.row < cand.col
    r, c = cand.row[keep], cand.col[keep]
    inter = np.asarray(X[r].multiply(X[c]).sum(axis=1)).ravel()
    jac = inter / np.maximum(n_sh[r] + n_sh[c] - inter, 1)
    ids = [s.doc_id for s in songs]
    same = np.array([cluster_of[ids[i]] == cluster_of[ids[j]] for i, j in zip(r, c)], dtype=bool)
    out = {"pairs_sharing_a_rare_shingle": int(len(r)), "by_threshold": {}}
    for t in thresholds:
        m = jac >= t
        out["by_threshold"][str(t)] = {"pairs": int(m.sum()), "in_one_cluster": int((m & same).sum()),
                                       "apart": int((m & ~same).sum())}
    return out


FRAGMENT_RUNS = ["restoreall_bm25_syl12", "lexall_global", "bm25_nodiac12", "bm25_syl12"]


def fragment_check(work, songs, queries, cluster_of) -> dict:
    """The pilot's duplicate filter tested only the ten best songs of a unigram BM25 index. The exact
    check finds every test fragment that also occurs in a song of another cluster (medleys, shared
    lines); judging those queries by passage counts all such clusters as correct."""
    chk = AmbiguityChecker(songs, cluster_of)
    test = [q for q in queries if q["split"] == "test" and q["type"] in ("Q1", "Q2", "Q3")]
    holders = {q["qid"]: chk.clusters_with(q["meta"]["source_fragment"]) | {q["cluster"]} for q in test}
    amb = [q for q in test if len(holders[q["qid"]]) > 1]
    out = {"test_fragments": len({(q["doc_id"], q["meta"]["source_fragment"]) for q in test}),
           "ambiguous_fragments": len({(q["doc_id"], q["meta"]["source_fragment"]) for q in amb}),
           "ambiguous_queries": len(amb), "queries": len(test), "runs": {}}
    for name in FRAGMENT_RUNS:
        path = os.path.join(work, "runs", f"{name}.trec")
        if not os.path.exists(path):
            continue
        run = read_trec(path)
        res = {"strict": {}, "passage": {}, "other_song_first": 0}
        for t in ("Q1", "Q2", "Q3"):
            qs = [q for q in test if q["type"] == t]
            strict, relaxed = [], []
            for q in qs:
                seen, r_strict, r_relaxed = [], None, None
                for d, _ in run.get(q["qid"], []):
                    c = cluster_of[d]
                    if c in seen:
                        continue
                    seen.append(c)
                    if r_relaxed is None and c in holders[q["qid"]]:
                        r_relaxed = len(seen)
                    if c == q["cluster"]:
                        r_strict = len(seen)
                        break
                strict.append(1 / r_strict if r_strict and r_strict <= 10 else 0.0)
                relaxed.append(1 / r_relaxed if r_relaxed and r_relaxed <= 10 else 0.0)
                res["other_song_first"] += int(r_relaxed == 1 and r_strict != 1)
            res["strict"][t], res["passage"][t] = float(np.mean(strict)), float(np.mean(relaxed))
        out["runs"][name] = res
    return out


def main(work: str) -> None:
    songs, cluster_of = load(work)
    queries = load_queries(work)
    result = {"text_stats": text_stats(songs), "restore_order": restore_order(songs, queries),
              "qtype": qtype(work, songs, queries), "noise": noise(work, queries, cluster_of),
              "clustering": clustering(songs, cluster_of), "pair_check": pair_check(songs, cluster_of),
              "fragment_check": fragment_check(work, songs, queries, cluster_of)}
    path = os.path.join(work, "results", "tmg_analyses.json")
    json.dump(result, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "work")
