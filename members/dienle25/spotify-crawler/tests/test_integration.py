"""Real requests against a local fixture server, never against Spotify.

These tests verify the complete HTTP -> BeautifulSoup -> SQLite pipeline.
All content is synthetic, all databases are temporary, and each server is
bound to loopback. A separate live audit is required for real Spotify results.
"""

from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
import requests

from config import Config
from crawler import Crawler


def html(body, title="Local integration fixture"):
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"


class LocalCrawlerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "integration.db"
        self.requests = []
        self.routes = {
            "/robots.txt": (200, {"Content-Type": "text/plain"}, "User-agent: *\nDisallow: /blocked\n"),
            "/seed": (200, {}, html(
                '<h1>Fixture seed</h1><a href="/a#title">A</a><a href="/b">B</a>'
                '<a href="/a">Duplicate A</a><a href="/blocked">Blocked</a>'
                '<a href="/song.mp3">Excluded media</a>'
                '<a href="http://outside.invalid/page">External</a>')),
            "/a": (200, {}, html('<p>Page A</p><a href="/a1">A1</a><a href="/seed">Cycle</a>')),
            "/b": (200, {}, html('<p>Page B</p><a href="/b1">B1</a>')),
            "/a1": (200, {}, html('<p>Grandchild A1</p><a href="/too-deep">Depth 3</a>')),
            "/b1": (200, {}, html("<p>Grandchild B1</p>")),
            "/blocked": (200, {}, html("Must never be requested")),
        }
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state.requests.append(self.path)
                status, headers, body = state.routes.get(self.path, (404, {}, "Not found"))
                body = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", headers.get("Content-Type", "text/html; charset=utf-8"))
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    if name.lower() not in ("content-type", "content-length"):
                        self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Rejecting media or an oversized response may close its socket early.
                    pass

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_crawler(self, seeds=("/seed",), **overrides):
        settings = dict(seed_urls=tuple(self.base + path for path in seeds), allowed_domains=("127.0.0.1",),
                        max_depth=2, max_pages=20, request_timeout=2, crawl_delay=0,
                        user_agent="SEG301-VietnameseMusicCrawler-Test/1.0", db_path=self.db_path)
        settings.update(overrides)
        return Crawler(Config(**settings)).run()

    def page_rows(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute("SELECT url, depth, status_code FROM pages ORDER BY id").fetchall()

    def test_real_http_bfs_and_sqlite_with_duplicate_cycle_filtering(self):
        result = self.run_crawler()
        self.assertEqual([path for path in self.requests if path != "/robots.txt"],
                         ["/seed", "/a", "/b", "/a1", "/b1"])
        self.assertEqual([row[1] for row in self.page_rows()], [0, 1, 1, 2, 2])
        self.assertEqual(result["pages_crawled"], 5)
        self.assertEqual(self.requests.count("/robots.txt"), 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), COUNT(DISTINCT url) FROM pages").fetchone(), (5, 5))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM links WHERE source_url = ? AND target_url = ?",
                (self.base + "/seed", self.base + "/a")).fetchone()[0], 1)

    def test_max_depth_zero_fetches_seed_only(self):
        result = self.run_crawler(max_depth=0)
        self.assertEqual(self.requests, ["/robots.txt", "/seed"])
        self.assertEqual(result["pages_crawled"], 1)
        self.assertEqual(self.page_rows(), [(self.base + "/seed", 0, 200)])

    def test_max_pages_bounds_successful_pages(self):
        result = self.run_crawler(max_pages=2)
        self.assertEqual(self.requests, ["/robots.txt", "/seed", "/a"])
        self.assertEqual(result["pages_crawled"], 2)
        self.assertEqual(len(self.page_rows()), 2)

    def test_robots_denial_sends_no_content_request(self):
        result = self.run_crawler(seeds=("/blocked",))
        self.assertEqual(self.requests, ["/robots.txt"])
        self.assertEqual(result["pages_crawled"], 0)
        self.assertEqual(self.page_rows(), [])

    def test_robots_unavailable_fails_closed(self):
        self.routes["/robots.txt"] = (503, {"Content-Type": "text/plain"}, "Unavailable")
        result = self.run_crawler()
        self.assertEqual(self.requests, ["/robots.txt"])
        self.assertEqual(result["pages_crawled"], 0)

    def test_404_and_500_are_recorded_and_other_seed_continues(self):
        self.routes["/fail"] = (500, {}, "Server error")
        result = self.run_crawler(seeds=("/missing", "/fail", "/b1"))
        self.assertEqual(result["pages_crawled"], 1)
        self.assertGreaterEqual(result["failed_requests"], 2)
        self.assertEqual(self.page_rows(), [(self.base + "/b1", 0, 200)])

    def test_403_stops_origin_without_retrying(self):
        self.routes["/forbidden"] = (403, {}, "Forbidden")
        result = self.run_crawler(seeds=("/forbidden", "/b1"))
        self.assertEqual(self.requests, ["/robots.txt", "/forbidden"])
        self.assertEqual(result["pages_crawled"], 0)

    def test_429_stops_origin_and_does_not_retry(self):
        self.routes["/rate-limit"] = (429, {"Retry-After": "300"}, "Too many requests")
        result = self.run_crawler(seeds=("/rate-limit", "/b1"))
        self.assertEqual(self.requests, ["/robots.txt", "/rate-limit"])
        self.assertEqual(result["pages_crawled"], 0)

    def test_redirect_to_robots_denied_target_is_not_followed(self):
        self.routes["/redirect"] = (302, {"Location": "/blocked"}, "Redirect")
        result = self.run_crawler(seeds=("/redirect",))
        self.assertEqual(self.requests, ["/robots.txt", "/redirect"])
        self.assertEqual(result["pages_crawled"], 0)

    def test_redirect_outside_allowlist_is_not_followed(self):
        self.routes["/redirect"] = (302, {"Location": f"http://localhost:{self.server.server_port}/b1"}, "Redirect")
        result = self.run_crawler(seeds=("/redirect",))
        self.assertEqual(self.requests, ["/robots.txt", "/redirect"])
        self.assertEqual(result["pages_crawled"], 0)

    def test_non_html_content_is_not_parsed_or_stored(self):
        self.routes["/binary"] = (200, {"Content-Type": "audio/mpeg"}, b"Synthetic test bytes, not audio")
        result = self.run_crawler(seeds=("/binary",))
        self.assertEqual(result["pages_crawled"], 0)
        self.assertEqual(self.page_rows(), [])

    def test_response_size_limit_prevents_storage(self):
        self.routes["/large"] = (200, {}, html("X" * 1000))
        result = self.run_crawler(seeds=("/large",), max_bytes=128)
        self.assertEqual(result["pages_crawled"], 0)
        self.assertEqual(self.page_rows(), [])

    def test_max_requests_counts_robots_and_content_requests(self):
        result = self.run_crawler(max_requests=2)
        self.assertLessEqual(len(self.requests), 2)
        self.assertLessEqual(result["pages_crawled"], 1)

    def test_queued_redirect_target_is_only_fetched_once(self):
        self.routes['/redirect'] = (302, {'Location': '/b1'}, '')
        result = self.run_crawler(seeds=('/redirect', '/b1'))
        self.assertEqual(self.requests, ['/robots.txt', '/redirect', '/b1'])
        self.assertEqual(result['pages_crawled'], 1)

    def test_redirect_loop_stops_without_repeated_request(self):
        self.routes['/r1'] = (302, {'Location': '/r2'}, '')
        self.routes['/r2'] = (302, {'Location': '/r1'}, '')
        result = self.run_crawler(seeds=('/r1',))
        self.assertEqual(self.requests, ['/robots.txt', '/r1', '/r2'])
        self.assertEqual(result['pages_crawled'], 0)

    def test_longest_robots_rule_denies_even_with_allow_root_first(self):
        self.routes['/robots.txt'] = (200, {'Content-Type': 'text/plain'},
                                      'User-agent: *\nAllow: /\nDisallow: /blocked')
        self.run_crawler(seeds=('/blocked',))
        self.assertEqual(self.requests, ['/robots.txt'])

    def test_challenge_200_stops_origin_without_storage(self):
        self.routes['/challenge'] = (200, {}, html('Verify you are human', 'Just a moment'))
        result = self.run_crawler(seeds=('/challenge', '/b1'))
        self.assertEqual(self.requests, ['/robots.txt', '/challenge'])
        self.assertEqual(result['pages_crawled'], 0)

    def test_empty_javascript_shell_is_not_reported_as_pass(self):
        self.routes['/shell'] = (200, {}, html('<div id="root"></div><script>render()</script>'))
        result = self.run_crawler(seeds=('/shell',))
        self.assertEqual(result['page_diagnostics'][0]['html_crawlability'], 'PARTIAL')
        self.assertEqual(result['status'], 'PARTIAL')

    def test_timeout_records_failure_and_continues_other_seed(self):
        real_get = requests.Session.get
        def get(session, url, **kwargs):
            if url.endswith('/timeout'):
                raise requests.Timeout('synthetic timeout')
            return real_get(session, url, **kwargs)
        with patch.object(requests.Session, 'get', get):
            result = self.run_crawler(seeds=('/timeout', '/b1'))
        self.assertEqual(result['failed_requests'], 1)
        self.assertEqual(result['pages_crawled'], 1)

    def test_connection_error_records_failure(self):
        with patch.object(requests.Session, 'get', side_effect=requests.ConnectionError('synthetic offline')):
            result = self.run_crawler()
        self.assertEqual(result['failed_requests'], 1)
        self.assertEqual(result['pages_crawled'], 0)

    def test_delay_used_between_same_origin_requests(self):
        sleeper = Mock()
        config = Config(seed_urls=(self.base + '/b1',), allowed_domains=('127.0.0.1',),
                        db_path=self.db_path, crawl_delay=2)
        Crawler(config, sleep=sleeper).run()
        sleeper.assert_called()
        self.assertGreater(sleeper.call_args.args[0], 0)


class SpotifyPolicyRegressionTests(unittest.TestCase):
    def test_spotify_policy_block_never_sends_http_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Mock()
            config = Config(seed_urls=("https://open.spotify.com/artist/policy-test",),
                            allowed_domains=("open.spotify.com",), max_depth=1, max_pages=1,
                            request_timeout=2, crawl_delay=0,
                            user_agent="SEG301-VietnameseMusicCrawler-Test/1.0",
                            db_path=Path(directory) / "policy.db")
            result = Crawler(config, session=session).run()
            self.assertEqual(result["pages_crawled"], 0)
            session.get.assert_not_called()
            with closing(sqlite3.connect(config.db_path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
