"""Noise channel for Vietnamese queries (TMG 01: edit distance and spelling errors).

It simulates how people type a half-remembered lyric:
- mishearing / regional pronunciation: s~x, tr~ch, l~n, d~gi~r, v~d at the start,
  n~ng and t~c at the end, hỏi~ngã tones;
- typing slips: a dropped tone, an adjacent key, a missing, doubled or swapped letter;
- input-method residue: Telex keys left in the text ("nhuwngx" for "những").
"""
from __future__ import annotations

import random
import re
import unicodedata

from .text import remove_tones, swap_tone, tone_of

_INITIAL = re.compile(r"^(ngh|ng|nh|ch|tr|th|ph|kh|gh|gi|qu|[bcdđghklmnprstvx])?(.*)$")

INITIAL_CONFUSIONS = {
    "s": ["x"], "x": ["s"],
    "tr": ["ch"], "ch": ["tr"],
    "l": ["n"], "n": ["l"],
    "d": ["gi", "r", "v"], "gi": ["d", "r"], "r": ["d", "gi"], "v": ["d"],
}
FINAL_CONFUSIONS = [("ng", "n"), ("n", "ng"), ("c", "t"), ("t", "c")]

_QWERTY = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_NEIGHBORS: dict[str, str] = {}
for r, row in enumerate(_QWERTY):
    for c, ch in enumerate(row):
        near = []
        for dr in (-1, 0, 1):
            rr = r + dr
            if 0 <= rr < len(_QWERTY):
                for dc in (-1, 0, 1):
                    cc = c + dc
                    if (dr or dc) and 0 <= cc < len(_QWERTY[rr]):
                        near.append(_QWERTY[rr][cc])
        _NEIGHBORS[ch] = "".join(near)

_TELEX_VOWEL = {"â": "aa", "ă": "aw", "ê": "ee", "ô": "oo", "ơ": "ow", "ư": "uw", "đ": "dd"}
_TELEX_TONE = {"sac": "s", "huyen": "f", "hoi": "r", "nga": "x", "nang": "j"}


def split_initial(syl: str) -> tuple[str, str]:
    m = _INITIAL.match(syl)
    return (m.group(1) or ""), m.group(2)


def confuse_initial(syl: str, rng: random.Random) -> str | None:
    ini, rest = split_initial(syl)
    if ini not in INITIAL_CONFUSIONS or not rest:
        return None
    new = rng.choice(INITIAL_CONFUSIONS[ini])
    if new == "gi" and rest.startswith("i"):
        return None
    return new + rest


def confuse_final(syl: str, rng: random.Random) -> str | None:
    options = [(a, b) for a, b in FINAL_CONFUSIONS
               if syl.endswith(a) and not (a == "n" and syl.endswith(("nh", "ng")))
               and not (a == "c" and syl.endswith("ch"))]
    if not options:
        return None
    a, b = rng.choice(options)
    return syl[: -len(a)] + b


def confuse_tone(syl: str, rng: random.Random) -> str | None:
    t = tone_of(syl)
    if t == "hoi":
        return swap_tone(syl, "hoi", "nga")
    if t == "nga":
        return swap_tone(syl, "nga", "hoi")
    if t is not None and rng.random() < 0.5:
        return remove_tones(syl)          # forgot the tone
    return None


def keyboard_typo(syl: str, rng: random.Random) -> str | None:
    positions = [i for i, ch in enumerate(syl) if ch in _NEIGHBORS]
    if not positions:
        return None
    i = rng.choice(positions)
    op = rng.choice(["sub", "del", "dup", "swap"])
    if op == "sub":
        return syl[:i] + rng.choice(_NEIGHBORS[syl[i]]) + syl[i + 1:]
    if op == "del" and len(syl) > 1:
        return syl[:i] + syl[i + 1:]
    if op == "dup":
        return syl[:i] + syl[i] + syl[i:]
    if op == "swap" and i + 1 < len(syl):
        return syl[:i] + syl[i + 1] + syl[i] + syl[i + 2:]
    return None


def telex(syl: str) -> str:
    """Type a syllable in Telex without the input method: 'những' -> 'nhuwngx'."""
    t = tone_of(syl)
    base = remove_tones(syl)
    out = "".join(_TELEX_VOWEL.get(ch, ch) for ch in base)
    out = unicodedata.normalize("NFC", out)
    return out + (_TELEX_TONE[t] if t else "")


OPERATIONS = {
    "initial": confuse_initial,
    "final": confuse_final,
    "tone": confuse_tone,
    "typo": keyboard_typo,
}
DEFAULT_WEIGHTS = {"initial": 0.3, "final": 0.15, "tone": 0.25, "typo": 0.3}


def corrupt_syllable(syl: str, rng: random.Random, weights=None) -> tuple[str, str | None]:
    weights = weights or DEFAULT_WEIGHTS
    ops = list(weights)
    order = rng.choices(ops, weights=[weights[o] for o in ops], k=len(ops))
    order += [o for o in ops if o not in order]
    for op in order:
        new = OPERATIONS[op](syl, rng)
        if new and new != syl:
            return new, op
    return syl, None


