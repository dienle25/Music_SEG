import sqlite3
from pathlib import Path


LYRIC_COLUMNS = {
    "lyric_excerpt": "TEXT",
    "lyric_chars": "INTEGER DEFAULT 0",
    "lyric_hash": "TEXT",
}


def init_db(path):
    Path(path).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    con = sqlite3.connect(path)

    con.execute(
        """CREATE TABLE IF NOT EXISTS songs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT,
            category TEXT,
            url TEXT UNIQUE NOT NULL,
            thumbnail TEXT NOT NULL,
            duration TEXT,
            lyric_url TEXT,
            lyric_status TEXT,
            lyric_excerpt TEXT,
            lyric_chars INTEGER DEFAULT 0,
            lyric_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # Nếu database cũ đã tồn tại, tự thêm cột mới.
    existing = {
        row[1] for row in con.execute("PRAGMA table_info(songs)").fetchall()
    }

    for name, typ in LYRIC_COLUMNS.items():
        if name not in existing:
            con.execute(f"ALTER TABLE songs ADD COLUMN {name} {typ}")

    con.commit()
    return con


def count(con):
    return con.execute(
        "SELECT COUNT(*) FROM songs"
    ).fetchone()[0]


def insert(con, s):
    cur = con.execute(
        """INSERT OR IGNORE INTO songs
        (
            title,
            artist,
            category,
            url,
            thumbnail,
            duration,
            lyric_url,
            lyric_status,
            lyric_excerpt,
            lyric_chars,
            lyric_hash
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            s["title"],
            s["artist"],
            s["category"],
            s["url"],
            s["thumbnail"],
            s["duration"],
            s["lyric_url"],
            s["lyric_status"],
            s.get("lyric_excerpt", ""),
            s.get("lyric_chars", 0),
            s.get("lyric_hash", ""),
        ),
    )

    con.commit()
    return cur.rowcount == 1
