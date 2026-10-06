"""Crawler tuần tự BFS, Requests + BeautifulSoup + SQLite, không dùng Spotify API."""
import time
from urllib.parse import urlsplit

import requests

from config import Config, policy_restriction
from database import Database
from parser import extract_links, parse_page
from robots import RobotsRules
from statistics import CrawlStatistics
from url_frontier import URLFrontier
from urls import is_allowed_url, normalize_url


class Crawler:
    def __init__(self, config: Config, session=None, sleep=time.sleep):
        self.config = config
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.sleep = sleep
        self.frontier = URLFrontier()
        self.stats = CrawlStatistics()
        self.robots_cache = {}
        self.robots_checks = []
        self.stopped_origins = set()
        self.last_request = {}
        self.request_count = 0
        self.page_diagnostics = []
        self.processed_urls = set()
        self.last_response_seconds = 0.0

    def _error(self, url, reason, failed=False):
        self.stats.errors.append({'url': url, 'reason': reason})
        if failed:
            self.stats.failed_requests += 1
        else:
            self.stats.skipped_urls += 1

    @staticmethod
    def _origin(url):
        p = urlsplit(url)
        return f'{p.scheme}://{p.netloc}'

    def _request(self, url, delay=None):
        if self.request_count >= self.config.max_requests:
            self._error(url, 'max_requests_reached')
            return None
        origin = self._origin(url)
        interval = max(self.config.crawl_delay, delay or 0)
        if origin in self.last_request:
            remaining = interval - (time.monotonic() - self.last_request[origin])
            if remaining > 0:
                self.sleep(remaining)
        self.request_count += 1
        started = time.monotonic()
        try:
            response = self.session.get(
                url, headers={'User-Agent': self.config.user_agent,
                              'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.8'},
                timeout=self.config.request_timeout, allow_redirects=False, stream=True)
            self.last_response_seconds = time.monotonic() - started
            self.last_request[origin] = time.monotonic()
            return response
        except requests.RequestException as exc:
            self.last_request[origin] = time.monotonic()
            self._error(url, f'{type(exc).__name__}: {exc}', failed=True)
            return None

    def _read_body(self, response, limit):
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16_384):
            body.extend(chunk)
            if len(body) > limit:
                raise ValueError('response_too_large')
        encoding = response.encoding
        if not encoding or encoding.lower() == 'iso-8859-1':
            encoding = 'utf-8'
        try:
            return bytes(body).decode(encoding, errors='replace'), len(body)
        except LookupError:
            return bytes(body).decode('utf-8', errors='replace'), len(body)

    def _robots(self, url):
        origin = self._origin(url)
        if origin not in self.robots_cache:
            robots_url = origin + '/robots.txt'
            response = self._request(robots_url)
            rules = None
            result = {'url': robots_url, 'status_code': None, 'available': False}
            if response is not None:
                result['status_code'] = response.status_code
                try:
                    if response.status_code == 200:
                        body, _ = self._read_body(response, 512_000)
                        content_type = response.headers.get('Content-Type', '').lower()
                        if 'html' in content_type or body.lstrip().lower().startswith('<!doctype html'):
                            result['reason'] = 'robots_response_is_html'
                        else:
                            rules = RobotsRules(body, self.config.user_agent)
                    elif response.status_code in (404, 410):
                        rules = RobotsRules('', self.config.user_agent)
                    else:
                        result['reason'] = 'robots_unavailable_or_restricted'
                        self._error(robots_url, f'robots_http_{response.status_code}', failed=True)
                except (requests.RequestException, ValueError) as exc:
                    result['reason'] = str(exc)
                finally:
                    response.close()
            result['available'] = rules is not None
            self.robots_checks.append(result)
            self.robots_cache[origin] = rules
        rules = self.robots_cache[origin]
        if rules is None or not rules.can_fetch(url):
            self._error(url, 'robots_denied_or_unavailable')
            return None
        return rules

    def _fetch_page(self, url):
        chain = set()
        current = url
        for _ in range(6):
            if current in chain:
                self._error(current, 'redirect_loop', failed=True)
                return None
            chain.add(current)
            if not is_allowed_url(current, self.config.allowed_domains):
                self._error(current, 'redirect_outside_allowed_scope')
                return None
            restriction = policy_restriction(urlsplit(current).hostname or '')
            if restriction:
                self._error(current, restriction)
                return None
            origin = self._origin(current)
            if origin in self.stopped_origins:
                self._error(current, 'origin_stopped_after_restriction')
                return None
            if current != url and current in self.frontier.visited_urls:
                self._error(current, 'redirect_duplicate')
                return None
            rules = self._robots(current)
            if rules is None:
                return None
            if current != url:
                self.frontier.visited_urls.add(current)
            response = self._request(current, rules.delay)
            self.processed_urls.add(current)
            if response is None:
                return None
            status = response.status_code
            self.stats.http_status_counts[status] += 1
            try:
                if status in (301, 302, 303, 307, 308):
                    target = normalize_url(response.headers.get('Location', ''), current)
                    if not target:
                        self._error(current, 'invalid_redirect', failed=True)
                        return None
                    current = target
                    continue
                if status != 200:
                    self._error(current, f'http_{status}', failed=True)
                    if status in (401, 403, 429):
                        self.stopped_origins.add(origin)
                    return None
                content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if content_type not in ('text/html', 'application/xhtml+xml'):
                    self._error(current, 'non_html_response')
                    return None
                body_started = time.monotonic()
                body, size = self._read_body(response, self.config.max_bytes)
                response_seconds = self.last_response_seconds + time.monotonic() - body_started
                return current, body, size, content_type, response_seconds
            except (requests.RequestException, ValueError) as exc:
                self._error(current, str(exc), failed=True)
                return None
            finally:
                response.close()
        self._error(url, 'too_many_redirects', failed=True)
        return None

    def run(self):
        try:
            with Database(self.config.db_path) as database:
                for seed in self.config.seed_urls:
                    url = normalize_url(seed)
                    if url and is_allowed_url(url, self.config.allowed_domains):
                        self.stats.discovered_urls.add(url)
                        if not self.frontier.add(url, 0):
                            self.stats.skipped_urls += 1
                    else:
                        self._error(str(seed), 'invalid_seed')
                while self.frontier and self.stats.pages_crawled < self.config.max_pages:
                    if self.request_count >= self.config.max_requests:
                        break
                    url, depth = self.frontier.pop()
                    if url in self.processed_urls:
                        self._error(url, 'already_fetched_redirect_target')
                        continue
                    if depth > self.config.max_depth:
                        self._error(url, 'max_depth')
                        continue
                    started = time.monotonic()
                    fetched = self._fetch_page(url)
                    if fetched is None:
                        continue
                    final_url, html, size, content_type, response_seconds = fetched
                    page = parse_page(html, final_url, depth, 200)
                    title = page['title'].lower()
                    text = page['content'].lower()
                    challenge = any(marker in title for marker in ('just a moment', 'access denied', 'captcha'))
                    challenge = challenge or any(marker in text for marker in ('verify you are human', 'complete the captcha'))
                    if challenge:
                        self.stopped_origins.add(self._origin(final_url))
                        self._error(final_url, 'anti_bot_challenge')
                        continue
                    links = extract_links(html, final_url)
                    self.stats.links_found += len(links)
                    self.stats.discovered_urls.update(links)
                    database.save_page(page)
                    database.save_links(final_url, links)
                    self.stats.pages_crawled += 1
                    self.stats.depth_counts[depth] += 1
                    diagnostic = {'url': url, 'final_url': final_url, 'status_code': 200,
                                  'content_type': content_type, 'html_size': size,
                                  'title': page['title'], 'visible_text_length': len(page['content']),
                                  'links_found': len(links), 'depth': depth,
                                  'response_time_seconds': round(response_seconds, 4),
                                  'processing_time_seconds': round(time.monotonic() - started, 4),
                                  'http_access': 'PASS',
                                  'html_crawlability': 'PASS' if len(page['content']) >= 40 and links else 'PARTIAL',
                                  'javascript_dependency': 'NOT_DETERMINED'}
                    self.page_diagnostics.append(diagnostic)
                    for link in links:
                        if depth >= self.config.max_depth:
                            self.stats.skipped_urls += 1
                        elif not is_allowed_url(link, self.config.allowed_domains):
                            self.stats.skipped_urls += 1
                        elif not self.frontier.add(link, depth + 1):
                            self.stats.skipped_urls += 1
                summary = self.stats.to_dict()
                summary.update(database_counts=database.counts(), request_count=self.request_count,
                               robots_checks=self.robots_checks, page_diagnostics=self.page_diagnostics,
                               frontier_remaining=len(self.frontier))
                if self.stats.pages_crawled >= self.config.max_pages:
                    stop = 'max_pages'
                elif self.request_count >= self.config.max_requests:
                    stop = 'max_requests'
                else:
                    stop = 'frontier_empty'
                summary['stop_reason'] = stop
                summary['status'] = 'PARTIAL' if self.stats.errors else 'PASS'
                if any(d['html_crawlability'] != 'PASS' for d in self.page_diagnostics):
                    summary['status'] = 'PARTIAL'
                if not self.config.seed_urls:
                    summary['status'] = 'EXTERNAL_BLOCKER'
                    summary['stop_reason'] = 'no_verified_seed_urls'
                elif any('policy_restricted' in e['reason'] for e in self.stats.errors):
                    summary['status'] = 'EXTERNAL_BLOCKER'
                return summary
        finally:
            if self._owns_session:
                self.session.close()
