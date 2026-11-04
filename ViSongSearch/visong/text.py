"""Vietnamese text normalisation shared by every component (TMG 01, SEG 4).

Vietnamese is written in syllables separated by spaces. Each syllable can carry
vowel marks (ă â ê ô ơ ư đ) and one tone mark (sắc, huyền, hỏi, ngã, nặng).
In Unicode NFD form the tone is a separate combining character, which makes it
easy to drop or swap tones without knowing Vietnamese tone-placement rules.
"""
from __future__ import annotations

import re
import unicodedata

# Combining tone marks in NFD form.
TONE_MARKS = {
    "́": "sac",    # á
    "̀": "huyen",  # à
    "̉": "hoi",    # ả
    "̃": "nga",    # ã
    "̣": "nang",   # ạ
}
# Combining vowel marks (not tones): breve ă, circumflex â ê ô, horn ơ ư.
VOWEL_MARKS = {"̆", "̂", "̛"}

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
_DIGIT_OR_LETTER = re.compile(r"\w", re.UNICODE)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def clean(text: str) -> str:
    """Lowercase, NFC, drop punctuation, collapse spaces."""
    text = nfc(text).lower().replace("_", " ")
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def remove_tones(text: str) -> str:
    """Drop tone marks only: 'người' -> 'ngươi'."""
    d = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in d if c not in TONE_MARKS))


def remove_diacritics(text: str) -> str:
    """Drop all marks: 'người đẹp' -> 'nguoi dep'."""
    d = unicodedata.normalize("NFD", text)
    d = "".join(c for c in d if unicodedata.category(c) != "Mn")
    return d.replace("đ", "d").replace("Đ", "D")


def has_diacritics(text: str) -> bool:
    return remove_diacritics(text) != text


def tone_of(syllable: str) -> str | None:
    for c in unicodedata.normalize("NFD", syllable):
        if c in TONE_MARKS:
            return TONE_MARKS[c]
    return None


def swap_tone(syllable: str, old: str, new: str) -> str:
    """Replace one tone with another, e.g. hỏi <-> ngã ('nhửng' <-> 'những')."""
    inv = {v: k for k, v in TONE_MARKS.items()}
    d = unicodedata.normalize("NFD", syllable)
    if inv[old] not in d:
        return syllable
    return unicodedata.normalize("NFC", d.replace(inv[old], inv[new]))


def syllables(text: str) -> list[str]:
    """Syllable tokens of cleaned text (the default index unit)."""
    return clean(text).split()


def char_ngrams(text: str, n: int = 3, strip: bool = True) -> list[str]:
    """Character n-grams inside each syllable, padded with '#' at the borders.

    'nhung' -> '#nh', 'nhu', 'hun', 'ung', 'ng#'. Robust to typos and mishearing.
    """
    t = clean(text)
    if strip:
        t = remove_diacritics(t)
    grams: list[str] = []
    for tok in t.split():
        padded = f"#{tok}#"
        if len(padded) <= n:
            grams.append(padded)
            continue
        grams.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return grams


_vi_tokenizer = None


def words(text: str) -> list[str]:
    """Word segmentation with pyvi ('sầu lo' may become 'sầu_lo')."""
    global _vi_tokenizer
    if _vi_tokenizer is None:
        from pyvi import ViTokenizer  # imported lazily: optional dependency
        _vi_tokenizer = ViTokenizer
    return _vi_tokenizer.tokenize(clean(text)).split()


def with_bigrams(tokens: list[str]) -> list[str]:
    """Unigrams plus adjacent pairs ('sầu', 'lo', 'sầu_lo'): keeps local word order,
    which a bag of syllables loses (SEG 5: phrase and proximity information)."""
    return tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def char_ngrams_across(text: str, n: int = 5) -> list[str]:
    """Character n-grams over the whole unmarked text, spaces included ('_'), so a gram can span
    two syllables: 'yeu em' -> 'yeu_e', 'eu_em' for n=5. Captures syllable order and survives
    a misspelled syllable boundary."""
    t = "_" + remove_diacritics(clean(text)).replace(" ", "_") + "_"
    if len(t) <= n:
        return [t]
    return [t[i:i + n] for i in range(len(t) - n + 1)]


# Dialect-normalised syllable key (the mergers of Southern and Northern speech, and spellings of
# one sound): initials x=s, ch=tr, n=l, d=gi=r=v, k=q=c, gh=g, ngh=ng; finals ng=n, nh=n, c=t,
# ch=t; tones ngã=hỏi; y=i. The confusions are the ones the query noise channel uses for Q3, so
# this analyzer is matched to that channel by construction.
_PHON_INITIAL = {"x": "s", "ch": "tr", "n": "l", "gi": "d", "r": "d", "v": "d", "k": "c", "q": "c",
                 "qu": "cu", "gh": "g", "ngh": "ng"}
_PHON_FINAL = (("ng", "n"), ("nh", "n"), ("ch", "t"), ("c", "t"))
_PHON_INITIAL_RE = re.compile(r"^(ngh|ng|nh|ch|tr|th|ph|kh|gh|gi|qu|[bcdđghklmnpqrstvx])?(.*)$")


def phonetic_key(syl: str) -> str:
    d = unicodedata.normalize("NFD", syl)
    d = d.replace("\u0303", "\u0309")                    # ngã -> hỏi
    s = unicodedata.normalize("NFC", d)
    m = _PHON_INITIAL_RE.match(s)
    ini, rest = m.group(1) or "", m.group(2)
    if ini == "gi" and not rest:
        ini, rest = "d", "i"
    if ini == "g" and remove_tones(rest)[:1] == "i":            # gì = gi + ì
        ini = "gi"
    elif ini == "gi" and rest and remove_tones(rest)[0] not in "aăâeêioôơuưy":
        rest = "i" + rest                                         # gin = gi + in
    ini = _PHON_INITIAL.get(ini, ini)
    base = remove_tones(rest)
    for a, b in _PHON_FINAL:
        if base.endswith(a) and len(base) > len(a):
            tone = tone_of(rest)
            base = base[: -len(a)] + b
            rest = base if tone is None else _put_tone(base, tone)
            break
    return (ini + rest).replace("y", "i").replace("ý", "í").replace("ỳ", "ì").replace("ỷ", "ỉ").replace("ỵ", "ị")


def _put_tone(base: str, tone: str) -> str:
    """Tone on the last vowel of a toneless syllable part (enough for a matching key)."""
    mark = {v: k for k, v in TONE_MARKS.items()}[tone]
    idx = [i for i, c in enumerate(base) if c in "aăâeêioôơuưy"]
    if not idx:
        return base
    i = idx[-1]
    return unicodedata.normalize("NFC", base[:i + 1] + mark + base[i + 1:])


def analyzer(name: str):
    """Return a function text -> tokens for an analyzer name used by indexes."""
    if name == "syl":
        return syllables
    if name == "syl12":
        return lambda t: with_bigrams(syllables(t))
    if name == "nodiac":
        return lambda t: remove_diacritics(clean(t)).split()
    if name == "nodiac12":
        return lambda t: with_bigrams(remove_diacritics(clean(t)).split())
    if name == "word":
        return words
    if name == "char3":
        return lambda t: char_ngrams(t, 3)
    if name == "char4":
        return lambda t: char_ngrams(t, 4)
    if name in ("char4x", "char5x"):
        n = int(name[4])
        return lambda t: char_ngrams_across(t, n)
    if name == "phon12":
        return lambda t: with_bigrams([phonetic_key(s) for s in syllables(t)])
    raise ValueError(f"unknown analyzer {name}")
