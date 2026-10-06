import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from config import (
    ALLOWED_DOMAINS,
    CRAWL_DELAY,
    MAX_DEPTH,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from database import Database
from parser import (
    extract_links,
    extract_page_info,
    is_allowed_url,
    normalize_url,
)
from url_frontier import URLFrontier


class FocusedCrawler:
    def __init__(self, seed_urls, db_path):
        self.seed_urls = seed_urls
        self.db = Database(db_path)

        self.frontier = URLFrontier()
        self.visited = set()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        self.robots = {}
        self.last_request_time = {}

        self.stats = {
            "pages_crawled": 0,
            "unique_urls_discovered": 0,
            "skipped_urls": 0,
            "failed_requests": 0,
            "depth_counts": Counter(),
            "status_counts": Counter(),
        }

        self._add_seeds()

    def _add_seeds(self):
        for seed in self.seed_urls:
            normalized = normalize_url(seed, seed)

            if normalized and is_allowed_url(normalized, ALLOWED_DOMAINS):
                if self.frontier.add(normalized, 0):
                    self.stats["unique_urls_discovered"] += 1

    def _get_robots(self, url):
        """Load robots.txt once per domain."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if origin in self.robots:
            return self.robots[origin]

        robots_url = origin + "/robots.txt"

        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            response = self.session.get(
                robots_url,
                timeout=REQUEST_TIMEOUT,
            )

            # A missing robots.txt is treated as no explicit restriction.
            if response.status_code == 404:
                parser.parse([])
                self.robots[origin] = parser
                return parser

            response.raise_for_status()
            parser.parse(response.text.splitlines())

            self.robots[origin] = parser
            return parser

        except requests.RequestException:
            # Fail closed if robots.txt cannot be obtained.
            # This avoids accidentally crawling a site when its
            # crawling rules cannot be checked.
            self.robots[origin] = None
            return None

    def _robots_allowed(self, url):
        parser = self._get_robots(url)

        if parser is None:
            return False

        return parser.can_fetch(USER_AGENT, url)

    def _respect_delay(self, url):
        domain = urlparse(url).netloc.lower()
        previous = self.last_request_time.get(domain)

        if previous is not None:
            elapsed = time.time() - previous
            if elapsed < CRAWL_DELAY:
                time.sleep(CRAWL_DELAY - elapsed)

        self.last_request_time[domain] = time.time()

    def _record_skip(self):
        self.stats["skipped_urls"] += 1

    def _save_response(self, url, depth, response, elapsed):
        parsed = urlparse(url)
        status_code = response.status_code
        self.stats["status_counts"][status_code] += 1

        title = ""
        content = ""

        content_type = response.headers.get("Content-Type", "").lower()
        is_html = "text/html" in content_type or "application/xhtml+xml" in content_type

        if is_html:
            title, content = extract_page_info(response.text)

        crawled_at = datetime.now(timezone.utc).isoformat()

        self.db.save_page(
            url=url,
            domain=parsed.netloc.lower(),
            title=title,
            content=content,
            depth=depth,
            status_code=status_code,
            crawled_at=crawled_at,
        )

        self.stats["pages_crawled"] += 1
        self.stats["depth_counts"][depth] += 1

        print(f"[Crawl #{self.stats['pages_crawled']:03d}]")
        print(f"Depth : {depth}")
        print(f"URL   : {url}")
        print(f"Status: {status_code}")
        print(f"Time  : {elapsed:.2f} sec")

        if is_html:
            links = extract_links(response.text, url)
            print(f"Links : {len(links)}")

            self._process_links(url, depth, links)
        else:
            print("Links : 0 (non-HTML response)")

        print()

    def _process_links(self, source_url, current_depth, links):
        if current_depth >= MAX_DEPTH:
            return

        next_depth = current_depth + 1

        for link in links:
            if not is_allowed_url(link, ALLOWED_DOMAINS):
                self._record_skip()
                continue

            if link in self.visited or link in self.frontier.queued:
                self._record_skip()
                continue

            # Check robots.txt before adding a discovered URL.
            if not self._robots_allowed(link):
                self._record_skip()
                continue

            self.db.save_link(source_url, link)

            if self.frontier.add(link, next_depth):
                self.stats["unique_urls_discovered"] += 1

    def crawl(self):
        print("=" * 63)
        print("                 FOCUSED WEB CRAWLER")
        print("=" * 63)

        while (
            not self.frontier.empty()
            and self.stats["pages_crawled"] < MAX_PAGES
        ):
            item = self.frontier.pop()

            if item is None:
                break

            url, depth = item

            if url in self.visited:
                self._record_skip()
                continue

            if depth > MAX_DEPTH:
                self._record_skip()
                continue

            if not is_allowed_url(url, ALLOWED_DOMAINS):
                self._record_skip()
                continue

            if not self._robots_allowed(url):
                self._record_skip()
                continue

            self.visited.add(url)

            self._respect_delay(url)

            start = time.time()

            try:
                response = self.session.get(
                    url,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )

                elapsed = time.time() - start

                # Validate the final redirect destination too.
                final_url = normalize_url(response.url, response.url)

                if not final_url or not is_allowed_url(
                    final_url, ALLOWED_DOMAINS
                ):
                    self._record_skip()
                    continue

                if final_url != url:
                    if not self._robots_allowed(final_url):
                        self._record_skip()
                        continue

                    if final_url in self.visited:
                        self._record_skip()
                        continue

                self._save_response(
                    final_url,
                    depth,
                    response,
                    elapsed,
                )

            except requests.RequestException as exc:
                self.stats["failed_requests"] += 1
                print(f"[Request failed] {url}")
                print(f"Reason: {exc}")
                print()

        return self.stats

    def close(self):
        self.db.close()
        self.session.close()
