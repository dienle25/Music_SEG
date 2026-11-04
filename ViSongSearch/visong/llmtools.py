"""Helpers for the round-2 LLM experiments (scripts/llm_r2.py): no GPU or model code here, so
everything below is tested on CPU (tests/test_llmtools.py).

1. Several open LLMs write the Q4 paraphrases and Q5 descriptions with the paper's prompts
   (visong.queries.PROMPT_Q4 / PROMPT_Q5), so the results no longer rest on one generator.
   Every output goes through visong.queries.check_llm_query plus the length limits that the
   prompts state; a rejected output is regenerated with a new seed (at most `attempts` times).
2. Closed-book identification: the LLM is asked which song a query is about, and its guesses
   (title - artist) are matched to clusters by normalised title.
3. Retrieval-augmented identification: the LLM picks the right song among the top lexical
   candidates (listwise reranking).
"""
from __future__ import annotations

import random
import re
from collections import defaultdict

from .corpus import composer_of, singers_of, vietnamese_ratio
from .dedup import norm_title
from .queries import PROMPT_Q4, PROMPT_Q5, check_llm_query, in_llm_sample, representatives, sample_key
from .text import clean, remove_diacritics

# Length limits stated in the prompts (syllables after visong.text.clean).
LENGTH = {"Q4": (8, 20), "Q5": (4, 35)}

PROMPT_CLOSED = """Một người đang cố tìm một bài hát tiếng Việt và gõ vào ô tìm kiếm:
"{query}"
Đó là bài hát nào? Hãy liệt kê tối đa 5 bài có khả năng nhất, bài khả năng nhất trước,
mỗi dòng một bài theo đúng dạng: Tên bài - Ca sĩ hoặc nhạc sĩ. Không giải thích."""

PROMPT_RAG = """Một người đang cố tìm một bài hát tiếng Việt và gõ vào ô tìm kiếm:
"{query}"
Dưới đây là các bài hát ứng viên do máy tìm kiếm trả về (số thứ tự, tên bài, nghệ sĩ, một đoạn lời):
{candidates}
Bài nào là bài người đó đang tìm? Chọn tối đa 5 bài phù hợp nhất, bài phù hợp nhất trước.
Chỉ trả lời các số thứ tự, cách nhau bằng dấu phẩy, ví dụ: 3, 1, 7"""


# ------------------------------------------------------------- items for generation

def good_fragment(text: str) -> bool:
    """Same rule as scripts/prep_llm_items.py: two lines worth paraphrasing."""
    syl = clean(text).split()
    return len(syl) >= 10 and len(set(syl)) >= 0.6 * len(syl) and vietnamese_ratio(text) >= 0.3


def item_of(song, cluster: str, frag: str | None, seed: int = 5) -> dict:
    lines = song.lines()
    if frag is None:
        cands = [i for i in range(len(lines) - 1) if good_fragment(lines[i] + " " + lines[i + 1])]
        i = random.Random(f"{seed}:{song.doc_id}").choice(cands) if cands else 0
        frag = " / ".join(lines[i:i + 2])
    return {"doc_id": song.doc_id, "cluster": cluster, "split": song.extra["split"], "title": song.title,
            "composer": composer_of(song), "singers": ", ".join(singers_of(song)[:3]), "frag": frag,
            "lyrics_head": " / ".join(lines[:16])}


