"""Biểu thức thời gian và suy luận thời gian (TMG 15 Time and Temporal Reasoning).

Nhận diện biểu thức thời gian tiếng Việt và chuẩn hóa theo kiểu TIMEX3 (type, value):
  DATE (hôm qua, ngày mai, tết, giáng sinh, tháng chạp, năm 1975, ngày xưa, mai sau)
  TIME (sáng, trưa, chiều, tối, đêm, khuya, hoàng hôn), SEASON (mùa thu, thu về, xuân sang)
  DURATION (ba năm, bao năm, ngàn năm, mãi mãi), SET (mỗi ngày, đêm đêm, từng ngày)
Suy luận: xếp từng dòng lời vào quá khứ / hiện tại / tương lai theo trạng từ và từ chỉ thể
(đã, từng, đang, sẽ), rồi xem mạch kể của bài đi theo thứ tự nào; thống kê xu hướng trong kho.
"""
from __future__ import annotations

import re
from collections import Counter

from visong.text import clean

NUM = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
       "mười": 10, "mười một": 11, "mười hai": 12, "trăm": 100, "ngàn": 1000, "nghìn": 1000, "vạn": 10000,
       "triệu": 1000000, "giêng": 1, "chạp": 12}
UNIT = {"năm": "Y", "tháng": "M", "tuần": "W", "ngày": "D", "đêm": "D", "giờ": "H", "phút": "MIN", "giây": "S",
        "mùa": "SEASON", "kiếp": "LIFE", "thu": "Y", "xuân": "Y", "đời": "LIFE"}
SEASON = {"xuân": "SP", "hạ": "SU", "hè": "SU", "thu": "FA", "đông": "WI"}
DAYPART = {"sáng": "MO", "ban mai": "MO", "sớm mai": "MO", "bình minh": "MO", "trưa": "MI", "chiều": "AF",
           "chiều tà": "EV", "hoàng hôn": "EV", "tối": "EV", "đêm": "NI", "khuya": "NI", "nửa đêm": "NI",
           "đêm khuya": "NI", "canh khuya": "NI"}
HOLIDAY = {"tết": "LUNAR-01-01", "tết nguyên đán": "LUNAR-01-01", "giao thừa": "LUNAR-12-30T24",
           "trung thu": "LUNAR-08-15", "rằm tháng tám": "LUNAR-08-15", "giáng sinh": "XXXX-12-25",
           "noel": "XXXX-12-25", "đêm giáng sinh": "XXXX-12-24TNI", "năm mới": "XXXX-01-01",
           "valentine": "XXXX-02-14", "lễ tình nhân": "XXXX-02-14", "ngày của mẹ": "XXXX-05-WXX-7"}
RELATIVE = {"hôm nay": "TODAY", "hôm qua": "TODAY-1", "hôm kia": "TODAY-2", "ngày mai": "TODAY+1",
            "ngày mốt": "TODAY+2", "đêm nay": "TODAY-TNI", "tối nay": "TODAY-TEV", "sáng nay": "TODAY-TMO",
            "chiều nay": "TODAY-TAF", "đêm qua": "TODAY-1-TNI", "tối qua": "TODAY-1-TEV", "năm nay": "THIS-YEAR",
            "năm sau": "THIS-YEAR+1", "năm ngoái": "THIS-YEAR-1", "tuần sau": "THIS-WEEK+1",
            "tuần trước": "THIS-WEEK-1", "tháng sau": "THIS-MONTH+1"}
VAGUE = {"PAST_REF": ["ngày xưa", "năm xưa", "thuở xưa", "ngày ấy", "năm ấy", "thuở ấy", "ngày trước", "thuở nào",
                      "xưa kia", "khi xưa", "một thời", "quá khứ", "hồi ấy", "dạo ấy", "dạo trước", "thuở"],
         "FUTURE_REF": ["mai sau", "mai này", "sau này", "tương lai", "mai kia", "một mai", "ngày sau", "mai đây"],
         "PRESENT_REF": ["bây giờ", "giờ đây", "lúc này", "hiện tại", "giờ này", "hiện giờ"]}
