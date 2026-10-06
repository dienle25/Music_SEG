"""Unit tests use synthetic HTML and temporary SQLite files only."""

from contextlib import closing
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from database import Database
from parser import extract_links, parse_page
from url_frontier import URLFrontier
from urls import is_allowed_url, normalize_url


FIXTURE = Path(__file__).parent / "fixtures" / "artist.html"


class URLTests(unittest.TestCase):
    def test_resolves_relative_url_and_removes_fragment(self):
        self.assertEqual(
            normalize_url("../album/a#tracks", "https://open.spotify.com/artist/b"),
            "https://open.spotify.com/album/a",
        )

    def test_normalizes_scheme_host_and_default_port(self):
        self.assertEqual(
            normalize_url("HTTPS://OPEN.SPOTIFY.COM:443/artist/a#about"),
            "https://open.spotify.com/artist/a",
        )

    def test_rejects_non_web_schemes_and_invalid_urls(self):
        for url in ("mailto:a@example.org", "tel:+84123", "javascript:void(0)",
                    "data:text/html,hello", "ftp://open.spotify.com/a", "https://"):
            with self.subTest(url=url):
                self.assertIsNone(normalize_url(url))

    def test_rejects_credentials_and_invalid_port(self):
        for url in ("https://user:secret@open.spotify.com/", "https://open.spotify.com:bad/"):
            with self.subTest(url=url):
                self.assertIsNone(normalize_url(url))

    def test_spotify_domain_filter_is_exact(self):
        allowed = ("open.spotify.com",)
        self.assertTrue(is_allowed_url("https://open.spotify.com/artist/a", allowed))
        for url in ("https://evilopen.spotify.com/a", "https://open.spotify.com.evil.test/a",
                    "https://accounts.spotify.com/a", "https://example.org/a"):
            with self.subTest(url=url):
                self.assertFalse(is_allowed_url(url, allowed))

    def test_non_html_resource_extensions_are_filtered(self):
        for extension in ("jpg", "jpeg", "png", "webp", "gif", "svg", "css", "js",
                          "zip", "pdf", "mp3", "wav", "flac", "m4a", "mp4", "webm"):
            with self.subTest(extension=extension):
                self.assertFalse(is_allowed_url(
                    f"https://open.spotify.com/file.{extension}?download=1", ("open.spotify.com",)
                ))

    def test_localhost_port_does_not_change_domain(self):
        self.assertTrue(is_allowed_url("http://127.0.0.1:54321/seed", ("127.0.0.1",)))


class FrontierTests(unittest.TestCase):
    def test_fifo_order_produces_breadth_first_traversal(self):
        frontier = URLFrontier()
        self.assertTrue(frontier.add("https://example.org/seed", 0))
        self.assertEqual(frontier.pop(), ("https://example.org/seed", 0))
        frontier.add("https://example.org/a", 1)
        frontier.add("https://example.org/b", 1)
        self.assertEqual(frontier.pop(), ("https://example.org/a", 1))
        frontier.add("https://example.org/a1", 2)
        self.assertEqual(frontier.pop(), ("https://example.org/b", 1))
        self.assertEqual(frontier.pop(), ("https://example.org/a1", 2))

    def test_duplicate_queued_and_visited_urls_are_rejected(self):
        frontier = URLFrontier()
        url = "https://example.org/seed"
        self.assertTrue(frontier.add(url, 0))
        self.assertFalse(frontier.add(url, 1))
        self.assertIn(url, frontier.queued_urls)
        frontier.pop()
        self.assertNotIn(url, frontier.queued_urls)
        self.assertIn(url, frontier.visited_urls)
        self.assertFalse(frontier.add(url, 2))


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.url = "https://open.spotify.com/artist/local-test"

    def test_extracts_required_assignment_fields_and_unicode(self):
        record = parse_page(self.html, self.url, 2, status_code=200)
        self.assertTrue({"url", "domain", "title", "content", "depth", "status_code", "crawled_at"}
                        <= set(record))
        self.assertEqual(record["url"], self.url)
        self.assertEqual(record["domain"], "open.spotify.com")
        self.assertEqual(record["title"], "Nghệ sĩ thử nghiệm | Local fixture")
        self.assertIn("Đây là HTML kiểm thử", record["content"])
        self.assertEqual(record["depth"], 2)
        self.assertEqual(record["status_code"], 200)
        self.assertIsNotNone(datetime.fromisoformat(record["crawled_at"].replace("Z", "+00:00")))

    def test_omits_script_and_style_text(self):
        content = parse_page(self.html, self.url, 0)["content"]
        self.assertNotIn("fixtureSecret", content)
        self.assertNotIn("display: none", content)

    def test_missing_title_does_not_crash(self):
        record = parse_page("<html><body><p>Nhạc Việt</p></body></html>", self.url, 0)
        self.assertEqual(record["title"], "")
        self.assertIn("Nhạc Việt", record["content"])

    def test_links_are_resolved_deduplicated_and_valid_http(self):
        links = extract_links(self.html, self.url)
        self.assertEqual(links.count("https://open.spotify.com/album/local-a"), 1)
        self.assertIn("https://open.spotify.com/track/local-song", links)
        self.assertIn("https://example.org/reference", links)
        self.assertTrue(all(url.startswith(("https://", "http://")) for url in links))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.page = parse_page("<title>Fixture</title><p>Unit test</p>", "https://example.org/", 0)

    def test_schema_has_required_pages_and_links_columns(self):
        with Database(self.path):
            pass
        with closing(sqlite3.connect(self.path)) as connection:
            pages = {row[1] for row in connection.execute("PRAGMA table_info(pages)")}
            links = {row[1] for row in connection.execute("PRAGMA table_info(links)")}
        self.assertTrue({"id", "url", "domain", "title", "content", "depth", "status_code", "crawled_at"} <= pages)
        self.assertTrue({"id", "source_url", "target_url"} <= links)

    def test_persists_pages_and_deduplicates_records(self):
        with Database(self.path) as database:
            database.save_page(self.page)
            database.save_page(self.page)
            database.save_links(self.page["url"], ["https://example.org/a", "https://example.org/a"])
            counts = database.counts()
            self.assertEqual(counts["pages"], 1)
            self.assertEqual(counts["links"], 1)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), COUNT(DISTINCT url) FROM pages").fetchone(), (1, 1))
            self.assertEqual(connection.execute("SELECT source_url, target_url FROM links").fetchone(),
                             ("https://example.org/", "https://example.org/a"))

    def test_parameterized_sql_preserves_quoted_unicode_text(self):
        self.page["title"] = "Nhạc Việt 'quoted'; DROP TABLE pages; --"
        with Database(self.path) as database:
            database.save_page(self.page)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT title FROM pages").fetchone()[0], self.page["title"])


if __name__ == "__main__":
    unittest.main()
