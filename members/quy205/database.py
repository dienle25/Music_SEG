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
            cur.execute("DROP TABLE IF EXISTS songs")
            cur.execute("DROP TABLE IF EXISTS pages")
            cur.execute("DROP TABLE IF EXISTS links")

        # Bảng pages giữ thông tin crawl/debug.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
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

        # KHÔNG có album.
        # Chỉ lưu bài hát đạt đủ điều kiện quality gate.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            genre TEXT,
            thumbnail_url TEXT NOT NULL,
            lyrics TEXT NOT NULL,
            crawled_at TEXT NOT NULL
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
        INSERT INTO pages(url, title, depth, status_code, crawled_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            depth=MIN(pages.depth, excluded.depth),
            status_code=excluded.status_code,
            crawled_at=excluded.crawled_at
        """, (
            page["url"], page["title"], page["depth"],
            page["status_code"], page["crawled_at"]
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
        INSERT INTO songs(
            url, title, artist, genre, thumbnail_url, lyrics, crawled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            genre=excluded.genre,
            thumbnail_url=excluded.thumbnail_url,
            lyrics=excluded.lyrics,
            crawled_at=excluded.crawled_at
        """, (
            song["url"], song["title"], song["artist"], song.get("genre"),
            song["thumbnail_url"], song["lyrics"], song["crawled_at"]
        ))


def count_songs():
    with connect_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]


def get_songs(limit=20):
    with connect_db() as conn:
        return conn.execute("""
        SELECT id, url, title, artist, genre,
               thumbnail_url, LENGTH(lyrics) AS lyrics_chars, crawled_at
        FROM songs
        ORDER BY id
        LIMIT ?
        """, (limit,)).fetchall()
