# =========================================================
# crawler.py  –  TASK 3, 6, 7, 9: crawler hoàn chỉnh (BFS)
# =========================================================

import os
import time
from collections import Counter, defaultdict

import requests
from bs4 import BeautifulSoup

import config
from database import create_tables, save_page, save_links, save_song, BASE_DIR
from parser import (extract_page_data, extract_links, read_meta_robots,
                    parse_sitemap)
from robots import RobotsChecker
from song_parser import extract_song_data
from url_filter import normalize_url, check_url_rules, get_domain, is_song_url
from url_frontier import URLFrontier


class Crawler:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        })

        self.frontier = URLFrontier()
        self.robots = RobotsChecker(self.session, config.BOT_NAME, config.REQUEST_TIMEOUT)

        # ---------- thống kê ----------
        self.seed_count = 0
        self.sitemap_count = 0
        self.pages_crawled = 0
        self.pages_saved = 0
        self.noindex_pages = 0
        self.failed_requests = 0
        self.duplicate_skipped = 0
        self.links_found = 0
        self.links_saved = 0
        self.total_time = 0.0
        self.failed_urls = []          # ghi lại request lỗi, không dừng crawler

        self.discovered = set()
        self.skipped = set()
        self.skip_reasons = Counter()
        self.status_counter = Counter()
        self.error_counter = Counter()
        self.depth_counter = Counter()
        self.crawled_per_domain = Counter()
        self.saved_per_domain = Counter()

        # ---------- thống kê bảng songs ----------
        self.songs_saved = 0
        self.songs_failed = 0                       # trang bài hát không tách được
        self.songs_per_domain = Counter()
        self.song_fields = defaultdict(Counter)     # domain -> số bài có artist / album / ...

    # =====================================================
    # HELPERS
    # =====================================================

    def skip(self, url, reason):
        if url not in self.skipped:
            self.skipped.add(url)
            self.skip_reasons[reason] += 1

    def try_add(self, url, depth):
        """
        TASK 6 + 7: lọc 1 URL rồi đưa vào frontier.
        Trả về "ACCEPT", "DUPLICATE" hoặc lý do bị loại.
        """
        self.discovered.add(url)

        if self.frontier.is_visited(url) or self.frontier.is_queued(url):
            self.duplicate_skipped += 1
            decision = "DUPLICATE"
        else:
            valid, reason = check_url_rules(url, depth)
            if valid:
                self.frontier.add(url, depth)
                decision = "ACCEPT"
            else:
                self.skip(url, reason)
                decision = reason

        if config.VERBOSE:
            if decision == "ACCEPT":
                print(f"    ACCEPT : {url}  (depth {depth})")
            elif decision == "DUPLICATE":
                print(f"    SKIP   : {url}  (already visited / queued)")
            else:
                print(f"    SKIP   : {url}  ({decision})")

        return decision

    def fetch(self, url):
        """TASK 3: gửi request, không để lỗi làm dừng crawler."""
        start = time.perf_counter()
        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            return response, time.perf_counter() - start, None
        except requests.Timeout:
            error = "Timeout"
        except requests.ConnectionError:
            error = "Connection Error"
        except requests.RequestException as e:
            error = type(e).__name__
        return None, time.perf_counter() - start, error

    def store_song(self, url, soup, page):
        """
        Trang bài hát: tách title / artist / album / genre / lyrics
        và lưu thêm vào bảng songs (ngoài bảng pages).
        """
        if not is_song_url(url):
            return

        try:
            song = extract_song_data(url, soup, page["crawled_at"])
            problem = "không tìm thấy tên bài hát"
        except Exception as error:        # lỗi ở một trang không được làm dừng crawler
            song = None
            problem = f"{type(error).__name__}: {error}"

        if song is None:
            self.songs_failed += 1
            print(f"Song  : không lưu vào songs ({problem})")
            return

        save_song(song)

        host = get_domain(url)
        self.songs_saved += 1
        self.songs_per_domain[host] += 1
        for column in ("artist", "album", "genre", "lyrics"):
            if song[column]:
                self.song_fields[host][column] += 1

        print(f"Song  : {song['title']} | {song['artist'] or '-'}")
        print(f"        album: {song['album'] or '-'} | genre: {song['genre'] or '-'} | "
              f"lyrics: {len(song['lyrics'] or '')} chars")

    # =====================================================
    # 1–2. LOAD SEEDS (+ SITEMAP)
    # =====================================================

    def load_seeds(self):
        for seed in config.SEED_URLS:
            url = normalize_url(seed, seed)
            if url and self.try_add(url, 0) == "ACCEPT":
                self.seed_count += 1
            else:
                print("[SEED SKIP]", seed)

    def load_sitemaps(self):
        if not config.USE_SITEMAPS:
            return

        feeds = [s for site in config.DOMAINS.values() for s in site.get("sitemaps", [])]
        if not feeds:
            return

        print()
        print("=" * 10, "SITEMAP FEEDS", "=" * 10)

        for sitemap_url in feeds:
            if not self.robots.can_fetch(sitemap_url):
                print("[SKIP ROBOTS]", sitemap_url)
                continue

            response, _, error = self.fetch(sitemap_url)
            time.sleep(config.CRAWL_DELAY)

            if response is None or response.status_code != 200:
                print(f"[SITEMAP ERROR] {sitemap_url} ->",
                      error or f"HTTP {response.status_code}")
                continue

            added = 0
            for loc in parse_sitemap(response.text):
                url = normalize_url(sitemap_url, loc)
                if url and self.try_add(url, 0) == "ACCEPT":
                    added += 1
                    if added >= config.SITEMAP_URL_LIMIT:
                        break

            self.sitemap_count += added
            print(f"{sitemap_url}  ->  +{added} URL")

    # =====================================================
    # 5–11. CRAWL 1 PAGE
    # =====================================================

    def crawl_page(self, url, depth, domain):
        self.pages_crawled += 1
        self.depth_counter[depth] += 1
        self.crawled_per_domain[domain] += 1

        print()
        print(f"[Crawl #{self.pages_crawled:03}]")
        print(f"Depth : {depth}")
        print(f"URL   : {url}")

        # ---------- HTTP REQUEST ----------
        response, elapsed, error = self.fetch(url)
        self.total_time += elapsed

        if response is None:
            self.failed_requests += 1
            self.error_counter[error] += 1
            self.failed_urls.append((url, error))
            print(f"Status: FAILED ({error})")
            print(f"Time  : {elapsed:.2f} sec")
            return

        status = response.status_code
        self.status_counter[status] += 1
        print(f"Status: {status}")

        if status != 200:
            self.failed_requests += 1
            self.failed_urls.append((url, f"HTTP {status}"))
            print(f"Time  : {elapsed:.2f} sec")
            return

        # ---------- REDIRECT: dùng URL cuối cùng ----------
        final_url = normalize_url(response.url, response.url)
        if final_url and final_url != url:
            print(f"Redirect -> {final_url}")
            valid, reason = check_url_rules(final_url, depth)
            if not valid:
                self.skip(final_url, "REDIRECT_" + reason)
                print(f"[SKIP] redirect tới trang không hợp lệ ({reason})")
                return
            if self.frontier.is_visited(final_url):
                self.duplicate_skipped += 1
                print("[SKIP] redirect tới trang đã crawl")
                return
            self.frontier.mark_visited(final_url)
            url = final_url

        # ---------- CHỈ XỬ LÝ HTML ----------
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            self.skip(url, "NOT_HTML")
            print("[SKIP] Not HTML:", content_type)
            return

        if "charset" not in content_type:          # tránh lỗi font tiếng Việt
            response.encoding = response.apparent_encoding or "utf-8"

        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        # ---------- EXTRACT DATA ----------
        page = extract_page_data(url, depth, status, soup)
        print(f"Title : {page['title']}")

        can_index, can_follow, robots_meta = read_meta_robots(soup, response.headers)

        # ---------- SAVE PAGE ----------
        if can_index:
            save_page(page)
            self.pages_saved += 1
            self.saved_per_domain[domain] += 1
            self.store_song(url, soup, page)       # trang bài hát -> thêm vào bảng songs
        else:
            self.noindex_pages += 1
            print(f"[META ROBOTS] '{robots_meta}' -> không lưu trang")

        # ---------- EXTRACT + FILTER LINKS ----------
        if not can_follow:
            print("Links : 0 (nofollow – không đi theo link)")
            print(f"Time  : {elapsed:.2f} sec")
            return

        links = extract_links(soup, html, url)
        self.links_found += len(links)
        self.links_saved += save_links(url, links)

        decisions = Counter(self.try_add(link, depth + 1) for link in links)
        new = decisions.pop("ACCEPT", 0)
        dup = decisions.pop("DUPLICATE", 0)

        print(f"Links : {len(links)}  (new: {new}, duplicate: {dup}, "
              f"skipped: {sum(decisions.values())})")
        print(f"Time  : {elapsed:.2f} sec")

    # =====================================================
    # 3–12. BFS LOOP
    # =====================================================

    def run(self):
        create_tables(reset=config.RESET_DB)

        self.load_seeds()
        self.load_sitemaps()
        self.frontier.show()

        print("-" * 50)

        try:
            # Giới hạn tính theo số trang ĐÃ LƯU: trang lỗi / noindex không được
            # lưu nên crawler lấy tiếp URL kế trong hàng đợi để bù.
            while not self.frontier.is_empty() and self.pages_saved < config.MAX_PAGES:

                url, depth = self.frontier.pop()
                domain = get_domain(url)

                if self.frontier.is_visited(url):
                    self.duplicate_skipped += 1
                    continue

                if self.saved_per_domain[domain] >= config.MAX_PAGES_PER_DOMAIN:
                    self.skip(url, "DOMAIN_LIMIT")
                    continue

                valid, reason = check_url_rules(url, depth)
                if not valid:
                    self.skip(url, reason)
                    print("[SKIP]", reason, url)
                    continue

                if not self.robots.can_fetch(url):
                    self.skip(url, "ROBOTS_TXT")
                    print("[SKIP ROBOTS]", url)
                    continue

                self.frontier.mark_visited(url)
                self.crawl_page(url, depth, domain)

                time.sleep(config.CRAWL_DELAY)

        except KeyboardInterrupt:
            print("\n[STOP] Người dùng dừng crawler (Ctrl+C)")

        self.print_summary()

    # =====================================================
    # 14. CRAWLING STATISTICS
    # =====================================================

    def print_summary(self):
        if self.pages_saved >= config.MAX_PAGES:
            stop_reason = "MAX_PAGES reached"
        elif self.frontier.is_empty():
            stop_reason = "URL Frontier is empty"
        else:
            stop_reason = "Stopped by user"

        avg_time = self.total_time / self.pages_crawled if self.pages_crawled else 0

        def row(label, value):
            return f"{label:<28}: {value}"

        lines = [
            "",
            "=" * 10 + " CRAWLING SUMMARY " + "=" * 10,
            "",
            row("Topic", config.TOPIC),
            row("Stop reason", stop_reason),
            "",
            row("Seed URLs", f"{self.seed_count} (+{self.sitemap_count} từ sitemap)"),
            row("Pages Crawled", self.pages_crawled),
            row("Pages Saved (DB)", self.pages_saved),
            row("Not saved (noindex)", self.noindex_pages),
            row("Songs Saved (DB)", self.songs_saved),
            row("Unique URLs Discovered", len(self.discovered)),
            row("Skipped URLs", len(self.skipped)),
            row("Duplicate URLs Skipped", self.duplicate_skipped),
            row("Failed Requests", self.failed_requests),
            row("Links Found", self.links_found),
            row("Links Saved (DB)", self.links_saved),
            row("Avg Response Time", f"{avg_time:.2f} sec"),
            "",
            row("Maximum Depth", config.MAX_DEPTH),
            "",
        ]

        for d in sorted(self.depth_counter):
            lines.append(row(f"Depth {d}", f"{self.depth_counter[d]} pages"))
        lines.append("")

        for status in sorted(self.status_counter):
            lines.append(row(f"HTTP {status}", self.status_counter[status]))
        for error, count in self.error_counter.most_common():
            lines.append(row(error, count))
        lines.append("")

        lines.append("Pages per domain (crawled / saved):")
        for host, site in config.DOMAINS.items():
            lines.append(row(f"    {site['name']}",
                             f"{self.crawled_per_domain[host]} / {self.saved_per_domain[host]}"))
        lines.append("")

        lines.append("Songs per domain (saved | artist / album / genre / lyrics):")
        for host, site in config.DOMAINS.items():
            fields = self.song_fields[host]
            lines.append(row(f"    {site['name']}",
                             f"{self.songs_per_domain[host]} | {fields['artist']} / "
                             f"{fields['album']} / {fields['genre']} / {fields['lyrics']}"))
        if self.songs_failed:
            lines.append(row("    Song pages not saved", self.songs_failed))
        lines.append("")

        lines.append("Skipped URLs by reason:")
        for reason, count in self.skip_reasons.most_common():
            lines.append(row(f"    {reason}", count))

        if self.failed_urls:
            lines.append("")
            lines.append("Failed requests:")
            for url, error in self.failed_urls[:15]:
                lines.append(f"    [{error}] {url}")

        lines.append("=" * 38)

        text = "\n".join(lines)
        print(text)

        summary_path = os.path.normpath(os.path.join(BASE_DIR, config.SUMMARY_FILE))
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        print(f"(Đã lưu thống kê vào {config.SUMMARY_FILE})")
