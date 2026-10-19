# =========================================================
# database.py - SQLite cho hopamchuan_phuquy
# =========================================================

import os
import sqlite3
from contextlib import contextmanager
import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(BASE_DIR, config.DB_FILE))


@contextmanager
def connect_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_tables(reset=False):
    with connect_db() as conn:
        cur = conn.cursor()

        if reset:
            cur.execute("DROP TABLE IF EXISTS thumbnails")
            cur.execute("DROP TABLE IF EXISTS songs")
            cur.execute("DROP TABLE IF EXISTS pages")
            cur.execute("DROP TABLE IF EXISTS links")

        # Bảng pages giữ thông tin crawl/debug.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            domain TEXT,
            title TEXT,
            content TEXT,
            depth INTEGER,
            status_code INTEGER,
            crawled_at TEXT
        )
        """)

        # Đồ thị URL discovery.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL,
            target_url TEXT NOT NULL,
            UNIQUE(source_url, target_url)
        )
        """)

        # Bảng bài hát theo schema chung; album giữ NULL theo yêu cầu.
        # artist cho phép NULL: trang ghi "N/A" ở mục Tác giả được lưu NULL.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT,
            album TEXT DEFAULT NULL,
            genre TEXT,
            lyrics TEXT NOT NULL,
            crawled_at TEXT NOT NULL
        )
        """)

        # Thumbnail tách khỏi bảng songs.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS thumbnails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_url TEXT UNIQUE NOT NULL,
            thumbnail_url TEXT NOT NULL,
            FOREIGN KEY(song_url) REFERENCES songs(url)
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_songs_artist
        ON songs(artist)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_songs_title
        ON songs(title)
        """)


def save_page(page):
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO pages(url, domain, title, content, depth, status_code, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            domain=excluded.domain,
            title=excluded.title,
            content=excluded.content,
            depth=MIN(pages.depth, excluded.depth),
            status_code=excluded.status_code,
            crawled_at=excluded.crawled_at
        """, (
            page["url"], page["domain"], page["title"], page["content"],
            page["depth"], page["status_code"], page["crawled_at"]
        ))


def save_links(source_url, target_urls):
    if not target_urls:
        return 0

    with connect_db() as conn:
        before = conn.total_changes
        conn.executemany("""
        INSERT OR IGNORE INTO links(source_url, target_url)
        VALUES (?, ?)
        """, [(source_url, u) for u in target_urls])
        return conn.total_changes - before


def save_song(song):
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO songs(url, title, artist, album, genre, lyrics, crawled_at)
        VALUES (?, ?, ?, NULL, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            album=NULL,
            genre=excluded.genre,
            lyrics=excluded.lyrics,
            crawled_at=excluded.crawled_at
        """, (
            song["url"], song["title"], song.get("artist"), song.get("genre"),
            song["lyrics"], song["crawled_at"]
        ))
        if song.get("thumbnail_url"):
            conn.execute("""
            INSERT INTO thumbnails(song_url, thumbnail_url) VALUES (?, ?)
            ON CONFLICT(song_url) DO UPDATE SET thumbnail_url=excluded.thumbnail_url
            """, (song["url"], song["thumbnail_url"]))



def count_songs():
    with connect_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]


def get_songs(limit=20):
    with connect_db() as conn:
        return conn.execute("""
        SELECT s.id, s.url, s.title, s.artist, s.genre,
               t.thumbnail_url, LENGTH(s.lyrics) AS lyrics_chars, s.crawled_at
        FROM songs s LEFT JOIN thumbnails t ON t.song_url=s.url
        ORDER BY s.id
        LIMIT ?
        """, (limit,)).fetchall()
