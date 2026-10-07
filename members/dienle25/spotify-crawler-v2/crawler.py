# =========================================================
# crawler.py  –  TASK 3, 6, 7, 9: crawler BFS (chỉ Spotify)
# =========================================================

import os
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

import config
from database import (BASE_DIR, cached_lyrics, create_tables, save_links, save_listing,
                      save_page, save_song, save_track, set_meta)
from parser import extract_links, extract_page_data, read_meta_robots
from robots import RobotsChecker
from spotify_parser import kind_and_id, parse_listing, parse_track, sha256_bytes
from url_filter import check_url_rules, normalize_url
from url_frontier import URLFrontier


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def retry_after_seconds(value):
    """Header Retry-After ("120" hoặc ngày giờ HTTP) -> số giây phải chờ; None nếu không đọc được."""
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


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
        self.pages_crawled = 0
        self.pages_saved = 0
        self.noindex_pages = 0
        self.failed_requests = 0
        self.retries = 0
        self.consecutive_failures = 0
        self.duplicate_skipped = 0
        self.links_found = 0
        self.links_saved = 0
        self.total_time = 0.0
        self.failed_urls = []          # ghi lại request lỗi, không dừng crawler
        self.stop_reason = None
        self.started = None            # time.monotonic() lúc bắt đầu
        self.started_utc = None

        self.discovered = set()
        self.skipped = set()
        self.skip_reasons = Counter()
        self.status_counter = Counter()
        self.error_counter = Counter()
        self.depth_counter = Counter()
        self.saved_per_kind = Counter()     # trang đã lưu theo loại: playlist / album / track
        self.found_on = {}                  # url -> trang đầu tiên có link tới url (seed: None)

        # ---------- thống kê bài hát (bảng tracks + songs) ----------
        self.tracks_saved = 0
        self.tracks_failed = 0                    # trang bài hát không tách được
        self.track_fields = Counter()             # số bài có album / ngày phát hành / thời lượng
        self.single_artist_tracks = 0
        self.artist_tracks = Counter()            # id nghệ sĩ -> số bài
        self.artist_names = {}                    # id nghệ sĩ -> tên
        self.listings_saved = Counter()           # số album / playlist đã lưu

    # =====================================================
    # HELPERS
    # =====================================================

    def skip(self, url, reason):
        if url not in self.skipped:
            self.skipped.add(url)
            self.skip_reasons[reason] += 1

    def try_add(self, url, depth, source=None):
        """
        TASK 6 + 7: lọc 1 URL rồi đưa vào frontier.
        source = trang chứa link (để biết bài hát được tìm thấy từ playlist / album nào).
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
                self.found_on.setdefault(url, source)
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
        """
        TASK 3: gửi request, không để lỗi làm dừng crawler.
        HTTP 429 / 503: chờ rồi thử lại (tối đa MAX_RETRIES lần), tôn trọng header
        Retry-After. Server yêu cầu chờ lâu hơn MAX_BACKOFF giây -> dừng crawl.
        Trả về (response, thời_gian_request, lỗi).
        """
        for attempt in range(config.MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            except requests.Timeout:
                return None, time.perf_counter() - start, "Timeout"
            except requests.ConnectionError:
                return None, time.perf_counter() - start, "Connection Error"
            except requests.RequestException as e:
                return None, time.perf_counter() - start, type(e).__name__
            elapsed = time.perf_counter() - start

            if response.status_code not in config.RETRY_STATUSES:
                return response, elapsed, None

            wait = retry_after_seconds(response.headers.get("Retry-After"))
            if wait is not None and wait > config.MAX_BACKOFF:
                self.stop_reason = (f"HTTP {response.status_code}: server yêu cầu chờ "
                                    f"{wait:.0f} giây (Retry-After)")
                return response, elapsed, None
            if attempt == config.MAX_RETRIES:
                return response, elapsed, None

            if wait is None:
                wait = min(config.MAX_BACKOFF, 5 * 2 ** attempt)      # 5, 10, 20 giây
            self.retries += 1
            print(f"[HTTP {response.status_code}] chờ {wait:.0f} giây rồi thử lại "
                  f"({attempt + 1}/{config.MAX_RETRIES})")
            time.sleep(wait)

    def record_failure(self, url, error, blocked=True):
        """blocked: lỗi có thể do bị chặn / quá tải (mạng, 403, 429, 5xx) -> tính vào lỗi liên tiếp."""
        self.failed_requests += 1
        self.failed_urls.append((url, error))
        if not error.startswith("HTTP"):
            self.error_counter[error] += 1
        if blocked:
            self.consecutive_failures += 1

    def store_track(self, url, soup, page, html_sha256, source):
        """Trang bài hát: lưu metadata vào bảng tracks và bảng songs (theo đề bài)."""
        try:
            track = parse_track(url, soup, utc_now(), html_sha256)
            problem = "không tìm thấy tên bài hát"
        except Exception as error:        # lỗi ở một trang không được làm dừng crawler
            track, problem = None, f"{type(error).__name__}: {error}"

        if track is None:
            self.tracks_failed += 1
            print(f"Track : không lưu ({problem})")
            return

        track["source_url"] = source
        save_track(track)
        lyrics = cached_lyrics(track["track_id"])      # lời đã tra ở lần trước (lyrics.py)
        save_song({
            "url": url,
            "title": track["title"],
            "artist": track["artist_text"] or ", ".join(track["artists"]) or None,
            "album": track["album"],
            "genre": None,      # HTML Spotify không có thể loại
            "lyrics": lyrics,   # HTML Spotify không có lời -> lyrics.py tra trên LRCLIB
            "crawled_at": page["crawled_at"],
        })

        self.tracks_saved += 1
        for field in ("album", "release_date", "duration_sec"):
            if track[field]:
                self.track_fields[field] += 1
        if lyrics:
            self.track_fields["lyrics"] += 1

        ids, names = track["artist_ids"], track["artists"]
        if len(ids) == 1 and len(names) == 1:
            self.single_artist_tracks += 1
        for i, artist_id in enumerate(ids):
            self.artist_tracks[artist_id] += 1
            if len(names) == len(ids):
                self.artist_names.setdefault(artist_id, names[i])

        print(f"Track : {track['title']} | {track['artist_text'] or '-'}")
        print(f"        album: {track['album'] or '-'} | {track['release_date'] or '-'} | "
              f"{track['duration_sec'] or '-'} sec")

    # =====================================================
    # 1. LOAD SEEDS
    # =====================================================

    def load_seeds(self):
        for seed in config.SEED_URLS:
            url = normalize_url(seed, seed)
            decision = self.try_add(url, 0) if url else "INVALID_URL"
            if decision == "ACCEPT":
                self.seed_count += 1
            else:
                print(f"[SEED SKIP] {seed}  ({decision})")

    # =====================================================
    # 5–11. CRAWL 1 PAGE
    # =====================================================

    def crawl_page(self, url, depth):
        self.pages_crawled += 1
        self.depth_counter[depth] += 1
        source = self.found_on.get(url)

        print()
        print(f"[Crawl #{self.pages_crawled:04}]")
        print(f"Depth : {depth}")
        print(f"URL   : {url}")

        # ---------- HTTP REQUEST ----------
        response, elapsed, error = self.fetch(url)
        self.total_time += elapsed

        if response is None:
            self.record_failure(url, error)
            print(f"Status: FAILED ({error})")
            print(f"Time  : {elapsed:.2f} sec")
            return

        status = response.status_code
        self.status_counter[status] += 1
        print(f"Status: {status}")

        if status != 200:
            # 404 / 410: link hỏng, không phải dấu hiệu bị chặn
            self.record_failure(url, f"HTTP {status}", blocked=status in (403, 429) or status >= 500)
            print(f"Time  : {elapsed:.2f} sec")
            return
        self.consecutive_failures = 0

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
        kind, _ = kind_and_id(url)
        listing = None

        # ---------- SAVE PAGE + TRACK ----------
        if can_index:
            save_page(page)
            self.pages_saved += 1
            self.saved_per_kind[kind] += 1
            html_sha256 = sha256_bytes(response.content)
            if kind == "track":
                self.store_track(url, soup, page, html_sha256, source)
            else:
                listing = parse_listing(url, soup, utc_now(), html_sha256)
        else:
            self.noindex_pages += 1
            print(f"[META ROBOTS] '{robots_meta}' -> không lưu trang")

        # ---------- EXTRACT + FILTER LINKS ----------
        new_tracks = 0
        if can_follow:
            links = extract_links(soup, html, url)
            self.links_found += len(links)
            self.links_saved += save_links(url, links)

            decisions = [self.try_add(link, depth + 1, url) for link in links]
            new_tracks = sum(1 for link, decision in zip(links, decisions)
                             if decision == "ACCEPT" and kind_and_id(link)[0] == "track")
            counts = Counter(decisions)
            new = counts.pop("ACCEPT", 0)
            dup = counts.pop("DUPLICATE", 0)
            print(f"Links : {len(links)}  (new: {new}, duplicate: {dup}, "
                  f"skipped: {sum(counts.values())})")
        else:
            print("Links : 0 (nofollow – không đi theo link)")

        # ---------- SAVE ALBUM / PLAYLIST ----------
        if listing:
            listing["n_new_tracks"] = new_tracks
            listing["source_url"] = source
            save_listing(listing)
            self.listings_saved[listing["kind"]] += 1
            print(f"List  : {listing['kind']} – {len(listing['track_ids'])} bài "
                  f"({new_tracks} bài mới)")

        print(f"Time  : {elapsed:.2f} sec")

    # =====================================================
    # 3–12. BFS LOOP
    # =====================================================

    def should_stop(self):
        """Kiểm tra điều kiện dừng trước mỗi request, trả về lý do (hoặc None)."""
        if self.stop_reason:                                   # fetch(): Retry-After quá dài
            return self.stop_reason
        if self.pages_saved >= config.MAX_PAGES:
            return "MAX_PAGES reached"
        if time.monotonic() - self.started >= config.MAX_HOURS * 3600:
            return f"MAX_HOURS reached ({config.MAX_HOURS} h)"
        if self.consecutive_failures >= config.STOP_AFTER_CONSECUTIVE_FAILURES:
            return f"{self.consecutive_failures} request lỗi liên tiếp (có thể bị chặn)"
        return None

    def run(self):
        create_tables(reset=config.RESET_DB)
        self.started = time.monotonic()
        self.started_utc = utc_now()

        self.load_seeds()
        self.frontier.show()

        print("-" * 50)

        try:
            # Giới hạn tính theo số trang ĐÃ LƯU: trang lỗi / noindex không được
            # lưu nên crawler lấy tiếp URL kế trong hàng đợi để bù.
            while not self.frontier.is_empty():
                self.stop_reason = self.should_stop()
                if self.stop_reason:
                    break

                url, depth = self.frontier.pop()

                if self.frontier.is_visited(url):
                    self.duplicate_skipped += 1
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
                self.crawl_page(url, depth)

                time.sleep(config.CRAWL_DELAY)

        except KeyboardInterrupt:
            self.stop_reason = "Stopped by user (Ctrl+C)"
            print("\n[STOP] Người dùng dừng crawler (Ctrl+C)")

        if not self.stop_reason:
            self.stop_reason = self.should_stop() or "URL Frontier is empty"

        self.print_summary()
        self.save_run_meta()

    # =====================================================
    # 14. CRAWLING STATISTICS
    # =====================================================

    def run_time(self):
        seconds = int(time.monotonic() - self.started) if self.started else 0
        return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"

    def print_summary(self):
        avg_time = self.total_time / self.pages_crawled if self.pages_crawled else 0

        def row(label, value):
            return f"{label:<28}: {value}"

        lines = [
            "",
            "=" * 10 + " CRAWLING SUMMARY " + "=" * 10,
            "",
            row("Topic", config.TOPIC),
            row("Crawler version", config.CRAWLER_VERSION),
            row("Stop reason", self.stop_reason),
            row("Run time", self.run_time()),
            "",
            row("Seed URLs", self.seed_count),
            row("Pages Crawled", self.pages_crawled),
            row("Pages Saved (DB)", self.pages_saved),
        ]
        for kind in config.CRAWL_KINDS + ("artist",):
            if self.saved_per_kind[kind]:
                lines.append(row(f"    {kind}", self.saved_per_kind[kind]))
        lines += [
            row("Not saved (noindex)", self.noindex_pages),
            row("Unique URLs Discovered", len(self.discovered)),
            row("Skipped URLs", len(self.skipped)),
            row("Duplicate URLs Skipped", self.duplicate_skipped),
            row("Failed Requests", self.failed_requests),
            row("Retries (HTTP 429/503)", self.retries),
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

        n = self.tracks_saved
        lines += [
            "Tracks (bảng tracks + songs):",
            row("    Tracks saved", n),
            row("    with album", self.track_fields["album"]),
            row("    with release date", self.track_fields["release_date"]),
            row("    with duration", self.track_fields["duration_sec"]),
            row("    single-artist tracks", self.single_artist_tracks),
            row("    distinct artists", len(self.artist_tracks)),
            row("    artists >= 30 tracks", sum(c >= 30 for c in self.artist_tracks.values())),
            row("    artists >= 24 tracks", sum(c >= 24 for c in self.artist_tracks.values())),
            row("    with lyrics", f"{self.track_fields['lyrics']} (chạy python lyrics.py để tra lời)"),
            row("    genre", "không có (NULL) – HTML Spotify không chứa"),
        ]
        if self.tracks_failed:
            lines.append(row("    track pages not parsed", self.tracks_failed))
        lines.append("")

        if self.listings_saved:
            lines.append("Albums / playlists saved:")
            for kind, count in self.listings_saved.most_common():
                lines.append(row(f"    {kind}", count))
            lines.append("")

        if self.artist_tracks:
            lines.append("Top artists (số bài):")
            for artist_id, count in self.artist_tracks.most_common(10):
                name = self.artist_names.get(artist_id, artist_id)
                lines.append(row(f"    {name[:24]}", count))
            lines.append("")

        for domain, info in self.robots.info.items():
            lines.append(row(f"robots.txt ({domain})",
                             f"HTTP {info['status']}, sha256 {info['sha256'][:16]}"))
            for signal in info["content_signal_lines"]:
                lines.append(f"    {signal}")
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
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        print(f"(Đã lưu thống kê vào {config.SUMMARY_FILE})")

    def save_run_meta(self):
        """Ghi thông tin lần chạy vào bảng meta (dùng khi xuất dữ liệu cho đề tài)."""
        set_meta("run", {
            "crawler_version": config.CRAWLER_VERSION,
            "topic": config.TOPIC,
            "start_utc": self.started_utc,
            "end_utc": utc_now(),
            "stop_reason": self.stop_reason,
            "seeds": config.SEED_URLS,
            "config": {key: getattr(config, key) for key in (
                "CRAWL_KINDS", "FETCH_ARTIST_PAGES", "MAX_DEPTH", "MAX_PAGES", "MAX_HOURS",
                "CRAWL_DELAY", "REQUEST_TIMEOUT", "MAX_RETRIES", "MAX_BACKOFF",
                "BOT_NAME", "USER_AGENT")},
            "robots": self.robots.info,
            "counts": {
                "pages_crawled": self.pages_crawled,
                "pages_saved": self.pages_saved,
                "tracks_saved": self.tracks_saved,
                "listings_saved": dict(self.listings_saved),
                "failed_requests": self.failed_requests,
                "retries": self.retries,
                "http_status": {str(k): v for k, v in self.status_counter.items()},
            },
        })
