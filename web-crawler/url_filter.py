# =========================================================
# url_filter.py  –  TASK 5 + 7: chuẩn hoá & lọc URL
# =========================================================

import re
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse

import config


# Biên dịch regex của từng domain 1 lần
ALLOW_RULES = {
    host: [re.compile(pattern) for pattern in site["allow"]]
    for host, site in config.DOMAINS.items()
}

REWRITE_RULES = {
    host: [(re.compile(pattern), repl) for pattern, repl in site.get("rewrite", [])]
    for host, site in config.DOMAINS.items()
}

IGNORED_SCHEMES = ("mailto:", "javascript:", "tel:", "sms:", "data:")


def canonical_host(netloc):
    """www.nhac.vn -> nhac.vn ; NhacCuaTui.com:443 -> www.nhaccuatui.com"""
    host = netloc.lower().strip(".")
    if host.endswith((":80", ":443")):
        host = host.rsplit(":", 1)[0]
    return config.DOMAIN_ALIASES.get(host, host)


def normalize_url(current_url, href):
    """
    Đưa mọi biến thể của cùng một trang về MỘT dạng chuẩn:
      - relative URL  -> absolute URL           (urljoin)
      - bỏ #fragment và ?query (?si=, ?st=...)
      - host viết thường, gộp www / không www
      - domain đang crawl: luôn dùng https
      - bỏ dấu / thừa ở cuối:  /news/1/  ->  /news/1
      - URL kiểu cũ -> kiểu mới (theo 'rewrite' trong config)
    Trả về None nếu không phải link web.
    """
    if not href:
        return None

    href = href.strip()

    if not href or href.startswith("#") or href.lower().startswith(IGNORED_SCHEMES):
        return None

    absolute_url, _ = urldefrag(urljoin(current_url, href))
    parsed = urlparse(absolute_url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    host = canonical_host(parsed.netloc)
    scheme = parsed.scheme

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    if host in ALLOW_RULES:
        scheme = "https"
        for pattern, repl in REWRITE_RULES[host]:
            path = pattern.sub(repl, path, count=1)

    return urlunparse((scheme, host, path, "", "", ""))


def get_domain(url):
    return urlparse(url).netloc


def check_url_rules(url, depth):
    """Trả về (True, "VALID") hoặc (False, lý_do)."""

    parsed = urlparse(url)

    # ---------- DEPTH ----------
    if depth > config.MAX_DEPTH:
        return False, "MAX_DEPTH"

    # ---------- PROTOCOL ----------
    if parsed.scheme not in ("http", "https"):
        return False, "INVALID_PROTOCOL"

    # ---------- DOMAIN: phải khớp đúng host đã khai báo ----------
    if parsed.netloc not in ALLOW_RULES:
        return False, "OUTSIDE_DOMAIN"

    # ---------- FILE TYPE ----------
    if parsed.path.lower().endswith(config.BLOCKED_EXTENSIONS):
        return False, "NON_HTML_RESOURCE"

    # ---------- FOCUSED CRAWLER: chỉ trang âm nhạc ----------
    if not any(rule.search(parsed.path) for rule in ALLOW_RULES[parsed.netloc]):
        return False, "NOT_MUSIC_PAGE"

    return True, "VALID"


def is_music_url(url):
    """URL thuộc domain đang crawl và là trang âm nhạc (bỏ qua depth)."""
    return check_url_rules(url, 0)[0] and urlparse(url).path != "/"
