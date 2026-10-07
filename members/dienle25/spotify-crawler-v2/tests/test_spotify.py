"""
Kiểm thử offline cho crawler Spotify (không gửi request ra Internet).

Chạy từ thư mục project:
    python -m unittest discover -s tests -v

HTML mẫu dựng lại đúng thứ tự thẻ <meta> mà open.spotify.com trả về cho Requests
(lần chạy 05/10/2026 của crawler 4 website).
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bs4 import BeautifulSoup  # noqa: E402

import config  # noqa: E402
import crawler as crawler_module  # noqa: E402
import database  # noqa: E402
import export_c2c  # noqa: E402
from parser import extract_links  # noqa: E402
from spotify_parser import (kind_and_id, parse_description, parse_listing,  # noqa: E402
                            parse_track, split_artists)
from url_filter import check_url_rules, normalize_url  # noqa: E402

BASE = "https://open.spotify.com"


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def sid(n, prefix="T"):
    """Id Spotify giả, đủ 22 ký tự."""
    return f"{prefix}{n}".ljust(22, "x")


# ---------------------------------------------------------------------------
# HTML mẫu
# ---------------------------------------------------------------------------
def track_html(track_id, title, artist_text, artist_ids, album, album_id, number=1,
               date="2018-05-12", duration=273, og_title=True, extra_head=""):
    head = [f"<title>{title} - song and lyrics by {artist_text} | Spotify</title>",
            f'<meta name="description" content="Listen to {title} on Spotify. Song · {artist_text} · {date[:4]}"/>']
    if og_title:
        head += [f'<meta property="og:title" content="{title}"/>',
                 f'<meta property="og:description" content="{artist_text} · {album} · Song · {date[:4]}"/>']
    head += [f'<meta property="og:url" content="{BASE}/track/{track_id}"/>',
             '<meta property="og:type" content="music.song"/>',
             f'<meta name="music:duration" content="{duration}"/>',
             f'<meta name="music:album" content="{BASE}/album/{album_id}"/>',
             f'<meta name="music:album:track" content="{number}"/>',
             f'<meta name="music:release_date" content="{date}"/>']
    head += [f'<meta name="music:musician" content="{BASE}/artist/{a}"/>' for a in artist_ids]
    head += [f'<meta name="music:musician_description" content="{artist_text}"/>', extra_head]
    return ("<!DOCTYPE html><html lang='vi'><head><meta charset='UTF-8'/>" + "".join(head)
            + "</head><body><div id='main'></div></body></html>")


def album_html(album_id, title, artist_name, artist_id, track_ids, date="2013-01-01"):
    head = [f"<title>{title} - Album by {artist_name} | Spotify</title>",
            f'<meta property="og:title" content="{title}"/>',
            f'<meta property="og:description" content="{artist_name} · album · {date[:4]} · {len(track_ids)} songs"/>',
            f'<meta name="music:musician" content="{BASE}/artist/{artist_id}"/>',
            f'<meta name="music:release_date" content="{date}"/>']
    for i, t in enumerate(track_ids, start=1):
        head += [f'<meta name="music:song" content="{BASE}/track/{t}"/>',
                 f'<meta name="music:song:track" content="{i}"/>']
    return "<html><head>" + "".join(head) + "</head><body></body></html>"


def playlist_html(playlist_id, title, track_ids):
    head = [f"<title>{title} | Spotify Playlist</title>",
            f'<meta property="og:title" content="{title}"/>',
            '<meta property="og:description" content="Bản nhạc mới nhất và những bài hit khác."/>',
            f'<meta name="music:creator" content="{BASE}/user/spotify"/>']
    for i, t in enumerate(track_ids, start=1):
        head += [f'<meta name="music:song" content="{BASE}/track/{t}"/>',
                 f'<meta name="music:song:track" content="{i}"/>']
    head.append(f'<meta name="music:song_count" content="{len(track_ids) + 13}"/>')
    return "<html><head>" + "".join(head) + "</head><body></body></html>"


REAL_TRACK = track_html(
    "4HlHNLtfxP7Y5z03j3eNOi", "CHẠY NGAY ĐI", "Sơn Tùng M-TP, Onionn.",
    ["5dfZ5uSmzR7VQK0udbAVpf", "25M5YMbLCbYDSFPhQXYE8c"], "CHẠY NGAY ĐI", "635YOdtbHIX1TlTn2oFHGh")
REAL_TRACK_URL = f"{BASE}/track/4HlHNLtfxP7Y5z03j3eNOi"


# ---------------------------------------------------------------------------
# spotify_parser
# ---------------------------------------------------------------------------
class SpotifyParserTests(unittest.TestCase):

    def test_track_page_metadata(self):
        track = parse_track(REAL_TRACK_URL, soup_of(REAL_TRACK), "2026-10-06T00:00:00+00:00", "abc")
        self.assertEqual(track["track_id"], "4HlHNLtfxP7Y5z03j3eNOi")
        self.assertEqual(track["title"], "CHẠY NGAY ĐI")
        self.assertEqual(track["artists"], ["Sơn Tùng M-TP", "Onionn."])     # giữ dấu chấm của tên
        self.assertEqual(track["artist_ids"], ["5dfZ5uSmzR7VQK0udbAVpf", "25M5YMbLCbYDSFPhQXYE8c"])
        self.assertEqual(track["artist_text"], "Sơn Tùng M-TP, Onionn.")
        self.assertEqual(track["album"], "CHẠY NGAY ĐI")
        self.assertEqual(track["album_id"], "635YOdtbHIX1TlTn2oFHGh")
        self.assertEqual((track["track_number"], track["release_date"], track["year"], track["duration_sec"]),
                         (1, "2018-05-12", 2018, 273))
        self.assertEqual(track["html_sha256"], "abc")

    def test_title_and_artist_fall_back_to_title_tag_and_description(self):
        html = track_html(sid(1), "Come My Way - softer version", "Sơn Tùng M-TP", [],
                          "Come My Way (softer version)", sid(9, "A"), og_title=False)
        html = html.replace('<meta name="music:musician_description" content="Sơn Tùng M-TP"/>', "")
        track = parse_track(f"{BASE}/track/{sid(1)}", soup_of(html), None)
        self.assertEqual(track["title"], "Come My Way - softer version")
        self.assertEqual(track["artists"], ["Sơn Tùng M-TP"])
        self.assertEqual(track["artist_ids"], [])

    def test_description_formats(self):
        self.assertEqual(parse_description("Sơn Tùng M-TP · m-tp M-TP · Song · 2013"),
                         ("Sơn Tùng M-TP", "m-tp M-TP", 2013))
        self.assertEqual(parse_description("Listen to X on Spotify. Song · Sơn Tùng M-TP, Onionn. · 2018"),
                         ("Sơn Tùng M-TP, Onionn.", None, 2018))
        self.assertEqual(parse_description(""), (None, None, None))

    def test_artist_names_follow_artist_ids(self):
        self.assertEqual(split_artists("Tyler, The Creator", 1), ["Tyler, The Creator"])   # 1 id = 1 tên
        self.assertEqual(split_artists("A, B", 2), ["A", "B"])
        self.assertEqual(split_artists("A và B", 2), [])          # không tách được -> không rõ
        self.assertEqual(split_artists("", 1), [])

    def test_album_and_playlist_pages(self):
        tracks = [sid(i) for i in range(1, 4)]
        album = parse_listing(f"{BASE}/album/{sid(1, 'A')}",
                              soup_of(album_html(sid(1, "A"), "m-tp M-TP", "Sơn Tùng M-TP", sid(1, "R"), tracks)),
                              None)
        self.assertEqual((album["kind"], album["title"], album["track_ids"], album["artist_ids"]),
                         ("album", "m-tp M-TP", tracks, [sid(1, "R")]))
        self.assertEqual(album["release_date"], "2013-01-01")

        playlist = parse_listing(f"{BASE}/playlist/{sid(1, 'P')}",
                                 soup_of(playlist_html(sid(1, "P"), "Mãi Yêu Sơn Tùng M-TP", tracks)), None)
        self.assertEqual(playlist["track_ids"], tracks)
        self.assertNotIn("user", json.dumps(playlist))           # không lưu người tạo playlist

    def test_not_a_track_page(self):
        self.assertIsNone(parse_track(f"{BASE}/album/{sid(1, 'A')}", soup_of(REAL_TRACK), None))
        self.assertIsNone(parse_track(REAL_TRACK_URL, soup_of("<html><head></head></html>"), None))
        self.assertEqual(kind_and_id(REAL_TRACK_URL), ("track", "4HlHNLtfxP7Y5z03j3eNOi"))


# ---------------------------------------------------------------------------
# Link + URL rules
# ---------------------------------------------------------------------------
class LinkAndUrlTests(unittest.TestCase):

    def test_track_links_have_no_junk_meta_values(self):
        links = extract_links(soup_of(REAL_TRACK), REAL_TRACK, REAL_TRACK_URL)
        self.assertIn(f"{BASE}/album/635YOdtbHIX1TlTn2oFHGh", links)
        self.assertIn(f"{BASE}/artist/5dfZ5uSmzR7VQK0udbAVpf", links)
        for junk in ("/track/273", "/track/1", "/track/2018-05-12", "Onionn"):
            self.assertFalse(any(link.endswith(junk) or junk in link for link in links), junk)

    def test_playlist_links_skip_user_pages_and_numbers(self):
        html = playlist_html(sid(1, "P"), "Test", [sid(1), sid(2)])
        links = extract_links(soup_of(html), html, f"{BASE}/playlist/{sid(1, 'P')}")
        self.assertEqual(links, [f"{BASE}/track/{sid(1)}", f"{BASE}/track/{sid(2)}"])

    def test_url_rules(self):
        self.assertEqual(check_url_rules(f"{BASE}/track/{sid(1)}", 1), (True, "VALID"))
        self.assertEqual(check_url_rules(f"{BASE}/album/{sid(1)}", 1), (True, "VALID"))
        self.assertEqual(check_url_rules(f"{BASE}/playlist/{sid(1)}", 1), (True, "VALID"))
        self.assertEqual(check_url_rules(f"{BASE}/artist/{sid(1)}", 1)[1], "NOT_MUSIC_PAGE")   # FETCH_ARTIST_PAGES
        self.assertEqual(check_url_rules(f"{BASE}/embed/track/{sid(1)}", 1)[1], "NOT_MUSIC_PAGE")
        self.assertEqual(check_url_rules(f"{BASE}/user/spotify", 1)[1], "NOT_MUSIC_PAGE")
        self.assertEqual(check_url_rules("https://www.nhaccuatui.com/song/abc", 1)[1], "OUTSIDE_DOMAIN")
        self.assertEqual(check_url_rules(f"{BASE}/track/{sid(1)}", config.MAX_DEPTH + 1)[1], "MAX_DEPTH")

    def test_normalize_spotify_urls(self):
        self.assertEqual(normalize_url(BASE, f"{BASE}/intl-vi/track/{sid(1)}?si=abc"), f"{BASE}/track/{sid(1)}")
        self.assertEqual(normalize_url(BASE, f"http://open.spotify.com/album/{sid(1)}/"), f"{BASE}/album/{sid(1)}")

    def test_every_seed_is_a_valid_spotify_page(self):
        self.assertGreater(len(config.SEED_URLS), 2)          # config.py + seeds.txt
        for seed in config.SEED_URLS:
            with self.subTest(seed=seed):
                self.assertEqual(check_url_rules(normalize_url(seed, seed), 0), (True, "VALID"))


# ---------------------------------------------------------------------------
# Crawl từ đầu tới cuối với website giả (không có mạng)
# ---------------------------------------------------------------------------
X, Y, Z = sid(1, "R"), sid(2, "R"), sid(3, "R")
P1, A1, A2 = sid(1, "P"), sid(1, "A"), sid(2, "A")
T1, T2, T3, T4, T5, T6, T7 = (sid(i) for i in range(1, 8))

ROBOTS = ("User-agent: *\nAllow: /\nDisallow: /embed/\n\n"
          "User-agent: GPTBot\nDisallow: /\n\nContent-Signal: search=yes, ai-train=no\n")

SITE = {
    f"{BASE}/robots.txt": (200, ROBOTS, {"Content-Type": "text/plain"}),
    f"{BASE}/playlist/{P1}": (200, playlist_html(P1, "Playlist thử", [T1, T2, T3]), {}),
    f"{BASE}/album/{A1}": (200, album_html(A1, "Album Một", "Ca Sĩ X", X, [T1, T4]), {}),
    f"{BASE}/album/{A2}": (200, album_html(A2, "Album Hai", "Ca Sĩ Y.", Y, [T2, T5, T6, T7]), {}),
    f"{BASE}/track/{T1}": (200, track_html(T1, "Bài Một", "Ca Sĩ X", [X], "Album Một", A1, 1), {}),
    f"{BASE}/track/{T2}": (200, track_html(T2, "Bài Hai", "Ca Sĩ X, Ca Sĩ Y.", [X, Y], "Album Hai", A2, 1), {}),
    f"{BASE}/track/{T3}": (200, track_html(T3, "Bài Ba", "Tyler, The Creator", [Z], "Album Một", A1, 3), {}),
    f"{BASE}/track/{T4}": (200, track_html(T4, "Bài Bốn", "Ca Sĩ X", [X], "Album Một", A1, 2), {}),
    f"{BASE}/track/{T5}": (200, track_html(T5, "Bài Năm", "Ca Sĩ Y.", [Y], "Album Hai", A2, 2), {}),
    f"{BASE}/track/{T6}": (200, track_html(T6, "Bài Sáu", "Ca Sĩ Y.", [Y], "Album Hai", A2, 3,
                                           extra_head='<meta name="robots" content="noindex"/>'), {}),
    f"{BASE}/track/{T7}": (404, "<html><head><title>Page not found</title></head></html>", {}),
}


def make_response(url, status, body, headers):
    response = requests.models.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")
    response.headers.update({"Content-Type": "text/html; charset=utf-8", **headers})
    response.encoding = "utf-8"
    return response


class FakeSiteCase(unittest.TestCase):
    """Chạy Crawler thật với session.get và time.sleep giả, database trong thư mục tạm."""

    SEEDS = [f"{BASE}/playlist/{P1}"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {name: getattr(config, name) for name in (
            "SEED_URLS", "CRAWL_DELAY", "SUMMARY_FILE", "MAX_PAGES", "MAX_HOURS", "MAX_RETRIES",
            "STOP_AFTER_CONSECUTIVE_FAILURES")}
        self.saved_db = database.DB_PATH
        self.saved_sleep = crawler_module.time.sleep

        config.SEED_URLS = list(self.SEEDS)
        config.CRAWL_DELAY = 0
        config.SUMMARY_FILE = os.path.join(self.tmp.name, "summary.txt")
        database.DB_PATH = os.path.join(self.tmp.name, "spotify.db")
        self.sleeps = []
        crawler_module.time.sleep = self.sleeps.append
        self.requests = []
        self.overrides = {}          # url -> list các response trả về trước response thật

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(config, name, value)
        database.DB_PATH = self.saved_db
        crawler_module.time.sleep = self.saved_sleep
        self.tmp.cleanup()

    def fake_get(self, url, timeout=None):
        self.requests.append(url)
        if self.overrides.get(url):
            status, body, headers = self.overrides[url].pop(0)
        else:
            status, body, headers = SITE.get(url, (404, "", {}))
        return make_response(url, status, body, headers)

    def crawl(self):
        bot = crawler_module.Crawler()
        bot.session.get = self.fake_get            # RobotsChecker dùng chung session
        with open(os.devnull, "w") as devnull:
            stdout, sys.stdout = sys.stdout, devnull
            try:
                bot.run()
            finally:
                sys.stdout = stdout
        return bot

    def query(self, sql, *args):
        conn = sqlite3.connect(database.DB_PATH)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


class CrawlEndToEndTests(FakeSiteCase):

    def test_full_crawl(self):
        # T3 bị 429 một lần (Retry-After: 2) rồi mới trả 200
        self.overrides[f"{BASE}/track/{T3}"] = [(429, "", {"Retry-After": "2"})]
        bot = self.crawl()

        self.assertEqual(bot.stop_reason, "URL Frontier is empty")
        self.assertEqual(self.requests[0], f"{BASE}/robots.txt")
        self.assertEqual(self.requests.count(f"{BASE}/track/{T3}"), 2)
        self.assertIn(2, self.sleeps)                                 # đã chờ đúng Retry-After
        self.assertEqual(bot.retries, 1)

        # pages: 1 playlist + 2 album + 5 track (T6 noindex, T7 lỗi 404)
        kinds = [url.split("/")[3] for (url,) in self.query("SELECT url FROM pages")]
        self.assertEqual(sorted(kinds), ["album"] * 2 + ["playlist"] + ["track"] * 5)
        self.assertEqual(bot.noindex_pages, 1)
        self.assertEqual(bot.failed_requests, 1)
        self.assertEqual(bot.consecutive_failures, 0)

        tracks = {row[0]: row for row in self.query(
            "SELECT track_id, artists_json, artist_ids_json, n_artists, album_id, source_url FROM tracks")}
        self.assertEqual(set(tracks), {T1, T2, T3, T4, T5})
        self.assertEqual(json.loads(tracks[T2][1]), ["Ca Sĩ X", "Ca Sĩ Y."])
        self.assertEqual(json.loads(tracks[T3][1]), ["Tyler, The Creator"])
        self.assertEqual(tracks[T2][3], 2)
        self.assertEqual(tracks[T1][5], f"{BASE}/playlist/{P1}")      # tìm thấy từ playlist
        self.assertEqual(tracks[T4][5], f"{BASE}/album/{A1}")         # tìm thấy từ album

        # bảng songs theo đề bài: genre và lyrics để NULL
        songs = self.query("SELECT url, title, artist, album, genre, lyrics FROM songs")
        self.assertEqual(len(songs), 5)
        self.assertTrue(all(genre is None and lyrics is None for *_, genre, lyrics in songs))
        self.assertIn(("Bài Hai", "Ca Sĩ X, Ca Sĩ Y.", "Album Hai"), [s[1:4] for s in songs])

        listings = {kind: (n, n_new) for kind, n, n_new in self.query(
            "SELECT kind, n_tracks, n_new_tracks FROM listings WHERE listing_id IN (?, ?)", P1, A2)}
        self.assertEqual(listings["playlist"], (3, 3))
        self.assertEqual(listings["album"], (4, 3))                   # T2 đã có từ playlist

        # không có link rác / trang người dùng trong bảng links
        targets = [t for (t,) in self.query("SELECT target_url FROM links")]
        self.assertFalse([t for t in targets if "/user/" in t or t.rsplit("/", 1)[1].isdigit()])

        # thống kê + bảng meta
        summary = Path(config.SUMMARY_FILE).read_text(encoding="utf-8")
        self.assertRegex(summary, r"Tracks saved\s+: 5\n")
        self.assertRegex(summary, r"single-artist tracks\s+: 4\n")
        self.assertIn("Content-Signal: search=yes, ai-train=no", summary)
        run = json.loads(self.query("SELECT v FROM meta WHERE k = 'run'")[0][0])
        self.assertEqual(run["counts"]["tracks_saved"], 5)
        self.assertEqual(run["robots"]["open.spotify.com"]["status"], 200)

    def test_songs_table_keeps_the_assignment_schema(self):
        self.crawl()
        columns = [row[1] for row in self.query("PRAGMA table_info(songs)")]
        self.assertEqual(columns, ["id", "url", "title", "artist", "album", "genre", "lyrics", "crawled_at"])

    def test_max_pages(self):
        config.MAX_PAGES = 3
        bot = self.crawl()
        self.assertEqual(bot.stop_reason, "MAX_PAGES reached")
        self.assertEqual(self.query("SELECT COUNT(*) FROM pages")[0][0], 3)

    def test_max_hours(self):
        config.MAX_HOURS = 0
        bot = self.crawl()
        self.assertTrue(bot.stop_reason.startswith("MAX_HOURS"))
        self.assertEqual(bot.pages_crawled, 0)

    def test_long_retry_after_stops_the_crawl(self):
        self.overrides[f"{BASE}/playlist/{P1}"] = [(429, "", {"Retry-After": "3600"})]
        bot = self.crawl()
        self.assertIn("Retry-After", bot.stop_reason)
        self.assertEqual(bot.pages_crawled, 1)
        self.assertNotIn(3600, self.sleeps)                           # không ngồi chờ 1 giờ

    def test_retry_after_as_http_date(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        wait = crawler_module.retry_after_seconds(format_datetime(when, usegmt=True))
        self.assertTrue(25 <= wait <= 30, wait)
        self.assertEqual(crawler_module.retry_after_seconds("7"), 7)
        self.assertIsNone(crawler_module.retry_after_seconds("ngày mai"))


class ConsecutiveFailureTests(FakeSiteCase):

    SEEDS = [f"{BASE}/playlist/{sid(i, 'Q')}" for i in range(1, 6)]

    def test_stops_after_consecutive_server_errors(self):
        config.STOP_AFTER_CONSECUTIVE_FAILURES = 3
        config.MAX_RETRIES = 0
        for seed in self.SEEDS:
            self.overrides[seed] = [(503, "", {})]
        bot = self.crawl()
        self.assertIn("lỗi liên tiếp", bot.stop_reason)
        self.assertEqual(bot.pages_crawled, 3)

    def test_404_does_not_count_as_blocked(self):
        config.STOP_AFTER_CONSECUTIVE_FAILURES = 3
        bot = self.crawl()                                            # 5 seed không tồn tại -> 404
        self.assertEqual(bot.stop_reason, "URL Frontier is empty")
        self.assertEqual(bot.pages_crawled, 5)


# ---------------------------------------------------------------------------
# Xuất dữ liệu cho đề tài C2C-VN
# ---------------------------------------------------------------------------
class ExportTests(FakeSiteCase):

    def test_export_matches_c2c_schema(self):
        self.crawl()
        out = os.path.join(self.tmp.name, "c2c.sqlite")
        report = export_c2c.export(database.DB_PATH, out)

        conn = sqlite3.connect(out)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(songs)")]
            self.assertEqual(columns, ["song_id", "url", "title", "authors_json", "author_ids_json", "n_authors",
                                       "genre", "rhythm", "record_json", "html_sha256", "http_status",
                                       "crawled_utc", "source_listing"])
            # đúng câu SELECT của scripts/build_testbed.py
            rows = conn.execute("SELECT song_id, url, title, authors_json, author_ids_json, genre, rhythm "
                                "FROM songs WHERE http_status=200").fetchall()
            self.assertEqual(len(rows), 5)
            for song_id, url, title, authors, ids, genre, rhythm in rows:
                self.assertEqual(int(song_id), export_c2c.song_id_of(url.rsplit("/", 1)[1]))
                self.assertLess(song_id, 2 ** 63)
                self.assertIsInstance(json.loads(authors), list)
                self.assertIsInstance(json.loads(ids), list)
                self.assertIsNone(genre)
                self.assertIsNone(rhythm)
            record = json.loads(conn.execute("SELECT record_json FROM songs LIMIT 1").fetchone()[0])
            self.assertEqual(record["role"], "performer")
            meta = dict(conn.execute("SELECT k, v FROM meta"))
            self.assertIn("export", meta)
            self.assertEqual(json.loads(meta["run"])["crawler_version"], config.CRAWLER_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 3)
        finally:
            conn.close()

        # T2 có 2 nghệ sĩ -> không dùng; còn 4 bài 1 nghệ sĩ của X (2), Y (1), Z (1)
        self.assertEqual((report["songs"], report["records"], report["authors"]), (5, 4, 3))
        self.assertEqual(report["not_single_author"], 1)

    def test_readiness_follows_testbed_rules(self):
        rows = [
            {"song_id": 1, "title": "Lạc Trôi", "authors": ["Sơn Tùng M-TP"], "author_ids": ["a"]},
            {"song_id": 2, "title": "Lạc  trôi", "authors": ["Sơn Tùng M-TP"], "author_ids": ["a"]},   # trùng
            {"song_id": 3, "title": "Bài khác", "authors": ["A", "B"], "author_ids": ["a", "b"]},     # 2 nghệ sĩ
            {"song_id": 4, "title": "Bài X", "authors": ["Sơn Tùng M-TP"], "author_ids": ["c"]},       # trùng tên
            {"song_id": 5, "title": "", "authors": ["C"], "author_ids": ["c"]},
        ]
        report = export_c2c.readiness(rows)
        self.assertEqual(report["records"], 1)
        self.assertEqual(report["duplicate_title_author"], 1)
        self.assertEqual(report["not_single_author"], 1)
        self.assertEqual(report["dropped_name_collision_keys"], 1)
        self.assertEqual(report["no_title"], 1)


if __name__ == "__main__":
    unittest.main()
