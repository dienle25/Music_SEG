"""Harvest real tip-of-the-tongue song requests ("tìm tên bài hát") with the answers they got.

A record is a post written by someone looking for a song whose title they had forgotten, plus the
reply that named the song when the thread shows one. Nothing is generated or rewritten: a post that
cannot be fetched is skipped, and a post without an identified answer keeps answer_text = null.

Sources (--sources, comma separated, run in this order; one failing source does not stop the others):
  tomtkis  TOMT-KIS (Fröbe et al., QPP++@ECIR 2023): r/tipofmytongue questions with full comment
           trees and the answer its heuristic picked (Hugging Face, Apache-2.0; one 2-3 GB download).
  arctic   Arctic Shift API (independent Reddit archive): keyword search in r/tipofmytongue (from
           shortly before the end of TOMT-KIS on), the song-identification subreddits and the
           Vietnamese ones, plus the comment tree of every kept post.
  reddit   Reddit's Data API: OAuth when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set (and
           REDDIT_USERNAME/REDDIT_PASSWORD for a script app), else the public .json pages. Reddit
           closed unauthenticated access in 2026; a 403 ends this source.
  forum    XenForo 2 threads given with --thread (voz.vn ...): request posts, the replies that quote
           them, and the asker's confirmation. robots.txt is read first and obeyed.

Answers on Reddit come from the asker: the comment they replied "Solved!" to (asker_solved, the
r/tipofmytongue rule), a reply such as "that's it" / "đúng rồi" (asker_confirmed), a plain "thanks"
(asker_thanked) or their own "found it: ..." (asker_self). Without one, TOMT-KIS's chosen answer
(tomtkis_heuristic) or, on a post flaired Solved, the best top-level reply that looks like an answer
(flair_top). In forum threads: the quoting reply the asker confirmed, else the first quoting reply that
looks like an answer (reply). resolve_tot.py treats asker_thanked, flair_top and reply as weak.

Output, one JSON object per line: id, source, url, query, answer_text, created, and answer_method,
answer_links, answer_link_titles (YouTube/Spotify oEmbed titles of the answer's links), asker (salted
hash), community, via, flair, n_replies, has_vi_text, fragments (quoted spans of the query). Usernames
never appear: authors become salted hashes, and u/name, @name, e-mail addresses and phone numbers in
the texts are masked. The --cache folder keeps raw responses (with usernames): keep it private.

    python scripts/harvest_tot.py --sources tomtkis,arctic,reddit --out realq/harvest.jsonl --cache /tmp/realq
    python scripts/harvest_tot.py --sources forum --thread https://voz.vn/t/<slug>.187681/ --out realq/voz.jsonl
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import secrets
import time
import traceback
import unicodedata
import urllib.robotparser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import urlencode, urljoin, urlparse

USER_AGENT = "ViSongSearch-research/1.0"
ARCTIC = "https://arctic-shift.photon-reddit.com"
TOMTKIS_REPO = "webis/tip-of-my-tongue-known-item-search"

# Subreddits and their keywords. In "tot" subreddits (song identification) a post is kept when it is
# about music and points to Vietnam or holds Vietnamese text; in "vi" subreddits when it asks for a song.
SUBREDDITS = {
    "tipofmytongue": ("tot", ["vietnamese", "vietnam", "viet", "vpop", "v-pop", "vinahouse"]),
    "NameThatSong": ("tot", ["vietnamese", "vietnam", "viet", "vpop"]),
    "WhatsThisSong": ("tot", ["vietnamese", "vietnam", "viet", "vpop"]),
    "VietNam": ("vi", ["song", "bài hát", "tên bài", "nhạc", "music"]),
    "vietnamese": ("vi", ["song", "bài hát", "tên bài", "nhạc", "music"]),
    "TiengViet": ("vi", ["song", "bài hát", "nhạc"]),
    "vpop": ("vi", ["song", "find", "name", "bài"]),
}
BUSY = {"tipofmytongue", "namethatsong", "whatsthissong"}    # searched year by year on Arctic Shift

# ---------------------------------------------------------------- text tests

URL = re.compile(r"https?://[^\s<>\"'\])]+", re.I)
TAGS = re.compile(r"^\s*(?:\[[^\]]{1,40}\]\s*)+")
MUSIC_TAG = re.compile(r"\[\s*(?:songs?|music|mv|lyrics?|melody|tune|album|band|artist|singer|"
                       r"instrumental|soundtrack|ost)\b", re.I)
MUSIC_WORD = re.compile(r"\b(?:songs?|music|melody|tune|lyrics?|singers?|sung|ballad|karaoke|mv|"
                        r"soundtrack|ost|rap|ca khúc|bài hát|bai hat|nhạc|nhac|ca sĩ|ca si)\b", re.I)
MUSIC_LINK = re.compile(r"youtube\.com|youtu\.be|spotify\.com|soundcloud\.com|nhaccuatui\.com|zingmp3\.vn|"
                        r"mp3\.zing\.vn|hopamchuan\.com|hopamviet\.vn|nhac\.vn|music\.apple\.com|deezer\.com|"
                        r"tiktok\.com|chiasenhac\.vn", re.I)
VI_STRONG = re.compile(r"\b(?:vietnamese|viet(?!\s?nam\b)|việt|vpop|v-pop|vinahouse|nhạc việt|nhac viet)\b", re.I)
VIETNAM = re.compile(r"\bviet\s?nam\b|\bviệt\s?nam\b", re.I)
WAR = re.compile(r"\bviet\s?nam\s+(?:war|era|vets?|veterans?|movies?|films?|documentar\w*)\b|"
                 r"\bwar\s+in\s+viet\s?nam\b|\bnam\s+war\b", re.I)
_VI_ONLY = set("ăđơưĂĐƠƯ")
VI_PLAIN = frozenset("anh em oi khong nguoi yeu nhung duoc minh cua trong toi roi nhe voi thuong nho "
                     "buon chua dau nao mot nhieu ngay dem mua".split())

REQUEST = re.compile(
    r"\b(?:tìm|tim|kiếm|kiem|xin|hỏi|hoi|nhờ|giúp|giup)\s+(?:\w+\s+){0,4}?(?:tên\s+|ten\s+)?"
    r"(?:bài|bai|ca\s+khúc|ca\s+khuc)\b"
    r"|\btên\s+(?:của\s+)?(?:bài|ca\s+khúc)\b|\bten\s+(?:cua\s+)?bai\b"
    r"|\b(?:bài|bai)\s+(?:hát\s+|hat\s+)?(?:gì|gi|nào|nao)\b"
    r"|\bnhớ\s+mang\s+máng\b|\bnho\s+mang\s+mang\b"
    r"|\b(?:không|ko|k|chẳng|chả|khong)\s+(?:nhớ|nho)\s+(?:\w+\s+){0,2}?(?:tên|ten)\b"
    r"|\bquên\s+(?:mất\s+)?tên\b|\bquen\s+(?:mat\s+)?ten\b"
    r"|\btip\s+of\s+my\s+tongue\b|\btomt\b"
    r"|\bwhat(?:'?s|\s+is)\s+(?:the\s+)?(?:name|title)\s+of\s+(?:this|the|a|that)\s+(?:\w+\s+){0,3}?song\b"
    r"|\b(?:what|which)\s+song\s+is\s+(?:this|that|it)\b"
    r"|\b(?:looking\s+for|trying\s+to\s+find|help\s+(?:me\s+)?(?:find|identify)|identify|find)\s+"
    r"(?:a|this|the|an|that)\s+(?:\w+\s+){0,4}?song\b"
    r"|\b(?:can'?t|cannot|don'?t)\s+remember\s+(?:the\s+)?(?:name|title)\b"
    r"|\bforgot\s+(?:the\s+)?(?:name|title)\b|\bsong\s+name\s*\?", re.I)
ANSWER_LIKE = re.compile(
    r"\b(?:bài|bai)\s+(?:này|nay|đó|do|đấy|day|kia|ấy|ay)?\s*(?:là|la|tên\s+là|ten\s+la)\b"
    r"|\btên\s+(?:bài\s+)?(?:là|la)\b|\b(?:là|la)\s+bài\b|\b(?:của|cua|by|ft\.?|feat\.?)\s+\w"
    r"|\s[-–—]\s|[\"“”]|\bit'?s\s+(?:called\s+)?\w|\bcalled\b", re.I)
MEGATHREAD = re.compile(r"tổng hợp|tong hop|topic|thread|\bnơi\b|\bnoi\b|chung|hỏi đáp|hoi dap|góc|goc", re.I)

SOLVED = re.compile(r"(?<!not )(?<!n't )\bsolved\b", re.I)
CONFIRM = re.compile(
    r"\b(?:that'?s\s+(?:it|the\s+one|the\s+song|exactly\s+it)|this\s+is\s+(?:it|the\s+one)|that\s+is\s+it|"
    r"yes+|yup|omg|found\s+it|bingo|exactly|you\s+(?:got|found)\s+it|"
    r"you'?re\s+(?:a|the)\s+(?:legend|hero|goat|god))\b"
    r"|đúng\s+rồi|\bdung\s+roi\b|đúng\s+(?:bài|nó|là\s+nó)|\bdung\s+(?:bai|no)\b|chính\s+(?:nó|là\s+nó|xác)|"
    r"\bchinh\s+no\b|chuẩn\s+(?:rồi|luôn|bài)|\bchuan\s+(?:roi|luon)\b|\bnó\s+đây\b|đây\s+rồi|\bday\s+roi\b|"
    r"\b(?:tìm|kiếm)\s+(?:được|ra|thấy)\s+rồi|\btim\s+(?:duoc|ra|thay)\s+roi\b", re.I)
THANKS = re.compile(r"\b(?:thanks?|thank\s+you|thx|tysm|ty|tks|cảm\s+ơn|cám\s+ơn|cam\s+on|camon)\b", re.I)
NEGATIVE = re.compile(
    r"\b(?:not\s+(?:it|this|that|the\s+(?:one|song|right\s+one))|isn'?t\s+(?:it|the\s+one|the\s+song)|"
    r"nope|unfortunately|sadly|wrong|but\s+no)\b|\bno\s*,"
    r"|không\s+phải|\bko\s+phải|\bk\s+phải|hông\s+phải|chưa\s+phải|\bkhong\s+phai\b|\bko\s+phai\b|"
    r"\bchua\s+phai\b|sai\s+rồi|(?:không|ko|chưa)\s+đúng|\bkhong\s+dung\b", re.I)
SELF_FOUND = re.compile(
    r"\b(?:found\s+it|i\s+found|figured\s+it\s+out|nvm|never\s*mind)\b"
    r"|\b(?:mình|em|tớ|tôi|minh)\s+(?:đã\s+)?(?:tìm|kiếm|tim)\s+(?:được|ra|thấy|duoc|thay)"
    r"|\b(?:tìm|tim)\s+(?:được|ra|thấy|duoc|thay)\s+(?:rồi|roi)\b", re.I)
BOTS = {"automoderator", "tipofmytongue", "tomtbot", "auddbot", "songfinderbot", "find-song", "redditmp3"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MENTION = re.compile(r"(?<![\w/])/?u/[\w-]{2,30}|(?<![\w.@])@[\w.]{2,30}")
USER_URL = re.compile(r"reddit\.com/(u|user)/[\w-]+", re.I)
PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)(?:[\s.-]?\d){8,10}(?!\d)")
QUOTED = re.compile(r"[\"“]([^\"“”\n]{3,200})[\"”]")


def words(text: str) -> list[str]:
    return re.findall(r"\w+", URL.sub(" ", text or ""))


def scrub(text: str) -> str:
    """Mask e-mail addresses, u/ and @ mentions, Reddit user links and phone numbers."""
    text = EMAIL.sub("[email]", text or "")
    text = USER_URL.sub(lambda m: f"reddit.com/{m.group(1)}/[user]", text)
    text = MENTION.sub("[user]", text)
    return PHONE.sub("[phone]", text)


def _vi_word(tok: str) -> bool:
    # "Ạ".."ỹ" is U+1EA0..U+1EF9, the Latin Extended Additional block of Vietnamese toned letters
    return any(c in _VI_ONLY or "Ạ" <= c <= "ỹ" for c in tok)


def has_vi_text(text: str) -> bool:
    """Vietnamese words in the text: two with a mark only Vietnamese uses, or four common unmarked ones."""
    toks = re.findall(r"\w+", unicodedata.normalize("NFC", text or "").lower())
    return sum(map(_vi_word, toks)) >= 2 or len(VI_PLAIN.intersection(toks)) >= 4


def mentions_vietnam(text: str) -> bool:
    """'Vietnamese', 'V-pop', 'nhạc Việt' ...; a bare 'Vietnam' counts unless it is 'the Vietnam war'."""
    return bool(VI_STRONG.search(text) or VIETNAM.search(WAR.sub(" ", text)))


def is_music(title: str, body: str = "", tags=()) -> bool:
    if MUSIC_TAG.search(title) or any(MUSIC_TAG.search(t) for t in tags):
        return True
    return bool(MUSIC_WORD.search(title) or MUSIC_WORD.search(body[:500]))


def is_tot_song(title: str, body: str, tags=()) -> bool:
    """A tip-of-the-tongue post about a Vietnamese song (r/tipofmytongue and the like)."""
    text = f"{title}\n{body}"
    return is_music(title, body, tags) and (mentions_vietnam(text) or has_vi_text(text))


def is_song_request(title: str, body: str) -> bool:
    """A post that asks for the name of a song (Vietnamese subreddits)."""
    text = f"{title}\n{body[:2000]}"
    return bool(REQUEST.search(text) and (MUSIC_WORD.search(text) or re.search(r"\bt[êe]n\s+b[àa]i\b", text, re.I)))


def keep_post(kind: str, title, body, tags=()) -> bool:
    title, body = html.unescape(title or ""), html.unescape(body or "")
    return is_tot_song(title, body, tags) if kind == "tot" else is_song_request(title, body)


def looks_like_answer(text: str) -> bool:
    return bool(MUSIC_LINK.search(text) or (ANSWER_LIKE.search(text) and len(words(text)) <= 80))


def is_bot(name: str) -> bool:
    n = (name or "").lower()
    return n in BOTS or n.endswith("bot")


def iso_time(x) -> str | None:
    if x in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(float(x)), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", str(x).strip()).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Anon:
    """Salted hash of a username; the salt is never written, so hashes link posts only within one run
    (or across runs that share VSS_ANON_SALT)."""

    def __init__(self, salt: str):
        self.salt = salt

    def __call__(self, name) -> str | None:
        if not name or name in ("[deleted]", "[removed]"):
            return None
        return "u" + hashlib.sha256(f"{self.salt}\0{name.lower()}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------- answers in comment trees

class Answer(NamedTuple):
    text: str
    method: str
    links: tuple = ()
    link_titles: tuple = ()


def flatten_comments(node, parent=None, out=None, path="c") -> list[dict]:
    """Comments of a Reddit listing, an Arctic Shift tree or TOMT-KIS's nested lists, in tree order:
    dicts with id, parent (None for a top-level comment), author, body, score."""
    out = [] if out is None else out
    if isinstance(node, list):
        for i, n in enumerate(node):
            flatten_comments(n, parent, out, f"{path}.{i}")
        return out
    if not isinstance(node, dict) or node.get("kind") == "more":
        return out
    if node.get("kind") == "Listing":
        return flatten_comments((node.get("data") or {}).get("children") or [], parent, out, path)
    data = node["data"] if isinstance(node.get("data"), dict) and node.get("kind") else node
    if "body" not in data:                         # a wrapper such as an API answer {"data": [...]}
        for key in ("data", "children", "comments", "replies"):
            if isinstance(data.get(key), (list, dict)):
                flatten_comments(data[key], parent, out, path)
        return out
    cid = str(data.get("id") or data.get("name") or path)
    cid = cid[3:] if cid.startswith("t1_") else cid
    pid = data.get("parent_id")
    par = pid[3:] if isinstance(pid, str) and pid.startswith("t1_") else (
        None if isinstance(pid, str) and pid.startswith("t3_") else parent)
    out.append({"id": cid, "parent": par, "author": data.get("author") or "",
                "body": html.unescape(data.get("body") or ""), "score": data.get("score") or 0})
    for key in ("replies", "children", "comments"):
        # replies may sit in the comment ({"kind": "t1", "data": {..., "replies": ...}}) or beside it
        ch = data.get(key) or (node.get(key) if node is not data else None)
        if isinstance(ch, (list, dict)) and ch:
            flatten_comments(ch, cid, out, f"{path}.{key[0]}")
    return out


def pick_answer(op, comments: list[dict], flair=None) -> Answer | None:
    """The answer the asker (op) accepted; see the module docstring for the order of the rules."""
    op = op if op and op not in ("[deleted]", "[removed]") else None
    by_id = {c["id"]: c for c in comments}

    def usable(c):
        return (c is not None and c["author"] != op and not is_bot(c["author"])
                and c["body"].strip() not in ("", "[deleted]", "[removed]"))

    best = None
    for c in comments if op else []:
        if c["author"] != op or NEGATIVE.search(c["body"]):
            continue
        parent = by_id.get(c["parent"]) if c["parent"] else None
        cand = None
        if usable(parent):
            for rank, rule, method in ((3, SOLVED, "asker_solved"), (2, CONFIRM, "asker_confirmed"),
                                       (1, THANKS, "asker_thanked")):
                if rule.search(c["body"]):
                    cand = (rank, parent["body"], method)
                    break
        if cand is None and SELF_FOUND.search(c["body"]) and len(words(c["body"])) >= 5:
            cand = (2, c["body"], "asker_self")
        if cand and (best is None or cand[0] > best[0]):
            best = cand
    if best:
        return Answer(best[1], best[2])
    if flair and "solved" in flair.lower() and "unsolved" not in flair.lower():
        tops = [c for c in comments if c["parent"] is None and usable(c) and looks_like_answer(c["body"])]
        if tops:
            return Answer(max(tops, key=lambda c: c["score"] or 0)["body"], "flair_top")
    return None


METHOD_RANK = {"asker_solved": 6, "asker_confirmed": 5, "asker_self": 5, "tomtkis_heuristic": 4,
               "asker_thanked": 3, "flair_top": 2, "reply": 2}


def make_record(ctx, source, native_id, url, title, body, created, answer: Answer | None, author,
                community, via, flair=None, n_replies=None) -> dict | None:
    title = TAGS.sub("", html.unescape(title or "")).strip()
    body = html.unescape(body or "").strip()
    if body in ("[deleted]", "[removed]"):
        body = ""
    query = scrub("\n\n".join(x for x in (title, body) if x))
    if len(words(query)) < ctx.args.min_words:
        return None
    text = scrub(html.unescape(answer.text)).strip() if answer and answer.text else ""
    links = list(dict.fromkeys(URL.findall(text) + [scrub(u) for u in (answer.links if answer else ())]))
    return {"id": f"{source}:{native_id}", "source": source, "url": url, "query": query,
            "answer_text": text or None, "created": iso_time(created),
            "answer_method": answer.method if text else None, "answer_links": links,
            "answer_link_titles": list(answer.link_titles) if text else [],
            "asker": ctx.anon(author), "community": community, "via": via, "flair": flair,
            "n_replies": n_replies, "has_vi_text": has_vi_text(query),
            "fragments": [f.strip() for f in QUOTED.findall(query) if len(f.split()) >= 2]}


# ---------------------------------------------------------------- HTTP

class Blocked(Exception):
    """The site refuses automated access (HTTP 401/403, a login wall or a bot challenge)."""


def _challenge(r) -> bool:
    if r.headers.get("cf-mitigated") == "challenge":
        return True
    if "html" not in r.headers.get("content-type", "") or r.status_code < 400:
        return False
    head = r.text[:5000]
    return any(s in head for s in ("cf-chl", "challenge-platform", "Just a moment...", "Attention Required!"))


def _retry_after(r, default: float) -> float:
    for h in ("Retry-After", "X-RateLimit-Reset", "x-ratelimit-reset"):
        try:
            return min(600.0, max(1.0, float(r.headers[h])))
        except (KeyError, ValueError):
            continue
    return min(600.0, default)


class Http:
    """requests with a User-Agent, a minimum gap per host, retries on 429/5xx (honouring Retry-After
    and rate-limit headers) and an optional cache of successful responses."""

    def __init__(self, user_agent=USER_AGENT, cache=None, delay=1.0, delays=None, timeout=60, log=print):
        import requests
        self.requests = requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "vi,en;q=0.8"})
        self.user_agent, self.cache, self.delay, self.timeout, self.log = user_agent, cache, delay, timeout, log
        self.delays = dict(delays or {})
        self.counts = Counter()
        self._last = {}

    def _pause(self, host):
        wait = self._last.get(host, -1e9) + self.delays.get(host, self.delay) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def status_text(self, url):
        """(status, text) without retries or exceptions (robots.txt)."""
        self._pause(urlparse(url).netloc)
        try:
            r = self.session.get(url, timeout=self.timeout)
        except self.requests.RequestException:
            return None, ""
        return r.status_code, r.text

    def get(self, url, params=None, headers=None, as_json=True, retries=4, soft=False):
        """JSON (or text) of a GET; None for 404 (any error when soft). JSON error bodies of other 4xx
        answers are returned as they are, so callers can read their "error" field."""
        if params:
            q = urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{q}" if q else url
        path = os.path.join(self.cache, "http", hashlib.sha1(url.encode()).hexdigest()) if self.cache else None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                txt = f.read()
            return json.loads(txt) if as_json else txt
        host = urlparse(url).netloc
        for attempt in range(retries + 1):
            self._pause(host)
            try:
                r = self.session.get(url, headers=headers, timeout=self.timeout)
            except self.requests.RequestException as e:
                if attempt < retries:
                    time.sleep(min(120, 5 * 2 ** attempt))
                    continue
                if soft:
                    return None
                raise RuntimeError(f"{host}: {type(e).__name__}") from e
            self.counts[host] += 1
            if r.status_code in (401, 403) or _challenge(r):
                if soft:
                    return None
                raise Blocked(f"{host}: HTTP {r.status_code}")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < retries:
                    wait = _retry_after(r, 10 * 2 ** attempt)
                    self.log(f"  {host}: HTTP {r.status_code}, waiting {wait:.0f} s")
                    time.sleep(wait)
                    continue
                if soft:
                    return None
                raise RuntimeError(f"{host}: HTTP {r.status_code} after {retries + 1} tries")
            if r.status_code == 404 or (soft and r.status_code >= 400):
                return None
            if r.status_code >= 400:
                try:
                    payload = r.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and "error" in payload:
                    return payload
                raise RuntimeError(f"{host}: HTTP {r.status_code}")
            remaining, reset = r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset")
            try:
                if remaining is not None and float(remaining) < 2 and reset:
                    time.sleep(min(600.0, float(reset) + 1))
            except ValueError:
                pass
            txt = r.text
            try:
                data = json.loads(txt) if as_json else txt
            except ValueError as e:
                if soft:
                    return None
                raise RuntimeError(f"{host}: answer is not JSON") from e
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt)
            return data
        return None


class Robots:
    """robots.txt per site. Unreadable (401/403, network error, 5xx) means: do not fetch."""

    def __init__(self, http: Http, log=print):
        self.http, self.log, self.parsers = http, log, {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        rp = self.parsers.get(root)
        if rp is None:
            status, txt = self.http.status_text(root + "/robots.txt")
            rp = urllib.robotparser.RobotFileParser()
            if status == 200:
                rp.parse(txt.splitlines())
                delay = rp.crawl_delay(self.http.user_agent)
                if delay:
                    self.http.delays[p.netloc] = max(self.http.delays.get(p.netloc, self.http.delay), float(delay))
            elif status is not None and 400 <= status < 500 and status not in (401, 403):
                rp.allow_all = True
            else:
                rp.disallow_all = True
            self.parsers[root] = rp
            self.log(f"  robots.txt of {p.netloc}: HTTP {status}")
        return rp.can_fetch(self.http.user_agent, url)


# ---------------------------------------------------------------- sources

class Ctx:
    def __init__(self, args, http, anon, log):
        self.args, self.http, self.anon, self.log = args, http, anon, log
        self.st = Counter()            # counters of the running source
        self.tomtkis_max = None        # newest post time seen in TOMT-KIS (epoch seconds)


# Cheap test on a raw (lowercased) TOMT-KIS line before parsing it: Vietnam words, Vietnamese letters
# (U+1EA0..U+1EF9 and ă đ ơ ư), the same letters as JSON escapes, or common unmarked Vietnamese words.
_PRE = re.compile(r"viet|vpop|v-pop|vinahouse|[ăđơưẠ-ỹ]|\\u1e[a-f][0-9a-f]|\\u01a[01]|\\u01b0|"
                  r"\\u0111|\\u0103|\b(?:khong|nguoi|yeu)\b")
_CREATED = re.compile(r'"created_utc"\s*:\s*"?(\d{9,10})')


def _note_created(ctx, value):
    try:
        t = int(float(value))
    except (TypeError, ValueError):
        return
    ctx.tomtkis_max = max(ctx.tomtkis_max or 0, t)


def _tomtkis_rows(ctx, path):
    """Rows of one TOMT-KIS file that may be about a Vietnamese song (cheap test before parsing)."""
    if path.endswith(".parquet"):
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        cols = set(pf.schema_arrow.names)
        light = [c for c in ("title", "content", "categories", "created_utc") if c in cols]
        for g in range(pf.num_row_groups):
            part = pf.read_row_group(g, columns=light).to_pydict()
            n = len(next(iter(part.values()))) if part else 0
            ctx.st["scanned"] += n
            for v in part.get("created_utc", []):
                _note_created(ctx, v)
            titles, contents = part.get("title") or [""] * n, part.get("content") or [""] * n
            cats = part.get("categories") or [[]] * n
            keep = [i for i in range(n) if is_tot_song(titles[i] or "", contents[i] or "", cats[i] or [])]
            if keep:
                yield from pf.read_row_group(g).take(keep).to_pylist()
        return
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            ctx.st["scanned"] += 1
            m = _CREATED.search(line, 0, 4000)
            if m:
                _note_created(ctx, m.group(1))
            if _PRE.search(line.lower()):
                yield json.loads(line)


def tomtkis_record(ctx, row) -> dict | None:
    title, content = row.get("title") or "", row.get("content") or ""
    cats = row.get("categories") or []
    cats = [cats] if isinstance(cats, str) else list(cats)
    if not is_tot_song(html.unescape(title), html.unescape(content), cats):
        return None
    comments = flatten_comments(row.get("comments") or [])
    ans = pick_answer(row.get("author"), comments, row.get("link_flair_text"))
    # links_on_answer_path is not used: it also holds the links of wrong guesses above the answer
    if (ans is None or METHOD_RANK.get(ans.method, 0) < METHOD_RANK["tomtkis_heuristic"]) \
            and row.get("answer_detected") and row.get("chosen_answer"):
        ans = Answer(row["chosen_answer"], "tomtkis_heuristic")
    pid = str(row.get("id"))
    link = row.get("permalink") or ""
    url = "https://www.reddit.com" + link if link.startswith("/") else (
        row.get("url") or f"https://www.reddit.com/comments/{pid}/")
    return make_record(ctx, "reddit", pid, url, title, content, row.get("created_utc"), ans, row.get("author"),
                       "r/tipofmytongue", "tomtkis", row.get("link_flair_text"), len(comments))


def source_tomtkis(ctx):
    """TOMT-KIS from the Hugging Face hub: the original files of the main branch, else the parquet copy."""
    from huggingface_hub import HfApi, hf_hub_download
    repo, api, files = ctx.args.tomtkis_repo, HfApi(), []
    for rev in ("main", "refs/convert/parquet"):
        try:
            names = api.list_repo_files(repo, repo_type="dataset", revision=rev)
        except Exception as e:            # noqa: BLE001 (any hub error: try the other branch)
            ctx.log(f"  {repo}@{rev}: {type(e).__name__}: {e}")
            continue
        data = sorted(n for n in names if n.endswith((".jsonl.gz", ".jsonl", ".json.gz", ".parquet")))
        if data:
            files = [(rev, n) for n in data]
            break
    if not files:
        raise RuntimeError(f"no data files found in {repo}")
    ctx.log(f"  {len(files)} file(s): {', '.join(n for _, n in files[:5])}{' ...' if len(files) > 5 else ''}")
    cache = os.path.join(ctx.args.cache, "hf") if ctx.args.cache else None
    for rev, name in files:
        path = hf_hub_download(repo, name, repo_type="dataset", revision=rev, cache_dir=cache)
        for row in _tomtkis_rows(ctx, path):
            rec = tomtkis_record(ctx, row)
            if rec:
                yield rec


def _windows(start: datetime, end: datetime, days: int):
    while start < end:
        nxt = min(end, start + timedelta(days=days))
        yield start, nxt
        start = nxt


def arctic_search(ctx, sub, kw, start: datetime, end: datetime):
    """Posts of r/sub with the keyword, oldest first. A window that fails is cut into months."""
    params = {"subreddit": sub, "query": kw, "limit": 100, "sort": "asc",
              "after": start.strftime("%Y-%m-%d"), "before": end.strftime("%Y-%m-%d"),
              "fields": "id,title,selftext,author,created_utc,link_flair_text,num_comments,subreddit"}
    while True:
        try:
            js = ctx.http.get(ARCTIC + "/api/posts/search", params, retries=2)
        except RuntimeError as e:
            js = {"error": str(e)}
        err = (js or {}).get("error") if isinstance(js, dict) else "unexpected answer"
        if err:
            if "not supported" not in str(err).lower() and (end - start).days > 40:
                for a, b in _windows(start, end, 31):
                    yield from arctic_search(ctx, sub, kw, a, b)
                return
            ctx.log(f"  arctic r/{sub} '{kw}' {start:%Y-%m-%d}..{end:%Y-%m-%d}: {str(err)[:150]}")
            ctx.st["errors"] += 1
            return
        data = js.get("data") or []
        yield from data
        if len(data) < 100:
            return
        last = int(max(float(d.get("created_utc") or 0) for d in data))
        params["after"] = last if str(last) != str(params["after"]) else last + 1


def source_arctic(ctx):
    now = datetime.now(timezone.utc) + timedelta(days=1)
    since = datetime.fromisoformat(ctx.args.since).replace(tzinfo=timezone.utc)
    seen = set()
    for sub, (kind, kws) in SUBREDDITS.items():
        start = since
        if sub == "tipofmytongue" and ctx.args.arctic_since == "auto" and ctx.tomtkis_max:
            start = datetime.fromtimestamp(ctx.tomtkis_max, tz=timezone.utc) - timedelta(days=60)
        elif ctx.args.arctic_since != "auto":
            start = datetime.fromisoformat(ctx.args.arctic_since).replace(tzinfo=timezone.utc)
        span = 366 if sub.lower() in BUSY else 100000
        kept = []
        for kw in kws:
            for a, b in _windows(start, now, span):
                for p in arctic_search(ctx, sub, kw, a, b):
                    if not p.get("id") or p["id"] in seen:
                        continue
                    seen.add(p["id"])
                    ctx.st["scanned"] += 1
                    if keep_post(kind, p.get("title"), p.get("selftext")):
                        kept.append(p)
        ctx.log(f"  r/{sub}: {len(kept)} posts kept since {start:%Y-%m-%d}")
        for p in kept:
            js = ctx.http.get(ARCTIC + "/api/comments/tree", {"link_id": f"t3_{p['id']}", "limit": 9999},
                              retries=2, soft=True)
            comments = flatten_comments((js or {}).get("data") or []) if isinstance(js, dict) else []
            if not comments and (p.get("num_comments") or 0) > 1:
                ctx.st["comment_trees_unread"] += 1      # many of these: the tree format has changed
            p.setdefault("subreddit", sub)
            rec = reddit_record(ctx, p, comments, "arctic_shift")
            if rec:
                yield rec


def reddit_record(ctx, p, comments, via):
    sub, pid = p.get("subreddit") or "", p["id"]
    ans = pick_answer(p.get("author"), comments, p.get("link_flair_text"))
    return make_record(ctx, "reddit", pid, f"https://www.reddit.com/r/{sub}/comments/{pid}/", p.get("title"),
                       p.get("selftext"), p.get("created_utc"), ans, p.get("author"), f"r/{sub}", via,
                       p.get("link_flair_text"), len(comments))


def reddit_token(ctx):
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None
    user, pw = os.environ.get("REDDIT_USERNAME"), os.environ.get("REDDIT_PASSWORD")
    data = {"grant_type": "password", "username": user, "password": pw} if user and pw else \
        {"grant_type": "client_credentials"}
    try:
        r = ctx.http.session.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret), data=data,
                                  timeout=ctx.http.timeout)
        token = r.json().get("access_token") if r.status_code == 200 else None
        status = r.status_code
    except (ctx.http.requests.RequestException, ValueError) as e:
        token, status = None, type(e).__name__
    ctx.log(f"  reddit OAuth: {'token received' if token else f'refused ({status})'}")
    return token


def source_reddit(ctx):
    token = reddit_token(ctx)
    if token:
        base, suffix, headers = "https://oauth.reddit.com", "", {"Authorization": f"bearer {token}"}
        ctx.http.delays["oauth.reddit.com"] = max(ctx.http.delays.get("oauth.reddit.com", 0.0), 1.0)
    else:
        base, suffix, headers = "https://www.reddit.com", ".json", None
        ctx.http.delays["www.reddit.com"] = max(ctx.http.delays.get("www.reddit.com", 0.0), 6.5)
        ctx.log("  reddit: no OAuth credentials, trying the public .json pages (at most 9 requests a minute)")
    seen = set()
    for sub, (kind, kws) in SUBREDDITS.items():
        for kw in kws:
            after = None
            for _ in range(ctx.args.reddit_pages):
                js = ctx.http.get(f"{base}/r/{sub}/search{suffix}",
                                  {"q": kw, "restrict_sr": 1, "sort": "new", "t": "all", "limit": 100,
                                   "raw_json": 1, "after": after}, headers=headers)
                listing = (js or {}).get("data") or {} if isinstance(js, dict) else {}
                for ch in listing.get("children") or []:
                    p = ch.get("data") or {}
                    if not p.get("id") or p["id"] in seen:
                        continue
                    seen.add(p["id"])
                    ctx.st["scanned"] += 1
                    if not keep_post(kind, p.get("title"), p.get("selftext")):
                        continue
                    tree = ctx.http.get(f"{base}/comments/{p['id']}{suffix}",
                                        {"raw_json": 1, "limit": 500, "depth": 10, "sort": "old"}, headers=headers)
                    comments = flatten_comments(tree[1] if isinstance(tree, list) and len(tree) > 1 else [])
                    rec = reddit_record(ctx, p, comments, "reddit_api" if token else "reddit_json")
                    if rec:
                        yield rec
                after = listing.get("after")
                if not after:
                    break


def parse_xenforo(page_html: str, page_url: str):
    """Thread title, posts and last page number of one XenForo 2 thread page. A post: id, author,
    created, text (without quotes), quotes [{post, author, text}], links, link_titles (link previews)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "html.parser")
    h1 = soup.select_one("h1.p-title-value") or soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    posts = []
    for art in soup.select("article.message"):
        m = re.search(r"(\d+)", art.get("data-content") or art.get("id") or "")
        body = art.select_one(".message-body .bbWrapper") or art.select_one(".bbWrapper")
        if not m or body is None:
            continue
        author = art.get("data-author") or ""
        if not author:
            u = art.select_one(".message-name, .username")
            author = u.get_text(strip=True) if u else ""
        t = art.select_one("time[data-time], time[datetime]")
        quotes = []
        for bq in body.select("blockquote.bbCodeBlock--quote, blockquote[data-quote]"):
            if bq.find_parent("blockquote") is not None:
                continue
            src = re.search(r"(\d+)", bq.get("data-source") or "")
            inner = bq.select_one(".bbCodeBlock-expandContent") or bq.select_one(".bbCodeBlock-content") or bq
            quotes.append({"post": src.group(1) if src else None, "author": bq.get("data-quote") or "",
                           "text": inner.get_text(" ", strip=True)})
            bq.decompose()
        links, link_titles = [], []
        for un in body.select(".bbCodeBlock--unfurl"):
            if un.get("data-url"):
                links.append(un["data-url"])
            h = un.select_one(".contentRow-header") or un.find(["h3", "h4"])
            if h:
                link_titles.append(h.get_text(" ", strip=True))
            un.decompose()
        links += [urljoin(page_url, a["href"]) for a in body.select("a[href]")]
        links += [urljoin(page_url, f["src"]) for f in body.select("iframe[src]")]
        links += [f"https://www.youtube.com/watch?v={e['data-media-key']}"
                  for e in body.select("[data-media-site-id=youtube][data-media-key]")]
        for tag in body.select("script, style, noscript"):
            tag.decompose()
        posts.append({"id": m.group(1), "author": author,
                      "created": (t.get("data-time") or t.get("datetime")) if t is not None else None,
                      "text": body.get_text("\n", strip=True), "quotes": quotes,
                      "links": [u for u in dict.fromkeys(links) if u.startswith("http")], "link_titles": link_titles})
    last = 1
    for a in soup.select(".pageNav-main a, .pageNav-page a"):
        mm = re.search(r"page-(\d+)", a.get("href") or "")
        last = max(last, int(mm.group(1)) if mm else (int(a.get_text(strip=True)) if a.get_text(strip=True).isdigit() else 1))
    return title, posts, last


