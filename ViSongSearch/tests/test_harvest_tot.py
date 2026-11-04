"""harvest_tot.py without the network: answer rules, filters, anonymisation and the XenForo parser on
small invented payloads shaped like the Reddit, TOMT-KIS and XenForo 2 formats."""
import importlib.util
import json
import os
from collections import Counter
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("harvest_tot", os.path.join(ROOT, "scripts", "harvest_tot.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def ctx():
    return SimpleNamespace(args=SimpleNamespace(min_words=6), anon=H.Anon("test-salt"), st=Counter(),
                           log=lambda msg: None)


def t1(cid, parent, author, body, score=1, replies=()):
    return {"kind": "t1", "data": {"id": cid, "parent_id": parent, "author": author, "body": body, "score": score,
                                   "replies": {"kind": "Listing", "data": {"children": list(replies)}} if replies else ""}}


REDDIT_COMMENTS = {"kind": "Listing", "data": {"children": [
    t1("c1", "t3_p1", "helper", "Is it Chieu Pho Xa by Nguyen Van Thu?", 5,
       [t1("c2", "t1_c1", "Asker77", "Solved! Thank you so much")]),
    t1("c3", "t3_p1", "guesser", "Maybe Mua by someone", 9,
       [t1("c4", "t1_c3", "Asker77", "thanks but that's not it")]),
    t1("c5", "t3_p1", "AutoModerator", "Your post has been flaired"),
    {"kind": "more", "data": {"children": ["c9"]}}]}}


def test_asker_solved_reply_names_the_answer_and_a_refusal_does_not():
    comments = H.flatten_comments(REDDIT_COMMENTS)
    assert [c["id"] for c in comments] == ["c1", "c2", "c3", "c4", "c5"]
    assert comments[1]["parent"] == "c1" and comments[0]["parent"] is None
    ans = H.pick_answer("Asker77", comments, "Solved")
    assert ans.method == "asker_solved" and ans.text.startswith("Is it Chieu Pho Xa")
    # without the asker the flair only says that some reply solved it: the top-voted one, a weak guess
    assert H.pick_answer("[deleted]", comments, "Solved").method == "flair_top"
    assert H.pick_answer("[deleted]", comments, "Open") is None


def test_flatten_reads_other_tree_shapes():
    plain = [{"id": "a", "author": "x", "body": "one", "children": [{"id": "b", "author": "y", "body": "two"}]}]
    beside = [{"kind": "t1", "data": {"id": "a", "author": "x", "body": "one"},
               "replies": [{"kind": "t1", "data": {"id": "b", "author": "y", "body": "two"}}]}]
    for tree in (plain, beside, {"data": plain}):
        assert [(c["id"], c["parent"]) for c in H.flatten_comments(tree)] == [("a", None), ("b", "a")]


def test_filters_keep_vietnamese_song_requests_only():
    assert H.is_tot_song("[TOMT][SONG] Vietnamese pop song from around 2015", "female singer")
    assert H.is_tot_song("[TOMT][SONG] Asian song from a cafe", 'it goes "người ơi đừng đi" or so')
    assert not H.is_tot_song("[TOMT][SONG] 60s rock song from a Vietnam war movie", "helicopters")
    assert not H.is_tot_song("[TOMT][MOVIE] Vietnamese film about two brothers", "set in Saigon")
    assert H.is_song_request("Tìm tên bài hát có câu ...", "")
    assert not H.is_song_request("Tìm bài viết về lịch sử", "")


def test_tomtkis_row_becomes_an_anonymised_record():
    row = {"id": "abc123", "author": "SomeUser42", "permalink": "/r/tipofmytongue/comments/abc123/x/",
           "title": "Vietnamese song with a female singer, maybe from the 2010s",
           "content": "I remember a slow chorus. u/friend_name heard it too, mail me at me@example.org",
           "created_utc": "1500000000", "link_flair_text": "Solved", "categories": ["[TOMT]", "[SONG]"],
           "answer_detected": True, "chosen_answer": "Chieu Pho Xa - Nguyen Van Thu",
           "comments": [{"author": "helper", "body": "Chieu Pho Xa - Nguyen Van Thu", "score": 3,
                         "comments": [{"author": "SomeUser42", "body": "Yes!! that's it", "score": 1, "comments": []}]}]}
    rec = H.tomtkis_record(ctx(), row)
    assert rec["id"] == "reddit:abc123" and rec["via"] == "tomtkis" and rec["created"] == "2017-07-14T02:40:00Z"
    assert rec["answer_method"] == "asker_confirmed" and rec["answer_text"] == "Chieu Pho Xa - Nguyen Van Thu"
    assert rec["url"] == "https://www.reddit.com/r/tipofmytongue/comments/abc123/x/"
    dumped = json.dumps(rec)
    assert "SomeUser42" not in dumped and "friend_name" not in dumped and "me@example.org" not in dumped
    assert rec["asker"] == H.Anon("test-salt")("SomeUser42")
    # the asker said nothing: TOMT-KIS's own heuristic answer is kept
    row["comments"][0]["comments"] = []
    assert H.tomtkis_record(ctx(), row)["answer_method"] == "tomtkis_heuristic"
    assert H.tomtkis_record(ctx(), dict(row, title="A song about trains", categories=["[SONG]"],
                                        content="an English folk tune I heard on the radio")) is None


def post(pid, author, body, quote=None):
    q = (f'<blockquote class="bbCodeBlock bbCodeBlock--expandable bbCodeBlock--quote" data-quote="{quote[1]}" '
         f'data-source="post: {quote[0]}"><div class="bbCodeBlock-content"><div class="bbCodeBlock-expandContent">'
         f'quoted words</div></div></blockquote>') if quote else ""
    return (f'<article class="message message--post" data-author="{author}" data-content="post-{pid}">'
            f'<header><time class="u-dt" datetime="2023-04-05T20:00:00+0700" data-time="1680699600">x</time></header>'
            f'<article class="message-body"><div class="bbWrapper">{q}{body}</div></article></article>')


def page(title, posts):
    nav = ('<ul class="pageNav-main"><li class="pageNav-page"><a href="/t/x.1/">1</a></li>'
           '<li class="pageNav-page"><a href="/t/x.1/page-3">3</a></li></ul>')
    return f'<html><body><h1 class="p-title-value">{title}</h1>{"".join(posts)}{nav}</body></html>'


def test_xenforo_single_question_thread_with_confirmation():
    html = page("Xin tên bài hát ạ", [
        post(101, "Alice", "Mình nhớ mang máng một bài có câu dòng thử nghiệm số không, ai biết tên giúp với"),
        post(102, "Bob", 'Bài này là Chiều Phố Xa của Nguyễn Văn Thử nhé <a href="https://youtu.be/xxxxxxxxxxx">nghe</a>',
             quote=(101, "Alice")),
        post(103, "Alice", "Đúng rồi, cảm ơn bạn nhiều", quote=(102, "Bob"))])
    title, posts, last = H.parse_xenforo(html, "https://forum.example.org/t/x.1/")
    assert title == "Xin tên bài hát ạ" and last == 3 and [p["id"] for p in posts] == ["101", "102", "103"]
    assert posts[1]["quotes"] == [{"post": "101", "author": "Alice", "text": "quoted words"}]
    assert "quoted words" not in posts[1]["text"]
    recs = list(H.forum_records(ctx(), posts, title, "https://forum.example.org/t/x.1/"))
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == "example:101" and r["url"] == "https://forum.example.org/goto/post?id=101"
    assert r["answer_method"] == "asker_confirmed" and r["answer_text"].startswith("Bài này là Chiều Phố Xa")
    assert r["answer_links"] == ["https://youtu.be/xxxxxxxxxxx"] and r["created"] == "2023-04-05T13:00:00Z"
    assert r["query"].startswith("Xin tên bài hát ạ") and "Alice" not in json.dumps(r) and "Bob" not in json.dumps(r)


def test_xenforo_megathread_pairs_each_request_with_its_quoting_answer():
    html = page("[TỔNG HỢP] Nơi nhờ vả tìm tên bài hát", [
        post(1, "Ann", "Ai biết bài gì có đoạn dòng thử nghiệm số một, nhạc trẻ khoảng năm 2010 không ạ"),
        post(2, "Cat", "Cho mình hỏi tên bài hát nữ hát, giai điệu buồn, có câu lời giả thứ hai"),
        post(3, "Ben", "Chiều Phố Xa - Nguyễn Văn Thử", quote=(1, "Ann")),
        post(4, "Dan", "mình cũng đang tìm bài này", quote=(2, "Cat")),
        post(5, "Eve", '<a href="https://www.youtube.com/watch?v=yyyyyyyyyyy">bài này</a>', quote=(2, "Cat")),
        post(6, "Ann", "chuẩn rồi, tks bác", quote=(3, "Ben"))])
    title, posts, _ = H.parse_xenforo(html, "https://voz.example.vn/t/x.1/page-2")
    recs = {r["id"]: r for r in H.forum_records(ctx(), posts, title, "https://voz.example.vn/t/x.1/")}
    assert set(recs) == {"voz:1", "voz:2"}
    assert recs["voz:1"]["answer_method"] == "asker_confirmed" and recs["voz:1"]["answer_text"].startswith("Chiều")
    assert recs["voz:2"]["answer_method"] == "reply" and recs["voz:2"]["answer_links"] == [
        "https://www.youtube.com/watch?v=yyyyyyyyyyy"]


def test_merge_keeps_the_stronger_answer():
    records = {}
    weak = {"id": "reddit:x", "answer_method": "flair_top"}
    strong = {"id": "reddit:x", "answer_method": "asker_solved"}
    H.merge(records, weak)
    H.merge(records, strong)
    H.merge(records, dict(weak))
    assert records["reddit:x"] is strong


def test_command_line_runs_without_any_fetch(tmp_path):
    out = tmp_path / "h.jsonl"
    stats = H.main(["--sources", "forum", "--out", str(out), "--no-oembed"])    # no --thread: nothing to fetch
    assert out.read_text() == "" and stats["forum"]["status"] == "ok" and stats["total"]["records"] == 0
    assert (tmp_path / "h_stats.json").exists() and (tmp_path / "h.log").exists()


def test_kaggle_notebook_embeds_the_current_scripts():
    with open(os.path.join(ROOT, "kaggle", "visongsearch_realq.ipynb"), encoding="utf-8") as f:
        cells = ["".join(c["source"]) for c in json.load(f)["cells"] if c["cell_type"] == "code"]
    for name in ("harvest_tot.py", "resolve_tot.py"):
        with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
            script = f.read()
        embedded = [c.split("\n", 1)[1] for c in cells if c.startswith("%%writefile") and c.split("\n", 1)[0].endswith(name)]
        assert embedded == [script], f"run scripts/make_realq_notebook.py: the notebook's copy of {name} is stale"
