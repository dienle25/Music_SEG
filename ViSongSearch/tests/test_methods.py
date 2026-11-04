"""Round-1 methods: n-best restoration (visong/nbest.py), learned fusion (visong/ltr.py) and the shared
statistics (visong/methods.py)."""
import numpy as np

from visong.diacritics import DiacriticRestorer
from visong.lexical import LexicalIndex, top_k
from visong.ltr import (Dataset, LTRModel, block, candidate_block, n_columns, pairs, query_features,
                        rank_candidates, with_interactions)
from visong.methods import comparison_family, macro_randomization_test, topk
from visong.nbest import evidence_weights, mixture_scores, nbest, posterior

LYRICS = ["em bỏ đi xa", "anh bỏ lại em", "ta bỏ quên nhau", "bờ biển xanh xa", "mưa rơi trên phố vắng",
          "em về đâu khi chiều buông", "anh yêu em nhiều lắm"]


def test_nbest_starts_with_the_one_best_restoration():
    r = DiacriticRestorer(LYRICS * 2)
    for q in ("em bo di xa", "bo bien xanh", "mua roi tren pho vang", "anh yeu em", "nhuwngx chieu", ""):
        nb = nbest(r, q, 5)
        assert nb[0][0] == r.restore(q)
        scores = [s for _, s in nb]
        assert scores == sorted(scores, reverse=True)
        assert len({t for t, _ in nb}) == len(nb)            # distinct restorations
    assert len(nbest(r, "em bo di xa", 1)) == 1


def test_nbest_keeps_the_less_likely_form():
    r = DiacriticRestorer(LYRICS)
    forms = {t for t, _ in nbest(r, "bo", 4)}
    assert {"bỏ", "bờ"} <= forms


def test_posterior_and_evidence_weights():
    lp = np.log([0.6, 0.3, 0.1])
    assert np.allclose(posterior(lp, 1.0), [0.6, 0.3, 0.1])
    assert np.allclose(posterior(lp, 0.0), [1 / 3] * 3)
    assert posterior(lp, 4.0)[0] > 0.9
    w = evidence_weights(lp, [10.0, 0.0, 5.0], alpha=1.0, beta=1.0)
    assert w[1] == 0 and abs(w.sum() - 1) < 1e-12 and w[0] > w[2]
    assert np.allclose(evidence_weights(lp, [10.0, 0.0, 5.0], 1.0, 0.0), posterior(lp, 1.0))


def test_score_mixture_is_the_weighted_sum_of_bm25_scores():
    ix = LexicalIndex(LYRICS, [f"d{i}" for i in range(len(LYRICS))], "syl12")
    r1, r2 = "em bỏ đi xa", "em bờ đi xa"
    mix = mixture_scores(ix, [r1, r2], [0.7, 0.3])
    assert np.allclose(mix, 0.7 * ix.scores(r1) + 0.3 * ix.scores(r2))
    assert np.array_equal(mixture_scores(ix, [r1], [1.0]), ix.scores(r1))


def test_topk_orders_documents_like_lexical_top_k():
    rng = np.random.default_rng(1)
    s = np.round(rng.random(300), 2) - 0.2                    # ties and non-positive scores
    ids = [f"d{i}" for i in range(300)]
    pos, sc = topk(s, 50)
    assert [ids[i] for i in pos] == [d for d, _ in top_k(s, ids, 50, positive_only=True)]
    assert np.all(sc > 0)