def build_items(songs, cluster_of: dict[str, str], existing: list[dict], n_test: int, n_dev: int,
                seed: int = 5) -> list[dict]:
    """The clusters of the existing LLM queries first (same song, same Q4 fragment, so every
    generator can be compared on paired queries), then more clusters of the same split in the
    order of the LLM sample key (a uniform random order that does not depend on the crawl), until
    the split has n_test / n_dev items."""
    by_id = {s.doc_id: s for s in songs}
    reps = representatives(songs, cluster_of)
    items, seen = [], set()
    frag_of = {}
    for q in existing:
        if q["type"] == "Q4" and q.get("meta", {}).get("source_fragment"):
            frag_of[q["doc_id"]] = q["meta"]["source_fragment"]
    for q in sorted(existing, key=lambda q: q["qid"]):
        if q["cluster"] in seen or q["doc_id"] not in by_id:
            continue
        seen.add(q["cluster"])
        it = item_of(by_id[q["doc_id"]], q["cluster"], frag_of.get(q["doc_id"]), seed)
        it["origin"] = "existing"
        items.append(it)
    want = {"test": n_test, "dev": n_dev}
    have = defaultdict(int)
    for it in items:
        have[it["split"]] += 1
    pool = []
    for c, d in reps.items():
        s = by_id[d]
        if c in seen or s.extra.get("split") not in want:
            continue
        pool.append((sample_key(s.extra.get("cluster_key", c)), c, s))
    for _, c, s in sorted(pool, key=lambda x: (x[0], x[1])):
        split = s.extra["split"]
        if have[split] >= want[split]:
            continue
        it = item_of(s, c, None, seed)
        if not good_fragment(it["frag"].replace(" / ", " ")):
            continue
        it["origin"] = "sample" if in_llm_sample(s) else "extended"
        items.append(it)
        have[split] += 1
    return items


def prompt_for(qtype: str, item: dict) -> str:
    if qtype == "Q4":
        return PROMPT_Q4.format(fragment=item["frag"])
    return PROMPT_Q5.format(title=item["title"], composer=item["composer"] or "không rõ",
                            singers=item["singers"] or "không rõ", lyrics=item["lyrics_head"])


# ------------------------------------------------------------- cleaning and filtering outputs

_THINK = re.compile(r"<think>.*?</think>", re.S)
_LABEL = re.compile(r"^\s*(?:câu(?: tìm kiếm)?|truy vấn|query|trả lời|đáp án)\s*[:：-]\s*", re.I)


def first_answer(text: str) -> str:
    """The first non-empty line of a reply, without a <think> block, a label or quotes."""
    text = _THINK.sub(" ", text or "")
    for line in text.splitlines():
        q = " \t\"'“”‘’«»*`-•"
        line = _LABEL.sub("", line.strip().strip(q)).strip(q)
        if line:
            return line
    return ""


def filter_output(qtype: str, raw: str, song, frag: str | None) -> tuple[str, bool, str]:
    """Clean a generated query and apply the paper's filter plus the prompt's length limit."""
    text = clean(first_answer(raw))
    if not text:
        return text, False, "empty"
    ok, why = check_llm_query(qtype, text, song, frag if qtype == "Q4" else None)
    if not ok:
        return text, False, why
    lo, hi = LENGTH[qtype]
    n = len(text.split())
    if n < lo:
        return text, False, "too_short"
    if n > hi:
        return text, False, "too_long"
    return text, True, "ok"


# ------------------------------------------------------------- closed-book identification

_NUM = re.compile(r"^\s*(?:\d+\s*[.)/:-]|[-*•])\s*")
_SEP = re.compile(r"\s+[-–—|]\s+|\s*[–—|]\s*")


