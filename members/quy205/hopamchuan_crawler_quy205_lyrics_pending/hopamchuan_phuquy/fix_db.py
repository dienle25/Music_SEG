# =========================================================
# fix_db.py - sửa database đã crawl sẵn, KHÔNG cần crawl lại
#   1. pages: đổi thứ tự cột -> id, url, domain, title, content,
#      depth, status_code, crawled_at
#   2. songs.artist: "N/A" -> NULL (bỏ ràng buộc NOT NULL)
#   3. songs.lyrics: bỏ các dòng Tone / Capo / Intro: / Chord: /
#      Vòng hợp âm: ở đầu lời
# Chạy:  python fix_db.py
# =========================================================

import sqlite3
from database import DB_PATH
from parser import normalize_artist, _strip_lyrics_header


def column_names(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def fix_pages(conn):
    expected = ["id", "url", "domain", "title", "content", "depth", "status_code", "crawled_at"]
    if column_names(conn, "pages") == expected:
        print("[pages] Thứ tự cột đã đúng.")
        return
    conn.execute("""
    CREATE TABLE pages_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        domain TEXT,
        title TEXT,
        content TEXT,
        depth INTEGER,
        status_code INTEGER,
        crawled_at TEXT
    )""")
    conn.execute("""
    INSERT INTO pages_new(id, url, domain, title, content, depth, status_code, crawled_at)
    SELECT id, url, domain, title, content, depth, status_code, crawled_at FROM pages
    """)
    conn.execute("DROP TABLE pages")
    conn.execute("ALTER TABLE pages_new RENAME TO pages")
    print("[pages] Đã đổi thứ tự cột:", ", ".join(column_names(conn, "pages")))


def fix_songs_schema(conn):
    """Tạo lại bảng songs để artist cho phép NULL (giữ nguyên id)."""
    notnull = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(songs)")}
    if not notnull.get("artist"):
        return
    conn.execute("""
    CREATE TABLE songs_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        artist TEXT,
        album TEXT DEFAULT NULL,
        genre TEXT,
        lyrics TEXT NOT NULL,
        crawled_at TEXT NOT NULL
    )""")
    conn.execute("""
    INSERT INTO songs_new(id, url, title, artist, album, genre, lyrics, crawled_at)
    SELECT id, url, title, artist, album, genre, lyrics, crawled_at FROM songs
    """)
    conn.execute("DROP TABLE songs")
    conn.execute("ALTER TABLE songs_new RENAME TO songs")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_songs_title ON songs(title)")
    print("[songs] Đã bỏ NOT NULL ở cột artist.")


def fix_songs_data(conn):
    rows = conn.execute("SELECT id, artist, lyrics FROM songs").fetchall()
    artist_fixed = lyrics_fixed = 0
    for song_id, artist, lyrics in rows:
        new_artist = normalize_artist(artist) if artist is not None else None
        lines = (lyrics or "").replace("\r", "").split("\n")
        # Bỏ dòng trống đầu rồi cắt header; giữ nguyên phần lời còn lại.
        while lines and not lines[0].strip():
            lines.pop(0)
        new_lyrics = "\n".join(_strip_lyrics_header(lines)).strip()
        if new_artist != artist:
            artist_fixed += 1
        if new_lyrics != (lyrics or "").strip():
            lyrics_fixed += 1
        if new_artist != artist or new_lyrics != lyrics:
            conn.execute("UPDATE songs SET artist=?, lyrics=? WHERE id=?",
                         (new_artist, new_lyrics, song_id))
    print(f"[songs] artist 'N/A' -> NULL: {artist_fixed} bài")
    print(f"[songs] Bỏ dòng header đầu lời: {lyrics_fixed} bài")


def main():
    print("DATABASE:", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        fix_pages(conn)
        fix_songs_schema(conn)
        fix_songs_data(conn)
        conn.commit()
        conn.execute("VACUUM")
        print("Xong. Chạy `python check_db.py` để kiểm tra.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
