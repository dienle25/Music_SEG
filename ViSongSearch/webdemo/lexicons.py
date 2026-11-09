"""Đọc các từ điển JSON trong thư mục lexicon/ và so khớp cụm từ nhiều âm tiết.

So khớp dài nhất trước (longest match): "nước mắt" được nhận là một cụm, không phải "nước" + "mắt".
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from visong.text import clean, remove_diacritics

HERE = os.path.dirname(os.path.abspath(__file__))


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    with open(os.path.join(HERE, "lexicon", f"{name}.json"), encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


class PhraseMatcher:
    """Tìm các cụm từ của một từ điển trong chuỗi âm tiết (có dấu hoặc không dấu)."""

    def __init__(self, phrases, plain_too: bool = True):
        self.table: dict[tuple[str, ...], str] = {}
        plain_seen: dict[tuple[str, ...], set] = {}
        for p in phrases:
            toks = tuple(clean(p).split())
            if toks:
                self.table[toks] = p
                plain_seen.setdefault(tuple(remove_diacritics(" ".join(toks)).split()), set()).add(p)
        if plain_too:   # dạng không dấu chỉ dùng khi không mơ hồ ("buon" -> "buồn")
            for k, ps in plain_seen.items():
                if len(ps) == 1 and k not in self.table:
                    self.table[k] = next(iter(ps))
        self.max_len = max((len(k) for k in self.table), default=1)

    def find(self, tokens: list[str]) -> list[tuple[int, int, str]]:
        """Danh sách (bắt đầu, kết thúc, cụm gốc), không chồng nhau, ưu tiên cụm dài."""
        out, i = [], 0
        while i < len(tokens):
            for n in range(min(self.max_len, len(tokens) - i), 0, -1):
                key = tuple(tokens[i:i + n])
                if key in self.table:
                    out.append((i, i + n, self.table[key]))
                    i += n
                    break
            else:
                i += 1
        return out

    def find_all(self, tokens: list[str]) -> list[tuple[int, int, str]]:
        """Mọi lần khớp ở mọi vị trí (có thể chồng nhau), để bộ tách cụm tự chọn."""
        out = []
        for i in range(len(tokens)):
            for n in range(1, min(self.max_len, len(tokens) - i) + 1):
                key = tuple(tokens[i:i + n])
                if key in self.table:
                    out.append((i, i + n, self.table[key]))
        return out


@lru_cache(maxsize=None)
def stopwords() -> frozenset:
    words = set(load("query")["STOP"])
    return frozenset(words | {remove_diacritics(w) for w in words})
