"""Trích xuất thông tin (SEG 4.6 Information Extraction; TMG 14 Relation and Event Extraction).

- Nhận diện thực thể bằng từ điển (gazetteer): tên ca sĩ lấy từ dữ liệu, địa danh trong lexicon/events.json
- Quan hệ từ dữ liệu có cấu trúc: (bài, trình bày bởi, ca sĩ), (bài, thuộc album, album),
  (ca sĩ, hợp tác với, ca sĩ), (bài, là phiên bản của, bài) theo cụm trùng lặp, (bài, nguồn, trang)
- Quan hệ từ lời bài hát bằng mẫu (kiểu mẫu Hearst): "X là Y", "X như Y", "X của Y"
- Sự kiện: từ kích hoạt (chia tay, gặp gỡ, cưới, trở về...) và các vai: ai, khi nào, ở đâu
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from visong.text import clean, has_diacritics, remove_diacritics

from . import temporal
from .lexicons import PhraseMatcher, load

_SPLIT = re.compile(r"\s*(?:,|&|;|\bft\.?|\bfeat\.?|\bx\b|\bvà\b|/)\s*", re.I)


def split_artists(artist: str) -> list[str]:
    names = [re.sub(r"\s+", " ", n.replace('"', " ")).strip(" -'") for n in _SPLIT.split(artist or "")]
    return [n for n in names if n and len(n) > 1 and n.casefold() not in ("various artists", "nhiều ca sĩ")]


class Gazetteer:
    """Tên ca sĩ (có dấu và không dấu) và địa danh, so khớp cụm dài nhất."""

    def __init__(self, artists: Counter):
        self.artists = {n: c for n, c in artists.items() if len(clean(n)) >= 2}
        self.by_clean: dict[str, str] = {}
        for n in sorted(self.artists, key=lambda x: -self.artists[x]):
            self.by_clean.setdefault(clean(n), n)
        self.artist_matcher = PhraseMatcher(list(self.by_clean))
        self.artist_exact = PhraseMatcher(list(self.by_clean), plain_too=False)
        ev = load("events")
        self.place_matcher = PhraseMatcher(ev["places"])
        self.place_exact = PhraseMatcher(ev["places"], plain_too=False)

    def find(self, text: str, strict: bool = False) -> list[dict]:
        """strict=True cho lời bài hát: chỉ khớp đúng dấu và bỏ tên ca sĩ một âm tiết ("ngọt", "will"),
        vì trong lời chúng thường là từ thường chứ không phải tên người."""
        toks = clean(text).split()
        exact = strict or has_diacritics(text)
        out = []
        for i, j, w in (self.artist_exact if exact else self.artist_matcher).find(toks):
            if j - i == 1 and (strict or (len(w) <= 3 and w not in ("đen",))):   # tên một âm tiết dễ nhầm
                continue
            out.append({"type": "CA_SĨ", "text": " ".join(toks[i:j]), "value": self.by_clean[w], "span": [i, j]})
        taken = {k for e in out for k in range(*e["span"])}
        for i, j, w in (self.place_exact if exact else self.place_matcher).find(toks):
            if not any(k in taken for k in range(i, j)):
                out.append({"type": "ĐỊA_DANH", "text": " ".join(toks[i:j]), "value": w, "span": [i, j]})
        for x in temporal.tag(text):
            out.append({"type": "THỜI_GIAN", "text": x["text"], "value": x["value"], "span": None})
        return out


def metadata_relations(songs, cluster_of) -> dict:
    """Bộ ba (chủ thể, quan hệ, đối tượng) từ siêu dữ liệu của cả kho."""
    triples = []
    collab = Counter()
    by_cluster = defaultdict(list)
    for s in songs:
        names = split_artists(s.artist)
        for n in names:
            triples.append((s.doc_id, "trình_bày_bởi", n))
        alb = s.extra.get("album")
        if alb and alb != "None":
            triples.append((s.doc_id, "thuộc_album", alb))
        triples.append((s.doc_id, "nguồn", s.source))
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                collab[tuple(sorted((names[i], names[j])))] += 1
        by_cluster[cluster_of.get(s.doc_id, s.doc_id)].append(s.doc_id)
    for (a, b), c in collab.items():
        triples.append((a, "hợp_tác_với", b))
    for c, ids in by_cluster.items():
        for d in ids[1:]:
            triples.append((d, "phiên_bản_của", ids[0]))
    counts = Counter(r for _, r, _ in triples)
    return {"triples": triples, "counts": dict(counts), "collab": collab}


_PRON = r"(anh|em|tôi|ta|mình|người|đời|tình yêu|tình|cuộc đời|con tim|trái tim|ký ức|kỷ niệm|nỗi nhớ|nỗi buồn|hạnh phúc|thanh xuân)"
LYRIC_PATTERNS = [
    ("LÀ", re.compile(r"(?<!\S)" + _PRON + r" là (?!vì|khi|lúc|để|do)((?:\S+ ){0,3}\S+)")),
    ("NHƯ", re.compile(r"(?<!\S)((?:\S+ ){0,2}\S+) như (?:là )?((?:\S+ ){0,3}\S+)")),
    ("CỦA", re.compile(r"(?<!\S)((?:nỗi|niềm|giấc|bàn|ánh|đôi|vòng|bóng|trái|con|lời|tiếng|nụ|mái) \S+) của (anh|em|tôi|ta|mình|người|mẹ|cha)(?!\S)")),
]


def lyric_relations(lyrics: str, limit: int = 12) -> list[dict]:
    out, seen = [], set()
    for line in lyrics.split("\n"):
        s = clean(line)
        for rel, rx in LYRIC_PATTERNS:
            for m in rx.finditer(s):
                a, b = m.group(1), m.group(2)
                key = (rel, a, b)
                if key in seen or len(b.split()) == 0:
                    continue
                seen.add(key)
                out.append({"relation": rel, "subject": a, "object": b, "line": line.strip()})
                if len(out) >= limit:
                    return out
    return out


class EventExtractor:
    def __init__(self):
        ev = load("events")
        self.trigger_type = {}
        for typ, ws in ev["triggers"].items():
            for w in ws:
                self.trigger_type[w] = typ
        self.triggers = PhraseMatcher(self.trigger_type)
        self.agents = PhraseMatcher(ev["agents"])
        self.places = PhraseMatcher(ev["places"])

    def extract(self, text: str, limit: int = 20) -> list[dict]:
        out = []
        for line in text.split("\n"):
            toks = clean(line).split()
            if not toks:
                continue
            agents = self.agents.find(toks)
            places = self.places.find(toks)
            times = temporal.tag(line)
            for i, j, w in self.triggers.find(toks):
                before = [a for a in agents if a[1] <= i]
                after = [a for a in agents if a[0] >= j]
                ev = {"type": self.trigger_type[w], "trigger": w, "line": line.strip(),
                      "agent": before[-1][2] if before else None,
                      "patient": after[0][2] if after and after[0][0] - j <= 2 else None,
                      "place": places[0][2] if places else None,
                      "time": times[0]["text"] if times else None,
                      "negated": any(t in ("không", "chẳng", "chưa", "đừng") for t in toks[max(0, i - 2):i])}
                out.append(ev)
                if len(out) >= limit:
                    return out
        return out

    def summary(self, lyrics: str) -> dict:
        evs = self.extract(lyrics, 200)
        return {"counts": dict(Counter(e["type"] for e in evs if not e["negated"]).most_common()),
                "examples": evs[:8]}


def plain_name(name: str) -> str:
    return remove_diacritics(clean(name))
