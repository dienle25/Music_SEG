# =========================================================
# lyrics.py  –  lấy lời bài hát cho các bài Spotify đã crawl
# =========================================================
# HTML công khai của Spotify không có lời bài hát (lời chỉ hiện qua trình phát,
# chủ yếu khi đăng nhập) và crawler không vượt qua bước đăng nhập. Vì vậy lời
# được lấy từ LRCLIB (https://lrclib.net), một kho lời bài hát mở có API công
# khai, không cần tài khoản. Crawler tra theo metadata Spotify đã crawl:
# tên bài, ca sĩ, album, thời lượng.
#
#     python lyrics.py                   # tra mọi bài chưa có kết quả
#     python lyrics.py --limit 30        # chạy thử 30 bài
#     python lyrics.py --retry           # tra lại cả các bài lần trước không tìm thấy
#     python lyrics.py --from-db A.db    # lấy thêm lời từ database crawl khác (bảng songs theo đề bài)
#
# Kết quả nằm trong bảng lyrics (nguồn, kiểu khớp, lời thường + lời có mốc thời
# gian) và cột songs.lyrics. Bảng lyrics được giữ lại khi crawl lại, nên mỗi bài
# chỉ phải tra một lần. Dừng giữa chừng (Ctrl+C) không mất các bài đã tra.
#
# Lời bài hát có bản quyền: chỉ lưu trong máy để học / nghiên cứu, không đăng lại.

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone

import requests

import config
from crawler import retry_after_seconds
from database import BASE_DIR, DB_PATH, LYRICS_SCHEMA

INSTRUMENTAL_TITLE = re.compile(r"(?i)\b(instrumental|karaoke)\b|\(\s*beat\s*\)|-\s*beat\s*$")

# Phần ghi phiên bản ở cuối tên bài: "- Live in Đà Lạt", "(feat. X)", "- Remix", "(Acoustic)"...
VERSION_WORDS = (r"feat\.?|ft\.?|with|live|remix|version|ver\.?|concert|tour|acoustic|edit|"
                 r"mix|remaster(?:ed)?|from|cover|ost|soundtrack|session|unplugged|lofi|"
                 r"speed ?up|sped up|slowed|phiên bản|bản")
VERSION_PARENS = re.compile(r"\s*[\(\[][^\)\]]*\b(?:%s)\b[^\)\]]*[\)\]]" % VERSION_WORDS, re.I)
VERSION_DASH = re.compile(r"\s+-\s+[^-]*\b(?:%s)\b.*$" % VERSION_WORDS, re.I)
LRC_TAG = re.compile(r"\[(?:\d{1,2}:\d{2}(?:[.:]\d{1,3})?|[a-z]+:[^\]]*)\]", re.I)

RETRY_STATUSES = (429, 500, 502, 503, 504)
LOCKED = ("Database đang bị khoá – có thể spotify.db đang mở trong DB Browser for SQLite "
          "(chưa bấm Write Changes). Đóng database đó rồi chạy lại.")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(text):
    """So khớp không phân biệt hoa thường, dấu tiếng Việt, dấu câu: "Bích Phương" == "Bich Phuong"."""
    text = unicodedata.normalize("NFC", text or "").lower().replace("đ", "d")
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def base_title(title):
    """Bỏ phần ghi phiên bản: "Có Đôi Lần - Live" -> "Có Đôi Lần"."""
    title = VERSION_PARENS.sub("", title or "")
    title = VERSION_DASH.sub("", title)
    return title.strip()


def plain_from_synced(synced):
    """Lời có mốc thời gian ([00:12.34] ...) -> lời thường."""
    lines = [LRC_TAG.sub("", line).strip() for line in (synced or "").splitlines()]
    return "\n".join(lines).strip() or None


def has_words(text, words):
    """ "son tung m tp tyga" chứa "son tung m tp" (theo nguyên từ: "min" KHÔNG nằm trong "minh")."""
    return bool(words) and f" {words} " in f" {text} "


