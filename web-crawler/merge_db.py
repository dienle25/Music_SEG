# =========================================================
# merge_db.py  –  gộp database của cả nhóm vào data/music_seg_merged.db
#
#   python merge_db.py           tạo lại data/music_seg_merged.db từ các database nguồn
#   python merge_db.py --check   kiểm tra file đã nộp khớp với database nguồn (không ghi vào repo)
# =========================================================
"""Gộp database của crawler chung và database của từng thành viên thành một file.

- Form giống data/crawler.db: bảng pages, links, songs, tạo bằng database.create_tables().
- Đọc các nguồn theo thứ tự trong SOURCES; trong mỗi nguồn đọc theo id tăng dần.
- Trùng url (pages, songs) hoặc trùng cặp (source_url, target_url) (links): giữ dòng của
  nguồn đứng trước, giống INSERT OR IGNORE.
- id được đánh lại từ 1 theo thứ tự đọc. Database nguồn chỉ được mở ở chế độ đọc.
"""

import argparse
import hashlib
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import database

PROJECT_DIR = Path(__file__).resolve().parent
REPO_DIR = PROJECT_DIR.parent
MERGED_PATH = PROJECT_DIR / "data" / "music_seg_merged.db"
FORM_PATH = PROJECT_DIR / "data" / "crawler.db"  # database của crawler chung, dùng làm form mẫu

# (ai crawl, nội dung, đường dẫn tính từ gốc repo); khi trùng thì giữ dòng của nguồn đứng trước
SOURCES = [
    ("thanhtrumbacang", "NhacCuaTui (bản đã làm sạch)",
     "members/thanhtrumbacang/nhaccuatui-crawler-v2/music_clean.db"),
    ("quy205", "Hợp Âm Chuẩn",
     "members/quy205/hopamchuan_crawler_quy205_lyrics_pending/hopamchuan_phuquy/data/hopamchuan_phuquy.db"),
    ("TranKhoaDang", "nhac.vn",
     "members/TranKhoaDang/nhacvn-crawler/data/crawler.db"),
    ("cả nhóm", "crawler chung (4 website, có Spotify)",
     "web-crawler/data/crawler.db"),
]

# bảng -> (cột dùng để chống trùng, các cột được chép theo thứ tự trong database.py)
TABLES = {
    "pages": (("url",), ("url", "domain", "title", "content", "depth", "status_code", "crawled_at")),
    "links": (("source_url", "target_url"), ("source_url", "target_url")),
    "songs": (("url",), ("url", "title", "artist", "album", "genre", "lyrics", "crawled_at")),
}


