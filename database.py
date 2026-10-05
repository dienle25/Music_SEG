# =========================================================
# database.py  –  TASK 8: lưu dữ liệu vào SQLite
# =========================================================

import os
import sqlite3

import config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(BASE_DIR, config.DB_FILE))


def connect_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def create_tables(reset=False):
    conn = connect_db()
    cursor = conn.cursor()

    if reset:
        cursor.execute("DROP TABLE IF EXISTS links")
        cursor.execute("DROP TABLE IF EXISTS pages")

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

    conn.commit()
    conn.close()


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

