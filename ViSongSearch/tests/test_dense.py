import numpy as np
import pytest

from visong.dense import DenseIndex, config_for


def test_config_for_prefixes():
    assert config_for("models/multilingual-e5-base")["query"] == "query: "
    assert config_for("work/models/naft_e5small_s1")["passage"] == "passage: "
    assert config_for("models/vietnamese-bi-encoder")["segment"] is True
    assert config_for("models/bge-m3")["query"] == ""


def test_maxp_search_matches_brute_force():
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(50, 8)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    docs = [f"s{i % 7}" for i in range(50)]
    pids = [f"{d}#{i}" for i, d in enumerate(docs)]
    index = DenseIndex(vecs, pids, docs)
    q = rng.normal(size=(3, 8)).astype(np.float32)
    res = index.search_batch(q, k=7)
    for qi in range(3):
        sims = vecs @ q[qi]
        best = {d: max(s for s, dd in zip(sims, docs) if dd == d) for d in set(docs)}
        expected = sorted(best, key=lambda d: -best[d])
        assert [d for d, _ in res[qi]] == expected
        assert np.allclose([s for _, s in res[qi]], [best[d] for d in expected], atol=1e-5)
    bp = index.best_passages(q[0], ["s1"], n=2)["s1"]
    assert all(index.pids[i].startswith("s1#") for i in bp)


