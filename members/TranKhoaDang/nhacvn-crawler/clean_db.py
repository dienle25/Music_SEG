"""
clean_db.py - Dua data/crawler.db ve FORM DB CHUNG cua nhom (khong can crawl lai).

Viec script lam:
  1. Sao luu DB (them duoi .bak). KHONG dua file .bak len git.
  2. songs: chi giu URL dang ...-so... (xoa cac trang danh sach ...-gr...).
  3. songs.artist: bo duoi "| NHAC.VN".
  4. Cot khong co du lieu thi de NULL (thay vi chuoi rong) o songs va pages.
  5. links: bo cap (source_url, target_url) trung va them UNIQUE index.
  6. In so lieu truoc / sau de cap nhat REPORT.

Cach chay (o thu muc chua data/):
    python clean_db.py                      # dung data/crawler.db
    python clean_db.py duong/dan/khac.db
"""

import re
import shutil
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/crawler.db"
SONG_URL = re.compile(r"-so\w+$")          # trang bai hat that: ...-soXXXX
ARTIST_SUFFIX = re.compile(r"\s*\|\s*NHAC\.VN\s*$", re.IGNORECASE)


def counts(cur):
    return {
        "pages": cur.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
        "links": cur.execute("SELECT COUNT(*) FROM links").fetchone()[0],
        "songs": cur.execute("SELECT COUNT(*) FROM songs").fetchone()[0],
        "songs co loi": cur.execute(
            "SELECT COUNT(*) FROM songs WHERE lyrics IS NOT NULL AND lyrics != ''"
        ).fetchone()[0],
    }


def main():
    shutil.copyfile(DB_PATH, DB_PATH + ".bak")
    print(f"Da sao luu: {DB_PATH}.bak (khong commit file nay)")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    before = counts(cur)

    # ---- songs: chi giu URL ...-so... ----
    rows = cur.execute("SELECT id, url FROM songs").fetchall()
    drop = [(i,) for i, url in rows if not SONG_URL.search(url)]
    cur.executemany("DELETE FROM songs WHERE id = ?", drop)

    # ---- songs.artist: bo "| NHAC.VN" ----
    for song_id, artist in cur.execute("SELECT id, artist FROM songs").fetchall():
        if artist:
            cleaned = ARTIST_SUFFIX.sub("", artist).strip()
            cur.execute("UPDATE songs SET artist = ? WHERE id = ?", (cleaned, song_id))

    # ---- chuoi rong -> NULL ----
    for col in ("title", "artist", "album", "genre", "lyrics"):
        cur.execute(f"UPDATE songs SET {col} = NULL WHERE {col} = ''")
    for col in ("title", "content"):
        cur.execute(f"UPDATE pages SET {col} = NULL WHERE {col} = ''")

    # ---- links: bo cap trung + UNIQUE index ----
    cur.execute(
        "DELETE FROM links WHERE id NOT IN "
        "(SELECT MIN(id) FROM links GROUP BY source_url, target_url)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_link "
        "ON links(source_url, target_url)"
    )

    conn.commit()
    after = counts(cur)
    conn.execute("VACUUM")
    conn.close()

    print()
    print(f"{'':<14}{'truoc':>8}{'sau':>8}")
    for key in before:
        print(f"{key:<14}{before[key]:>8}{after[key]:>8}")
    print(f"\nDa xoa {len(drop)} dong songs khong phai bai hat (URL khong co -so...).")


if __name__ == "__main__":
    main()