def artist_ok(candidate, artists):
    """Ca sĩ của kết quả phải trùng / chứa một trong các ca sĩ của bài trên Spotify (hoặc ngược lại)."""
    cand = norm(candidate)
    return bool(cand) and any(
        has_words(cand, a) or has_words(a, cand) for a in (norm(x) for x in artists))


class LyricsFinder:

    def __init__(self, conn, sleep=time.sleep, session=None):
        self.conn = conn
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.LYRICS_USER_AGENT,
                                     "Accept": "application/json"})
        self.delay = config.LYRICS_DELAY
        self.requests = 0
        self.retries = 0
        self.get_errors = 0
        self.consecutive_failures = 0
        self.stats = Counter()
        self.local = []                 # lời từ database khác: (title_key, title, artist, lyrics, url, source)

    # ---------------- HTTP ----------------

    def request(self, path, params, retry=True):
        """
        GET tới LRCLIB, nghỉ LYRICS_DELAY giây trước mỗi request. Lỗi -> None.
        retry=True  (/api/search): 429 / 5xx -> chậm lại, chờ (Retry-After) rồi thử lại;
                                   lỗi mãi -> tính vào số lỗi liên tiếp (dừng khi quá nhiều).
        retry=False (/api/get): server có thể lỗi khi tra nguồn ngoài -> bỏ qua, không thử lại.
        """
        for attempt in range(config.MAX_RETRIES + 1):
            self.sleep(self.delay)
            self.requests += 1
            try:
                response = self.session.get(config.LYRICS_API + path, params=params,
                                            timeout=config.REQUEST_TIMEOUT * 2)
            except requests.RequestException:
                break
            if response.status_code not in RETRY_STATUSES:
                self.delay = max(config.LYRICS_DELAY, self.delay * 0.9)
                self.consecutive_failures = 0
                return response
            if retry or response.status_code == 429:
                self.delay = min(5.0, self.delay * 2)      # server quá tải -> chậm lại
            if not retry:
                return None
            if attempt == config.MAX_RETRIES:
                break
            wait = retry_after_seconds(response.headers.get("Retry-After"))
            wait = min(config.MAX_BACKOFF, wait if wait is not None else 5 * 2 ** attempt)
            self.retries += 1
            print(f"    [LRCLIB HTTP {response.status_code}] chờ {wait:.0f} giây rồi thử lại")
            self.sleep(wait)
        self.consecutive_failures += 1
        return None

    def search(self, title, artist):
        response = self.request("/search", {"track_name": title, "artist_name": artist})
        if response is None:
            return None
        if response.status_code != 200:
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    # ---------------- chọn kết quả ----------------

    @staticmethod
    def pick(candidates, title, artists, duration):
        """Chọn kết quả khớp nhất: cùng tên bài + ca sĩ, thời lượng gần nhất."""
        want_full, want_base = norm(title), norm(base_title(title))
        best = None
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if not (cand.get("plainLyrics") or cand.get("syncedLyrics") or cand.get("instrumental")):
                continue
            if not artist_ok(cand.get("artistName"), artists):
                continue
            name = cand.get("trackName") or ""
            try:
                diff = abs(float(cand["duration"]) - duration) if duration and cand.get("duration") else None
            except (TypeError, ValueError):
                diff = None
            if norm(name) == want_full:
                if diff is None or diff <= 3:
                    rank, match = 0, "search"
                elif diff <= config.LYRICS_MAX_DURATION_DIFF:
                    rank, match = 1, "other_version"
                else:
                    continue
            elif want_base and norm(base_title(name)) == want_base:
                if diff is not None and diff > 3 * config.LYRICS_MAX_DURATION_DIFF:
                    continue
                rank, match = 2, "base_title"
            else:
                continue
            key = (rank, diff if diff is not None else 999)
            if best is None or key < best[0]:
                best = (key, match, cand)
        return (best[1], best[2]) if best else (None, None)

    # ---------------- tra một bài ----------------

    def from_local(self, title, artists):
        key = norm(base_title(title))
        for title_key, other_title, artist, lyrics, url, source in self.local:
            if title_key == key and artist_ok(artist, artists):
                return {"status": "found", "source": source, "source_id": url, "match": "title_artist",
                        "matched_title": other_title, "matched_artist": artist, "matched_duration": None,
                        "plain_lyrics": lyrics, "synced_lyrics": None}
        return None

    def from_lrclib(self, title, artists, album, duration):
        """Tìm theo thứ tự: tên đầy đủ -> tên gốc (bỏ "- Live", "(feat. ...)") -> /api/get."""
        artist = artists[0]
        failed = False

        attempts = [title]
        if norm(base_title(title)) not in ("", norm(title)):
            attempts.append(base_title(title))
        for query in attempts:
            results = self.search(query, artist)
            if results is None:
                failed = True
                continue
            match, cand = self.pick(results, title, artists, duration)
            if cand:
                return self.result(cand, match), False

        # /api/get: khớp theo tên + ca sĩ + album + thời lượng (±2 giây); không thử lại khi lỗi
        params = {"track_name": title, "artist_name": artist, "duration": duration}
        if album:
            params["album_name"] = album
        response = self.request("/get", params, retry=False)
        if response is None:
            self.get_errors += 1          # tìm kiếm đã trả lời "không có" -> coi như không tìm thấy
        elif response.status_code == 200:
            try:
                cand = response.json()
            except ValueError:
                cand = None
            if isinstance(cand, dict) and (cand.get("plainLyrics") or cand.get("syncedLyrics")
                                           or cand.get("instrumental")):
                return self.result(cand, "exact"), False
        return None, failed

    @staticmethod
    def result(cand, match):
        synced = cand.get("syncedLyrics") or None
        plain = (cand.get("plainLyrics") or "").strip() or plain_from_synced(synced)
        instrumental = bool(cand.get("instrumental")) and not plain
        return {
            "status": "instrumental" if instrumental else "found",
            "source": "lrclib",
            "source_id": str(cand.get("id")) if cand.get("id") is not None else None,
            "match": match,
            "matched_title": cand.get("trackName"),
            "matched_artist": cand.get("artistName"),
            "matched_duration": cand.get("duration"),
            "plain_lyrics": None if instrumental else plain,
            "synced_lyrics": None if instrumental else synced,
        }

    def lookup(self, track, use_lrclib=True):
        """Trả về dict kết quả, hoặc None nếu lỗi mạng / server (để lần sau tra lại)."""
        title, artists = track["title"], track["artists"]
        if INSTRUMENTAL_TITLE.search(title):
            return {"status": "instrumental", "source": "title", "match": "title"}

        found = self.from_local(title, artists)
        if found:
            return found
        if not use_lrclib:
            return {"status": "skipped"}          # không lưu: lần sau còn tra LRCLIB

        found, failed = self.from_lrclib(title, artists, track["album"], track["duration"])
        if found:
            return found
        return None if failed else {"status": "not_found", "source": "lrclib"}

    # ---------------- database ----------------

    def save(self, track_id, url, result):
        row = {key: result.get(key) for key in (
            "status", "source", "source_id", "match", "matched_title", "matched_artist",
            "matched_duration", "plain_lyrics", "synced_lyrics")}
        self.conn.execute("""
        INSERT OR REPLACE INTO lyrics
            (track_id, status, source, source_id, match, matched_title, matched_artist,
             matched_duration, plain_lyrics, synced_lyrics, fetched_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (track_id, row["status"], row["source"], row["source_id"], row["match"],
              row["matched_title"], row["matched_artist"], row["matched_duration"],
              row["plain_lyrics"], row["synced_lyrics"], utc_now()))
        if row["status"] == "found":
            self.conn.execute("UPDATE songs SET lyrics = ? WHERE url = ?", (row["plain_lyrics"], url))
        self.conn.commit()


def load_local_lyrics(paths):
    """Đọc lời từ database crawl khác có bảng songs(title, artist, lyrics, url) theo đề bài."""
    rows = []
    for path in paths:
        if not os.path.exists(path):
            sys.exit(f"Không thấy {path}")
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
        try:
            for title, artist, lyrics, url in conn.execute(
                    "SELECT title, artist, lyrics, url FROM songs WHERE LENGTH(lyrics) > 50"):
                rows.append((norm(base_title(title)), title, artist or "", lyrics.strip(), url,
                             "db:" + os.path.basename(path)))
        except sqlite3.OperationalError:
            sys.exit(f"{path} không có bảng songs(title, artist, lyrics, url)")
        finally:
            conn.close()
    return rows


def sync_songs(conn):
    """Chép lời đã có trong bảng lyrics vào songs.lyrics (vd sau khi crawl lại)."""
    conn.execute("""
    UPDATE songs SET lyrics = (
        SELECT l.plain_lyrics FROM tracks t JOIN lyrics l ON l.track_id = t.track_id
        WHERE t.url = songs.url AND l.status = 'found')
    WHERE url IN (SELECT t.url FROM tracks t JOIN lyrics l ON l.track_id = t.track_id
                  WHERE l.status = 'found')
    """)
    conn.commit()


def pending_tracks(conn, retry):
    statuses = ("found", "instrumental") if retry else ("found", "instrumental", "not_found")
    rows = conn.execute(f"""
    SELECT t.track_id, t.url, t.title, t.artists_json, t.artist_text, t.album, t.duration_sec
    FROM tracks t LEFT JOIN lyrics l ON l.track_id = t.track_id
    WHERE l.status IS NULL OR l.status NOT IN ({",".join("?" * len(statuses))})
    ORDER BY t.rowid
    """, statuses).fetchall()
    tracks = []
    for track_id, url, title, artists_json, artist_text, album, duration in rows:
        artists = json.loads(artists_json or "[]") or ([artist_text] if artist_text else [])
        if title and artists:
            tracks.append({"track_id": track_id, "url": url, "title": title, "artists": artists,
                           "album": album, "duration": duration})
    return tracks


def run(db_path, limit=None, retry=False, from_db=(), use_lrclib=True, sleep=time.sleep, session=None):
    if not os.path.exists(db_path):
        sys.exit(f"Không thấy {db_path} – chạy python main.py trước.")
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        try:
            conn.executescript(LYRICS_SCHEMA)
            if not conn.execute("SELECT name FROM sqlite_master WHERE name = 'tracks'").fetchone():
                sys.exit("Database chưa có bảng tracks – chạy python main.py (bản Spotify) trước.")
            sync_songs(conn)
        except sqlite3.OperationalError as error:
            sys.exit(LOCKED if "locked" in str(error) else f"Lỗi database: {error}")

        finder = LyricsFinder(conn, sleep=sleep, session=session)
        finder.local = load_local_lyrics(from_db)
        tracks = pending_tracks(conn, retry)
        stop_reason = "Đã tra hết"
        if limit is not None and len(tracks) > limit:
            tracks = tracks[:limit]
            stop_reason = f"Đã tra {limit} bài (--limit), chạy lại để tra tiếp"

        print(f"Tra lời cho {len(tracks)} bài "
              f"(nguồn: {'LRCLIB' if use_lrclib else ''}{' + ' if use_lrclib and from_db else ''}"
              f"{', '.join(os.path.basename(p) for p in from_db)})")
        try:
            for i, track in enumerate(tracks, start=1):
                if finder.consecutive_failures >= config.STOP_AFTER_CONSECUTIVE_FAILURES:
                    stop_reason = (f"{finder.consecutive_failures} request lỗi liên tiếp – "
                                   "LRCLIB đang lỗi / quá tải, chạy lại sau")
                    break
                result = finder.lookup(track, use_lrclib=use_lrclib)
                if result is None:
                    finder.stats["error"] += 1
                    label = "LỖI (tra lại sau)"
                elif result["status"] == "skipped":
                    finder.stats["skipped"] += 1
                    label = "không có trong database khác"
                else:
                    try:
                        finder.save(track["track_id"], track["url"], result)
                    except sqlite3.OperationalError as error:
                        stop_reason = LOCKED if "locked" in str(error) else f"Lỗi database: {error}"
                        break
                    status = result["status"]
                    finder.stats[status] += 1
                    if status == "found":
                        finder.stats["match:" + result["match"]] += 1
                    label = {"found": f"có lời ({result.get('match')})",
                             "instrumental": "không lời",
                             "not_found": "không tìm thấy"}[status]
                print(f"[{i}/{len(tracks)}] {track['title'][:40]} | {track['artists'][0][:22]} -> {label}")
        except KeyboardInterrupt:
            stop_reason = "Người dùng dừng (Ctrl+C) – chạy lại để tra tiếp"

        summary = make_summary(conn, finder, len(tracks), stop_reason)
    finally:
        conn.close()

    print(summary)
    path = os.path.normpath(os.path.join(BASE_DIR, config.LYRICS_SUMMARY_FILE))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(summary.strip() + "\n")
    print(f"(Đã lưu thống kê vào {config.LYRICS_SUMMARY_FILE})")
    return finder


def make_summary(conn, finder, n_pending, stop_reason):
    def row(label, value):
        return f"{label:<30}: {value}"

    s = finder.stats
    total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    with_lyrics = conn.execute("SELECT COUNT(*) FROM songs WHERE LENGTH(lyrics) > 0").fetchone()[0]
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM lyrics GROUP BY status"))
    by_source = conn.execute("""
    SELECT source, match, COUNT(*) FROM lyrics WHERE status = 'found'
    GROUP BY source, match ORDER BY COUNT(*) DESC""").fetchall()

    lines = [
        "",
        "=" * 10 + " LYRICS SUMMARY " + "=" * 10,
        row("Nguồn lời", "LRCLIB (lrclib.net) + database khác nếu có --from-db"),
        row("Kết thúc", stop_reason),
        "",
        "Lần chạy này:",
        row("    Bài cần tra", n_pending),
        row("    Có lời", s["found"]),
        row("    Nhạc không lời", s["instrumental"]),
        row("    Không tìm thấy", s["not_found"]),
        row("    Lỗi (lần sau tra lại)", s["error"]),
        row("    Request LRCLIB (retry)", f"{finder.requests} ({finder.retries})"),
        "",
        "Toàn bộ database:",
        row("    Bài hát (tracks)", total),
        row("    Có lời (songs.lyrics)", f"{with_lyrics} ({with_lyrics / total:.0%})" if total else 0),
        row("    Nhạc không lời", by_status.get("instrumental", 0)),
        row("    Không tìm thấy", by_status.get("not_found", 0)),
        row("    Chưa tra", total - sum(by_status.values())),
        "Có lời theo nguồn / kiểu khớp:",
    ]
    for source, match, count in by_source:
        lines.append(row(f"    {source} / {match}", count))
    lines += [
        "  exact / search  : cùng tên bài, ca sĩ, thời lượng lệch <= 3 giây",
        "  other_version   : cùng tên bài và ca sĩ, thời lượng khác (bản edit, bản khác)",
        "  base_title      : lời của bản gốc (bài Spotify là bản live / remix / feat.)",
        "=" * 36,
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Lấy lời bài hát cho các bài Spotify đã crawl (LRCLIB)")
    ap.add_argument("--db", default=DB_PATH, help="database của crawler (mặc định: %(default)s)")
    ap.add_argument("--limit", type=int, help="chỉ tra N bài (chạy thử)")
    ap.add_argument("--retry", action="store_true", help="tra lại cả các bài lần trước không tìm thấy")
    ap.add_argument("--from-db", action="append", default=[], metavar="FILE",
                    help="database crawl khác có bảng songs(title, artist, lyrics); dùng nhiều lần được")
    ap.add_argument("--no-lrclib", action="store_true", help="chỉ dùng --from-db, không gọi LRCLIB")
    args = ap.parse_args()
    run(args.db, limit=args.limit, retry=args.retry, from_db=args.from_db,
        use_lrclib=not args.no_lrclib)


if __name__ == "__main__":
    main()
