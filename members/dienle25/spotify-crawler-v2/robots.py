# =========================================================
# robots.py  –  Kiểm tra robots.txt (mỗi domain đọc 1 lần)
# =========================================================

import hashlib
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


def normalize_robots(text):
    """
    Chuẩn hoá robots.txt trước khi đưa cho RobotFileParser.

    1. Gộp các nhóm trùng User-agent.
       NhacCuaTui có 2 nhóm "User-agent: *"; Python bản cũ chỉ đọc nhóm đầu
       (chỉ có "Allow: /") nên bỏ sót các dòng Disallow ở nhóm sau.
    2. Sắp xếp luật theo path dài -> ngắn (luật cụ thể hơn được ưu tiên,
       theo chuẩn RFC 9309). Python bản cũ dùng luật khớp ĐẦU TIÊN, nên
       "Allow: /" đứng đầu sẽ làm mọi Disallow bị bỏ qua
       (ví dụ Spotify: Allow: /  rồi mới  Disallow: /embed/).
    """
    groups = {}          # user-agent -> [(directive, path)]
    sitemaps = []
    agents = []
    in_rules = False

    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.strip()

        if key == "user-agent":
            if in_rules:                 # bắt đầu một nhóm mới
                agents, in_rules = [], False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])

        elif key in ("allow", "disallow"):
            in_rules = True
            for agent in agents:
                groups[agent].append((key, value))

        elif key == "sitemap":
            sitemaps.append(value)

    lines = []
    for agent, rules in groups.items():
        lines.append(f"User-agent: {agent}")
        # dài trước; cùng độ dài thì Allow trước
        for key, path in sorted(rules, key=lambda r: (-len(r[1]), r[0] != "allow")):
            lines.append(f"{key.capitalize()}: {path}")
        lines.append("")

    lines += [f"Sitemap: {url}" for url in sitemaps]
    return lines


class RobotsChecker:

    def __init__(self, session, bot_name, timeout):
        self.session = session
        self.bot_name = bot_name
        self.timeout = timeout
        self.cache = {}          # domain -> RobotFileParser | None (= cấm hết)
        self.info = {}           # domain -> mã HTTP, SHA-256, dòng Content-Signal (ghi vào bảng meta)

    def _load(self, scheme, domain):
        robots_url = f"{scheme}://{domain}/robots.txt"

        try:
            response = self.session.get(robots_url, timeout=self.timeout)
        except requests.RequestException as error:
            print(f"[ROBOTS] {domain}: lỗi kết nối ({error}) -> không crawl domain này")
            return None

        parser = RobotFileParser(robots_url)
        self.info[domain] = {
            "url": robots_url,
            "status": response.status_code,
            "sha256": hashlib.sha256(response.content).hexdigest(),
            # Content-Signal (vd "search=yes, ai-train=no"): RobotFileParser bỏ qua dòng này,
            # nên ghi lại để người dùng dữ liệu tự kiểm tra.
            "content_signal_lines": [
                line.strip() for line in response.text.splitlines()
                if line.strip().lower().startswith("content-signal")
            ] if response.status_code == 200 else [],
        }

        if response.status_code == 200:
            parser.parse(normalize_robots(response.text))
            print(f"[ROBOTS] {domain}: đã đọc robots.txt")
        elif response.status_code in (404, 410):
            parser.parse([])     # không có robots.txt -> được phép crawl
            print(f"[ROBOTS] {domain}: không có robots.txt")
        else:
            print(f"[ROBOTS] {domain}: HTTP {response.status_code} -> không crawl domain này")
            return None

        return parser

    def can_fetch(self, url):
        parsed = urlparse(url)
        domain = parsed.netloc

        if domain not in self.cache:
            self.cache[domain] = self._load(parsed.scheme, domain)

        parser = self.cache[domain]
        return parser is not None and parser.can_fetch(self.bot_name, url)