FOREVER = ["mãi mãi", "trọn đời", "suốt đời", "muôn đời", "vĩnh viễn", "cả đời", "mãi về sau", "trăm năm", "ngàn năm",
           "nghìn năm", "vạn năm"]

_W = r"(?<!\S)"
_E = r"(?!\S)"


def _alt(words):
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


_NUMW = _alt([w for w in NUM if w not in ("giêng", "chạp")]) + r"|\d+|mấy|bao|bao nhiêu|nhiều|vài|bao lâu"
_UNITW = _alt(UNIT)
PATTERNS = [
    ("DATE", "holiday", re.compile(_W + "(" + _alt(HOLIDAY) + ")" + _E)),
    ("DATE", "relative", re.compile(_W + "(" + _alt(RELATIVE) + ")" + _E)),
    ("DURATION", "forever", re.compile(_W + "(" + _alt(FOREVER) + ")" + _E)),
    ("DATE", "vague", re.compile(_W + "(" + _alt(sum(VAGUE.values(), [])) + ")" + _E)),
    ("DATE", "year", re.compile(_W + r"(năm\s+(?:19|20)\d\d|những năm\s+\d0|(?:19|20)\d\d)" + _E)),
    ("DATE", "month", re.compile(_W + r"(tháng\s+(?:giêng|chạp|mười một|mười hai|mười|một|hai|ba|tư|bốn|sáu|bảy|tám|chín|1[0-2]|[1-9]))" + _E)),
    ("SEASON", "season", re.compile(_W + r"(mùa\s+(?:xuân|hạ|hè|thu|đông)|(?:xuân|thu|đông|hè)\s+(?:về|sang|đến|qua|tới|đi|này|ấy|xưa|nay))" + _E)),
    ("SET", "set", re.compile(_W + r"((?:mỗi|từng)\s+(?:ngày|đêm|sáng|chiều|tối|năm|mùa|sớm|tuần|tháng)|ngày ngày|đêm đêm|chiều chiều|năm năm|sớm sớm)" + _E)),
    ("DURATION", "duration", re.compile(_W + "((?:" + _NUMW + r")\s+(?:" + _UNITW + "))" + _E)),
    ("TIME", "daypart", re.compile(_W + r"((?:buổi\s+|ban\s+|trời\s+)?(?:" + _alt(DAYPART) + "))" + _E)),
]

PAST = ["đã từng", "đã", "từng", "ngày xưa", "năm xưa", "ngày ấy", "khi xưa", "lúc trước", "hôm qua", "khi ấy",
        "thuở", "xưa", "hồi ấy", "đã qua", "ngày trước"]
PRESENT = ["đang", "bây giờ", "giờ đây", "hôm nay", "lúc này", "hiện tại", "đêm nay", "giờ này", "vẫn đang"]
FUTURE = ["sẽ", "mai sau", "ngày mai", "mai này", "sau này", "tương lai", "rồi sẽ", "một mai", "sắp", "sẽ mãi"]
_TENSE = [(t, re.compile(_W + "(" + _alt(ws) + ")" + _E)) for t, ws in
          (("quá khứ", PAST), ("hiện tại", PRESENT), ("tương lai", FUTURE))]