def ro_uri(path):
    """URI mở database ở chế độ chỉ đọc (dùng được cả trên Windows)."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(out_path):
    """Gộp SOURCES vào out_path (tạo mới). Trả về [(nguồn, {bảng: (số dòng thêm, số dòng trong nguồn)})]."""
    out_path = Path(out_path)
    out_path.unlink(missing_ok=True)
    saved_path = database.DB_PATH
    database.DB_PATH = str(out_path)
    try:
        database.create_tables()
    finally:
        database.DB_PATH = saved_path

    conn = sqlite3.connect(str(out_path), uri=True)  # uri=True để ATTACH nhận "file:...?mode=ro"
    report = []
    for source in SOURCES:
        rel = source[2]
        src_path = REPO_DIR / rel
        if not src_path.is_file():
            sys.exit(f"Không thấy database nguồn: {rel}")
        conn.execute("ATTACH DATABASE ? AS src", (ro_uri(src_path),))
        added = {}
        for table, (key, cols) in TABLES.items():
            have = {row[1] for row in conn.execute(f"PRAGMA src.table_info({table})")}
            missing = [c for c in ("id",) + cols if c not in have]
            if missing:
                sys.exit(f"{rel}: bảng {table} thiếu cột {', '.join(missing)} (không đúng form chung)")
            null_keys = conn.execute(f"SELECT COUNT(*) FROM src.{table} WHERE "
                                     + " OR ".join(f"{k} IS NULL" for k in key)).fetchone()[0]
            if null_keys:
                sys.exit(f"{rel}: {null_keys} dòng trong bảng {table} không có {', '.join(key)}")

            n_src = conn.execute(f"SELECT COUNT(*) FROM src.{table}").fetchone()[0]
            n_before, last_id = conn.execute(f"SELECT COUNT(*), IFNULL(MAX(id), 0) FROM main.{table}").fetchone()
            conn.execute(f"""
                INSERT INTO main.{table} (id, {", ".join(cols)})
                SELECT ? + ROW_NUMBER() OVER (ORDER BY s.id), {", ".join("s." + c for c in cols)}
                FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {", ".join(key)} ORDER BY id) AS nth
                      FROM src.{table}) AS s
                WHERE s.nth = 1
                  AND NOT EXISTS (SELECT 1 FROM main.{table} AS m
                                  WHERE {" AND ".join(f"m.{k} = s.{k}" for k in key)})
                ORDER BY s.id
            """, (last_id,))
            n_after = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
            added[table] = (n_after - n_before, n_src)
        conn.commit()
        conn.execute("DETACH DATABASE src")
        report.append((source, added))
    conn.execute("VACUUM")
    conn.close()
    return report


def content_hash(path):
    """SHA-256 của nội dung 3 bảng và sqlite_sequence (đọc theo id).

    Không phụ thuộc cách SQLite xếp trang trong file, nên hai file tạo ở hai máy khác nhau
    vẫn so được với nhau.
    """
    conn = sqlite3.connect(ro_uri(path), uri=True)
    conn.text_factory = bytes  # so từng byte của chuỗi, không cần giải mã
    h = hashlib.sha256()
    for table, (_, cols) in TABLES.items():
        select = ", ".join(f"typeof({c}), {c}" for c in ("id",) + cols)
        h.update(table.encode() + b"\n")
        for row in conn.execute(f"SELECT {select} FROM {table} ORDER BY id"):
            h.update(repr(row).encode() + b"\n")
    for row in conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name"):
        h.update(repr(row).encode() + b"\n")
    conn.close()
    return h.hexdigest()


def schema(path):
    """Các bảng, chỉ mục và câu lệnh tạo (bỏ khác biệt về khoảng trắng)."""
    conn = sqlite3.connect(ro_uri(path), uri=True)
    rows = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    conn.close()
    return [(t, n, tbl, " ".join(sql.split()) if sql else None) for t, n, tbl, sql in rows]


def integrity(path):
    conn = sqlite3.connect(ro_uri(path), uri=True)
    result = "; ".join(row[0] for row in conn.execute("PRAGMA integrity_check"))
    conn.close()
    return result


def print_report(report, path):
    print("Nguồn (theo thứ tự; trùng thì giữ dòng của nguồn đứng trước)   số dòng thêm / số dòng trong nguồn")
    for i, ((member, label, rel), added) in enumerate(report, 1):
        counts = "   ".join(f"{t} {a}/{n}" for t, (a, n) in added.items())
        print(f"{i}. {member} – {label}\n   {rel}  (sha256 {file_sha256(REPO_DIR / rel)[:12]})\n   {counts}")

    conn = sqlite3.connect(ro_uri(path), uri=True)
    totals = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    pages = conn.execute("SELECT domain, COUNT(*) FROM pages GROUP BY domain ORDER BY COUNT(*) DESC, domain").fetchall()
    songs = Counter(urlparse(url).netloc for (url,) in conn.execute("SELECT url FROM songs"))
    conn.close()
    print(f"\nTổng: {totals['pages']} trang, {totals['links']} link, {totals['songs']} bài hát")
    print("Trang theo domain   :", ", ".join(f"{d} {n}" for d, n in pages))
    print("Bài hát theo domain :", ", ".join(f"{d} {n}" for d, n in songs.most_common()))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gộp database của cả nhóm vào data/music_seg_merged.db")
    parser.add_argument("--check", action="store_true",
                        help="chỉ kiểm tra file đã nộp với database nguồn, không ghi đè")
    args = parser.parse_args()

    if not args.check:
        report = build(MERGED_PATH)
        print_report(report, MERGED_PATH)
        print(f"\nĐã ghi {MERGED_PATH.relative_to(REPO_DIR).as_posix()} ({MERGED_PATH.stat().st_size:,} byte)")
        return 0

    if not MERGED_PATH.is_file():
        print("Chưa có data/music_seg_merged.db: chạy python merge_db.py")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "fresh.db"
        report = build(fresh)
        print_report(report, fresh)
        problems = []
        result = integrity(MERGED_PATH)
        if result != "ok":
            problems.append(f"PRAGMA integrity_check: {result}")
        if schema(MERGED_PATH) != schema(FORM_PATH):
            problems.append("bảng, cột hoặc chỉ mục khác data/crawler.db")
        merged_hash, fresh_hash = content_hash(MERGED_PATH), content_hash(fresh)
        if merged_hash != fresh_hash:
            problems.append("nội dung khác bản gộp mới tạo từ database nguồn (chạy lại python merge_db.py)")

    print("\nKiểm tra data/music_seg_merged.db:")
    print(f"  integrity_check : {result}")
    print(f"  nội dung (sha256): {merged_hash[:16]} | bản gộp mới: {fresh_hash[:16]}")
    if problems:
        for p in problems:
            print("  LỖI:", p)
        return 1
    print("  Khớp: đúng form của data/crawler.db và đúng nội dung gộp từ các database nguồn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