def corrupt(text: str, rate: float, rng: random.Random, weights=None) -> tuple[str, dict]:
    """Corrupt about `rate` of the syllables (at least one when rate > 0)."""
    syl = text.split()
    n = len(syl)
    k = max(1 if rate > 0 else 0, round(rate * n))
    idx = rng.sample(range(n), min(k, n))
    ops = {}
    done = set()
    for i in idx:
        new, op = corrupt_syllable(syl[i], rng, weights)
        if op:
            syl[i] = new
            ops[op] = ops.get(op, 0) + 1
            done.add(i)
    # A chosen syllable can resist every operation (all of them may fail on "thấy"). Top up
    # from the other syllables with a second generator seeded by the text, so that the main
    # stream, and with it every query that did not fall short, stays the same.
    if len(done) < len(idx):
        extra = random.Random(text)
        rest = [i for i in range(n) if i not in done]
        extra.shuffle(rest)
        for i in rest:
            if len(done) >= len(idx):
                break
            for _ in range(3):
                new, op = corrupt_syllable(syl[i], extra, weights)
                if op:
                    syl[i] = new
                    ops[op] = ops.get(op, 0) + 1
                    done.add(i)
                    break
    return " ".join(syl), {"rate": rate, "changed": sum(ops.values()), "ops": ops}


def add_telex_residue(text_with_marks: str, stripped: str, rng: random.Random, max_syl: int = 2) -> str:
    """Replace 1-2 syllables of a no-diacritic query by their Telex spelling."""
    marked, plain = text_with_marks.split(), stripped.split()
    cand = [i for i, s in enumerate(marked) if s != plain[i]]
    if not cand:
        return stripped
    for i in rng.sample(cand, min(len(cand), rng.randint(1, max_syl))):
        plain[i] = telex(marked[i])
    return " ".join(plain)


_TELEX_TOKEN = re.compile(r"^[a-z]+$")
_TONE_KEY = {v: k for k, v in _TELEX_TONE.items()}
_INV_VOWEL = {v: k for k, v in _TELEX_VOWEL.items()}


