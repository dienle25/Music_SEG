from visong.corpus import Song
from visong.diacritics import DiacriticRestorer, syllable_accuracy
from visong.queries import check_llm_query, longest_common_run, split_of

SONG = Song("d", "u", "s", "Nắng Ấm Xa Dần",
            lyrics="nắng ấm xa dần rồi\nnắng ấm xa dần rồi bỏ rơi để lại những giấc mơ\ngiữ lại đi")


def test_split_is_deterministic_and_balanced():
    assert split_of("abc") == split_of("abc")
    splits = [split_of(f"title {i}") for i in range(3000)]
    share = {s: splits.count(s) / len(splits) for s in ("train", "dev", "test")}
    assert 0.65 < share["train"] < 0.75 and 0.07 < share["dev"] < 0.13 and 0.17 < share["test"] < 0.23


def test_llm_filter_rules():
    assert check_llm_query("Q5", "bài nắng ấm xa dần của sơn tùng", SONG)[1] == "title_leak"
    assert check_llm_query("Q5", "ngắn quá", SONG)[1] == "too_short"
    ok, why = check_llm_query("Q4", "nắng ấm xa dần rồi bỏ rơi để lại", SONG, "nắng ấm xa dần rồi")
    assert not ok and why.startswith("copied")
    assert check_llm_query("Q5", "bài của sơn tùng về chia tay, cô gái bỏ đi để lại mộng mơ", SONG) == (True, "ok")
    assert longest_common_run("a b c d".split(), "x b c d y".split()) == 3


def test_diacritic_restorer_on_seen_text():
    lyrics = ["anh yêu em nhiều lắm", "mưa rơi trên phố vắng", "em về đâu khi chiều buông"] * 3
    r = DiacriticRestorer(lyrics)
    out = r.restore("mua roi tren pho vang")
    assert out == "mưa rơi trên phố vắng"
    assert syllable_accuracy(out, "mưa rơi trên phố vắng") == (5, 5)


def test_unmarked_variants_strip_marks_and_keep_target():
    from visong.queries import unmarked_variants
    qs = [{"qid": "s1-Q4-0", "type": "Q4", "text": "chiều mưa trên phố vắng", "doc_id": "s1", "cluster": "c1",
           "split": "test", "meta": {}},
          {"qid": "s1-Q1-0", "type": "Q1", "text": "mưa", "doc_id": "s1", "cluster": "c1", "split": "test", "meta": {}}]
    out = unmarked_variants(qs)
    assert len(out) == 1
    q = out[0]
    assert q["qid"] == "s1-Q4u-0" and q["type"] == "Q4u" and q["text"] == "chieu mua tren pho vang"
    assert q["cluster"] == "c1" and q["meta"]["from"] == "s1-Q4-0"


def test_restorer_order_one_takes_the_most_frequent_form_and_trigram_uses_context():
    # "bo" is "bỏ" three times but "bờ" in "bờ biển"; only the context model gets the second right
    lyrics = ["em bỏ đi", "anh bỏ lại", "ta bỏ quên", "bờ biển xanh"]
    r = DiacriticRestorer(lyrics, order=1)
    assert r.restore("bo bien xanh") == "bỏ biển xanh"
    r.order = 3
    assert r.restore("bo bien xanh") == "bờ biển xanh"
    assert r.restore("em bo di") == "em bỏ đi"


def test_ambiguity_checker_finds_a_fragment_in_a_long_medley():
    """The fragment of song a also occurs in a long medley: the check must look at every song."""
    from visong.queries import AmbiguityChecker
    a = Song("a", "u", "s", "Gió vẫn hát", lyrics="gió vẫn hát bên thềm xưa\nmình em đứng đợi")
    filler = "\n".join(f"dòng thứ {i} của liên khúc dài" for i in range(200))
    medley = Song("m", "u", "s", "Mashup", lyrics=filler + "\ngió vẫn hát bên thềm xưa\n" + filler)
    other = Song("o", "u", "s", "Khác", lyrics="thanh gió vẫn hát bên thềm")
    checker = AmbiguityChecker([a, medley, other], {"a": "c1", "m": "c2", "o": "c3"})
    assert checker.ambiguous("gió vẫn hát bên thềm xưa", "a")
    assert not checker.ambiguous("mình em đứng đợi", "a")
    # whole syllables only: "thanh gió vẫn hát" does not contain the fragment "anh gió vẫn hát"
    y = Song("y", "u", "s", "Y", lyrics="anh gió vẫn hát mãi")
    assert not AmbiguityChecker([other, y], {"o": "c3", "y": "c4"}).ambiguous("anh gió vẫn hát", "y")


def test_one_target_version_per_cluster_in_dev_and_test():
    from visong.corpus import Song
    from visong.queries import generate_rule_queries, representatives
    lyric = "\n".join(f"dòng thứ {i} của bài hát này có nhiều chữ" for i in range(12))
    songs = [Song("a", "u", "s", "Bài", lyrics=lyric), Song("b", "u", "s", "Bài", lyrics=lyric + "\nthêm một dòng nữa"),
             Song("c", "u", "s", "Mashup Bài", lyrics=lyric + "\nthêm một dòng nữa\nvà dòng nữa")]
    cluster_of = {"a": "a", "b": "a", "c": "a"}
    assert representatives(songs, cluster_of) == {"a": "b"}          # longest lyrics, never the medley
    counts = {sp: {"Q1": 1, "Q2": 1, "Q3": 1} for sp in ("train", "dev", "test")}
    for split, docs in (("test", {"b"}), ("train", {"a", "b"})):
        qs = generate_rule_queries(songs, cluster_of, counts, split_of_doc={d: split for d in cluster_of},
                                   one_per_cluster=("dev", "test"))
        assert {q["doc_id"] for q in qs} == docs


def test_llm_sample_is_a_fixed_share_of_clusters_by_hash():
    from visong.corpus import Song
    from visong.queries import LLM_SAMPLE, in_llm_sample, sample_key
    keys = [f"bai hat {i}" for i in range(4000)]
    assert all(0 <= sample_key(k) <= 1 for k in keys) and sample_key("em") == sample_key("em")
    share = sum(sample_key(k) < LLM_SAMPLE["test"] for k in keys) / len(keys)
    assert abs(share - LLM_SAMPLE["test"]) < 0.02
    s = Song("d", "u", "s", "t", lyrics="x")
    s.extra.update(cluster_key=keys[0], split="train")
    assert not in_llm_sample(s)                       # train clusters are never sampled
    s.extra.pop("cluster_key")
    assert not in_llm_sample(s)                       # pilot-format corpora have no keys
