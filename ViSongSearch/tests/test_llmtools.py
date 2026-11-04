from visong import llmtools as L
from visong.corpus import Song


def song(doc_id, title, artist="", lyrics="", split="test", singers=""):
    return Song(doc_id=doc_id, url=f"https://x/{doc_id}", source="x", title=title, artist=artist, lyrics=lyrics,
                extra={"split": split, "cluster_key": doc_id, "singers": singers})


LYR = ("Chiều nay mưa rơi trên phố vắng em đi\nAnh đứng lặng nhìn theo bóng dáng ai xa\n"
       "Ngày xưa ta hẹn thề bên nhau mãi mãi\nGiờ chỉ còn mình anh với cơn mưa chiều")


def test_first_answer_strips_labels_quotes_and_think():
    assert L.first_answer('<think>hmm</think>\n\n"Câu tìm kiếm: mưa chiều phố vắng"') == "mưa chiều phố vắng"
    assert L.first_answer("  \n- bài hát về mưa\nthêm dòng") == "bài hát về mưa"
    assert L.first_answer("") == ""


def test_filter_output_length_and_copy():
    s = song("a1", "Mưa Chiều", lyrics=LYR)
    frag = "Chiều nay mưa rơi trên phố vắng em đi / Anh đứng lặng nhìn theo bóng dáng ai xa"
    text, ok, why = L.filter_output("Q4", "buổi chiều trời đổ mưa con phố không người em bước đi xa", s, frag)
    assert ok and why == "ok", why
    _, ok, why = L.filter_output("Q4", "chiều nay mưa rơi trên phố vắng em đi mất rồi", s, frag)
    assert not ok and why.startswith("copied") or why == "too_close"
    _, ok, why = L.filter_output("Q4", "trời buồn quá đi thôi", s, frag)
    assert not ok and why == "too_short"
    long = " ".join(["mưa"] * 3 + ["ngày xưa có một người con gái bước đi trong chiều mưa lạnh lẽo buồn hiu hắt mãi không quay về nữa"])
    _, ok, why = L.filter_output("Q4", long, s, frag)
    assert not ok and why == "too_long"


def test_parse_guesses():
    g = L.parse_guesses("1. Mưa Chiều - Lam Phương\n2) Em Đi – Trịnh Công Sơn\n3. Mưa Chiều - X\n4. Diễm Xưa")
    assert g == [("Mưa Chiều", "Lam Phương"), ("Em Đi", "Trịnh Công Sơn"), ("Diễm Xưa", "")]


def test_title_matcher_blocks_and_expected_rr():
    songs = [song("a", "Mưa", "Lam Phương"), song("b", "Mưa", "Trịnh Công Sơn"), song("c", "Mưa (Remix)", "Ai Đó"),
             song("d", "Em Đi", "Trịnh Công Sơn")]
    cluster_of = {"a": "a", "b": "b", "c": "c", "d": "d"}
    m = L.TitleMatcher(songs, cluster_of)
    blocks = m.blocks([("Mưa", "Trịnh Công Sơn"), ("Em Đi", "")])
    assert blocks[0] == ["b"] and set(blocks[1]) == {"a", "c"} and blocks[2] == ["d"]
    assert L.expected_rr(blocks, "b")[0] == 1.0
    rr, er = L.expected_rr(blocks, "a")
    assert abs(rr - (1 / 2 + 1 / 3) / 2) < 1e-9 and er == 2.5
    assert L.expected_rr(blocks, "zzz") == (0.0, None)
    assert L.expected_rr([["x"] * 12, ["g"]], "g")[0] == 0.0


def test_choice_and_rerank():
    assert L.parse_choice("3, 1, 3, 9, 25", n=10) == [2, 0, 8]
    cands = [("c1", "d1"), ("c2", "d2"), ("c3", "d3")]
    run = [("d1", 3.0), ("d2", 2.0), ("d3", 1.0), ("d4", 0.5)]
    out = [d for d, _ in L.rerank(run, cands, [2])]
    assert out == ["d3", "d1", "d2", "d4"]


def test_snippet_and_top_clusters():
    s = song("a", "Mưa Chiều", lyrics=LYR)
    sn = L.snippet(s, "hen the ben nhau mai mai")
    assert "hẹn thề" in sn
    assert L.top_clusters([("a", 1), ("a2", 0.9), ("b", 0.8)], {"a": "A", "a2": "A", "b": "B"}, 5) == [("A", "a"), ("B", "b")]


def test_build_items_keeps_existing_then_samples():
    songs = [song(f"s{i}", f"Bài {i}", lyrics=LYR, split="test" if i % 2 else "dev") for i in range(10)]
    cluster_of = {s.doc_id: s.doc_id for s in songs}
    existing = [{"qid": "s1-Q4-0", "type": "Q4", "doc_id": "s1", "cluster": "s1", "split": "test",
                 "meta": {"source_fragment": "frag cũ"}}]
    items = L.build_items(songs, cluster_of, existing, n_test=3, n_dev=2)
    assert items[0]["doc_id"] == "s1" and items[0]["frag"] == "frag cũ" and items[0]["origin"] == "existing"
    assert sum(i["split"] == "test" for i in items) == 3 and sum(i["split"] == "dev" for i in items) == 2
    assert len({i["cluster"] for i in items}) == len(items)
    assert "Bài 1" not in L.prompt_for("Q4", items[0]) and "Bài 1" in L.prompt_for("Q5", items[0])