def untelex(token: str) -> tuple[str, str | None] | None:
    """Decode a Telex-typed token into (syllable without tone, tone name).

    'nhuwngx' -> ('nhưng', 'nga'). The caller looks the toneless form up in a
    vocabulary and keeps the syllables with that tone, so we never have to place
    the tone mark ourselves. Returns None when the token does not look like Telex.
    """
    if not _TELEX_TOKEN.match(token):
        return None
    tone = None
    if len(token) > 2 and token[-1] in _TONE_KEY and any(v in token[:-1] for v in "aeiouy"):
        tone, token = _TONE_KEY[token[-1]], token[:-1]
    out = token
    for seq in ("uwow", "uow"):
        out = out.replace(seq, "ươ")
    for tel, ch in sorted(_INV_VOWEL.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(tel, ch)
    out = out.replace("w", "")
    if out == token and tone is None:
        return None
    return unicodedata.normalize("NFC", out), tone


# ----------------------------------------------------------------- empirical channel (W)
# The channel above models dialect, tone and typing errors only. Versions of one song typed by
# different people differ in other ways too, above all by whole words (scripts/real_noise_stats.py:
# about half of the substitutions). The W channel samples the kind of each substituted syllable
# from the observed shares and draws the replacement from the observed substitution pairs.

_VOWELS = "aăâeêioôơuưy"
VOWEL_MARK_ALTERNATIVES = {"a": "ăâ", "ă": "aâ", "â": "aă", "e": "ê", "ê": "e", "o": "ôơ", "ô": "oơ",
                           "ơ": "oô", "u": "ư", "ư": "u", "d": "đ", "đ": "d"}


def place_tone(base: str, tone: str | None) -> str:
    """Put a tone on a toneless syllable with the usual rules: on the marked vowel (ơ in ươ),
    else on the only vowel, else on the last vowel of a closed syllable or the second-to-last
    vowel of an open one ('hoa' -> 'hóa', 'hoang' -> 'hoáng'). The qu and gi glides never carry it."""
    if not tone:
        return base
    from .text import TONE_MARKS
    mark = {v: k for k, v in TONE_MARKS.items()}[tone]
    chars = list(unicodedata.normalize("NFC", base))
    start = 2 if base.startswith(("qu", "gi")) and len(base) > 2 and any(c in _VOWELS for c in base[2:]) else 0
    pos = [i for i in range(start, len(chars)) if chars[i] in _VOWELS]
    if not pos:
        return base
    run = [pos[0]]
    for i in pos[1:]:
        if i == run[-1] + 1:
            run.append(i)
    marked = [i for i in run if chars[i] in "ăâêôơư"]
    if marked:
        i = marked[-1]
    elif len(run) == 1:
        i = run[0]
    elif run[-1] < len(chars) - 1:
        i = run[-1]
    else:
        i = run[-2]
    chars[i] = unicodedata.normalize("NFC", chars[i] + mark)
    return "".join(chars)


class EmpiricalChannel:
    """Substitutions sampled like those observed between versions of one song.

    pairs: [{"a": syllable, "b": syllable, "kind": kind}] from real_noise_stats.py, where kind is
    other_word, tone, initial_known, initial_other, final, vowel_mark or one_letter."""

    KINDS = ("other_word", "tone", "initial_known", "initial_other", "final", "vowel_mark", "one_letter")

    def __init__(self, pairs: list[dict]):
        from collections import Counter, defaultdict
        self.kind_counts = Counter(p["kind"] for p in pairs if p["kind"] in self.KINDS)
        self.word_subs, self.word_pool = defaultdict(Counter), Counter()
        self.tone_trans, self.initial_trans, self.initial_pool = defaultdict(Counter), defaultdict(Counter), Counter()
        for p in pairs:
            for x, y in ((p["a"], p["b"]), (p["b"], p["a"])):        # alignments have no direction
                if p["kind"] == "other_word":
                    self.word_subs[x][y] += 1
                    self.word_pool[y] += 1
                elif p["kind"] == "tone":
                    self.tone_trans[tone_of(x)][tone_of(y)] += 1
                elif p["kind"] == "initial_other":
                    ix, iy = split_initial(remove_tones(x))[0], split_initial(remove_tones(y))[0]
                    self.initial_trans[ix][iy] += 1
                    self.initial_pool[iy] += 1
        self.ops = {"other_word": self.other_word, "tone": self.tone, "initial_known": confuse_initial,
                    "initial_other": self.initial_other, "final": confuse_final,
                    "vowel_mark": self.vowel_mark, "one_letter": keyboard_typo}

    @staticmethod
    def _draw(counter, rng, exclude=None):
        items = [(k, c) for k, c in counter.items() if k != exclude]
        if not items:
            return None
        keys, weights = zip(*items)
        return rng.choices(keys, weights=weights)[0]

    def other_word(self, syl, rng):
        """A word seen replacing this one, else one of the words seen in whole-word substitutions."""
        if syl in self.word_subs and rng.random() < 0.5:
            return self._draw(self.word_subs[syl], rng, exclude=syl)
        return self._draw(self.word_pool, rng, exclude=syl)

    def tone(self, syl, rng):
        cur = tone_of(syl)
        options = self.tone_trans.get(cur) or {t: 1 for t in (None, "sac", "huyen", "hoi", "nga", "nang")}
        new = self._draw(options, rng, exclude=cur)
        if new == cur:
            return None
        return place_tone(remove_tones(syl), new)

    def initial_other(self, syl, rng):
        ini, rest = split_initial(syl)
        if not rest:
            return None
        for _ in range(5):
            new = self._draw(self.initial_trans.get(ini) or self.initial_pool, rng, exclude=ini)
            if new is None or new in INITIAL_CONFUSIONS.get(ini, []):
                continue
            front = rest[:1] in ("i", "e", "ê", "y") or remove_tones(rest)[:1] in ("i", "e", "ê", "y")
            if (new in ("k", "gh", "ngh") and not front) or (new in ("c", "g", "ng") and front) \
                    or (new == "qu" and remove_tones(rest)[:1] in ("u", "o")) or (new == "gi" and rest[:1] == "i"):
                continue
            return new + rest
        return None

    def vowel_mark(self, syl, rng):
        cur = tone_of(syl)
        base = remove_tones(syl)
        pos = [i for i, c in enumerate(base) if c in VOWEL_MARK_ALTERNATIVES]
        if not pos:
            return None
        i = rng.choice(pos)
        new = base[:i] + rng.choice(VOWEL_MARK_ALTERNATIVES[base[i]]) + base[i + 1:]
        return place_tone(new, cur)

    def corrupt(self, text: str, rate: float, rng: random.Random) -> tuple[str, dict]:
        """Substitute about `rate` of the syllables (at least one when rate > 0). The kind is drawn
        first and applied to a syllable it fits, so the kinds keep their observed shares."""
        syl = text.split()
        n = len(syl)
        k = min(n, max(1 if rate > 0 else 0, round(rate * n)))
        done, kinds = set(), {}
        kind_list = [kk for kk in self.KINDS if self.kind_counts[kk]]
        weights = [self.kind_counts[kk] for kk in kind_list]
        tries = 0
        while len(done) < k and tries < 50 * max(1, k):
            tries += 1
            kind = rng.choices(kind_list, weights=weights)[0]
            free = [i for i in range(n) if i not in done]
            rng.shuffle(free)
            for i in free:
                new = self.ops[kind](syl[i], rng)
                if new and new != syl[i]:
                    syl[i] = new
                    done.add(i)
                    kinds[kind] = kinds.get(kind, 0) + 1
                    break
        return " ".join(syl), {"rate": rate, "changed": len(done), "ops": kinds, "channel": "observed"}
