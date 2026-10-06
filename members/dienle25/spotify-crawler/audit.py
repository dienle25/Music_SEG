"""Audit trực tiếp robots.txt; dừng ở chính sách trước khi tải HTML Spotify."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import CANDIDATE_SEED_URLS, POLICY_REVIEWED_AT, POLICY_SOURCE, USER_AGENT
from robots import RobotsRules


def spotify_audit(output_dir: Path, timeout=10):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    url = 'https://open.spotify.com/robots.txt'
    evidence = {'checked_at': datetime.now(timezone.utc).isoformat(),
                'user_agent': USER_AGENT, 'domain': 'open.spotify.com',
                'robots_url': url, 'robots_http_status': None,
                'robots_allows_candidates': None,
                'policy_url': POLICY_SOURCE, 'policy_reviewed_at': POLICY_REVIEWED_AT,
                'policy_status': 'RESTRICTED',
                'policy_reason': 'Spotify User Guidelines, mục 5: hạn chế crawling/scraping.',
                'seed_urls_used': [], 'candidate_urls': list(CANDIDATE_SEED_URLS),
                'seed_validation': 'PENDING_POLICY_RESTRICTION',
                'page_http_status': None, 'page_content_type': None,
                'html_size': None, 'title': None, 'visible_text_length': None,
                'links_found': None, 'spotify_links': None,
                'http_access': 'NOT_TESTED_FOR_CONTENT',
                'html_crawlability': 'NOT_TESTED', 'javascript_dependency': 'NOT_DETERMINED',
                'crawlability_decision': 'EXTERNAL_BLOCKER',
                'content_requests_sent': 0,
                'stages': {'robots': 'ATTEMPTED', 'one_seed': 'BLOCKED_BY_POLICY',
                           '3_pages': 'NOT_RUN', '5_pages': 'NOT_RUN', '10_pages': 'NOT_RUN'}}
    try:
        with requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout,
                          allow_redirects=False, stream=True) as response:
            evidence.update(robots_http_status=response.status_code,
                            robots_final_url=response.url,
                            robots_content_type=response.headers.get('Content-Type'))
            body = bytearray()
            for chunk in response.iter_content(16_384):
                body.extend(chunk)
                if len(body) > 512_000:
                    raise ValueError('robots_response_too_large')
            data = bytes(body)
            evidence['robots_size_bytes'] = len(data)
            evidence['robots_sha256'] = hashlib.sha256(data).hexdigest()
            (output_dir / 'robots.txt').write_bytes(data)
            if response.status_code == 200:
                text = data.decode('utf-8', errors='replace')
                if 'html' not in response.headers.get('Content-Type', '').lower():
                    rules = RobotsRules(text, USER_AGENT)
                    evidence['robots_allows_candidates'] = {
                        candidate: rules.can_fetch(candidate)
                        for candidate in ('https://open.spotify.com/', *CANDIDATE_SEED_URLS,
                                          'https://open.spotify.com/embed/playlist/example')}
                    evidence['robots_crawl_delay'] = rules.delay
    except (requests.RequestException, ValueError) as exc:
        evidence['robots_error'] = f'{type(exc).__name__}: {exc}'
    (output_dir / 'spotify_audit.json').write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return evidence
