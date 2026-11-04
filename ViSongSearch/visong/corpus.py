"""Build the song corpus: load, clean lyrics, cut passages (SEG 3-4, TMG 01).

Sources
- The group's SQLite databases (table `songs` with a `lyrics` column).
- JSONL written by crawler/hac_crawler.py (one song per line).

Output: corpus.jsonl (one song per line) and passages.jsonl (one passage per line).
"""
from __future__ import annotations

import glob
import hashlib
import html
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field

from .text import clean, has_diacritics, nfc, syllables

# ---------------------------------------------------------------- cleaning

# Cyrillic and Greek letters that look like Latin ones. Some lyric sites mix them
# into Vietnamese words ("Chuуển" with a Cyrillic у). We only replace them inside
# tokens that also contain Latin letters, so real Russian or Greek lyrics stay.
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ӏ": "l", "ԛ": "q", "ԝ": "w",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "ο": "o", "ν": "v", "Σ": "C", "Ѕ": "S",      # "Σhỉ" = "Chỉ" on one lyric site
})
# Latin letters with hooks used as decoration ("Ɲhìn", "Ɓởi"); replaced everywhere.
_DECORATIVE = str.maketrans({"Ɲ": "N", "ɲ": "n", "Ɓ": "B", "ɓ": "b", "Ɗ": "D", "ɗ": "d",
                             "Ɛ": "E", "ɛ": "e", "Ð": "Đ", "ð": "đ"})
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _is_latin(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith("LATIN")


def fix_homoglyphs(text: str) -> str:
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if any(_is_latin(c) for c in tok) and any(c.isalpha() and not _is_latin(c) for c in tok):
            return nfc(tok.translate(_HOMOGLYPHS))
        return tok
    return _TOKEN.sub(repl, text)


_CHORD = re.compile(
    r"\[\s*[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|M)?\d*(?:[#b]\d+)*"
    r"(?:sus\d*|add\d*)?(?:/[A-G](?:#|b)?)?\s*\]"
)
_SECTION = (r"(?:đk|dk|điệp khúc|diep khuc|chorus|pre[\s-]?chorus|post[\s-]?chorus|verse|"
            r"rap|bridge|intro|outro|hook|interlude|refrain|break|drop|chuyển tiếp|coda|"
            r"lời \d+|lời bài hát|tone|tông|capo|nhạc dạo|kết|solo|phiên khúc|lần \d+|"
            r"gian tấu|giang tấu|gian tau|giang tau)")
# A whole line that is only a section marker: "Chorus:", "VERSE", "Intro: x2", "ĐK"
_MARKER_LINE = re.compile(rf"^\s*{_SECTION}\s*\d*\s*:?\s*(?:x\s?\d)?\s*$", re.I)
# A line fully inside square brackets ("[Verse 1: (S)TRONG]", "[Chorus]:"), or a
# parenthesised section marker ("(ĐK)"). Parenthesised lyrics such as "(anh yêu em)" stay.
_BRACKET_LINE = re.compile(rf"^\s*(?:\[[^\]]{{0,60}}\]|\(\s*{_SECTION}[^)]{{0,30}}\))\s*:?\s*$", re.I)
# A marker at the start of a lyric line: "ĐK: Giờ đây anh biết"
_MARKER_PREFIX = re.compile(rf"^\s*[\[(]?\s*{_SECTION}\s*\d*\s*[\])]?\s*[:.\-]\s*", re.I)
_REPEAT = re.compile(r"[\[(]?\s*\b(?:x\s?\d|\d\s?lần|\d\s?lan)\b\s*[\])]?", re.I)
# "1. Ngày xưa", "(2) Mẹ bảo", "...Em gọi anh": verse numbers and leading dots
_ENUM_PREFIX = re.compile(r"^\s*(?:\(?\d{1,2}[.)/:]\s*|\.{2,}\s*|[*•]\s*)")
_ONLY_SYMBOLS = re.compile(r"^[\W\d_]*$", re.UNICODE)
# guitar fingering lines such as "x4535x" or "x3545x (không chơi dây cuối)"
_FINGERING = re.compile(r"(?i)^\s*[x0-9]{5,6}\b")
_CREDIT = re.compile(r"(?i)^\s*(sáng tác|tác giả|trình bày|ca sĩ|ca sỹ|lời việt|lời:|nhạc:|beat|ver\b|viết ở tông|tông gốc|"
                     r"mix|master|producer|composer|lyrics by|hợp âm|vòng hợp âm|vòng hòa âm|vong hop am|người đăng)\b")
# "Tone chuan Eb. Capo 1", "Tông: Am": key and capo notes; "Vòng --- hết cả bài": chord loop notes
_KEY_LINE = re.compile(r"(?i)^\s*(tone|tông|capo)\b.{0,40}$|^\s*vòng\s*[-–—:.]|^\s*vòng\b.*\b(cả bài|hết bài|toàn bài)\b")
# a line of chord names only ("Am Em C G"); a lone "Em" may be a lyric word, so it stays
_CHORD_TOKEN = re.compile(r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|M)?\d*(?:/[A-G](?:#|b)?)?$")


def _chord_line(line: str) -> bool:
    toks = re.sub(r"[|,.\-]", " ", line).split()
    return bool(toks) and all(_CHORD_TOKEN.match(x) for x in toks) and set(toks) != {"Em"}
# Page furniture copied together with the lyrics by the group's crawlers.
_HEADER = re.compile(r"(?i)^\s*(đăng bởi|dang boi|bài hát\s*:|bai hat\s*:)")          # "Đăng bởicool"
_CHROME_LINE = re.compile(r"(?i)^\s*(nghệ sĩ|theo dõi|follow|artist)\s*$|\d+\s*người theo dõi\s*$|\blyrics\s*$")
_CHROME_STOP = re.compile(r"(?i)^\s*(xem thêm|xem hết|thu gọn|see more|show more)\s*\.{0,3}\s*$")
_CHROME_TAIL = re.compile(r"(?i)\s+(xem thêm|xem hết|thu gọn)\s*$")                    # "... mãi mãi. Xem hết"


def clean_lyrics(raw: str) -> str:
    """Remove chords, section markers, credits and repeat marks; keep stanza breaks."""
    text = nfc(html.unescape(raw or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = fix_homoglyphs(text).translate(_DECORATIVE)
    text = _CHORD.sub("", text)
    out: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if out and out[-1] != "":
                out.append("")          # stanza break
            continue
        if _CHROME_STOP.match(line):
            break                       # "Xem thêm" ends the lyric; artist links follow it
        line, last = _CHROME_TAIL.subn("", line)
        if (_MARKER_LINE.match(line) or _BRACKET_LINE.match(line) or _CREDIT.match(line)
                or _FINGERING.match(line) or _HEADER.match(line) or _CHROME_LINE.search(line)
                or _KEY_LINE.match(line) or _chord_line(line)):
            continue
        line = _MARKER_PREFIX.sub("", line)
        line = _ENUM_PREFIX.sub("", line)
        line = _REPEAT.sub("", line).strip(" -|:_")
        if not line or _ONLY_SYMBOLS.match(line):
            continue
        out.append(line)
        if last:
            break
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


_COMPILATION = re.compile(r"(?i)\b(mashup|mashub|mash up|medley|liên khúc|lien khuc|lk)\b")


def is_compilation(title: str) -> bool:
    """Mashups and medleys mix several songs; they stay in the index as distractors
    but are never used as query targets."""
    return bool(_COMPILATION.search(title or ""))


def is_compilation_song(song) -> bool:
    """Title rule, or found by its lyrics and title parts (`dedup.find_compilations`, set at build)."""
    return is_compilation(song.title) or bool(song.extra.get("compilation"))


# Placeholders some sites put in the composer field.
_NO_NAME = re.compile(r"(?i)^\s*(nhạc ngoại|nhac ngoai|nhạc \w+ lời việt|nhiều tác giả|sưu tầm|suu tam|đang cập nhật|"
                      r"n/?a|không rõ|unknown|various artists?)\s*$")


def composer_of(song) -> str:
    return "" if _NO_NAME.match(song.artist or "") else (song.artist or "")


def singers_of(song) -> list[str]:
    """Performers, main one first. Hợp Âm Chuẩn lists them apart from the composer (its `artist`
    field); in the group's databases `artist` already names the performers."""
    return [x.strip() for x in (song.extra.get("singers") or "").split(",") if x.strip()]


def credits(song) -> str:
    """Composer and performers in one string, without repeats: the names people search a song by."""
    names = []
    for n in [x.strip() for x in composer_of(song).split(",")] + singers_of(song):
        if n and n.casefold() not in {m.casefold() for m in names}:
            names.append(n)
    return ", ".join(names)


# Pinyin tone marks that Vietnamese never uses (macron, caron, and ü with a mark): romanised
# Chinese lyrics carry marks too and would otherwise pass for Vietnamese.
_PINYIN = re.compile("[āēīōūǖǎěǐǒǔǚǘǜĀĒĪŌŪǍĚǏǑǓ]")


def pinyin_ratio(text: str) -> float:
    syl = syllables(text)
    return sum(bool(_PINYIN.search(s)) for s in syl) / max(1, len(syl))


# Marks only Vietnamese uses among Latin-script lyrics: hook above, dot below, horn, circumflex, đ.
# Romanised Chinese typed with Vietnamese letters ("shăng", "gãn") has acute, grave, tilde and breve,
# but almost none of these.
_VI_ONLY = {"\u0309", "\u0323", "\u031b", "\u0302"}


def vietnamese_only_ratio(text: str) -> float:
    syl = syllables(text)
    hits = sum(("đ" in s.lower()) or any(c in _VI_ONLY for c in unicodedata.normalize("NFD", s)) for s in syl)
    return hits / max(1, len(syl))


def vietnamese_ratio(text: str) -> float:
    """Share of syllables that carry a Vietnamese mark. V-pop with English lines
    is usually above 0.4; English songs are near 0."""
    syl = syllables(text)
    return sum(has_diacritics(s) for s in syl) / max(1, len(syl))


# ---------------------------------------------------------------- records

@dataclass
class Song:
    doc_id: str
    url: str
    source: str
    title: str
    artist: str = ""
    genre: str = ""
    lyrics: str = ""
    extra: dict = field(default_factory=dict)

    def lines(self) -> list[str]:
        return [l for l in self.lyrics.split("\n") if l.strip()]


def make_doc_id(url: str) -> str:
    m = re.search(r"hopamchuan\.com/song/(\d+)", url)
    if m:
        return f"hac{m.group(1)}"
    host = re.sub(r"^www\.", "", url.split("/")[2]) if "://" in url else "x"
    return host.split(".")[0][:6] + hashlib.sha1(url.encode()).hexdigest()[:10]


def source_of(url: str) -> str:
    return re.sub(r"^www\.", "", url.split("/")[2]) if "://" in url else "unknown"


def load_sqlite_songs(paths: list[str]) -> list[Song]:
    """Read every `songs` table that has lyrics; first copy of a URL wins."""
    seen: dict[str, Song] = {}
    for path in paths:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        if "songs" not in tables:
            continue
        cols = {r[1] for r in con.execute("pragma table_info(songs)")}
        if "lyrics" not in cols:
            continue
        sel = [c if c in cols else f"NULL as {c}" for c in ("url", "title", "artist", "genre", "lyrics")]
        for url, title, artist, genre, lyrics in con.execute(f"select {', '.join(sel)} from songs"):
            if not url or url in seen or not lyrics:
                continue
            seen[url] = Song(make_doc_id(url), url, source_of(url), nfc(title or "").strip(),
                             nfc(artist or "").strip(), nfc(genre or "").strip(), lyrics,
                             {"db": os.path.relpath(path)})
        con.close()
    return list(seen.values())


def load_jsonl_songs(path: str) -> list[Song]:
    songs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            url = o.get("url", "")
            songs.append(Song(o.get("doc_id") or make_doc_id(url), url, o.get("source") or source_of(url),
                              nfc(o.get("title", "")), nfc(o.get("artist", "")), nfc(o.get("genre", "")),
                              o.get("lyrics", ""), {k: v for k, v in o.items() if k not in
                                                    {"doc_id", "url", "source", "title", "artist", "genre", "lyrics"}}))
    return songs


_WORD = re.compile(r"\w+")


def repair_split_syllables(songs: list[Song], min_df: int = 10, ratio: int = 20) -> int:
    """Join syllables that uploaders cut in two around a chord ("sươ [Am]ng" -> "sươ ng").

    Two neighbouring tokens are joined when the joined form is a common token (in at least
    `min_df` songs) and one of the parts is at least `ratio` times rarer than it: "sươ"+"ng"
    gives "sương", while two real syllables ("em"+"a") never join into a common token.
    Frequencies come from the collection itself. Returns the number of joins."""
    df: Counter = Counter()
    for s in songs:
        df.update({w.lower() for w in _WORD.findall(s.lyrics)})
    joins = 0

    def fix(line: str) -> str:
        nonlocal joins
        toks = line.split(" ")
        out: list[str] = []
        for tok in toks:
            if out and out[-1].isalpha():
                m = _WORD.match(tok)
                if m and m.group(0).isalpha():
                    a, b = out[-1].lower(), m.group(0).lower()
                    joined = df[a + b]
                    if joined >= min_df and min(df[a], df[b]) * ratio <= joined:
                        out[-1] = out[-1] + tok
                        joins += 1
                        continue
            out.append(tok)
        return " ".join(out)

    for s in songs:
        s.lyrics = "\n".join(fix(l) for l in s.lyrics.split("\n"))
    return joins


def prepare(songs: list[Song], min_syllables: int = 40, min_vi_ratio: float = 0.25,
            repair_splits: bool = True) -> tuple[list[Song], dict]:
    """Clean lyrics, join syllables cut around chords, and drop songs that are too short or not
    Vietnamese. The pilot collection (tag bundle-v7) was built without the syllable repair."""
    kept, stats = [], {"input": len(songs), "too_short": 0, "not_vietnamese": 0, "romanised": 0, "dup_id": 0}
    ids = set()
    for s in songs:
        s.lyrics = clean_lyrics(s.lyrics)
    if repair_splits:
        stats["split_syllables_joined"] = repair_split_syllables(songs)
    for s in songs:
        n = len(syllables(s.lyrics))
        if n < min_syllables:
            stats["too_short"] += 1
            continue
        if vietnamese_ratio(s.lyrics) < min_vi_ratio:
            stats["not_vietnamese"] += 1          # mostly English songs
            continue
        if pinyin_ratio(s.lyrics) > 0.05 or vietnamese_only_ratio(s.lyrics) < 0.1:
            stats["romanised"] += 1               # romanised Chinese (pinyin or Vietnamese letters)
            continue
        if s.doc_id in ids:
            stats["dup_id"] += 1
            continue
        ids.add(s.doc_id)
        kept.append(s)
    stats["kept"] = len(kept)
    return kept, stats


# ---------------------------------------------------------------- passages

def passages_of(song: Song, window: int = 4, stride: int = 2, min_syl: int = 8) -> list[dict]:
    """Overlapping windows of `window` lyric lines with step `stride`.

    With window 4 and stride 2, any span of up to 3 consecutive lines is inside
    one passage, so a query cut from the lyrics is never split across passages.
    """
    lines = song.lines()
    out = []
    if not lines:
        return out
    starts = list(range(0, max(1, len(lines) - window + stride), stride))
    for k, i in enumerate(starts):
        chunk = lines[i:i + window]
        text = "\n".join(chunk)
        if len(syllables(text)) < min_syl and out:
            continue
        out.append({"pid": f"{song.doc_id}#{k}", "doc_id": song.doc_id, "line_start": i, "text": text})
    return out


def write_jsonl(path: str, rows) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r if isinstance(r, dict) else asdict(r), ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_corpus(path: str) -> list[Song]:
    return [Song(**{k: r.get(k, "" if k != "extra" else {}) for k in Song.__dataclass_fields__})
            for r in read_jsonl(path)]


def find_group_dbs(repo_root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(repo_root, "**", "*.db"), recursive=True))
