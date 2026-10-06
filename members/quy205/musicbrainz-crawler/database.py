import sqlite3
import os


class Database:
    def __init__(self, db_path):
        # Tạo thư mục chứa database nếu chưa tồn tại
        db_dir = os.path.dirname(db_path)

        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Kết nối SQLite
        self.connection = sqlite3.connect(db_path)

        # Tạo các bảng cần thiết
        self.create_tables()

    def create_tables(self):
        cursor = self.connection.cursor()

        # Bảng lưu thông tin các trang đã crawl
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

        # Bảng lưu các liên kết giữa các trang
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT,
                target_url TEXT
            )
        """)

        self.connection.commit()

    def save_page(
        self,
        url,
        domain,
        title,
        content,
        depth,
        status_code,
        crawled_at
    ):
        cursor = self.connection.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO pages
            (
                url,
                domain,
                title,
                content,
                depth,
                status_code,
                crawled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            url,
            domain,
            title,
            content,
            depth,
            status_code,
            crawled_at
        ))

        self.connection.commit()

    def save_link(self, source_url, target_url):
        cursor = self.connection.cursor()

        cursor.execute("""
            INSERT INTO links
            (
                source_url,
                target_url
            )
            VALUES (?, ?)
        """, (
            source_url,
            target_url
        ))

        self.connection.commit()

    def close(self):
        self.connection.close()