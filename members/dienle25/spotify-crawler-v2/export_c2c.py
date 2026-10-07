# =========================================================
# export_c2c.py  –  xuất bảng tracks sang schema của đề tài C2C-VN
# =========================================================
#     python export_c2c.py                  # data/spotify.db -> data/c2c_spotify.sqlite
#     python export_c2c.py --db A --out B
#
# File xuất ra có đúng 3 bảng như crawler của đề tài (c2c_crawl.py):
#     songs(song_id, url, title, authors_json, author_ids_json, n_authors, genre,
#           rhythm, record_json, html_sha256, http_status, crawled_utc, source_listing)
#     listings(url, kind, n_song_links, n_new_song_links, http_status, html_sha256, crawled_utc)
#     meta(k, v)
# nên dùng được với các script của đề tài, ví dụ (đặt C2C_HOME là một thư mục
# riêng để không ghi đè dữ liệu HopAmChuan):
#     python scripts/build_testbed.py --db <đường dẫn>/c2c_spotify.sqlite
#
# Khác biệt về NGHĨA so với dữ liệu HopAmChuan (ghi vào meta và record_json):
#   authors        = nghệ sĩ TRÌNH BÀY (performer) trên Spotify, không phải tác giả sáng tác
#   genre, rhythm  = NULL (trang Spotify không có)
#   song_id        = 60 bit đầu của SHA-1(track_id): số nguyên, giữ nguyên giữa các lần chạy
# Dữ liệu Spotify chỉ dùng cho phân tích thăm dò (exploratory), không thay testbed
# HopAmChuan đã đăng ký trước (PREREG) của đề tài.

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

import config
from database import BASE_DIR, DB_PATH

C2C_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
  song_id INTEGER PRIMARY KEY, url TEXT, title TEXT, authors_json TEXT, author_ids_json TEXT,
  n_authors INTEGER, genre TEXT, rhythm TEXT, record_json TEXT, html_sha256 TEXT,
  http_status INTEGER, crawled_utc TEXT, source_listing TEXT);
