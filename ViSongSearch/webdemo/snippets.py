"""Đoạn trích kết quả (SEG 6.3 Result pages and snippets) theo cách của Luhn: câu (dòng lời) có
nhiều từ của truy vấn đứng gần nhau nhất được chọn; từ khớp được tô đậm."""
from __future__ import annotations

import html

from visong.text import clean, remove_diacritics

from .lexicons import stopwords


def _plain(w: str) -> str:
    return remove_diacritics(w.lower())


def luhn_score(line_toks: list[str], qset: set[str]) -> float:
    """Điểm Luhn: (số từ có ý nghĩa trong cụm)^2 / độ dài cụm; cụm = đoạn giữa hai từ có ý nghĩa
    cách nhau không quá 4 từ không có ý nghĩa."""
    pos = [i for i, t in enumerate(line_toks) if _plain(t) in qset]
    if not pos:
        return 0.0
    best, start, count = 0.0, pos[0], 1
    for a, b in zip(pos, pos[1:]):
        if b - a <= 4:
            count += 1
        else:
            best = max(best, count * count / (a - start + 1))
            start, count = b, 1
    return max(best, count * count / (pos[-1] - start + 1))


def snippet(lyrics: str, query: str, max_lines: int = 2) -> dict:
    qset = {_plain(w) for w in clean(query).split() if _plain(w) not in stopwords()} or \
           {_plain(w) for w in clean(query).split()}
    lines = [l.strip() for l in lyrics.split("\n") if l.strip()]
    scored = []
    for i, l in enumerate(lines):
        toks = l.split()
        s = luhn_score([clean(t) for t in toks], qset)
        if s > 0:
            scored.append((s, i))
    if not scored:
        chosen = list(range(min(max_lines, len(lines))))
    else:
        best = max(scored)[1]
        chosen = [best] + ([best + 1] if best + 1 < len(lines) and max_lines > 1 else [])
    out_html, out_tagged = [], []
    for i in chosen:
        h, t = [], []
        for tok in lines[i].split():
            hit = _plain(clean(tok)) in qset
            h.append(f"<mark>{html.escape(tok)}</mark>" if hit else html.escape(tok))
            t.append(f"__ais-highlight__{tok}__/ais-highlight__" if hit else tok)
        out_html.append(" ".join(h))
        out_tagged.append(" ".join(t))
    return {"html": " / ".join(out_html), "tagged": " / ".join(out_tagged), "lines": chosen,
            "score": max(scored)[0] if scored else 0.0}


def highlight(text: str, query: str, pre: str = "__ais-highlight__", post: str = "__/ais-highlight__") -> tuple[str, str, list[str]]:
    """Tô từ khớp truy vấn (không phân biệt dấu) trong tên bài / ca sĩ."""
    qset = {_plain(w) for w in clean(query).split()}
    out, matched = [], []
    for tok in (text or "").split():
        if _plain(clean(tok)) in qset and clean(tok):
            out.append(f"{pre}{tok}{post}")
            matched.append(tok)
        else:
            out.append(tok)
    level = "none" if not matched else ("full" if len(matched) == len((text or "").split()) else "partial")
    return " ".join(out), level, matched
