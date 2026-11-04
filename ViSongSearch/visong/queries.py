"""Known-item query generation (SEG 9: building a test collection).

Every query is produced from one song, so its correct answer is that song's
cluster. This gives labels for free and does not favour any retrieval model,
unlike labels made by "does the lyric contain the query string".

Types
  Q1 exact     5-12 consecutive syllables copied from the lyrics (people recall short hooks)
  Q2 nodiac    Q1 without diacritics; 15% also keep 1-2 Telex-typed syllables
  Q3 noisy     Q1 with 10-40% of syllables misheard or mistyped (noise.py)
  Q4 paraphr.  an LLM rewrites a lyric fragment keeping the meaning
  Q5 describe  an LLM plays a user who forgot the title (tip-of-the-tongue)
  Q4u, Q5u     Q4 and Q5 with every mark removed (paraphrases and descriptions typed without marks)
  Q3u, Q4s, Q5g  test-only variants (scripts/build_variants.py): Q3 typed without marks, a shorter
               paraphrase prompt, and descriptions written from a gist without lyrics or singer
  H  human     written by people; test only
"""
from __future__ import annotations

import hashlib
import random
import re

from .corpus import is_compilation_song
from .noise import add_telex_residue, corrupt
from .text import clean, remove_diacritics

SPLITS = (("train", 0.7), ("dev", 0.8), ("test", 1.0))
# Every query file of a work folder, in the order they are read.
QUERY_FILES = ("rule.jsonl", "llm.jsonl", "llm_unmarked.jsonl", "human.jsonl", "variants.jsonl")
MAIN_TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5")
UNMARKED_TYPES = ("Q4u", "Q5u")      # Q4 and Q5 typed without marks


# Q4/Q5 are written for every dev/test cluster of the pilot collection and, among the clusters the
# full crawl added, for those whose key hashes below LLM_SAMPLE[split]: a random sample that does not
# depend on the order in which songs were crawled. Queries for other clusters go to llm_extra.jsonl.
LLM_SAMPLE = {"dev": 0.06, "test": 0.12}


