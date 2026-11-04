import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("validity", os.path.join(ROOT, "scripts", "validity_analyses.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def test_discriminative_power_counts_only_real_differences():
    qids = [f"q{i}" for i in range(60)]
    pq = {"good": {q: {"rr10": 1.0} for q in qids}, "bad": {q: {"rr10": 0.0} for q in qids},
          "same": {q: {"rr10": 1.0} for q in qids}}
    dp = V.discriminative_power(pq, qids, perms=500)
    assert dp["pairs"] == 3 and dp["significant"] == 2


def test_tau_of_identical_orderings_is_one():
    a = {"x": 0.1, "y": 0.5, "z": 0.9}
    assert V.tau(a, dict(a))["tau"] == 1.0