def forum_records(ctx, posts, thread_title, thread_url):
    """Request/answer pairs of one thread (see the module docstring)."""
    p = urlparse(thread_url)
    labels = [x for x in p.netloc.lower().split(".") if x not in ("www", "forum", "forums", "diendan")]
    source = labels[0] if labels else p.netloc
    order = {q["id"]: i for i, q in enumerate(posts)}
    by_id = {q["id"]: q for q in posts}
    quoted_by = defaultdict(list)
    for q in posts:
        for qu in q["quotes"]:
            tgt = by_id.get(qu["post"])
            if tgt is not None and tgt["author"] != q["author"] and order[tgt["id"]] < order[q["id"]]:
                quoted_by[tgt["id"]].append(q)

    def answer_like(q):
        return looks_like_answer(" ".join([q["text"]] + q["links"] + q["link_titles"]))

    single = bool(posts) and bool(REQUEST.search(thread_title)) and not MEGATHREAD.search(thread_title)
    for i, r in enumerate(posts):
        if single and i > 0:
            break
        # a request opens a single-question thread, or is a post that quotes nobody and either asks in
        # its own words or was answered by a quoting reply. A post that quotes is a reply: an answer, a
        # "me too" or a bump of one's own request, never a new request.
        is_req = (i == 0 and REQUEST.search(thread_title)) or (not r["quotes"] and (
            REQUEST.search(r["text"]) or any(answer_like(c) for c in quoted_by[r["id"]])))
        if not is_req:
            continue
        cands = [c for c in posts[i + 1:] if c["author"] != r["author"]] if single else quoted_by[r["id"]]
        answer, method = None, None
        for later in posts[i + 1:]:
            if later["author"] != r["author"] or NEGATIVE.search(later["text"]):
                continue
            quoted = [by_id[qu["post"]] for qu in later["quotes"] if qu["post"] in by_id]
            hit = next((c for c in quoted if c in cands), None)
            if hit is None and single:
                before = [c for c in cands if order[c["id"]] < order[later["id"]]]
                hit = before[-1] if before else None
            if hit is not None and (CONFIRM.search(later["text"]) or THANKS.search(later["text"])):
                answer, method = hit, "asker_confirmed" if CONFIRM.search(later["text"]) else "asker_thanked"
                break
            if SELF_FOUND.search(later["text"]) and len(words(later["text"])) >= 5 and \
                    (single or any(c is r for c in quoted)):
                answer, method = later, "asker_self"
                break
        if answer is None:
            answer = next((c for c in cands if answer_like(c)), None)
            method = "reply" if answer is not None else None
        ans = Answer(answer["text"], method, tuple(answer["links"]), tuple(answer["link_titles"])) if answer else None
        head = thread_title if single else ""
        rec = make_record(ctx, source, r["id"], f"{p.scheme}://{p.netloc}/goto/post?id={r['id']}", head, r["text"],
                          r["created"], ans, r["author"], f"{p.netloc}: {thread_title}"[:120], "xenforo",
                          None, len(quoted_by[r["id"]]))
        if rec:
            yield rec


