"""Sửa lỗi chính tả theo mô hình kênh nhiễu (TMG 01 khoảng cách sửa, TMG 02 mô hình n-gram,
SEG 6.2 Spell checking and suggestions).

    ŵ = argmax_w  P(t | w) · P(w)        t: câu người dùng gõ, w: câu đúng
- P(w): mô hình ngôn ngữ trigram Kneser-Ney học trên lời bài hát (visong/diacritics.py)
- P(t | w): mô hình kênh, cho mỗi âm tiết một chi phí theo loại lỗi (thêm dấu, sai dấu,
  nghe nhầm theo vùng miền, gõ nhầm phím, kiểu gõ Telex còn sót)
Giải mã bằng beam search trên các trạng thái (u, v) của mô hình trigram, giữ n đường tốt nhất.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from visong.diacritics import BOS, EOS
from visong.noise import _NEIGHBORS, untelex
from visong.text import clean, has_diacritics, phonetic_key, remove_diacritics, remove_tones, tone_of

# Chi phí kênh (càng lớn càng ít khả năng): -log P(t | w) xấp xỉ
COST = {"giữ nguyên": 0.0, "thêm dấu": 0.0, "telex": 0.3, "sai dấu": 1.5, "nghe nhầm": 2.0,
        "gõ nhầm": 3.0, "gõ nhầm 2": 4.5}


# ------------------------------------------------------------------ khoảng cách sửa (TMG 01)

def edit_distance(a: str, b: str, sub_cost: float = 1.0, transpose: bool = True,
                  keyboard: bool = False) -> dict:
    """Khoảng cách Levenshtein (thêm Damerau: đổi chỗ hai ký tự kề nhau) bằng quy hoạch động.
    Trả về khoảng cách, bảng D và dãy phép biến đổi (truy vết ngược)."""
    n, m = len(a), len(b)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = float(i)
    for j in range(1, m + 1):
        D[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                s = 0.0
            elif keyboard and b[j - 1] in _NEIGHBORS.get(a[i - 1], ""):
                s = sub_cost * 0.5               # phím kề nhau: lỗi gõ hay gặp hơn
            else:
                s = sub_cost
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + s)
            if transpose and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                D[i][j] = min(D[i][j], D[i - 2][j - 2] + 1)
    ops, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and D[i][j] == D[i - 1][j - 1]:
            ops.append(("giữ", a[i - 1], b[j - 1])); i, j = i - 1, j - 1
        elif (transpose and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]
              and D[i][j] == D[i - 2][j - 2] + 1):
            ops.append(("đổi chỗ", a[i - 2:i], b[j - 2:j])); i, j = i - 2, j - 2
        elif i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (
                0.5 * sub_cost if keyboard and b[j - 1] in _NEIGHBORS.get(a[i - 1], "") else sub_cost):
            ops.append(("thay", a[i - 1], b[j - 1])); i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            ops.append(("xóa", a[i - 1], "")); i -= 1
        else:
            ops.append(("chèn", "", b[j - 1])); j -= 1
    return {"distance": D[n][m], "matrix": D, "ops": ops[::-1], "a": a, "b": b}


def _osa(a: str, b: str) -> int:
    return int(edit_distance(a, b)["distance"])


def deletes(word: str, max_d: int) -> set[str]:
    """Mọi chuỗi có được khi xóa tối đa max_d ký tự (ý tưởng SymSpell)."""
    out, frontier = {word}, {word}
    for _ in range(max_d):
        nxt = set()
        for w in frontier:
            for i in range(len(w)):
                nxt.add(w[:i] + w[i + 1:])
        out |= nxt
        frontier = nxt
    return out


class DeleteIndex:
    """Chỉ mục xóa ký tự: tìm nhanh các âm tiết cách từ gõ sai tối đa 2 phép sửa."""

    def __init__(self, words, max_d: int = 2):
        self.max_d = max_d
        self.idx: dict[str, set[str]] = defaultdict(set)
        for w in words:
            for d in deletes(w, max_d):
                self.idx[d].add(w)

    def lookup(self, t: str, max_d: int | None = None) -> list[tuple[str, int]]:
        max_d = self.max_d if max_d is None else max_d
        cands = set()
        for d in deletes(t, max_d):
            cands |= self.idx.get(d, set())
        out = [(w, _osa(t, w)) for w in cands if w != t]
        return sorted([x for x in out if x[1] <= max_d], key=lambda x: (x[1], x[0]))


# ------------------------------------------------------------------ mô hình kênh nhiễu

class NoisyChannelSpeller:
    def __init__(self, restorer, min_count: int = 2, max_candidates: int = 12, beam: int = 10):
        self.r = restorer
        self.lm = restorer.lm
        self.freq = Counter()
        for w, c in restorer.lm.unigram.items():
            if w not in (EOS,):
                self.freq[w] += c
        self.marked_vocab = {w for w, c in self.freq.items() if c >= min_count}
        plain = Counter()
        for w, c in self.freq.items():
            plain[remove_diacritics(w)] += c
        self.plain_freq = plain
        self.plain_vocab = {w for w, c in plain.items() if c >= min_count and w.isalpha()}
        self.delete_index = DeleteIndex(self.plain_vocab, 2)
        self.by_phon: dict[str, list[str]] = defaultdict(list)
        for w in self.marked_vocab:
            self.by_phon[phonetic_key(w)].append(w)
        self.by_phon_plain: dict[str, list[str]] = defaultdict(list)
        for w in self.plain_vocab:
            self.by_phon_plain[phonetic_key(w)].append(w)
        self.max_candidates = max_candidates
        self.beam = beam

    def _marked_forms(self, plain: str, k: int = 3) -> list[str]:
        return [w for w, _ in self.r.by_plain.get(plain, Counter()).most_common(k)]

    def candidates(self, tok: str) -> list[tuple[str, float, str]]:
        """(âm tiết ứng viên, chi phí kênh, loại lỗi) cho một âm tiết gõ vào."""
        out: dict[str, tuple[float, str]] = {}

        def add(w, kind):
            c = COST[kind]
            if w not in out or out[w][0] > c:
                out[w] = (c, kind)
        marked = has_diacritics(tok)
        plain = remove_diacritics(tok)
        if marked:
            if tok in self.freq:
                add(tok, "giữ nguyên")
            for w in self._marked_forms(plain, 6):
                add(w, "sai dấu")
            for w in self.by_phon.get(phonetic_key(tok), [])[:8]:
                add(w, "nghe nhầm")
        else:
            for w in self._marked_forms(plain, 8):
                add(w, "thêm dấu")
            dec = untelex(tok)
            if dec:
                base, tone = dec
                pool = self.r.by_toneless.get(base, Counter())
                for w, _ in pool.most_common(20):
                    if tone_of(w) == tone:
                        add(w, "telex")
            for p in self.by_phon_plain.get(phonetic_key(plain), [])[:6]:
                for w in self._marked_forms(p, 2):
                    add(w, "nghe nhầm")
        if plain.isalpha() and len(plain) >= 2:
            for p, d in self.delete_index.lookup(plain, 2 if len(plain) >= 4 else 1)[:12]:
                for w in self._marked_forms(p, 2):
                    if marked and tone_of(w) != tone_of(tok) and d > 1:
                        continue
                    add(w, "gõ nhầm" if d == 1 else "gõ nhầm 2")
        if not out:
            add(tok, "giữ nguyên")
        ranked = sorted(out.items(), key=lambda kv: (kv[1][0], -self.freq.get(kv[0], 0)))
        return [(w, c, k) for w, (c, k) in ranked[: self.max_candidates]]

    def decode(self, query: str, n: int = 5, channel_weight: float = 1.0) -> list[dict]:
        """n câu sửa tốt nhất: điểm = log P_LM(w) - channel_weight * chi phí kênh."""
        toks = clean(query).split()
        if not toks:
            return []
        cand = [self.candidates(t) for t in toks]
        states = {(BOS, BOS): [(0.0, 0.0, ())]}       # (điểm, chi phí, đường đi)
        for options in cand:
            new: dict = {}
            for (u, v), paths in states.items():
                for w, cost, kind in options:
                    lp = self.lm.logp(u, v, w)
                    lst = new.setdefault((v, w), [])
                    for s, c, path in paths:
                        lst.append((s + lp - channel_weight * cost, c + cost, path + ((w, kind),)))
            pruned = []
            for key, lst in new.items():
                lst.sort(key=lambda x: -x[0])
                pruned.append((key, lst[:n]))
            pruned.sort(key=lambda kv: -kv[1][0][0])
            states = dict(pruned[: self.beam])
        finals = []
        for (u, v), paths in states.items():
            end = self.lm.logp(u, v, EOS)
            finals.extend((s + end, c, p) for s, c, p in paths)
        finals.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for s, c, path in finals:
            text = " ".join(w for w, _ in path)
            if text in seen:
                continue
            seen.add(text)
            out.append({"text": text, "score": round(s, 3), "channel_cost": round(c, 2),
                        "edits": [{"from": t, "to": w, "kind": k} for t, (w, k) in zip(toks, path)
                                  if k not in ("giữ nguyên", "thêm dấu")]})
            if len(out) >= n:
                break
        return out

    def suggest(self, query: str, margin: float = 1.0) -> dict | None:
        """Gợi ý "Có phải bạn muốn tìm" khi câu sửa tốt hơn hẳn câu chỉ thêm dấu."""
        best = self.decode(query, 3)
        if not best or not best[0]["edits"]:
            return None
        plain_only = [h for h in best if not h["edits"]]
        if plain_only and best[0]["score"] - plain_only[0]["score"] < margin:
            return None
        return best[0]
