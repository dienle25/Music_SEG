import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "tmg_analyses", os.path.join(os.path.dirname(__file__), "..", "scripts", "tmg_analyses.py"))
tmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tmg)


def test_osa_distance_counts_a_transposition_as_one_edit():
    assert tmg.osa_distance("tình", "tình") == 0
    assert tmg.osa_distance("tình", "tìnn") == 1      # substitution
    assert tmg.osa_distance("nhau", "hnau") == 1      # adjacent transposition
    assert tmg.osa_distance("từng", "ừng") == 1       # deletion
    assert tmg.osa_distance("abc", "") == 3


def test_prf_from_confusion():
    conf = {"A": {"A": 8, "B": 2}, "B": {"B": 5, "A": 5}}
    out = tmg.prf(conf)
    a = out["per_class"]["A"]
    assert abs(a["precision"] - 8 / 13) < 1e-9 and abs(a["recall"] - 0.8) < 1e-9
    assert abs(out["accuracy"] - 13 / 20) < 1e-9