def parse_guesses(text: str, k: int = 5) -> list[tuple[str, str]]:
    """Lines 'Tên bài - Nghệ sĩ' -> [(title, artist)], at most k, duplicates dropped."""
    out, seen = [], set()
    for line in _THINK.sub(" ", text or "").splitlines():
        line = _NUM.sub("", line.strip()).strip(" \"'“”*`")
        if not line:
            continue
        parts = _SEP.split(line, maxsplit=1)
        title = parts[0].strip(" \"'“”*`")
        artist = parts[1].strip(" \"'“”*`().") if len(parts) > 1 else ""
        key = norm_title(title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((title, artist))
        if len(out) >= k:
            break
    return out


def _artist_tokens(text: str) -> set[str]:
    return set(remove_diacritics(clean(text)).split())


class TitleMatcher:
    """Normalised title -> clusters. A guessed title can match several clusters (common titles
    such as 'Mưa' or 'Em'); the artist, when given, puts the clusters whose credits share a word
    with it first, and the remaining ties are scored by their expected rank."""

    def __init__(self, songs, cluster_of: dict[str, str]):
        self.by_title: dict[str, set[str]] = defaultdict(set)
        self.credits: dict[str, set[str]] = defaultdict(set)
        for s in songs:
            c = cluster_of.get(s.doc_id, s.doc_id)
            t = norm_title(s.title)
            if t:
                self.by_title[t].add(c)
            self.credits[c] |= _artist_tokens(" ".join([s.artist or "", composer_of(s), " ".join(singers_of(s))]))

    def blocks(self, guesses: list[tuple[str, str]]) -> list[list[str]]:
        """Ranked blocks of tied clusters, earlier guesses first; a cluster appears once."""
        out, seen = [], set()
        for title, artist in guesses:
            cl = sorted(self.by_title.get(norm_title(title), set()) - seen)
            if not cl:
                continue
            a = _artist_tokens(artist) - {"va", "ft", "feat"}
            if a:
                first = [c for c in cl if a & self.credits[c]]
                rest = [c for c in cl if c not in first]
                for b in (first, rest):
                    if b:
                        out.append(b)
            else:
                out.append(cl)
            seen.update(cl)
        return out


def expected_rr(blocks: list[list[str]], gold: str, depth: int = 10) -> tuple[float, float | None]:
    """Expected reciprocal rank (cut at depth) when ties inside a block are in random order,
    and the expected rank (None if the gold cluster is not in any block)."""
    pos = 0
    for b in blocks:
        if gold in b:
            ranks = range(pos + 1, pos + len(b) + 1)
            rr = sum(1.0 / r for r in ranks if r <= depth) / len(b)
            return rr, (pos + 1 + pos + len(b)) / 2
        pos += len(b)
    return 0.0, None


# ------------------------------------------------------------- retrieval-augmented identification

def snippet(song, query: str, max_syl: int = 30) -> str:
    """The two consecutive lyric lines that share most unmarked syllables with the query,
    cut to max_syl syllables, like a search-result snippet."""
    lines = [ln for ln in song.lines() if ln.strip()]
    if not lines:
        return ""
    q = set(remove_diacritics(clean(query)).split())
    best, best_i = -1, 0
    for i in range(len(lines)):
        win = remove_diacritics(clean(" ".join(lines[i:i + 2]))).split()
        sc = len(q & set(win))
        if sc > best:
            best, best_i = sc, i
    toks = clean(" / ".join(lines[best_i:best_i + 2]).replace("/", " / ")).split()
    return " ".join(toks[:max_syl])


def top_clusters(run: list[tuple[str, float]], cluster_of: dict[str, str], n: int) -> list[tuple[str, str]]:
    """The first n distinct clusters of a ranked list, each with its first-ranked version."""
    out, seen = [], set()
    for d, _ in run:
        c = cluster_of.get(d, d)
        if c in seen:
            continue
        seen.add(c)
        out.append((c, d))
        if len(out) >= n:
            break
    return out


def candidate_block(cands: list[tuple[str, str]], by_id: dict, query: str) -> str:
    rows = []
    for i, (_, d) in enumerate(cands, 1):
        s = by_id[d]
        artist = s.artist or ", ".join(singers_of(s)[:2]) or "không rõ"
        rows.append(f"{i}. {s.title} | {artist} | {snippet(s, query)}")
    return "\n".join(rows)


def parse_choice(text: str, n: int, k: int = 5) -> list[int]:
    """Candidate numbers (1..n) in the order given, duplicates dropped, at most k (0-based)."""
    out = []
    for m in re.finditer(r"\d+", _THINK.sub(" ", text or "")):
        v = int(m.group()) - 1
        if 0 <= v < n and v not in out:
            out.append(v)
        if len(out) >= k:
            break
    return out


def rerank(run: list[tuple[str, float]], cands: list[tuple[str, str]], chosen: list[int]) -> list[tuple[str, float]]:
    """Chosen candidates first (in the LLM's order), then the first-stage list unchanged."""
    first = [cands[i][1] for i in chosen]
    rest = [d for d, _ in run if d not in set(first)]
    order = first + rest
    return [(d, float(len(order) - i)) for i, d in enumerate(order)]
