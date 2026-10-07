# =========================================================
# database.py  –  TASK 8: lưu dữ liệu vào SQLite
# =========================================================
# Bảng theo đề bài (giữ nguyên tên + thứ tự cột):
#   pages     mỗi trang HTML crawl thành công
#   links     đồ thị liên kết source_url -> target_url
#   songs     mỗi bài hát: title, artist, album, genre, lyrics
# Bảng thêm cho Spotify / đề tài C2C-VN:
#   tracks    metadata đầy đủ của bài hát (id nghệ sĩ, album, năm, thời lượng...)
#   listings  album / playlist đã crawl và danh sách bài trong đó
#   meta      thông tin lần chạy (phiên bản, seed, robots.txt, thống kê)
#   lyrics    lời bài hát do lyrics.py tra từ LRCLIB (giữ lại khi crawl lại)

import json
import os
import sqlite3

import config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(BASE_DIR, config.DB_FILE))

# Các bảng bị xoá khi crawl lại (RESET_DB). Bảng lyrics KHÔNG nằm trong danh
# sách này: lời đã tra được giữ lại, mỗi bài chỉ phải tra một lần.
TABLES = ("pages", "links", "songs", "tracks", "listings", "meta")

LYRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics (
    track_id TEXT PRIMARY KEY,
    status TEXT,              -- found / not_found / instrumental
    source TEXT,              -- lrclib / db:<file> / title
    source_id TEXT,           -- id trên LRCLIB hoặc url trong database khác
    match TEXT,               -- exact / search / other_version / base_title / title_artist
    matched_title TEXT,
    matched_artist TEXT,
    matched_duration REAL,
    plain_lyrics TEXT,
    synced_lyrics TEXT,       -- lời có mốc thời gian (LRC), nếu có
    fetched_utc TEXT
);
"""


def connect_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def create_tables(reset=False):
    conn = connect_db()
    cursor = conn.cursor()

    if reset:
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

    # ---------- TABLE: pages ----------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        domain TEXT,
        title TEXT,
        content TEXT,
        depth INTEGER,
        status_code INTEGER,
        crawled_at TEXT
    )
    """)

    # ---------- TABLE: links ----------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_url TEXT,
        target_url TEXT
    )
    """)

    # Không lưu trùng cặp source -> target
    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_link
    ON links(source_url, target_url)
    """)

    # ---------- TABLE: songs ----------
    # Đúng tên bảng, tên cột và thứ tự cột theo yêu cầu đề bài.
    # Spotify không có thể loại và lời bài hát -> genre, lyrics = NULL.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        title TEXT,
        artist TEXT,
        album TEXT,
        genre TEXT,
        lyrics TEXT,
        crawled_at TEXT
    )
    """)

    # ---------- TABLE: tracks ----------
    # Một dòng / bài hát Spotify. artists_json và artist_ids_json cùng thứ tự;
    # artists_json = [] khi trang có nhiều nghệ sĩ mà không tách được tên.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tracks (
        track_id TEXT PRIMARY KEY,
        url TEXT UNIQUE,
        title TEXT,
        artists_json TEXT,
        artist_ids_json TEXT,
        n_artists INTEGER,
        artist_text TEXT,
        album TEXT,
        album_id TEXT,
        track_number INTEGER,
        release_date TEXT,
        year INTEGER,
        duration_sec INTEGER,
        source_url TEXT,
        html_sha256 TEXT,
        crawled_utc TEXT
    )
    """)

    # ---------- TABLE: listings ----------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        url TEXT PRIMARY KEY,
        kind TEXT,
        listing_id TEXT,
        title TEXT,
        description TEXT,
        artist_ids_json TEXT,
        release_date TEXT,
        track_ids_json TEXT,
        n_tracks INTEGER,
        n_new_tracks INTEGER,
        source_url TEXT,
        html_sha256 TEXT,
        crawled_utc TEXT
    )
    """)

    # ---------- TABLE: meta ----------
    cursor.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    # ---------- TABLE: lyrics (lyrics.py) ----------
    conn.commit()
    conn.executescript(LYRICS_SCHEMA)
    conn.close()


def cached_lyrics(track_id):
    """Lời đã tra trước đó cho bài này (bảng lyrics), hoặc None."""
    conn = connect_db()
    row = conn.execute("SELECT plain_lyrics FROM lyrics WHERE track_id = ? AND status = 'found'",
                       (track_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def save_page(page):
    conn = connect_db()

    # URL đã có -> cập nhật nội dung mới
    conn.execute("""
    INSERT INTO pages
        (url, domain, title, content, depth, status_code, crawled_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(url) DO UPDATE SET
        domain      = excluded.domain,
        title       = excluded.title,
        content     = excluded.content,
        depth       = MIN(pages.depth, excluded.depth),
        status_code = excluded.status_code,
        crawled_at  = excluded.crawled_at
    """, (
        page["url"],
        page["domain"],
        page["title"],
        page["content"],
        page["depth"],
        page["status_code"],
        page["crawled_at"],
    ))

    conn.commit()
    conn.close()


def save_song(song):
    """Lưu một bài hát vào bảng songs (URL đã có -> cập nhật, không lưu trùng)."""
    conn = connect_db()

    conn.execute("""
    INSERT INTO songs
        (url, title, artist, album, genre, lyrics, crawled_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(url) DO UPDATE SET
        title      = excluded.title,
        artist     = excluded.artist,
        album      = excluded.album,
        genre      = excluded.genre,
        lyrics     = excluded.lyrics,
        crawled_at = excluded.crawled_at
    """, (
        song["url"],
        song["title"],
        song["artist"],
        song["album"],
        song["genre"],
        song["lyrics"],
        song["crawled_at"],
    ))

    conn.commit()
    conn.close()


def save_track(track):
    """Lưu metadata một bài hát Spotify vào bảng tracks (track_id đã có -> cập nhật)."""
    artist_ids = track["artist_ids"]
    conn = connect_db()
    conn.execute("""
    INSERT OR REPLACE INTO tracks
        (track_id, url, title, artists_json, artist_ids_json, n_artists, artist_text,
         album, album_id, track_number, release_date, year, duration_sec,
         source_url, html_sha256, crawled_utc)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        track["track_id"],
        track["url"],
        track["title"],
        json.dumps(track["artists"], ensure_ascii=False),
        json.dumps(artist_ids),
        len(artist_ids) or len(track["artists"]),
        track.get("artist_text"),
        track.get("album"),
        track.get("album_id"),
        track.get("track_number"),
        track.get("release_date"),
        track.get("year"),
        track.get("duration_sec"),
        track.get("source_url"),
        track.get("html_sha256"),
        track.get("crawled_utc"),
    ))
    conn.commit()
    conn.close()


