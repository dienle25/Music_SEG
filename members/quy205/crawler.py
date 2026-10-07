# =========================================================
# crawler.py - hopamchuan_phuquy | BFS crawler cho HopAmChuan
# =========================================================

import os
import time
from collections import Counter
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

import config
from database import create_tables, save_page, save_links, save_song, count_songs, DB_PATH, BASE_DIR
from parser import extract_links, extract_page, extract_song
from robots import RobotsChecker
from url_filter import normalize_url, check_url, is_song_url
from url_frontier import URLFrontier


class Crawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        })
        self.robots = RobotsChecker(self.session, config.BOT_NAME, config.REQUEST_TIMEOUT)
        self.frontier = URLFrontier()
        self.discovered = set()
        self.skipped = set()
        self.skip_reasons = Counter()
        self.pages_crawled = 0
        self.pages_saved = 0
        self.song_pages_seen = 0
        self.songs_saved = 0
        self.songs_rejected = 0
        self.links_found = 0
        self.links_saved = 0
        self.failed_requests = 0
        self.failed_urls = []
        self.depth_counter = Counter()

    def close(self):
        self.session.close()

    def add_url(self, url, depth):
        if not url or url in self.discovered:
            return False
        if len(self.discovered) >= config.MAX_DISCOVERED_URLS:
            return False
        self.discovered.add(url)

        valid, reason = check_url(url, depth)
        if not valid:
            self.skipped.add(url)
            self.skip_reasons[reason] += 1
            return False

        if not self.robots.can_fetch(url):
            self.skipped.add(url)
            self.skip_reasons["ROBOTS_TXT"] += 1
            return False

        return self.frontier.add(url, depth)

    def load_seeds(self):
        added = 0
        for seed in config.SEED_URLS:
            url = normalize_url(seed, seed)
            if self.add_url(url, 0):
                added += 1
        print(f"[SEED] Added: {added}/{len(config.SEED_URLS)}")

    def _pagination_candidates(self, url, soup, depth):
        # Trang rhythm dùng ?offset=10, ?offset=20...
        found = set()
        parsed = urlparse(url)
        if not parsed.path.startswith("/rhythm/"):
            return found
        qs = parse_qs(parsed.query)
        current = int(qs.get("offset", [0])[0] or 0)
        for delta in (10, 20, 30, 40):
            next_offset = current + delta
            candidate = f"https://{config.DOMAIN}{parsed.path}?offset={next_offset}"
            if depth + 1 <= config.MAX_DEPTH:
                found.add(candidate)
        # Đồng thời lấy pagination links thật trên trang.
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "offset=" in href and "/rhythm/" in href:
                u = normalize_url(url, href)
                if u:
                    found.add(u)
        return found

    def crawl_page(self, url, depth):
        self.pages_crawled += 1
        self.depth_counter[depth] += 1
        print(f"\n[PAGE {self.pages_crawled}] depth={depth}\n{url}")

        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException as error:
            self.failed_requests += 1
            self.failed_urls.append((url, str(error)))
            print("  ERROR:", error)
            return

        final_url = normalize_url(url, response.url) or url
        soup = BeautifulSoup(response.text, "html.parser")
        save_page(extract_page(final_url, depth, response.status_code, soup))
        self.pages_saved += 1

        links = extract_links(soup, final_url)
        links.update(self._pagination_candidates(final_url, soup, depth))
        self.links_found += len(links)
        self.links_saved += save_links(final_url, links)

        if is_song_url(final_url):
            self.song_pages_seen += 1
            song = extract_song(final_url, soup)
            if song is None:
                self.songs_rejected += 1
                print("  SONG: rejected by quality gate")
            else:
                save_song(song)
                self.songs_saved = count_songs()
                print(f"  SONG OK: {song['title']} | {song['artist']} | lyrics={len(song['lyrics'])} chars")
                print(f"  thumbnail={song['thumbnail_url']}")

        if depth < config.MAX_DEPTH and self.songs_saved < config.MAX_SONGS:
            ordered = sorted(links, key=lambda u: (0 if is_song_url(u) else 1, u))
            added = sum(1 for link in ordered if self.add_url(link, depth + 1))
            print(f"  links={len(links)} new={added}")

        print(f"  progress: {self.songs_saved}/{config.MAX_SONGS} valid songs | frontier={len(self.frontier)}")

    def run(self):
        create_tables(reset=config.RESET_DB)
        self.load_seeds()
        started = time.time()
        try:
            while not self.frontier.is_empty() and self.songs_saved < config.MAX_SONGS:
                url, depth = self.frontier.pop()
                if self.frontier.is_visited(url):
                    continue
                valid, reason = check_url(url, depth)
                if not valid:
                    self.skip_reasons[reason] += 1
                    continue
                self.frontier.mark_visited(url)
                self.crawl_page(url, depth)
                time.sleep(config.CRAWL_DELAY)
        except KeyboardInterrupt:
            print("\n[STOP] User stopped crawler.")
        finally:
            self.print_summary(time.time() - started)
            self.close()

    def print_summary(self, elapsed):
        songs = count_songs()
        stop_reason = "300 valid songs reached" if songs >= config.MAX_SONGS else "frontier empty / crawl stopped"
        lines = [
            "", "=" * 58, "hopamchuan_phuquy - HOPAMCHUAN CRAWLING SUMMARY", "=" * 58,
            f"Topic                 : {config.TOPIC}",
            f"Target valid songs    : {config.MAX_SONGS}",
            f"Valid songs in DB     : {songs}",
            f"Stop reason           : {stop_reason}",
            f"Pages fetched         : {self.pages_crawled}",
            f"Pages saved           : {self.pages_saved}",
            f"Song pages seen       : {self.song_pages_seen}",
            f"Songs rejected        : {self.songs_rejected}",
            f"URLs discovered       : {len(self.discovered)}",
            f"Links found           : {self.links_found}",
            f"Links saved           : {self.links_saved}",
            f"Failed pages          : {self.failed_requests}",
            f"Maximum depth         : {config.MAX_DEPTH}",
            f"Elapsed               : {elapsed:.1f} seconds",
            f"Database              : {DB_PATH}",
            "", "Skipped by reason:",
        ]
        for reason, count in self.skip_reasons.most_common():
            lines.append(f"  {reason:<24}: {count}")
        if self.failed_urls:
            lines += ["", "Failed URLs:"]
            for url, error in self.failed_urls[:20]:
                lines.append(f"  [{error}] {url}")
        text = "\n".join(lines)
        print(text)
        summary_path = os.path.join(BASE_DIR, config.SUMMARY_FILE)
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
