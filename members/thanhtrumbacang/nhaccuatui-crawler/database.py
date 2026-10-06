import sqlite3
from pathlib import Path


class Database:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        self.conn.execute("""
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

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT,
                target_url TEXT
            )
        """)

        self.conn.commit()

    def save_page(self, page):
        self.conn.execute("""
            INSERT OR REPLACE INTO pages
            (url, domain, title, content, depth, status_code, crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            page["url"],
            page["domain"],
            page["title"],
            page["content"],
            page["depth"],
            page["status_code"],
            page["crawled_at"],
        ))
        self.conn.commit()

    def save_link(self, source_url, target_url):
        self.conn.execute("""
            INSERT INTO links (source_url, target_url)
            VALUES (?, ?)
        """, (source_url, target_url))
        self.conn.commit()

    def close(self):
        self.conn.close()
