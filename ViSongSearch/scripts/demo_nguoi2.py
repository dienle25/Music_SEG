"""Demo phần Người 2 (tạo truy vấn, sinh nhiễu, đánh giá) chạy được ngay, không cần dữ liệu crawl.

    python scripts/demo_nguoi2.py                               # 5 bài mẫu có sẵn, chạy ngay
    python scripts/demo_nguoi2.py --songs data/hac.jsonl --n 2000   # bài thật, nếu có file dữ liệu
    python scripts/demo_nguoi2.py --songs music_seg_merged_preprocesing.db   # file .db của nhóm (bảng songs)

Dùng vài bài hát ngắn tự viết ở dưới (không phải lời bài thật) để:
  1. sinh truy vấn Q1 (đúng lời), Q2 (không dấu), Q3 (nghe nhầm / gõ sai) bằng visong/queries.py + noise.py
  2. tìm kiếm bằng một bộ đếm âm tiết trùng đơn giản (chỉ để minh họa; mô hình thật là việc của Người 3)
  3. chấm điểm MRR@10, Success@1 theo từng loại bằng visong/evaluate.py
  4. tăng nhiễu từ 0% đến 50% để thấy điểm giảm dần (ý tưởng của fig_noise.pdf)
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.corpus import Song, make_doc_id                      # noqa: E402
from visong.evaluate import by_type, per_query, summarize   # noqa: E402
from visong.noise import corrupt                     # noqa: E402
from visong.queries import AmbiguityChecker, generate_rule_queries    # noqa: E402
from visong.text import clean, remove_diacritics     # noqa: E402

SONGS = [
    ("s1", "Mưa Trên Phố Cũ", "Chiều nay mưa rơi trên phố cũ\nEm đi qua con đường ngày xưa\nTiếng ai gọi tên em khe khẽ\nLá vàng rơi bên hiên nhà ai"),
    ("s2", "Biển Xanh", "Sóng vỗ về bờ cát trắng tinh khôi\nAnh ngồi đây nhớ một người xa xôi\nGió mang theo lời hát ru dịu dàng\nBiển xanh ơi giữ giùm anh mùa hạ"),
    ("s3", "Đường Về Quê Mẹ", "Con đường làng quanh co ruộng lúa\nKhói lam chiều bay trên mái tranh\nMẹ ngồi chờ con bên bậc cửa\nThương lời ru ngọt ngào năm xưa"),
    ("s4", "Thành Phố Đêm", "Ánh đèn đường vàng trên vai áo\nThành phố đêm không ngủ bao giờ\nMình anh đi giữa dòng người vội\nNghe tim mình khẽ gọi tên em"),
    ("s5", "Mùa Thu Hà Nội", "Gió heo may về trên phố nhỏ\nHương hoa sữa nồng nàn đêm thu\nTay trong tay mình đi bên hồ\nMùa thu ơi xin đừng vội qua"),
]


def bigrams(text):
    t = remove_diacritics(clean(text)).split()
    return set(zip(t, t[1:]))


def make_search(songs):
    """Tìm kiếm đơn giản: đếm số cặp âm tiết liền nhau (không dấu) trùng với lời bài.
    Chỉ để minh họa cách đánh giá; mô hình tìm kiếm thật (BM25, dense...) là phần của Người 3."""
    index = {}
    for s in songs:
        for b in bigrams(s.lyrics):
            index.setdefault(b, []).append(s.doc_id)

    def search(query):
        score = Counter()
        for b in bigrams(query):
            for d in index.get(b, ()):
                score[d] += 1
        return [(d, float(v)) for d, v in score.most_common(100)]
    return search


def read_db(path):
    """Đọc bảng songs của file .db (mở thẳng bằng đường dẫn, chạy được trên Windows)."""
    con = sqlite3.connect(path)
    seen = {}
    for url, title, artist, lyrics in con.execute("select url, title, artist, lyrics from songs"):
        if url and lyrics and url not in seen:
            seen[url] = Song(make_doc_id(url), url, "", (title or "").strip(), (artist or "").strip(), "", lyrics)
    con.close()
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", help="file bài hát: .jsonl (doc_id, title, lyrics) hoặc .db/.sqlite có bảng songs")
    ap.add_argument("--n", type=int, default=2000, help="số bài lấy từ file")
    a = ap.parse_args()
    if a.songs and not os.path.isfile(a.songs):
        sys.exit(f"Không tìm thấy file: {os.path.abspath(a.songs)}\n"
                 "Hãy kiểm tra tên file (gõ 'dir *.db' để xem) hoặc dùng đường dẫn đầy đủ trong dấu ngoặc kép.")
    if a.songs and a.songs.endswith((".db", ".sqlite")):
        songs = [s for s in read_db(a.songs) if len(s.lyrics.split()) >= 20]
        songs = random.Random(0).sample(songs, min(a.n, len(songs)))
    elif a.songs:
        songs = []
        for line in open(a.songs, encoding="utf-8"):
            d = json.loads(line)
            if d.get("lyrics", "").strip():
                songs.append(Song(doc_id=d["doc_id"], url="", source="", title=d.get("title", ""), lyrics=d["lyrics"]))
        songs = random.Random(0).sample(songs, min(a.n, len(songs)))
    else:
        songs = [Song(doc_id=i, url="", source="demo", title=t, lyrics=l) for i, t, l in SONGS]
    search = make_search(songs)
    title = {s.doc_id: s.title for s in songs}
    # Một bài có thể có nhiều bản (nhiều trang, nhiều ca sĩ): gom theo tên bài không dấu, nên tìm ra
    # bản nào của đúng bài cũng tính là đúng. (Dự án thật gom bằng MinHash trong visong/dedup.py.)
    cluster_of = {s.doc_id: remove_diacritics(clean(s.title)) or s.doc_id for s in songs}
    split = {s.doc_id: "test" for s in songs}

    print("== 1. Sinh truy vấn Q1, Q2, Q3 ==")
    print(f"  dùng {len(songs)} bài, {len(set(cluster_of.values()))} bài khác nhau")
    queries = generate_rule_queries(songs, cluster_of, {"test": {"Q1": 1 if a.songs else 2, "Q2": 1 if a.songs else 2, "Q3": 1 if a.songs else 2}},
                                    seed=13, split_of_doc=split,
                                    checker=AmbiguityChecker(songs, cluster_of))   # bỏ đoạn lời trùng bài khác
    for q in queries[:9]:
        print(f"  {q['type']}  {q['text']:<45} -> bài đúng: {title[q['doc_id']]}")
    print(f"  ... tổng {len(queries)} truy vấn")

    print("\n== 2-3. Tìm kiếm và chấm điểm theo loại ==")
    run = {q["qid"]: search(q["text"]) for q in queries}
    pq = per_query(run, queries, cluster_of)
    for t, qids in sorted(by_type(queries).items()):
        s = summarize(pq, qids)
        print(f"  {t}: {s['n']} câu  MRR@10 = {s['rr10']:.3f}  Success@1 = {s['s1']:.3f}")

    print("\n== 4. Tăng nhiễu từ 0% đến 50% (trên các câu Q1) ==")
    q1 = [q for q in queries if q["type"] == "Q1"]
    for rate in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
        rng = random.Random(7)
        noisy = [{**q, "qid": f"{q['qid']}@{rate}", "text": corrupt(q["text"], rate, rng)[0]} for q in q1]
        r = {q["qid"]: search(q["text"]) for q in noisy}
        s = summarize(per_query(r, noisy, cluster_of))
        print(f"  nhiễu {int(rate * 100):>2}%  MRR@10 = {s['rr10']:.3f}   ví dụ: {noisy[0]['text']}")


if __name__ == "__main__":
    main()