def thread_base(url: str) -> str:
    url = re.sub(r"(/page-\d+)?/?(#.*)?$", "", url.strip())
    return url + "/"


def source_forum(ctx):
    robots = Robots(ctx.http, ctx.log)
    for thread in ctx.args.thread:
        base = thread_base(thread)
        ctx.http.delays.setdefault(urlparse(base).netloc, 3.0)      # HTML pages: one every 3 s at most
        if not robots.allowed(base):
            ctx.log(f"  {base}: not allowed by robots.txt, skipped")
            continue
        try:
            first = ctx.http.get(base, as_json=False)
            if not first:
                ctx.log(f"  {base}: not found")
                continue
            title, posts, last = parse_xenforo(first, base)
            pages = range(2, last + 1) if not ctx.args.forum_max_pages else range(2, min(last, ctx.args.forum_max_pages) + 1)
            for n in pages:
                url = f"{base}page-{n}"
                if not robots.allowed(url):
                    break
                posts += parse_xenforo(ctx.http.get(url, as_json=False) or "", url)[1]
        except Blocked as e:
            ctx.log(f"  {base}: {e}; thread skipped")
            ctx.st["errors"] += 1
            continue
        ctx.st["scanned"] += len(posts)
        ctx.log(f"  {base}: '{title}', {last} page(s), {len(posts)} posts parsed")
        yield from forum_records(ctx, posts, title, base)


