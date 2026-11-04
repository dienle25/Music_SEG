"""resolve_tot.py on a tiny invented collection: the titles, names and lyric lines below are made up."""
import csv
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("resolve_tot", os.path.join(ROOT, "scripts", "resolve_tot.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

# doc_id, title, composer, singers, cluster, split
SONGS = [
    ("hac1", "Chiều Phố Xa", "Nguyễn Văn Thử", "Ca Sĩ Mẫu", "hac1", "test"),
    ("nhaccu1", "Chiều Phố Xa (Remix)", "", "Ca Sĩ Mẫu", "hac1", "test"),      # second version, same cluster
    ("hac2", "Mưa", "Tác Giả Bê", "", "hac2", "dev"),
    ("hac3", "Mưa", "Tác Giả Xê", "", "hac3", "train"),                        # same title, another song
    ("hac5", "Lời Hứa Mùa Thu", "Nhạc Sĩ Đê", "", "hac5", "test"),
    ("hac6", "Liên Khúc Chiều Phố Xa", "", "", "hac6", "test"),               # medley: never a target
]


def write_work(folder):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "corpus.jsonl"), "w", encoding="utf-8") as f:
        for i, (doc, title, composer, singers, cluster, split) in enumerate(SONGS):
            lyrics = f"dòng thử nghiệm số {i} của bài giả\nlời giả thứ {i} để kiểm tra bộ khớp"
            f.write(json.dumps({"doc_id": doc, "url": f"https://example.org/{doc}", "source": "example.org",
                                "title": title, "artist": composer, "genre": "", "lyrics": lyrics,
                                "extra": {"singers": singers, "cluster": cluster, "split": split}},
                               ensure_ascii=False) + "\n")
    with open(os.path.join(folder, "clusters.json"), "w", encoding="utf-8") as f:
        json.dump({s[0]: s[4] for s in SONGS}, f)
    return folder


@pytest.fixture(scope="module")
def cat(tmp_path_factory):
    work = write_work(str(tmp_path_factory.mktemp("work")))
    with open(os.path.join(work, "clusters.json")) as f:
        return R.Catalogue(R.load_corpus(os.path.join(work, "corpus.jsonl")), json.load(f))


def rec(answer, method="asker_solved", query="a sad song I heard years ago", links=(), titles=()):
    return {"id": "test:1", "source": "test", "url": "https://example.org/q", "query": query,
            "answer_text": answer, "created": None, "answer_method": method,
            "answer_links": list(links), "answer_link_titles": list(titles)}


def test_title_and_composer_resolve_without_manual_check(cat):
    r = R.resolve(rec("It's Chieu Pho Xa by Nguyen Van Thu!"), cat)
    assert r["cluster_id"] == "hac1" and r["split"] == "test"
    assert r["confidence"] >= R.ACCEPT and not r["needs_manual_check"]


def test_title_alone_is_flagged(cat):
    r = R.resolve(rec("bài này là Lời hứa mùa thu nhé bạn", "asker_confirmed"), cat)
    assert r["cluster_id"] == "hac5" and r["needs_manual_check"]
    assert "no_name_match" in r["check_reasons"] and r["confidence"] < R.ACCEPT


def test_shared_title_is_ambiguous_until_a_name_decides(cat):
    r = R.resolve(rec("Mưa"), cat)
    assert "ambiguous" in r["check_reasons"]
    assert {c["cluster_id"] for c in r["candidates"]} == {"hac2", "hac3"}
    r = R.resolve(rec('"Mưa" - Tác Giả Xê'), cat)      # a name one letter from the other composer's
    assert r["cluster_id"] == "hac3" and "ambiguous" not in r["check_reasons"]
    assert r["candidates"][0]["confidence"] - r["candidates"][1]["confidence"] >= R.MARGIN


def test_hopamchuan_link_and_link_title(cat):
    r = R.resolve(rec("here: https://hopamchuan.com/song/5/loi-hua-mua-thu/"), cat)
    assert r["cluster_id"] == "hac5" and r["match_method"] == "hac_link" and r["confidence"] == 1.0
    # a bare video link: the oEmbed title (with a typo) names the song and the composer
    r = R.resolve(rec("https://youtu.be/xxxxxxxxxxx", titles=["NGUYỄN VĂN THỬ | CHIỀU PHỐ XAA | OFFICIAL MV"]), cat)
    assert r["cluster_id"] == "hac1" and r["match_method"] == "title_fuzzy" and r["confidence"] >= R.ACCEPT


def test_medleys_unknown_songs_and_missing_answers(cat):
    r = R.resolve(rec("Liên khúc Chiều Phố Xa"), cat)
    assert r["cluster_id"] == "hac1"                    # the song inside, not the medley hac6
    r = R.resolve(rec("Fortunate Son by Creedence Clearwater Revival"), cat)
    assert r["cluster_id"] is None and r["check_reasons"] == ["no_match"]
    r = R.resolve(rec(None, method=None), cat)
    assert r["cluster_id"] is None and r["check_reasons"] == ["no_answer"] and r["needs_manual_check"]


def test_weak_answer_method_is_flagged(cat):
    r = R.resolve(rec("Chiều phố xa - Nguyễn Văn Thử", method="reply"), cat)
    assert r["cluster_id"] == "hac1" and r["check_reasons"] == ["weak_answer_method"]


def test_lyric_overlap_is_reported_but_never_changes_the_confidence(cat):
    plain = R.resolve(rec("Chieu Pho Xa by Nguyen Van Thu"), cat)
    quoting = R.resolve(rec("Chieu Pho Xa by Nguyen Van Thu", query="it goes dong thu nghiem so 0 cua bai"), cat)
    assert quoting["query_lyrics_hits"] >= 3 and plain["query_lyrics_hits"] == 0
    assert quoting["confidence"] == plain["confidence"]


def test_command_line_writes_records_csv_and_stats(tmp_path):
    work = write_work(str(tmp_path / "work"))
    harvest = tmp_path / "harvest.jsonl"
    rows = [dict(rec("Chieu Pho Xa by Nguyen Van Thu"), id="test:1"), dict(rec("Mưa"), id="test:2"),
            dict(rec(None, method=None), id="test:3"), dict(rec("Mưa"), id="test:2")]     # repeated id
    harvest.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "out" / "resolved.jsonl"
    stats = R.main([work, str(harvest), "--out", str(out)])
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [x["id"] for x in lines] == ["test:1", "test:2", "test:3"]
    assert stats["records"] == 3 and stats["auto_accepted"] == 1 and stats["matched_by_split"] == {"test": 1, "dev": 1}
    with open(tmp_path / "out" / "resolved_check.csv", encoding="utf-8-sig") as f:
        table = list(csv.DictReader(f))
    assert len(table) == 3 and table[-1]["id"] == "test:1" and "verified_cluster_id" in table[0]
    assert "dòng thử nghiệm" not in out.read_text(encoding="utf-8")     # no lyrics in the output
    assert json.loads((tmp_path / "out" / "resolved_stats.json").read_text())["records"] == 3
