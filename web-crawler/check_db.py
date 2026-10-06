# =========================================================
# check_db.py  –  xem nhanh dữ liệu đã crawl:  python check_db.py
# =========================================================

import sqlite3
from collections import defaultdict
from urllib.parse import urlparse

from database import DB_PATH

conn = sqlite3.connect(DB_PATH)

print("===== PAGES PER DOMAIN =====")
for domain, count in conn.execute(
        "SELECT domain, COUNT(*) FROM pages GROUP BY domain ORDER BY COUNT(*) DESC"):
    print(f"{domain:<22} {count}")

print("\n===== PAGES =====")
rows = conn.execute("""
SELECT id, domain, depth, status_code, title, LENGTH(content)
FROM pages
ORDER BY id
""").fetchall()

for page_id, domain, depth, status, title, length in rows:
    print(f"{page_id:>3} | {domain:<20} | d={depth} | {status} | {length:>6} chars | {title[:60]}")

print("\n===== SONGS: TABLE STRUCTURE =====")
columns = [row[1] for row in conn.execute("PRAGMA table_info(songs)")]
if not columns:
    print("(chưa có bảng songs - chạy lại: python main.py)")
else:
    print(", ".join(columns))

    songs = conn.execute("""
    SELECT id, url, title, artist, album, genre, lyrics
    FROM songs
    ORDER BY id
    """).fetchall()

    # số bài có đủ từng cột, theo domain: [tổng, artist, album, genre, lyrics]
    stats = defaultdict(lambda: [0, 0, 0, 0, 0])
    for _, url, _, artist, album, genre, lyrics in songs:
        row = stats[urlparse(url).netloc]
        row[0] += 1
        for i, value in enumerate((artist, album, genre, lyrics), start=1):
            row[i] += 1 if value else 0

    print("\n===== SONGS PER DOMAIN (songs | artist / album / genre / lyrics) =====")
    for domain, (total, artist, album, genre, lyrics) in stats.items():
        print(f"{domain:<22} {total:>3} | {artist} / {album} / {genre} / {lyrics}")
    print("Total songs:", len(songs))

    print("\n===== SONGS =====")
    for song_id, url, title, artist, album, genre, lyrics in songs:
        print(f"{song_id:>3} | {urlparse(url).netloc:<20} | {title[:32]:<32} | "
              f"{(artist or '-')[:18]:<18} | {(album or '-')[:18]:<18} | "
              f"{(genre or '-')[:10]:<10} | {len(lyrics or ''):>5} chars")

print("\n===== LINKS =====")
total = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
print("Total links:", total)

for row in conn.execute("SELECT id, source_url, target_url FROM links LIMIT 20"):
    print(row)

conn.close()