SOURCES = {"tomtkis": source_tomtkis, "arctic": source_arctic, "reddit": source_reddit, "forum": source_forum}

# ---------------------------------------------------------------- link titles and main

YOUTUBE_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|embed/|shorts/|live/|v/))([\w-]{11})")


def link_titles(ctx, links) -> list[str]:
    """Titles (and channel) of up to three YouTube/Spotify links, from their public oEmbed endpoints."""
    out = []
    for u in links:
        m = YOUTUBE_ID.search(u)
        if m:
            endpoint, target = "https://www.youtube.com/oembed", f"https://www.youtube.com/watch?v={m.group(1)}"
        elif "open.spotify.com/" in u:
            endpoint, target = "https://open.spotify.com/oembed", u.split("?")[0]
        else:
            continue
        js = ctx.http.get(endpoint, {"url": target, "format": "json"}, retries=1, soft=True)
        if isinstance(js, dict) and js.get("title"):
            out.append(" | ".join(x for x in (js.get("title"), js.get("author_name")) if x))
        if len(out) >= 3:
            break
    return out


def merge(records: dict, rec: dict):
    """One record per post: the copy with the stronger answer wins."""
    old = records.get(rec["id"])
    if old is None or METHOD_RANK.get(rec["answer_method"], 0) > METHOD_RANK.get(old["answer_method"], 0):
        records[rec["id"]] = rec


