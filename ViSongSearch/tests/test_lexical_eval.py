import numpy as np

from visong.evaluate import bootstrap_ci, holm, per_query, randomization_test, target_rank
from visong.fusion import WeightTuner, rrf, weight_grid
from visong.lexical import LexicalIndex

TEXTS = ["anh yêu em nhiều lắm", "mưa rơi trên phố vắng", "em về đâu khi chiều buông", "đường xa vạn dặm"]
IDS = ["a", "b", "c", "d"]


def test_bm25_finds_exact_fragment_and_only_positive_scores():
    ix = LexicalIndex(TEXTS, IDS, "syl12")
    res = ix.search("mưa rơi trên phố", k=10)
    assert res[0][0] == "b"
    assert all(s > 0 for _, s in res)
    assert ix.search("không có từ nào", k=10) == []


def test_nodiac_index_matches_unmarked_query():
    ix = LexicalIndex(TEXTS, IDS, "nodiac12")
    assert ix.search("duong xa van dam")[0][0] == "d"


def test_ql_and_rm3_rank_the_right_song():
    ix = LexicalIndex(TEXTS, IDS, "syl")
    assert ix.search("chiều buông em về", model="ql", mu=100)[0][0] == "c"
    assert ix.search("chiều buông em về", model="rm3", mu=100)[0][0] == "c"


def test_target_rank_collapses_versions_of_a_song():
    cluster_of = {"a": "C1", "b": "C1", "c": "C2"}
    assert target_rank([("a", 3), ("b", 2), ("c", 1)], "C2", cluster_of) == 2
    q = [{"qid": "q", "cluster": "C2"}]
    assert per_query({"q": [("c", 1)]}, q, cluster_of)["q"]["rr10"] == 1.0
    assert per_query({}, q, cluster_of)["q"]["rr10"] == 0.0


def test_statistics():
    assert randomization_test(np.ones(10), np.ones(10)) == 1.0
    assert randomization_test(np.ones(30), np.zeros(30), n=2000) < 0.01
    adj = holm({"x": 0.01, "y": 0.04, "z": 0.03})
    assert adj["x"] == 0.03 and adj["y"] == adj["z"] == 0.06
    lo, hi = bootstrap_ci(np.r_[np.ones(50), np.zeros(50)], n=500)
    assert lo < 0.5 < hi


def test_rrf_and_weight_tuner_agree_with_exact_fusion():
    rng = np.random.default_rng(0)
    docs = [f"d{i}" for i in range(60)]
    cluster_of = {d: f"c{int(d[1:]) // 3}" for d in docs}
    lists, target = {}, {}
    for i in range(20):
        lists[f"q{i}"] = [[(d, 1.0) for d in rng.permutation(docs)[:30]] for _ in range(3)]
        target[f"q{i}"] = cluster_of[docs[int(rng.integers(60))]]
    tuner = WeightTuner(lists, target, cluster_of)
    grid = weight_grid(3)
    approx = tuner.mrr(grid)
    exact = np.array([tuner.exact_mrr(w) for w in grid])
    assert np.all(approx <= exact + 1e-9)
    w, m = tuner.best(grid)
    assert abs(m - exact.max()) < 1e-9
    fused = rrf([[("x", 1), ("y", 1)], [("y", 1), ("x", 1)]], weights=[2, 1])
    assert fused[0][0] == "x"