def test_candidate_features_from_two_lists():
    lists = [(np.array([5, 3, 9]), np.array([4.0, 2.0, 1.0])), (np.array([3, 7]), np.array([8.0, 8.0]))]
    cand, X = candidate_block(lists, depth=100)
    assert list(cand) == [3, 5, 7, 9]
    row = {d: X[i] for i, d in enumerate(cand)}
    # list 0 columns: in, log rank, rr60, score, score / top, min-max
    assert np.allclose(row[3][:6], [1, np.log(2), 1 / 62, 2.0, 0.5, 1 / 3])
    assert np.allclose(row[7][:6], [0, np.log(101), 0, 0, 0, 0])
    assert np.allclose(row[7][6:12], [1, np.log(2), 1 / 62, 8.0, 1.0, 1.0])     # tied list: min-max 1
    qf = query_features("mưa rơi tren pho", np.full(7, 1 / 7), lists)
    assert len(qf) == n_columns(2) - 12 and qf[7] == 4 and qf[8] == 0.5
    cand2, F = block(lists, qf)
    assert F.shape == (4, n_columns(2))
    assert with_interactions(F, 2).shape == (4, n_columns(2) + 2 * 4 * 7)


def _toy_dataset(n_queries=60, seed=0):
    """List 0 ranks the relevant candidate first in most queries; list 1 is noise."""
    rng = np.random.default_rng(seed)
    qids, cands, Xs, ys = [], [], [], []
    for i in range(n_queries):
        docs = rng.permutation(40)[:20]
        target = int(docs[rng.integers(3)]) if rng.random() < 0.8 else int(docs[5])
        order0 = [target] + [int(d) for d in docs if d != target]
        lists = [(np.array(order0), np.linspace(10, 1, 20)), (rng.permutation(docs), np.linspace(5, 1, 20))]
        cand, X = block(lists, query_features("a b c", np.full(7, 1 / 7), lists))
        qids.append(f"q{i}")
        cands.append(cand)
        Xs.append(X)
        ys.append((cand == target).astype(np.int8))
    return Dataset(qids, cands, Xs, ys)


def test_ltr_models_learn_which_list_to_trust():
    ds = _toy_dataset()
    tr, te = list(range(40)), list(range(40, 60))
    types = ["Q1"] * 20 + ["Q4"] * 20
    for kind, kw in (("logreg", {"C": 1.0}), ("pairwise", {"C": 1.0}), ("gbt", {"gbt": {"max_iter": 30}})):
        m = LTRModel(kind, 2, balance=True, **kw).fit(ds, tr, types)
        top1 = []
        for i in te:
            lo, hi = ds.offsets[i], ds.offsets[i + 1]
            ranking = rank_candidates(m.score(ds.X[lo:hi]), ds.cand[lo:hi], [str(d) for d in range(40)])
            top1.append(int(ranking[0][0]) == int(ds.cand[lo:hi][ds.y[lo:hi] == 1][0]))
        assert np.mean(top1) >= 0.7, kind


def test_pairs_balance_labels_and_share_query_weight():
    Z = np.arange(12, dtype=float).reshape(6, 2)
    y = np.array([1, 0, 0, 0, 1, 0])
    D, lab, w = pairs(Z, y, [4, 2], np.array([1.0, 1.0]), np.zeros(6), n_hard=2, n_rand=1)
    assert len(D) == 3 + 1 and set(lab) <= {0, 1}
    assert abs(w[:3].sum() - w[3:].sum()) < 1e-12            # each query carries the same weight


def test_macro_test_and_holm_family():
    assert macro_randomization_test({"A": np.zeros(20), "B": np.zeros(5)}) == 1.0
    assert macro_randomization_test({"A": np.ones(30), "B": np.ones(30)}, n=999) < 0.01
    qa = {f"q{i}": 0.0 for i in range(40)}
    rr = {"a": qa, "b": {q: 1.0 for q in qa}, "c": dict(qa)}
    groups = {"T1": list(qa)[:20], "T2": list(qa)[20:]}
    fam = comparison_family(rr, [("b vs a", "a", "b"), ("c vs a", "a", "c")], groups, perms=999, macro_types=["T1", "T2"])
    t = fam["tests"]
    assert fam["family_size"] == 6
    assert t["b vs a|T1"]["significant"] and t["b vs a|macro"]["delta"] == 1.0
    assert t["c vs a|T2"]["p"] == 1.0 and t["c vs a|T2"]["p_holm"] == 1.0
    assert all(v["p_holm"] >= v["p"] for v in t.values())