def write_jsonl(path, records):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in sorted(records.values(), key=lambda r: (r["created"] or "", r["id"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sources", default="tomtkis,arctic,reddit", help=", ".join(SOURCES))
    ap.add_argument("--out", required=True, help="output .jsonl (a .log and a _stats.json are written next to it)")
    ap.add_argument("--cache", help="folder for downloads and raw responses (they hold usernames: keep it private)")
    ap.add_argument("--thread", action="append", default=[], help="XenForo thread URL for --sources forum")
    ap.add_argument("--since", default="2008-01-01", help="earliest post date on Arctic Shift")
    ap.add_argument("--arctic-since", default="auto",
                    help="start of r/tipofmytongue on Arctic Shift: a date, or auto = 60 days before the newest "
                         "TOMT-KIS post when tomtkis ran first (else --since)")
    ap.add_argument("--reddit-pages", type=int, default=3, help="search pages of 100 per keyword on Reddit")
    ap.add_argument("--forum-max-pages", type=int, default=0, help="0 = every page of a thread")
    ap.add_argument("--max-posts", type=int, default=0, help="stop a source after this many kept posts (0 = all)")
    ap.add_argument("--min-words", type=int, default=6, help="drop queries with fewer words (links not counted)")
    ap.add_argument("--no-oembed", action="store_true", help="do not look up the titles of answer links")
    ap.add_argument("--salt", default=os.environ.get("VSS_ANON_SALT"), help="salt of the author hashes (default: random)")
    ap.add_argument("--user-agent", default=USER_AGENT)
    ap.add_argument("--tomtkis-repo", default=TOMTKIS_REPO)
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    stem = args.out[:-6] if args.out.endswith(".jsonl") else args.out
    logf = open(stem + ".log", "a", encoding="utf-8")

    def log(msg):
        line = time.strftime("[%H:%M:%S] ") + msg
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    http = Http(args.user_agent, cache=args.cache, log=log,
                delays={"www.youtube.com": 0.5, "open.spotify.com": 1.0, "arctic-shift.photon-reddit.com": 1.0})
    ctx = Ctx(args, http, Anon(args.salt or secrets.token_hex(16)), log)
    records, stats = {}, {}
    for name in [s.strip() for s in args.sources.split(",") if s.strip()]:
        if name not in SOURCES:
            raise SystemExit(f"unknown source {name!r}; choose from {', '.join(SOURCES)}")
        st = ctx.st = Counter()
        methods = Counter()
        t0, status = time.time(), "ok"
        log(f"== {name}")
        gen = SOURCES[name](ctx)
        try:
            for rec in gen:
                st["kept"] += 1
                if rec["answer_text"]:
                    st["answered"] += 1
                    methods[rec["answer_method"]] += 1
                merge(records, rec)
                if args.max_posts and st["kept"] >= args.max_posts:
                    log(f"  --max-posts {args.max_posts} reached")
                    break
        except Blocked as e:
            status = f"blocked: {e}"
        except Exception as e:      # noqa: BLE001 (a failing source must not stop the others)
            status = f"error: {type(e).__name__}: {e}"
            log(traceback.format_exc())
        finally:
            gen.close()
        stats[name] = {**st, "answer_methods": dict(methods), "status": status,
                       "minutes": round((time.time() - t0) / 60, 1)}
        log(f"  {name}: {stats[name]}")
        write_jsonl(args.out, records)
    if not args.no_oembed:
        todo = [r for r in records.values() if r["answer_links"] and not r["answer_link_titles"]]
        log(f"== oEmbed titles for the links of {len(todo)} answers")
        for r in todo:
            r["answer_link_titles"] = link_titles(ctx, r["answer_links"])
    write_jsonl(args.out, records)
    recs = list(records.values())
    stats["total"] = {"records": len(recs), "answered": sum(bool(r["answer_text"]) for r in recs),
                      "with_vietnamese_text": sum(r["has_vi_text"] for r in recs),
                      "by_community": dict(Counter(r["community"] for r in recs).most_common()),
                      "answer_methods": dict(Counter(r["answer_method"] for r in recs if r["answer_method"])),
                      "requests_per_host": dict(http.counts)}
    with open(stem + "_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    log(f"{args.out}: {stats['total']}")
    logf.close()
    return stats


if __name__ == "__main__":
    main()
