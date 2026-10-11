# =========================================================
# check_db.py - kiểm tra SQLite hopamchuan_phuquy
# =========================================================

import sqlite3
from database import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("DATABASE:", DB_PATH)
print("=" * 80)

tables = [
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
]
print("Tables:", ", ".join(tables))

print("\n===== SONG TABLE COLUMNS =====")
for row in conn.execute("PRAGMA table_info(songs)"):
    print(f"{row['cid']}: {row['name']} ({row['type']})")

print("\n===== SONG STATISTICS =====")
total = conn.execute("SELECT COUNT(*) AS n FROM songs").fetchone()["n"]
with_lyrics = conn.execute(
    "SELECT COUNT(*) AS n FROM songs WHERE LENGTH(lyrics) >= 30"
).fetchone()["n"]
with_thumb = conn.execute(
    "SELECT COUNT(*) AS n FROM thumbnails "
    "WHERE thumbnail_url LIKE 'http://%' OR thumbnail_url LIKE 'https://%'"
).fetchone()["n"]

print("Total songs :", total)
print("With lyrics :", with_lyrics)
print("With thumb  :", with_thumb)

print("\n===== FIRST 20 SONGS =====")
rows = conn.execute("""
SELECT s.id, s.title, s.artist, s.genre, t.thumbnail_url,
       LENGTH(s.lyrics) AS lyric_chars, s.url
FROM songs s LEFT JOIN thumbnails t ON t.song_url=s.url
ORDER BY s.id
LIMIT 20
""").fetchall()

for r in rows:
    print(
        f"{r['id']:>3} | {r['title'][:35]:<35} | "
        f"{r['artist'][:25]:<25} | lyrics={r['lyric_chars']:>5}"
    )
    print(f"      URL : {r['url']}")
    print(f"      IMG : {r['thumbnail_url']}")

print("\n===== CHECK: ALBUM COLUMN EXISTS AND IS NULL =====")
columns = [row["name"] for row in conn.execute("PRAGMA table_info(songs)")]
print("album" in columns and conn.execute("SELECT COUNT(*) FROM songs WHERE album IS NOT NULL").fetchone()[0] == 0)

conn.close()
