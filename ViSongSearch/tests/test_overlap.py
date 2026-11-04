import importlib.util
import os


def _load():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_naft_overlap.py")
    spec = importlib.util.spec_from_file_location("check_naft_overlap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_training_song_that_became_test_marks_its_cluster():
    mod = _load()
    song = lambda d, split, cl: {"doc_id": d, "extra": {"split": split, "cluster": cl}}
    snapshot = [song("a", "train", "a"), song("b", "train", "b"), song("c", "test", "c")]
    # "a" merged into a test cluster; "b" stayed in training; "d" is new
    now = [song("a", "test", "x"), song("x", "test", "x"), song("b", "train", "b"), song("d", "dev", "d")]
    seen, moved, bad = mod.overlap(snapshot, now)
    assert seen == {"a", "b"} and moved == ["a"] and bad == {"x"}
