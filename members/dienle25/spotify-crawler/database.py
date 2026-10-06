"""SQLite storage for unique pages and source/target hyperlink relationships."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class Database:
    """Persist each operation atomically; previously saved pages survive interruption."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.path = str(Path(self.path).expanduser())
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._closed = False
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    depth INTEGER NOT NULL CHECK(depth >= 0),
                    status_code INTEGER NOT NULL,
                    crawled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    UNIQUE(source_url, target_url)
                );
                CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain);
                CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_url);
            """)

    def save_page(self, record: Mapping[str, Any]) -> bool:
        """Upsert one page; True means newly inserted, False means updated."""
        values = tuple(record[key] for key in (
            "url", "domain", "title", "content", "depth", "status_code", "crawled_at",
        ))
        with self.connection:
            exists = self.connection.execute("SELECT 1 FROM pages WHERE url = ?", (record["url"],)).fetchone()
            self.connection.execute("""
                INSERT INTO pages (url, domain, title, content, depth, status_code, crawled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    domain = excluded.domain,
                    title = excluded.title,
                    content = excluded.content,
                    depth = excluded.depth,
                    status_code = excluded.status_code,
                    crawled_at = excluded.crawled_at
            """, values)
        return exists is None

    def save_links(self, source_url: str, targets: Iterable[str]) -> None:
        """Keep one row for each directed edge, including uncrawled targets."""
        with self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO links (source_url, target_url) VALUES (?, ?)",
                ((source_url, target) for target in targets),
            )

    def counts(self) -> dict[str, int]:
        row = self.connection.execute("""
            SELECT
                (SELECT COUNT(*) FROM pages) AS pages,
                (SELECT COUNT(*) FROM links) AS links,
                (SELECT COUNT(DISTINCT url) FROM pages) AS unique_urls
        """).fetchone()
        return {key: int(row[key]) for key in ("pages", "links", "unique_urls")}

    def close(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