def _value(kind: str, typ: str, text: str) -> str:
    t = re.sub(r"^(buổi|ban|trời)\s+", "", text)
    if kind == "holiday":
        return HOLIDAY[text]
    if kind == "relative":
        return RELATIVE[text]
    if kind == "vague":
        return next(k for k, ws in VAGUE.items() if text in ws)
    if kind == "forever":
        return {"trăm năm": "P100Y", "ngàn năm": "P1000Y", "nghìn năm": "P1000Y", "vạn năm": "P10000Y"}.get(text, "PINF")
    if kind == "year":
        m = re.search(r"(19|20)\d\d", text)
        if m:
            return m.group(0)
        d = re.search(r"\d0", text).group(0)
        return ("19" if int(d) >= 30 else "20") + d[0] + "X"
    if kind == "month":
        m = t.split(None, 1)[1]
        n = int(m) if m.isdigit() else NUM.get(m, 0)
        return f"XXXX-{n:02d}"
    if kind == "season":
        w = t.split()[1] if t.startswith("mùa") else t.split()[0]
        return f"XXXX-{SEASON[w]}"
    if kind == "set":
        unit = t.split()[-1]
        return "P1" + {"ngày": "D", "đêm": "D", "sáng": "D", "chiều": "D", "tối": "D", "sớm": "D", "năm": "Y",
                       "mùa": "SEASON", "tuần": "W", "tháng": "M"}.get(unit, "D")
    if kind == "duration":
        a, u = t.rsplit(" ", 1)
        n = int(a) if a.isdigit() else NUM.get(a)
        return f"P{n if n else 'X'}{UNIT[u]}"
    if kind == "daypart":
        return "T" + DAYPART.get(text, DAYPART.get(t, "XX"))
    return ""


def tag(text: str) -> list[dict]:
    """Biểu thức thời gian trong câu: [{text, type, kind, value, start, end}] (vị trí theo ký tự của câu đã chuẩn hóa)."""
    s = clean(text)
    taken = [False] * (len(s) + 1)
    out = []
    for typ, kind, rx in PATTERNS:
        for m in rx.finditer(s):
            a, b = m.span(1)
            if any(taken[a:b]):
                continue
            if kind == "duration" and m.group(1).split()[0] == "năm" and m.group(1).split()[1] in ("năm", "tháng"):
                continue
            val = _value(kind, typ, m.group(1))
            for i in range(a, b):
                taken[i] = True
            out.append({"text": m.group(1), "type": typ, "kind": kind, "value": val, "start": a, "end": b})
    return sorted(out, key=lambda x: x["start"])


def tense_of(line: str) -> str | None:
    s = clean(line)
    hits = [(m.start(), t) for t, rx in _TENSE for m in rx.finditer(s)]
    if not hits:
        return None
    c = Counter(t for _, t in hits)
    return c.most_common(1)[0][0]


def song_timeline(lyrics: str) -> dict:
    """Mạch thời gian của bài: mỗi dòng có dấu hiệu thời gian được xếp vào quá khứ/hiện tại/tương lai."""
    lines = [l for l in lyrics.split("\n") if l.strip()]
    seq, examples = [], {"quá khứ": [], "hiện tại": [], "tương lai": []}
    for l in lines:
        t = tense_of(l)
        if t:
            if not seq or seq[-1] != t:
                seq.append(t)
            if len(examples[t]) < 2:
                examples[t].append(l.strip())
    timexes = Counter()
    for l in lines:
        for x in tag(l):
            timexes[(x["type"], x["text"])] += 1
    return {"order": seq, "examples": examples,
            "timex": [{"type": t, "text": w, "count": c} for (t, w), c in timexes.most_common(12)]}


TIME_TAG_NAMES = {"XXXX-SP": "mùa xuân", "XXXX-SU": "mùa hè", "XXXX-FA": "mùa thu", "XXXX-WI": "mùa đông",
                  "LUNAR-01-01": "tết", "LUNAR-12-30T24": "tết", "XXXX-12-25": "giáng sinh", "XXXX-12-24TNI": "giáng sinh",
                  "LUNAR-08-15": "trung thu", "XXXX-02-14": "valentine", "TNI": "đêm", "TEV": "chiều tối",
                  "TMO": "buổi sáng", "TAF": "buổi chiều", "PAST_REF": "hoài niệm", "FUTURE_REF": "tương lai"}


def time_tags(lyrics: str, min_count: int = 1) -> list[str]:
    """Nhãn thời gian của bài (dùng cho bộ lọc "Thời gian" và tăng điểm khi truy vấn nhắc thời gian)."""
    c = Counter()
    for l in lyrics.split("\n"):
        for x in tag(l):
            name = TIME_TAG_NAMES.get(x["value"])
            if name:
                c[name] += 1
    return [n for n, k in c.most_common() if k >= min_count]
