"""
Kiểm thử offline cho crawler chung (không gửi request ra Internet).

Chạy từ thư mục web-crawler:
    python -m unittest discover -s tests -v
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.robotparser import RobotFileParser

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bs4 import BeautifulSoup  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from parser import extract_links, extract_page_data, extract_title, read_meta_robots  # noqa: E402
from robots import normalize_robots  # noqa: E402
from song_parser import clean_lyrics, extract_song_data, title_parts  # noqa: E402
from url_filter import check_url_rules, is_song_url, normalize_url  # noqa: E402
from url_frontier import URLFrontier  # noqa: E402

SPOTIFY_TRACK = "https://open.spotify.com/track/0XaY8eVU9eZO4OdIV6agx1"


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# Task 5 + 7: chuẩn hoá URL
# ---------------------------------------------------------------------------
class NormalizeUrlTests(unittest.TestCase):

    def test_relative_url_becomes_absolute_without_fragment_and_query(self):
        url = normalize_url("https://nhac.vn/bai-hat/abc-so1", "/nghe-si/son-tung-m-tp-atbQz5?st=1#top")
        self.assertEqual(url, "https://nhac.vn/nghe-si/son-tung-m-tp-atbQz5")

    def test_host_aliases_and_https_are_unified(self):
        self.assertEqual(normalize_url("https://nhac.vn/", "https://www.nhac.vn/"), "https://nhac.vn/")
        self.assertEqual(normalize_url("https://nhac.vn/", "http://nhaccuatui.com/song/abc"),
                         "https://www.nhaccuatui.com/song/abc")
        self.assertEqual(normalize_url("https://nhac.vn/", "https://www.hopamchuan.com/song/8926/lac-troi/"),
                         "https://hopamchuan.com/song/8926/lac-troi")

    def test_old_url_formats_are_rewritten(self):
        self.assertEqual(
            normalize_url("https://www.nhaccuatui.com/",
                          "https://www.nhaccuatui.com/bai-hat/lac-troi-son-tung-m-tp.3tvGi3UEZLVT.html"),
            "https://www.nhaccuatui.com/song/3tvGi3UEZLVT")
        self.assertEqual(
            normalize_url("https://open.spotify.com/", "https://open.spotify.com/intl-vi/track/0XaY8eVU9eZO4OdIV6agx1?si=x"),
            SPOTIFY_TRACK)

    def test_non_web_links_are_ignored(self):
        for href in ("mailto:a@b.vn", "javascript:void(0)", "tel:0123", "#top", "", None):
            with self.subTest(href=href):
                self.assertIsNone(normalize_url("https://nhac.vn/", href))

    def test_outside_domain_keeps_its_scheme(self):
        self.assertEqual(normalize_url("https://nhac.vn/", "http://example.com/a/"), "http://example.com/a")


# ---------------------------------------------------------------------------
# Task 5, 6, 7: luật lọc URL
# ---------------------------------------------------------------------------
class UrlRuleTests(unittest.TestCase):

    def test_every_seed_url_is_valid(self):
        for seed in config.SEED_URLS:
            with self.subTest(seed=seed):
                self.assertEqual(check_url_rules(normalize_url(seed, seed), 0), (True, "VALID"))

    def test_depth_limit(self):
        seed = config.SEED_URLS[0]
        self.assertEqual(check_url_rules(seed, config.MAX_DEPTH), (True, "VALID"))
        self.assertEqual(check_url_rules(seed, config.MAX_DEPTH + 1), (False, "MAX_DEPTH"))

    def test_rejection_reasons(self):
        cases = {
            "ftp://nhac.vn/": "INVALID_PROTOCOL",
            "https://example.com/": "OUTSIDE_DOMAIN",
            "https://m.nhaccuatui.com/song/abc": "OUTSIDE_DOMAIN",   # sub-domain khác bị loại
            "https://nhac.vn/static/logo.png": "NON_HTML_RESOURCE",
            "https://hopamchuan.com/files/app.js": "NON_HTML_RESOURCE",
            "https://nhac.vn/tag/son-tung": "NOT_MUSIC_PAGE",
            "https://hopamchuan.com/profile/abc": "NOT_MUSIC_PAGE",
            "https://open.spotify.com/embed/track/0XaY8eVU9eZO4OdIV6agx1": "NOT_MUSIC_PAGE",
        }
        for url, reason in cases.items():
            with self.subTest(url=url):
                self.assertEqual(check_url_rules(url, 0), (False, reason))

    def test_song_page_detection(self):
        songs = [
            "https://nhac.vn/bai-hat/chung-ta-cua-hien-tai-son-tung-m-tp-solpo1X",
            "https://www.nhaccuatui.com/song/3tvGi3UEZLVT",
            SPOTIFY_TRACK,
            "https://hopamchuan.com/song/8926/lac-troi",
        ]
        not_songs = [
            "https://nhac.vn/nghe-si/son-tung-m-tp-atbQz5",
            "https://www.nhaccuatui.com/playlist/rRmPsafCqIXD",
            "https://open.spotify.com/album/1V77kA4O1MlAUwfyBONfp1",
            "https://hopamchuan.com/artist/21205/son-tung-m-tp",
        ]
        for url in songs:
            with self.subTest(url=url):
                self.assertTrue(is_song_url(url))
        for url in not_songs:
            with self.subTest(url=url):
                self.assertFalse(is_song_url(url))


# ---------------------------------------------------------------------------
# Task 2 + 7: URL Frontier (BFS, chống trùng)
# ---------------------------------------------------------------------------
class FrontierTests(unittest.TestCase):

    def test_fifo_order_gives_breadth_first_search(self):
        frontier = URLFrontier()
        frontier.add("a", 0)
        frontier.add("b", 0)
        url, depth = frontier.pop()
        frontier.mark_visited(url)
        frontier.add("a1", depth + 1)          # link tìm thấy ở trang a
        order = [url] + [frontier.pop()[0] for _ in range(len(frontier))]
        self.assertEqual(order, ["a", "b", "a1"])

    def test_duplicates_are_not_added(self):
        frontier = URLFrontier()
        self.assertTrue(frontier.add("a", 0))
        self.assertFalse(frontier.add("a", 0))          # đang trong hàng đợi
        frontier.mark_visited(frontier.pop()[0])
        self.assertFalse(frontier.add("a", 1))          # đã crawl
        self.assertTrue(frontier.is_empty())


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
class RobotsTests(unittest.TestCase):

    @staticmethod
    def parser_for(text):
        parser = RobotFileParser()
        parser.parse(normalize_robots(text))
        return parser

    def test_allow_root_first_does_not_hide_disallow(self):
        parser = self.parser_for("User-agent: *\nAllow: /\nDisallow: /embed/\n")
        self.assertFalse(parser.can_fetch(config.BOT_NAME, "https://open.spotify.com/embed/track/x"))
        self.assertTrue(parser.can_fetch(config.BOT_NAME, SPOTIFY_TRACK))

    def test_duplicate_user_agent_groups_are_merged(self):
        parser = self.parser_for("User-agent: *\nAllow: /\n\nUser-agent: *\nDisallow: /api/\n")
        self.assertFalse(parser.can_fetch(config.BOT_NAME, "https://www.nhaccuatui.com/api/song"))
        self.assertTrue(parser.can_fetch(config.BOT_NAME, "https://www.nhaccuatui.com/song/abc"))

    def test_rules_for_other_bots_do_not_apply(self):
        parser = self.parser_for("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        self.assertTrue(parser.can_fetch(config.BOT_NAME, "https://nhac.vn/"))
        self.assertFalse(parser.can_fetch("BadBot", "https://nhac.vn/"))


# ---------------------------------------------------------------------------
# Task 4 + 5: lấy thông tin trang và hyperlink
# ---------------------------------------------------------------------------
class ParserTests(unittest.TestCase):

    def test_page_record_has_required_fields(self):
        page = extract_page_data("https://nhac.vn/", 0, 200,
                                 soup_of("<title>Nhac.vn - nghe nhac</title><p>Xin chao</p>"))
        self.assertEqual(set(page), {"url", "domain", "title", "content", "depth", "status_code", "crawled_at"})
        self.assertEqual(page["domain"], "nhac.vn")
        self.assertIn("Xin chao", page["content"])

    def test_generic_title_falls_back_to_og_title(self):
        soup = soup_of('<title>Nhac.vn</title><meta property="og:title" content="Bai Thu - Ca Si A">')
        self.assertEqual(extract_title(soup), "Bai Thu - Ca Si A")

    def test_a_href_links_are_normalized_and_deduplicated(self):
        html = ('<a href="/nghe-si/son-tung-m-tp-atbQz5">1</a>'
                '<a href="https://www.nhac.vn/nghe-si/son-tung-m-tp-atbQz5#x">2</a>'
                '<a href="mailto:x@y.vn">3</a>'
                '<a href="https://facebook.com/page">4</a>')
        links = extract_links(soup_of(html), html, "https://nhac.vn/")
        self.assertEqual(links, ["https://nhac.vn/nghe-si/son-tung-m-tp-atbQz5", "https://facebook.com/page"])

    def test_spotify_meta_music_links(self):
        html = f'<meta property="music:song" content="{SPOTIFY_TRACK}">'
        links = extract_links(soup_of(html), html, "https://open.spotify.com/album/1V77kA4O1MlAUwfyBONfp1")
        self.assertEqual(links, [SPOTIFY_TRACK])

    def test_links_inside_script_json_keep_only_music_pages(self):
        html = ('<script>{"song":"\\/song\\/3tvGi3UEZLVT","js":"/static/app.js",'
                '"pl":"https:\\/\\/www.nhaccuatui.com\\/playlist\\/rRmPsafCqIXD"}</script>')
        links = extract_links(soup_of(html), html, "https://www.nhaccuatui.com/")
        self.assertIn("https://www.nhaccuatui.com/song/3tvGi3UEZLVT", links)
        self.assertIn("https://www.nhaccuatui.com/playlist/rRmPsafCqIXD", links)
        self.assertFalse(any(link.endswith(".js") for link in links))

    def test_meta_robots_and_x_robots_tag(self):
        self.assertEqual(read_meta_robots(soup_of(""))[:2], (True, True))
        self.assertEqual(read_meta_robots(soup_of('<meta name="robots" content="noindex, nofollow">'))[:2],
                         (False, False))
        self.assertEqual(read_meta_robots(soup_of(""), {"X-Robots-Tag": "noindex"})[:2], (False, True))


# ---------------------------------------------------------------------------
# Bảng songs: tách title / artist / album / genre / lyrics
# ---------------------------------------------------------------------------
class SongParserTests(unittest.TestCase):

    def test_site_names_and_noise_are_removed_from_titles(self):
        self.assertEqual(title_parts("Hợp âm Lạc Trôi - Sơn Tùng M-TP (Hợp âm cơ bản) - Hợp Âm Chuẩn"),
                         ["Lạc Trôi", "Sơn Tùng M-TP"])
        self.assertEqual(title_parts("Lạc Trôi - Sơn Tùng M-TP - mp3 download | lyric - NhacCuaTui"),
                         ["Lạc Trôi", "Sơn Tùng M-TP"])

    def test_chords_and_heading_are_removed_from_lyrics(self):
        text = "Lời bài hát\n[Am]Đây là câu hát thử nghiệm số một\n[F]Và đây là câu hát [G]thử thứ hai"
        self.assertEqual(clean_lyrics(text),
                         "Đây là câu hát thử nghiệm số một\nVà đây là câu hát thử thứ hai")

    def test_song_page_with_labels_and_lyrics(self):
        html = """<title>Bài Hát Thử - Ca Sĩ A - Nhac.vn</title>
        <h1>Bài Hát Thử</h1>
        <div><span>Thể loại:</span> <a href="/the-loai/pop">Nhạc Pop</a></div>
        <h2>Lời bài hát</h2>
        <div class="lyric">Đây là câu hát thử nghiệm số một<br>Và đây là câu hát thử nghiệm thứ hai</div>"""
        song = extract_song_data("https://nhac.vn/bai-hat/bai-hat-thu-ca-si-a-so1AbC", soup_of(html), "t")
        self.assertEqual((song["title"], song["artist"], song["album"], song["genre"]),
                         ("Bài Hát Thử", "Ca Sĩ A", None, "Nhạc Pop"))
        self.assertTrue(song["lyrics"].startswith("Đây là câu hát thử nghiệm số một"))

    def test_spotify_track_reads_description_and_has_no_lyrics(self):
        html = """<title>Bài Thử - song and lyrics by Ca Sĩ B | Spotify</title>
        <meta property="og:type" content="music.song"><meta property="og:title" content="Bài Thử">
        <meta property="og:description" content="Ca Sĩ B · Album C · Song · 2020">"""
        song = extract_song_data(SPOTIFY_TRACK, soup_of(html), "t")
        self.assertEqual((song["title"], song["artist"], song["album"], song["lyrics"]),
                         ("Bài Thử", "Ca Sĩ B", "Album C", None))

    def test_page_without_title_is_not_a_song(self):
        self.assertIsNone(extract_song_data("https://nhac.vn/bai-hat/x-so1", soup_of("<p></p>"), "t"))


# ---------------------------------------------------------------------------
# Task 8: SQLite (database tạm, không đụng data/crawler.db)
# ---------------------------------------------------------------------------
class DatabaseTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, "test.db")
        database.create_tables(reset=True)

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def query(self, sql):
        conn = sqlite3.connect(database.DB_PATH)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_schema(self):
        columns = {table: [row[1] for row in self.query(f"PRAGMA table_info({table})")]
                   for table in ("pages", "links", "songs")}
        self.assertEqual(columns["pages"],
                         ["id", "url", "domain", "title", "content", "depth", "status_code", "crawled_at"])
        self.assertEqual(columns["links"], ["id", "source_url", "target_url"])
        self.assertEqual(columns["songs"],
                         ["id", "url", "title", "artist", "album", "genre", "lyrics", "crawled_at"])

    def test_no_duplicate_pages_links_or_songs(self):
        page = {"url": "https://nhac.vn/", "domain": "nhac.vn", "title": "t", "content": "c",
                "depth": 1, "status_code": 200, "crawled_at": "t"}
        database.save_page(page)
        database.save_page(dict(page, depth=0, title="t2"))
        self.assertEqual(self.query("SELECT COUNT(*), MIN(depth), MAX(title) FROM pages"), [(1, 0, "t2")])

        self.assertEqual(database.save_links("a", ["b", "c", "b"]), 2)
        self.assertEqual(database.save_links("a", ["b"]), 0)

        song = {"url": SPOTIFY_TRACK, "title": "x", "artist": None, "album": None,
                "genre": None, "lyrics": None, "crawled_at": "t"}
        database.save_song(song)
        database.save_song(dict(song, artist="y"))
        self.assertEqual(self.query("SELECT COUNT(*), MAX(artist) FROM songs"), [(1, "y")])


# ---------------------------------------------------------------------------
# Dữ liệu đã nộp: data/crawler.db và data/crawl_summary.txt (chỉ đọc)
# ---------------------------------------------------------------------------
class CommittedDataTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(f"file:{PROJECT_DIR / 'data' / 'crawler.db'}?mode=ro", uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def count(self, sql):
        return self.conn.execute(sql).fetchone()[0]

    def test_pages_respect_crawl_limits(self):
        self.assertLessEqual(self.count("SELECT COUNT(*) FROM pages"), config.MAX_PAGES)
        for domain, pages in self.conn.execute("SELECT domain, COUNT(*) FROM pages GROUP BY domain"):
            with self.subTest(domain=domain):
                self.assertIn(domain, config.ALLOWED_DOMAINS)
                self.assertLessEqual(pages, config.MAX_PAGES_PER_DOMAIN)
        self.assertEqual(self.count(f"SELECT COUNT(*) FROM pages WHERE depth > {config.MAX_DEPTH}"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM pages WHERE status_code != 200"), 0)

    def test_every_saved_page_passes_the_url_rules(self):
        for url, depth in self.conn.execute("SELECT url, depth FROM pages"):
            with self.subTest(url=url):
                self.assertEqual(check_url_rules(url, depth), (True, "VALID"))

    def test_songs_come_from_saved_song_pages(self):
        self.assertEqual(self.count("SELECT COUNT(*) FROM songs WHERE url NOT IN (SELECT url FROM pages)"), 0)
        for (url,) in self.conn.execute("SELECT url FROM songs"):
            with self.subTest(url=url):
                self.assertTrue(is_song_url(url))

    def test_summary_file_matches_database(self):
        summary = (PROJECT_DIR / "data" / "crawl_summary.txt").read_text(encoding="utf-8")

        def number(label):
            return int(re.search(rf"^{re.escape(label)}\s*:\s*(\d+)", summary, re.M).group(1))

        self.assertEqual(number("Pages Saved (DB)"), self.count("SELECT COUNT(*) FROM pages"))
        self.assertEqual(number("Links Saved (DB)"), self.count("SELECT COUNT(*) FROM links"))
        self.assertEqual(number("Songs Saved (DB)"), self.count("SELECT COUNT(*) FROM songs"))


if __name__ == "__main__":
    unittest.main()
