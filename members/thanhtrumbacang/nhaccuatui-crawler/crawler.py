import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests

from config import (
    ALLOWED_DOMAINS,
    CRAWL_DELAY,
    IGNORED_EXTENSIONS,
    MAX_DEPTH,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    USER_AGENT,
    TOPIC,
)
from database import Database
from parser import parse_html
from url_frontier import URLFrontier


class FocusedMusicCrawler:
    def __init__(self, seed_urls, db_path):
        self.seed_urls = seed_urls
        self.db = Database(db_path)
        self.frontier = URLFrontier()
        self.visited = set()
        self.discovered = set(seed_urls)
        self.failed = 0
        self.skipped = 0
        self.pages_crawled = 0
        self.status_counter = Counter()
        self.depth_counter = Counter()
        self.robots_cache = {}

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        for url in seed_urls:
            normalized = self.normalize_url(url)
            if normalized:
                self.frontier.add(normalized, 0)

    def normalize_url(self, url, base_url=None):
        if base_url:
            url = urljoin(base_url, url)

        url, _ = urldefrag(url)
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return None

        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        # Cho phép domain chính và subdomain của domain đó.
        allowed = any(
            host == domain or host.endswith("." + domain)
            for domain in ALLOWED_DOMAINS
        )
        if not allowed:
            return None

        path = parsed.path or "/"
        lower_path = path.lower()

        if any(lower_path.endswith(ext) for ext in IGNORED_EXTENSIONS):
            return None

        # Loại bỏ fragment và chuẩn hóa host.
        normalized = f"{parsed.scheme}://{host}{path}"
        if parsed.query:
            normalized += "?" + parsed.query

        return normalized

    def can_fetch(self, url):
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        if host not in self.robots_cache:
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                rp.read()
                self.robots_cache[host] = rp
            except Exception:
                # Nếu robots.txt không đọc được, không tự động giả định
                # rằng mọi URL đều được phép. Ta bỏ qua URL này.
                self.robots_cache[host] = None

        rp = self.robots_cache[host]
        if rp is None:
            return False

        return rp.can_fetch(USER_AGENT, url)

    def add_links(self, current_url, current_depth, raw_links):
        for raw_link in raw_links:
            new_url = self.normalize_url(raw_link, current_url)

            if not new_url:
                self.skipped += 1
                continue

            if new_url in self.visited or new_url in self.discovered:
                self.skipped += 1
                continue

            new_depth = current_depth + 1

            if new_depth > MAX_DEPTH:
                self.skipped += 1
                continue

            self.discovered.add(new_url)
            self.frontier.add(new_url, new_depth)
            self.db.save_link(current_url, new_url)

    def crawl_one(self, url, depth):
        if url in self.visited:
            self.skipped += 1
            return

        self.visited.add(url)

        if not self.can_fetch(url):
            self.skipped += 1
            print("  SKIP robots.txt:", url)
            return

        start = time.time()

        try:
            response = self.session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            elapsed = time.time() - start
            self.status_counter[response.status_code] += 1

        except requests.RequestException as exc:
            self.failed += 1
            print(f"  REQUEST FAILED: {exc}")
            time.sleep(CRAWL_DELAY)
            return

        title = ""
        content = ""
        raw_links = []

        content_type = response.headers.get("Content-Type", "").lower()

        if response.status_code == 200 and "text/html" in content_type:
            try:
                title, content, raw_links = parse_html(response.text)
            except Exception as exc:
                print("  PARSE ERROR:", exc)

        domain = urlparse(url).netloc

        page = {
            "url": url,
            "domain": domain,
            "title": title,
            "content": content,
            "depth": depth,
            "status_code": response.status_code,
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }

        self.db.save_page(page)

        self.pages_crawled += 1
        self.depth_counter[depth] += 1

        if response.status_code == 200 and raw_links:
            self.add_links(url, depth, raw_links)

        print(f"[Crawl #{self.pages_crawled:03d}]")
        print(f"  Depth : {depth}")
        print(f"  URL   : {url}")
        print(f"  Status: {response.status_code}")
        print(f"  Title : {title[:100]}")
        print(f"  Links : {len(raw_links)}")
        print(f"  Time  : {elapsed:.2f} sec")

        time.sleep(CRAWL_DELAY)

    def run(self):
        while not self.frontier.empty() and self.pages_crawled < MAX_PAGES:
            item = self.frontier.get_next()
            if item is None:
                break

            url, depth = item
            self.crawl_one(url, depth)

        self.print_summary()

    def print_summary(self):
        print("\n" + "=" * 50)
        print(" CRAWLING SUMMARY")
        print("=" * 50)
        print(f"Topic                : {TOPIC}")
        print(f"Pages Crawled        : {self.pages_crawled}")
        print(f"Unique URLs Discovered: {len(self.discovered)}")
        print(f"Skipped URLs         : {self.skipped}")
        print(f"Failed Requests      : {self.failed}")
        print(f"Maximum Depth        : {MAX_DEPTH}")

        for depth in sorted(self.depth_counter):
            print(f"Depth {depth:<2}             : {self.depth_counter[depth]} pages")

        print("\nHTTP status:")
        for status, count in sorted(self.status_counter.items()):
            print(f"  HTTP {status}: {count}")

        print("=" * 50)

    def close(self):
        self.db.close()