def save_listing(listing):
    """Lưu một album / playlist vào bảng listings."""
    conn = connect_db()
    conn.execute("""
    INSERT OR REPLACE INTO listings
        (url, kind, listing_id, title, description, artist_ids_json, release_date,
         track_ids_json, n_tracks, n_new_tracks, source_url, html_sha256, crawled_utc)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        listing["url"],
        listing["kind"],
        listing["listing_id"],
        listing.get("title"),
        listing.get("description"),
        json.dumps(listing.get("artist_ids") or []),
        listing.get("release_date"),
        json.dumps(listing.get("track_ids") or []),
        len(listing.get("track_ids") or []),
        listing.get("n_new_tracks", 0),
        listing.get("source_url"),
        listing.get("html_sha256"),
        listing.get("crawled_utc"),
    ))
    conn.commit()
    conn.close()


def save_links(source_url, target_urls):
    """Lưu các cạnh source -> target, trả về số dòng mới thêm."""
    conn = connect_db()
    before = conn.total_changes

    conn.executemany("""
    INSERT OR IGNORE INTO links (source_url, target_url)
    VALUES (?, ?)
    """, [(source_url, target) for target in target_urls])

    conn.commit()
    inserted = conn.total_changes - before
    conn.close()
    return inserted


def set_meta(key, value):
    """Ghi thông tin lần chạy (JSON) vào bảng meta."""
    conn = connect_db()
    conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)",
                 (key, json.dumps(value, ensure_ascii=False)))
    conn.commit()
    conn.close()