CREATE TABLE IF NOT EXISTS listings (
  url TEXT PRIMARY KEY, kind TEXT, n_song_links INTEGER, n_new_song_links INTEGER,
  http_status INTEGER, html_sha256 TEXT, crawled_utc TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

TOP_COUNT = 30      # testbed: tác giả của câu F3 có 30 bài trong một ngữ cảnh
M_MAX = 24          # testbed: mức m lớn nhất của câu F2

SEMANTICS = {
    "source": "open.spotify.com (HTML công khai, không đăng nhập, tôn trọng robots.txt)",
    "authors": "performer – nghệ sĩ trình bày trên Spotify, KHÔNG phải tác giả sáng tác",
    "author_ids": "id nghệ sĩ Spotify (chuỗi 22 ký tự)",
    "genre": "NULL – trang Spotify không có thể loại",
    "rhythm": "NULL – trang Spotify không có điệu",
    "song_id": "int(sha1(track_id).hexdigest()[:15], 16)",
    "lyrics": "không có – lời bài hát chỉ hiện khi đăng nhập, crawler không lấy",
    "use": "exploratory / post hoc; không thay testbed HopAmChuan đã đăng ký trước",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def song_id_of(track_id):
    """Id số nguyên ổn định cho một track Spotify (60 bit, vừa INTEGER của SQLite)."""
    return int(hashlib.sha1(track_id.encode("utf-8")).hexdigest()[:15], 16)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# Hai hàm chuẩn hoá giống hệt c2c/testbed.py của đề tài
def nfc(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def norm_key(s):
    s = nfc(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def readiness(rows):
    """
    Ước tính số bản ghi testbed dùng được, theo đúng luật prepare_records() của C2C-VN:
    chỉ bài có đúng 1 nghệ sĩ; mỗi nghệ sĩ (theo id) một tên chuẩn; bỏ nghệ sĩ trùng tên
    chuẩn với nghệ sĩ lớn hơn; gộp bài trùng (tên bài + nghệ sĩ).
    rows: dict có song_id, title, authors, author_ids.
    """
    rep = Counter()
    single = []
    for r in rows:
        rep["rows"] += 1
        title = nfc(r["title"])
        authors = [nfc(a) for a in (r["authors"] or []) if nfc(a)]
        ids = r["author_ids"] or []
        if not title:
            rep["no_title"] += 1
            continue
        if len(authors) != 1:
            rep["not_single_author"] += 1
            continue
        key = str(ids[0]) if len(ids) == 1 else "name:" + norm_key(authors[0])
        single.append({"song_id": int(r["song_id"]), "title": title,
                       "author_raw": authors[0], "author_key": key})

    names = defaultdict(Counter)
    for s in single:
        names[s["author_key"]][s["author_raw"]] += 1
    canon = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in names.items()}

    by_name = defaultdict(list)
    for k, name in canon.items():
        by_name[norm_key(name)].append(k)
    drop = set()
    for keys in by_name.values():
        if len(keys) > 1:
            keys = sorted(keys, key=lambda k: -sum(names[k].values()))
            drop.update(keys[1:])
            rep["dropped_name_collision_keys"] += len(keys) - 1

    seen, records = set(), []
    for s in sorted(single, key=lambda x: x["song_id"]):
        if s["author_key"] in drop:
            continue
        dedup_key = (norm_key(s["title"]), s["author_key"])
        if dedup_key in seen:
            rep["duplicate_title_author"] += 1
            continue
        seen.add(dedup_key)
        records.append(s)

    per_author = Counter(s["author_key"] for s in records)
    report = dict(rep)
    report.update({
        "records": len(records),
        "authors": len(per_author),
        "authors_ge30": sum(1 for v in per_author.values() if v >= TOP_COUNT),
        "authors_ge24": sum(1 for v in per_author.values() if v >= M_MAX),
        "top_authors": [[canon[k], v] for k, v in per_author.most_common(20)],
    })
    return report


def read_source(db_path):
    src = sqlite3.connect(db_path)
    try:
        tracks = src.execute("""
        SELECT track_id, url, title, artists_json, artist_ids_json, artist_text, album, album_id,
               track_number, release_date, year, duration_sec, source_url, html_sha256, crawled_utc
        FROM tracks ORDER BY rowid
        """).fetchall()
        listings = src.execute("""
        SELECT url, kind, n_tracks, n_new_tracks, html_sha256, crawled_utc
        FROM listings ORDER BY rowid
        """).fetchall()
        run = src.execute("SELECT v FROM meta WHERE k = 'run'").fetchone()
    except sqlite3.OperationalError:
        sys.exit(f"{db_path} chưa có bảng tracks – chạy python main.py (bản Spotify) trước.")
    finally:
        src.close()
    return tracks, listings, json.loads(run[0]) if run else None


def export(db_path, out_path):
    tracks, listings, run = read_source(db_path)
    if not tracks:
        sys.exit("Bảng tracks đang rỗng – chưa có bài hát nào để xuất.")

    if os.path.exists(out_path):
        os.remove(out_path)                      # file dẫn xuất: tạo lại từ đầu
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    out = sqlite3.connect(out_path)
    out.executescript(C2C_SCHEMA)

    rows, owner, collisions = [], {}, 0
    for (track_id, url, title, artists_json, ids_json, artist_text, album, album_id,
         track_number, release_date, year, duration_sec, source_url, html_sha256, crawled_utc) in tracks:
        song_id = song_id_of(track_id)
        if owner.setdefault(song_id, track_id) != track_id:      # gần như không thể xảy ra
            collisions += 1
            continue
        authors = json.loads(artists_json or "[]")
        author_ids = json.loads(ids_json or "[]")
        record = {
            "song_id": song_id, "url": url, "title": title,
            "authors": authors, "author_ids": author_ids,
            "genre": None, "rhythm": None,
            "role": "performer", "source": "open.spotify.com",
            "spotify_track_id": track_id, "artist_text": artist_text,
            "album": album, "album_id": album_id, "track_number": track_number,
            "release_date": release_date, "year": year, "duration_sec": duration_sec,
            "html_sha256": html_sha256,
        }
        out.execute("INSERT INTO songs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            song_id, url, title,
            json.dumps(authors, ensure_ascii=False), json.dumps(author_ids), len(authors),
            None, None, json.dumps(record, ensure_ascii=False),
            html_sha256, 200, crawled_utc, source_url,
        ))
        rows.append({"song_id": song_id, "title": title, "authors": authors, "author_ids": author_ids})

    out.executemany("INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?,?,?)", [
        (url, kind, n_tracks, n_new, 200, html_sha256, crawled_utc)
        for url, kind, n_tracks, n_new, html_sha256, crawled_utc in listings
    ])

    report = readiness(rows)
    report.update({
        "exported_utc": utc_now(),
        "source_db": os.path.basename(db_path),
        "source_db_sha256": sha256_file(db_path),
        "out_db": os.path.basename(out_path),
        "songs": len(rows),
        "listings": len(listings),
        "song_id_collisions": collisions,
        "semantics": SEMANTICS,
    })

    meta = {
        "run": run,                       # thông tin lần crawl (phiên bản, seed, robots.txt...)
        "export": {key: report[key] for key in (
            "exported_utc", "source_db", "source_db_sha256", "songs", "listings",
            "song_id_collisions", "semantics")} | {"exporter": "export_c2c.py",
                                                  "crawler_version": config.CRAWLER_VERSION},
    }
    out.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                    [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
    out.commit()
    out.close()
    return report


def print_report(report, out_path, report_path):
    def row(label, value):
        return f"{label:<34}: {value}"

    skipped_multi = report.get("not_single_author", 0)
    skipped_dup = report.get("duplicate_title_author", 0)
    lines = [
        "",
        "=" * 10 + " XUẤT DỮ LIỆU CHO ĐỀ TÀI C2C-VN " + "=" * 10,
        row("Nguồn", f"{report['source_db']} (sha256 {report['source_db_sha256'][:16]})"),
        row("File xuất", out_path),
        row("Bài hát (bảng songs)", report["songs"]),
        row("Album / playlist (bảng listings)", report["listings"]),
        "",
        "Ước tính theo luật testbed (prepare_records):",
        row("    Bài 1 nghệ sĩ, không trùng", report["records"]),
        row("    Bỏ: nhiều nghệ sĩ / trùng bài", f"{skipped_multi} / {skipped_dup}"),
        row("    Số nghệ sĩ", report["authors"]),
        row(f"    Nghệ sĩ >= {TOP_COUNT} bài", report["authors_ge30"]),
        row(f"    Nghệ sĩ >= {M_MAX} bài", report["authors_ge24"]),
    ]
    if report["top_authors"]:
        lines.append("Top nghệ sĩ (số bài dùng được):")
        for name, count in report["top_authors"][:10]:
            lines.append(row(f"    {name[:30]}", count))
    lines += [
        "",
        "Lưu ý:",
        "  - authors = nghệ sĩ TRÌNH BÀY; câu hỏi testbed đang viết 'tác giả / sáng tác',",
        "    muốn dùng dữ liệu này phải đổi câu hỏi và ghi là phân tích thăm dò (exploratory).",
        "  - genre, rhythm = NULL; không có lời bài hát.",
        f"  - Testbed cần nhiều nghệ sĩ có >= {TOP_COUNT} bài: thêm playlist 'This Is <nghệ sĩ>'",
        "    hoặc album vào seeds.txt rồi crawl lại.",
        f"(Báo cáo JSON: {report_path})",
        "=" * 52,
    ]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Xuất dữ liệu Spotify theo schema của đề tài C2C-VN")
    ap.add_argument("--db", default=DB_PATH, help="database của crawler (mặc định: %(default)s)")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, config.C2C_DB_FILE),
                    help="file SQLite xuất ra (mặc định: %(default)s)")
    ap.add_argument("--report", default=os.path.join(BASE_DIR, config.C2C_REPORT_FILE),
                    help="báo cáo JSON (mặc định: %(default)s)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"Không thấy {args.db} – chạy python main.py trước.")

    report = export(args.db, args.out)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print_report(report, args.out, args.report)


if __name__ == "__main__":
    main()
