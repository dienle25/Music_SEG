"""Quy tắc robots: nhóm user-agent cụ thể, đường dẫn khớp dài nhất, Allow thắng hòa.

Không dùng kết quả can_fetch làm bằng chứng cho phép theo điều khoản sử dụng.
"""
import re
import math
from urllib.parse import quote, urlsplit


def _octets(value):
    """Đồng nhất percent-encoding theo RFC 9309, không giải mã ký tự reserved."""
    value = quote(value, safe="/%?=&;:@!$'()*+,-._~")
    def convert(match):
        number = int(match.group()[1:], 16)
        char = chr(number)
        return char if char.isascii() and (char.isalnum() or char in '-._~') else match.group().upper()
    return re.sub(r'%[0-9a-fA-F]{2}', convert, value)


class RobotsRules:
    def __init__(self, text: str, user_agent: str):
        groups = []
        agents, rules, delay = [], [], 0.0
        has_directives = False
        for raw in text.splitlines() + ['User-agent: __end__']:
            line = raw.split('#', 1)[0].strip()
            if ':' not in line:
                continue
            key, value = [part.strip() for part in line.split(':', 1)]
            key = key.lower()
            if key == 'user-agent':
                if has_directives:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, has_directives = [], [], 0.0, False
                agents.append(value.lower())
            elif agents and key in ('allow', 'disallow', 'crawl-delay', 'request-rate'):
                has_directives = True
                if key in ('allow', 'disallow') and value:
                    rules.append((key == 'allow', value))
                elif key == 'crawl-delay':
                    try:
                        seconds = float(value)
                        if math.isfinite(seconds):
                            delay = max(delay, seconds)
                    except ValueError:
                        pass
                elif key == 'request-rate':
                    try:
                        count, seconds = map(float, value.split('/'))
                        if count > 0 and math.isfinite(seconds) and math.isfinite(count):
                            delay = max(delay, seconds / count)
                    except ValueError:
                        pass
        token = user_agent.split('/', 1)[0].lower()
        selected, longest = [], -1
        for agents, rules, delay in groups:
            matches = [0 if agent == '*' else len(agent) for agent in agents
                       if agent == '*' or agent in token]
            if not matches:
                continue
            specificity = max(matches)
            if specificity > longest:
                selected, longest = [], specificity
            if specificity == longest:
                selected.append((rules, delay))
        self.rules = [rule for rules, _ in selected for rule in rules]
        self.delay = max((delay for _, delay in selected), default=0.0)

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path or '/'
        if parts.query:
            target += '?' + parts.query
        target = _octets(target)
        matches = []
        for allow, pattern in self.rules:
            anchored = pattern.endswith('$')
            body = _octets(pattern[:-1] if anchored else pattern)
            expression = '^' + re.escape(body).replace(r'\*', '.*') + ('$' if anchored else '')
            if re.search(expression, target):
                matches.append((len(body.replace('*', '').encode('utf-8')), allow))
        return max(matches, default=(0, True))[1]
