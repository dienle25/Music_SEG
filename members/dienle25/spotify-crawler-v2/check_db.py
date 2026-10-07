# =========================================================
# check_db.py  –  xem nhanh dữ liệu đã crawl:  python check_db.py
# =========================================================

import json
import os
import sqlite3
import sys
from collections import Counter

from database import DB_PATH

if not os.path.exists(DB_PATH):
    sys.exit(f"Chưa có {DB_PATH} – chạy python main.py trước.")

conn = sqlite3.connect(DB_PATH)
tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def kind_of(url):
    parts = url.split("/")
    return parts[3] if len(parts) > 3 else "?"


print("===== PAGES PER TYPE =====")
kinds = Counter(kind_of(url) for (url,) in conn.execute("SELECT url FROM pages"))
for kind, count in kinds.most_common():
    print(f"{kind:<10} {count}")
print("Total pages:", sum(kinds.values()))

print("\n===== PAGES (20 trang đầu) =====")
for page_id, depth, status, title, length in conn.execute("""
SELECT id, depth, status_code, title, LENGTH(content) FROM pages ORDER BY id LIMIT 20
"""):
    print(f"{page_id:>4} | d={depth} | {status} | {length:>5} chars | {(title or '')[:70]}")

print("\n===== SONGS: TABLE STRUCTURE (theo đề bài) =====")
print(", ".join(row[1] for row in conn.execute("PRAGMA table_info(songs)")))
total, artist, album, genre, lyrics = conn.execute("""
SELECT COUNT(*), COUNT(artist), COUNT(album), COUNT(genre), COUNT(lyrics) FROM songs
""").fetchone()
print(f"songs: {total} | artist {artist} / album {album} / genre {genre} / lyrics {lyrics}")
print("(HTML Spotify không có thể loại -> genre = NULL; lời bài hát do lyrics.py tra trên LRCLIB)")

if "tracks" in tables:
    print("\n===== TRACKS (metadata cho đề tài) =====")
    n, with_album, with_date, with_duration = conn.execute("""
    SELECT COUNT(*), COUNT(album), COUNT(release_date), COUNT(duration_sec) FROM tracks
    """).fetchone()
    print(f"tracks: {n} | album {with_album} / release date {with_date} / duration {with_duration}")

    rows = conn.execute("""
    SELECT title, artists_json, artist_ids_json, artist_text, album, year, duration_sec
    FROM tracks ORDER BY rowid
    """).fetchall()

    per_artist, names, single = Counter(), {}, 0
    for _, artists_json, ids_json, *_ in rows:
        artists, ids = json.loads(artists_json), json.loads(ids_json)
        single += len(ids) == 1 and len(artists) == 1
        for i, artist_id in enumerate(ids):
            per_artist[artist_id] += 1
            if len(artists) == len(ids):
                names.setdefault(artist_id, artists[i])
    print(f"bài 1 nghệ sĩ: {single} | số nghệ sĩ: {len(per_artist)} | "
          f"nghệ sĩ >= 30 bài: {sum(c >= 30 for c in per_artist.values())} | "
          f">= 24 bài: {sum(c >= 24 for c in per_artist.values())}")

    print("\nTop nghệ sĩ:")
    for artist_id, count in per_artist.most_common(10):
        print(f"    {names.get(artist_id, artist_id)[:30]:<30} {count}")

    print("\n20 bài đầu:")
    for title, _, _, artist_text, album, year, duration in rows[:20]:
        print(f"    {(title or '')[:32]:<32} | {(artist_text or '-')[:22]:<22} | "
              f"{(album or '-')[:22]:<22} | {year or '-'} | {duration or '-'} s")

if "lyrics" in tables and "tracks" in tables:
    print("\n===== LYRICS (lyrics.py – LRCLIB) =====")
    n_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    statuses = dict(conn.execute("""
    SELECT l.status, COUNT(*) FROM lyrics l JOIN tracks t ON t.track_id = l.track_id GROUP BY l.status
    """))
    found = statuses.get("found", 0)
    print(f"có lời: {found} / {n_tracks} bài | không lời: {statuses.get('instrumental', 0)} | "
          f"không tìm thấy: {statuses.get('not_found', 0)} | "
          f"chưa tra: {n_tracks - sum(statuses.values())}")
    for source, match, count in conn.execute("""
    SELECT l.source, l.match, COUNT(*) FROM lyrics l JOIN tracks t ON t.track_id = l.track_id
    WHERE l.status = 'found' GROUP BY l.source, l.match ORDER BY COUNT(*) DESC
    """):
        print(f"    {source} / {match}: {count}")
    for title, artist_text, chars in conn.execute("""
    SELECT t.title, t.artist_text, LENGTH(l.plain_lyrics) FROM lyrics l JOIN tracks t ON t.track_id = l.track_id
    WHERE l.status = 'found' ORDER BY t.rowid LIMIT 10
    """):
        print(f"    {(title or '')[:32]:<32} | {(artist_text or '-')[:22]:<22} | {chars} ký tự")

if "listings" in tables:
    print("\n===== ALBUMS / PLAYLISTS =====")
    for kind, count, n_tracks in conn.execute("""
    SELECT kind, COUNT(*), SUM(n_tracks) FROM listings GROUP BY kind ORDER BY COUNT(*) DESC
    """):
        print(f"{kind:<10} {count} trang, {n_tracks or 0} bài được liệt kê")
    for kind, title, n_tracks, n_new in conn.execute("""
    SELECT kind, title, n_tracks, n_new_tracks FROM listings ORDER BY rowid LIMIT 10
    """):
        print(f"    {kind:<8} | {(title or '')[:40]:<40} | {n_tracks} bài ({n_new} bài mới)")

print("\n===== LINKS =====")
print("Total links:", conn.execute("SELECT COUNT(*) FROM links").fetchone()[0])
for row in conn.execute("SELECT id, source_url, target_url FROM links LIMIT 10"):
    print(row)

conn.close()