def test_encoder_runs_with_a_tiny_local_model(tmp_path):
    pytest.importorskip("torch")
    st = pytest.importorskip("sentence_transformers")
    from transformers import BertConfig, BertModel, BertTokenizerFast
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, trainers
    tok = Tokenizer(models.WordPiece(unk_token="[UNK]"))
    tok.normalizer = normalizers.BertNormalizer(lowercase=True, strip_accents=False)
    tok.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tok.train_from_iterator(["anh yêu em", "mưa rơi trên phố"] * 10,
                            trainers.WordPieceTrainer(vocab_size=200, special_tokens=["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]))
    path = str(tmp_path / "tiny-e5")
    tok.save(path + ".json")
    BertTokenizerFast(tokenizer_file=path + ".json", unk_token="[UNK]", pad_token="[PAD]", cls_token="[CLS]",
                      sep_token="[SEP]", mask_token="[MASK]").save_pretrained(path)
    BertModel(BertConfig(vocab_size=200, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
                         intermediate_size=32, max_position_embeddings=64)).save_pretrained(path)
    from sentence_transformers import models as stm
    model = st.SentenceTransformer(modules=[stm.Transformer(path, max_seq_length=32),
                                            stm.Pooling(16, "mean"), stm.Normalize()])
    model.save(path)
    from visong.dense import DenseEncoder
    enc = DenseEncoder(path, device="cpu")
    v = enc.encode_passages(["anh yêu em", "mưa rơi trên phố vắng"])
    assert v.shape == (2, 16) and np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5)


def test_search_with_restored_queries(tmp_path, monkeypatch):
    """--restored searches the same index with the restorer's text and names the run dense_<name>_rest."""
    import json

    from visong import dense
    from visong.evaluate import read_trec
    from visong.text import has_diacritics
    (tmp_path / "queries").mkdir()
    qs = [{"qid": "test-Q2-1", "split": "test", "type": "Q2", "text": "anh yeu em"},
          {"qid": "test-Q1-2", "split": "test", "type": "Q1", "text": "mưa rơi"}]
    with open(tmp_path / "queries" / "rule.jsonl", "w", encoding="utf-8") as f:
        for q in qs:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    # the second query is missing from the restorer's output: it keeps its own text
    json.dump({"all": {"test-Q2-1": "anh yêu em"}}, open(tmp_path / "queries" / "restored.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    (tmp_path / "dense" / "toy").mkdir(parents=True)
    np.save(tmp_path / "dense" / "toy" / "passages.npy", np.eye(2, dtype=np.float32))
    json.dump({"pids": ["a#0", "b#0"], "docs": ["a", "b"]}, open(tmp_path / "dense" / "toy" / "meta.json", "w"))
    seen = []

    class FakeEncoder:            # marked text points to song a, unmarked text to song b
        def __init__(self, *args, **kwargs):
            pass

        def encode_queries(self, texts):
            seen.append(list(texts))
            return np.array([[1, 0] if has_diacritics(t) else [0, 1] for t in texts], dtype=np.float32)

    monkeypatch.setattr(dense, "DenseEncoder", FakeEncoder)
    dense.search_queries(str(tmp_path), "fake", "toy", include_sweep=False)
    dense.search_queries(str(tmp_path), "fake", "toy", include_sweep=False, restored="all")
    assert seen == [["anh yeu em", "mưa rơi"], ["anh yêu em", "mưa rơi"]]
    raw = read_trec(str(tmp_path / "runs" / "dense_toy.trec"))
    rest = read_trec(str(tmp_path / "runs" / "dense_toy_rest.trec"))
    assert raw["test-Q2-1"][0][0] == "b" and rest["test-Q2-1"][0][0] == "a"
    assert rest["test-Q1-2"][0][0] == "a"
    stats = json.load(open(tmp_path / "dense" / "toy" / "search_stats_rest.json"))
    assert stats["restored"] == "all" and stats["queries_changed"] == 1
    assert dense.rest_suffix(None) == "" and dense.rest_suffix("train") == "_resttrain"


def test_paper_labels_of_restored_and_fine_tuned_runs():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "make_paper_tables", os.path.join(os.path.dirname(__file__), "..", "scripts", "make_paper_tables.py"))
    mpt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mpt)
    assert mpt.dense_label("e5base") == "mE5-base"
    assert mpt.dense_label("e5base_rest") == "mE5-base on restored queries"
    assert mpt.dense_label("naft_e5small_s2_rest") == "NA-FT mE5-small (seed 2) on restored queries"
    assert mpt.hybrid_label("hybrid_e5base_rest_adaptive") == \
        "hybrid with mE5-base on restored queries, weights per predicted type"
    assert mpt.hybrid_label("lexall_global") == "RRF, weights tuned on dev"


def test_vector_cache_encodes_each_new_text_once(tmp_path):
    import numpy as np
    from visong.dense import _cached_vectors
    calls = []

    class Fake:
        def encode_passages(self, texts):
            calls.append(list(texts))
            return np.array([[len(t), 1.0] for t in texts], dtype=np.float32)

    v1, n1, _ = _cached_vectors(Fake, ["a", "bb", "a"], ["ka", "kb", "ka"], str(tmp_path))
    assert n1 == 2 and calls == [["a", "bb"]] and v1[:, 0].tolist() == [1, 2, 1]
    v2, n2, _ = _cached_vectors(Fake, ["bb", "ccc"], ["kb", "kc"], str(tmp_path))
    assert n2 == 1 and calls[-1] == ["ccc"] and v2[:, 0].tolist() == [2, 3]
    v3, n3, _ = _cached_vectors(Fake, ["a", "ccc"], ["ka", "kc"], str(tmp_path))
    assert n3 == 0 and len(calls) == 2 and v3[:, 0].tolist() == [1, 3]


def test_vector_cache_saves_every_chunk(tmp_path):
    import numpy as np
    from visong.dense import _cached_vectors

    class Fake:
        def encode_passages(self, texts):
            return np.array([[len(t), 0.0] for t in texts], dtype=np.float32)

    texts = ["x" * i for i in range(1, 8)]
    v, n, _ = _cached_vectors(Fake, texts, [f"k{i}" for i in range(7)], str(tmp_path), chunk=3)
    assert n == 7 and sorted(f for f in __import__("os").listdir(tmp_path) if f.endswith(".keys")) == \
        ["shard-00000.keys", "shard-00001.keys", "shard-00002.keys"]
    v2, n2, _ = _cached_vectors(Fake, texts[::-1], [f"k{i}" for i in range(7)][::-1], str(tmp_path), chunk=3)
    assert n2 == 0 and v2[:, 0].tolist() == list(range(7, 0, -1))
