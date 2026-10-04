"""
crawler.py - Complete focused web crawler (Tasks 3, 6, 9)

Pipeline:
  seeds -> frontier -> select URL -> robots.txt check -> HTTP request
  -> parse HTML -> extract page info -> save to SQLite
  -> extract links -> filter -> add to frontier -> repeat
"""

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config
from database import Database
from parser import extract_links, extract_page_info, extract_song_info
from url_frontier import URLFrontier, get_domain


class Crawler:
    def __init__(self, reset_db=True):
        self.frontier = URLFrontier(max_depth=config.MAX_DEPTH)
        self.db = Database()
        if reset_db:
            self.db.reset()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

        self.robots_cache = {}      # base_url -> RobotFileParser or None
        self.pages_crawled = 0      # pages actually processed (any status)
        self.failed_requests = 0    # timeout / connection errors
        self.robots_blocked = 0
        self.non_html_skipped = 0
        self.stop_reason = ""

    # ----------------------------------------------------------
    # robots.txt
    # ----------------------------------------------------------
    def _get_robot_parser(self, url):
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if base in self.robots_cache:
            return self.robots_cache[base]

        rp = RobotFileParser()
        try:
            # Fetch robots.txt with our own headers/timeout
            resp = self.session.get(base + "/robots.txt", timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                rp.disallow_all = True      # site refuses us -> respect it
            else:
                rp.allow_all = True         # no robots.txt -> allowed
        except requests.RequestException:
            rp = None                       # cannot read robots.txt -> allow

        self.robots_cache[base] = rp
        return rp

    def can_fetch(self, url):
        if not config.RESPECT_ROBOTS:
            return True
        rp = self._get_robot_parser(url)
        if rp is None:
            return True
        return rp.can_fetch(config.USER_AGENT, url)

    # ----------------------------------------------------------
    # Task 3 - download a page
    # ----------------------------------------------------------
    def fetch(self, url):
        """
        Download one URL.
        Returns (response or None, elapsed_seconds, error_message or None).
        """
        start = time.time()
        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            elapsed = time.time() - start
            return response, elapsed, None
        except requests.RequestException as e:
            elapsed = time.time() - start
            return None, elapsed, str(e)

    # ----------------------------------------------------------
    # main loop
    # ----------------------------------------------------------
    def run(self):
        print("=========================================")
        print("          FOCUSED WEB CRAWLER")
        print("=========================================")
        config.print_config()
        print()

        # 1-2. load seeds and add them to the frontier
        for seed in config.SEED_URLS:
            self.frontier.add_seed(seed)
        self.frontier.show()
        print()

        # 3-12. main BFS loop
        while not self.frontier.is_empty() and self.pages_crawled < config.MAX_PAGES:
            url, depth = self.frontier.next()

            # 4. check crawling constraints (robots.txt)
            if not self.can_fetch(url):
                self.robots_blocked += 1
                print(f"[SKIP robots.txt] {url}")
                continue

            # 5. send HTTP request
            response, elapsed, error = self.fetch(url)
            self.pages_crawled += 1
            number = self.pages_crawled

            # request failed (timeout / connection error): record and continue
            if response is None:
                self.failed_requests += 1
                self.db.save_page(
                    {
                        "url": url,
                        "domain": get_domain(url),
                        "title": "",
                        "content": "",
                        "depth": depth,
                        "status_code": 0,   # 0 = no HTTP response
                        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                print(f"[Crawl #{number:03d}] FAILED  depth={depth}  {url}")
                print(f"             error: {error[:100]}")
                time.sleep(config.CRAWL_DELAY)
                continue

            status = response.status_code
            content_type = response.headers.get("Content-Type", "").lower()

            # 6. parse HTML (only if 200 and HTML)
            if status == 200 and "html" in content_type:
                soup = BeautifulSoup(response.text, "html.parser")

                # extract links and song info BEFORE extract_page_info
                # (extract_page_info modifies the soup: removes <script> etc.)
                links = extract_links(soup, url)

                song = extract_song_info(soup, url)
                if song:
                    self.db.save_song(song)

                # 7-8. extract page info and save
                page = extract_page_info(soup, url, depth, status)
                self.db.save_page(page)

                # 9-11. save links and add to frontier (depth + 1)
                self.db.save_links(url, links)
                new_depth = depth + 1
                accepted = 0
                if new_depth <= config.MAX_DEPTH:
                    for link in links:
                        ok, _reason = self.frontier.add(link, new_depth)
                        if ok:
                            accepted += 1

                print(f"[Crawl #{number:03d}]")
                print(f"  Depth : {depth}")
                print(f"  URL   : {url}")
                print(f"  Status: {status}")
                print(f"  Title : {page['title'][:70]}")
                if song:
                    has_lyrics = "yes" if song["lyrics"] else "no"
                    print(f"  Song  : {song['title'][:40]} | {song['artist'][:30]} | lyrics: {has_lyrics}")
                print(f"  Links : {len(links)} (new in frontier: {accepted})")
                print(f"  Time  : {elapsed:.2f} sec")
            else:
                # non-200 (404, 403, 500 ...) or non-HTML: record the status only
                if status == 200:
                    self.non_html_skipped += 1
                self.db.save_page(
                    {
                        "url": url,
                        "domain": get_domain(url),
                        "title": "",
                        "content": "",
                        "depth": depth,
                        "status_code": status,
                        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                print(f"[Crawl #{number:03d}]")
                print(f"  Depth : {depth}")
                print(f"  URL   : {url}")
                print(f"  Status: {status}  (not parsed, content-type: {content_type[:30]})")
                print(f"  Time  : {elapsed:.2f} sec")

            print(f"  Frontier size: {len(self.frontier)}")
            print("-" * 41)

            # politeness delay
            time.sleep(config.CRAWL_DELAY)

        # stopping reason
        if self.pages_crawled >= config.MAX_PAGES:
            self.stop_reason = "MAX_PAGES reached"
        elif self.frontier.is_empty():
            self.stop_reason = "URL Frontier is empty"
        else:
            self.stop_reason = "Stopped"

        self.print_summary()

    # ----------------------------------------------------------
    # crawling statistics (computed from data)
    # ----------------------------------------------------------
    def print_summary(self):
        depth_stats = self.db.pages_per_depth()
        status_stats = self.db.pages_per_status()
        domain_stats = self.db.pages_per_domain()

        print()
        print("========== CRAWLING SUMMARY ==========")
        print(f"Topic                  : {config.TOPIC}")
        print(f"Seed URLs              : {len(config.SEED_URLS)}")
        print(f"Stop reason            : {self.stop_reason}")
        print(f"Pages Crawled          : {self.pages_crawled}")
        print(f"Unique URLs Discovered : {len(self.frontier.discovered)}")
        print(f"Skipped URLs           : {self.frontier.skipped_count}")
        print(f"Blocked by robots.txt  : {self.robots_blocked}")
        print(f"Failed Requests        : {self.failed_requests}")
        print(f"Links stored           : {self.db.count_links()}")
        print(f"Songs stored           : {self.db.count_songs()}")
        print(f"Songs with lyrics      : {self.db.count_songs_with_lyrics()}")
        print(f"Maximum Depth (config) : {config.MAX_DEPTH}")

        print("Pages per depth:")
        for d, n in depth_stats.items():
            print(f"  Depth {d} : {n} pages")

        print("Pages per domain:")
        for dom, n in domain_stats.items():
            print(f"  {dom} : {n}")

        print("HTTP status:")
        for code, n in status_stats.items():
            label = "No response" if code == 0 else f"HTTP {code}"
            print(f"  {label} : {n}")
        print("=======================================")

        self.db.close()