def sample_key(cluster_key: str) -> float:
    """Uniform in [0, 1], independent of the split hash."""
    return int(hashlib.md5(f"llm:{cluster_key}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def in_llm_sample(song) -> bool:
    key = song.extra.get("cluster_key")
    return key is not None and sample_key(key) < LLM_SAMPLE.get(song.extra.get("split"), 0.0)


def split_of(cluster_id: str, salt: str = "visong") -> str:
    """Deterministic 70/10/20 split by cluster, so versions of a song never cross splits."""
    h = int(hashlib.md5(f"{salt}:{cluster_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    for name, upper in SPLITS:
        if h < upper:
            return name
    return "test"


def fragment(song, rng: random.Random, min_len: int = 5, max_len: int = 12) -> tuple[str, int] | None:
    """Consecutive syllables starting at a random line (may cross line breaks)."""
    lines = [clean(l) for l in song.lines()]
    lines = [l for l in lines if l]
    if not lines:
        return None
    total = sum(len(l.split()) for l in lines)
    if total < min_len:
        return None
    for _ in range(20):
        start = rng.randrange(len(lines))
        length = rng.randint(min_len, max_len)
        toks: list[str] = []
        i = start
        while i < len(lines) and len(toks) < length:
            toks.extend(lines[i].split())
            i += 1
        toks = toks[:length]
        if len(toks) >= min_len:
            return " ".join(toks), start
    return None


class AmbiguityChecker:
    """Reject fragments that also occur verbatim in a different cluster (a stock phrase shared
    by two songs, or a line quoted in a medley). The candidates are every song that contains all
    syllables of the fragment (posting lists of unmarked syllables), so no song is missed; each
    is then tested for the whole syllable sequence on its diacritic-free lyrics.

    The pilot queries were made with an earlier version that only tested the ten best songs of
    a unigram BM25 index. It missed three test fragments whose other occurrence is a long medley
    ranked 16th to 51st (nine Q1-Q3 queries)."""

    def __init__(self, songs, cluster_of):
        self.text = {s.doc_id: " " + remove_diacritics(" ".join(clean(l) for l in s.lines())) + " "
                     for s in songs}
        self.cluster_of = cluster_of
        self.postings: dict[str, set[str]] = {}
        for d, t in self.text.items():
            for tok in set(t.split()):
                self.postings.setdefault(tok, set()).add(d)

    def clusters_with(self, frag: str) -> set[str]:
        """Clusters with a song whose lyrics contain the fragment, marks ignored."""
        toks = remove_diacritics(clean(frag)).split()
        if not toks:
            return set()
        lists = sorted((self.postings.get(t, set()) for t in set(toks)), key=len)
        plain = " " + " ".join(toks) + " "
        return {self.cluster_of[d] for d in set.intersection(*lists) if plain in self.text[d]}

    def ambiguous(self, frag: str, doc_id: str) -> bool:
        return bool(self.clusters_with(frag) - {self.cluster_of[doc_id]})


def representatives(songs, cluster_of) -> dict[str, str]:
    """One target version per cluster: the longest lyrics that are not a medley (ties: smallest id)."""
    best: dict[str, tuple] = {}
    for s in songs:
        if is_compilation_song(s):
            continue
        key = (-len(s.lyrics.split()), s.doc_id)
        c = cluster_of[s.doc_id]
        if c not in best or key < best[c]:
            best[c] = key
    return {c: k[1] for c, k in best.items()}


def generate_rule_queries(songs, cluster_of, per_song: dict[str, dict[str, int]], seed: int = 13,
                          checker: AmbiguityChecker | None = None, telex_prob: float = 0.15,
                          noise_rates=(0.1, 0.2, 0.3, 0.4), split_of_doc: dict | None = None,
                          one_per_cluster: tuple[str, ...] = ()) -> list[dict]:
    """Q1, Q2 and Q3 for every song. `per_song[split][type]` = how many per song. In the splits
    named by `one_per_cluster`, only the representative version of each cluster gets queries."""
    rng = random.Random(seed)
    out: list[dict] = []
    rep = set(representatives(songs, cluster_of).values()) if one_per_cluster else set()
    for song in songs:
        if is_compilation_song(song):
            continue
        cluster = cluster_of[song.doc_id]
        split = split_of_doc[song.doc_id] if split_of_doc else split_of(cluster)
        if split in one_per_cluster and song.doc_id not in rep:
            continue
        counts = per_song[split]
        n_frag = max(counts.get("Q1", 0), counts.get("Q2", 0), counts.get("Q3", 0))
        frags = []
        tries = 0
        while len(frags) < n_frag and tries < n_frag * 6:
            tries += 1
            f = fragment(song, rng)
            if f is None:
                break
            if checker and checker.ambiguous(f[0], song.doc_id):
                continue
            if f[0] not in [x[0] for x in frags]:
                frags.append(f)
        for j, (frag, line) in enumerate(frags):
            base = {"doc_id": song.doc_id, "cluster": cluster, "split": split}
            meta = {"line": line, "source_fragment": frag, "length": len(frag.split())}
            if j < counts.get("Q1", 0):
                out.append({**base, "qid": f"{song.doc_id}-Q1-{j}", "type": "Q1", "text": frag, "meta": meta})
            if j < counts.get("Q2", 0):
                plain = remove_diacritics(frag)
                telex_used = rng.random() < telex_prob
                text = add_telex_residue(frag, plain, rng) if telex_used else plain
                out.append({**base, "qid": f"{song.doc_id}-Q2-{j}", "type": "Q2", "text": text,
                            "meta": {**meta, "telex": telex_used}})
            if j < counts.get("Q3", 0):
                rate = rng.choice(noise_rates)
                text, info = corrupt(frag, rate, rng)
                out.append({**base, "qid": f"{song.doc_id}-Q3-{j}", "type": "Q3", "text": text,
                            "meta": {**meta, **info}})
    return out


def noise_sweep(queries: list[dict], rates=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5), seed: int = 7) -> list[dict]:
    """Same test fragments at increasing noise rates, for the robustness curve."""
    rng = random.Random(seed)
    out = []
    for q in queries:
        if q["type"] != "Q1" or q["split"] != "test":
            continue
        for r in rates:
            text, info = corrupt(q["text"], r, rng) if r > 0 else (q["text"], {"rate": 0.0})
            out.append({**q, "qid": f"{q['qid']}-r{int(r * 100)}", "type": f"N{int(r * 100)}",
                        "text": text, "meta": {**q["meta"], **info}})
    return out


# ------------------------------------------------------------- LLM queries

PROMPT_Q4 = """Bạn là người nghe nhạc chỉ nhớ mang máng một đoạn lời bài hát tiếng Việt.
Đây là đoạn lời thật:
"{fragment}"
Hãy viết lại đoạn này như cách bạn nhớ sai: giữ ý chính, nhưng đổi phần lớn từ ngữ
(dùng từ đồng nghĩa, đảo trật tự, bỏ bớt chữ). Không chép nguyên cụm từ 4 chữ trở lên.
Viết 1 câu, 8-20 âm tiết, chữ thường, không dấu câu. Chỉ trả lời câu đó."""

PROMPT_Q5 = """Bạn đang cố tìm một bài hát tiếng Việt nhưng quên tên bài.
Thông tin bạn còn nhớ (KHÔNG được nhắc tên bài):
- Tên bài (chỉ để bạn biết, cấm viết ra): {title}
- Sáng tác: {composer}
- Ca sĩ: {singers}
- Lời bài hát (một phần): {lyrics}
Hãy viết một câu tìm kiếm như người thật gõ vào Google khi quên tên bài: mô tả nội dung,
cảm xúc, hoàn cảnh, có thể nhớ lờ mờ vài chữ hoặc tên ca sĩ. Không viết tên bài.
1-2 câu, tối đa 35 âm tiết, chữ thường. Chỉ trả lời câu đó."""


# The pilot's prompt had one line "- Tác giả / ca sĩ: {artist}": its sources keep a single artist field.

# Prompts of the test-only variants (scripts/build_variants.py). Q4s: the Q4 prompt asking for the
# length of Q1. Q5g: a description written by a writer that sees only a one-line gist of the song,
# itself written from the first lines of the lyrics by another call that never sees the title or singer.
PROMPT_Q4S = """Bạn là người nghe nhạc chỉ nhớ mang máng một đoạn lời bài hát tiếng Việt. Đây là đoạn lời thật:
"{fragment}". Hãy viết lại đoạn này như cách bạn nhớ sai: giữ ý chính, nhưng đổi phần lớn từ ngữ
(dùng từ đồng nghĩa, đảo trật tự, bỏ bớt chữ). Không chép nguyên cụm từ 4 chữ trở lên.
Viết 1 câu ngắn, 5-12 âm tiết, chữ thường, có dấu tiếng Việt, không dấu câu. Chỉ trả lời câu đó."""

PROMPT_GIST = """Đọc lời bài hát dưới đây và tóm tắt nội dung của bài trong 1 câu, tối đa 20 âm tiết: bài nói về ai,
chuyện gì, cảm xúc và hoàn cảnh ra sao. Viết bằng lời của bạn: không dùng lại cụm từ 3 chữ liên tiếp trở lên
của lời bài hát, không trích lời. Chữ thường, có dấu tiếng Việt.
{lyrics}"""

PROMPT_Q5G = """Bạn đang cố tìm một bài hát tiếng Việt nhưng quên tên bài và không nhớ lời. Bạn chỉ nhớ bài hát nói
về: "{gist}". Hãy viết một câu tìm kiếm như người thật gõ vào Google khi quên tên bài: mô tả nội dung, cảm xúc,
hoàn cảnh. 1-2 câu, tối đa 35 âm tiết, chữ thường, có dấu. Chỉ trả lời câu đó."""


def longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def check_llm_query(qtype: str, text: str, song, fragment_text: str | None = None) -> tuple[bool, str]:
    """Automatic filters for LLM queries: no title leak, not a copy of the lyrics."""
    q = remove_diacritics(clean(text))
    if len(q.split()) < 4:
        return False, "too_short"
    title = remove_diacritics(clean(re.sub(r"\(.*?\)", " ", song.title)))
    # One-syllable titles such as "Mưa" or "Em" are ordinary words a description
    # cannot avoid, so only titles of two or more syllables count as a leak.
    if qtype == "Q5" and title and len(title.split()) >= 2:
        if re.search(rf"\b{re.escape(title)}\b", q):
            return False, "title_leak"
    lyr = remove_diacritics(clean(" ".join(song.lines()))).split()
    run = longest_common_run(q.split(), lyr)
    limit = 6 if qtype == "Q5" else 5
    if run >= limit:
        return False, f"copied_{run}"
    if qtype == "Q4" and fragment_text:
        frag = set(remove_diacritics(clean(fragment_text)).split())
        overlap = sum(t in frag for t in q.split()) / len(q.split())
        if overlap > 0.7:
            return False, "too_close"
    return True, "ok"


def unmarked_variants(queries: list[dict]) -> list[dict]:
    """Q4u/Q5u: the LLM paraphrases and descriptions with every mark removed. People often type
    without marks, and the LLM always wrote with them."""
    out = []
    for q in queries:
        if q["type"] not in ("Q4", "Q5"):
            continue
        out.append({**q, "qid": q["qid"].replace(f"-{q['type']}-", f"-{q['type']}u-"), "type": q["type"] + "u",
                    "text": remove_diacritics(q["text"]), "meta": {**q.get("meta", {}), "from": q["qid"]}})
    return out


def write_unmarked_variants(work: str) -> int:
    import os
    from .corpus import read_jsonl, write_jsonl
    src = os.path.join(work, "queries", "llm.jsonl")
    if not os.path.exists(src):
        return 0
    rows = unmarked_variants(read_jsonl(src))
    write_jsonl(os.path.join(work, "queries", "llm_unmarked.jsonl"), rows)
    return len(rows)
