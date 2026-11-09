"""Chỉ mục ngược và xử lý truy vấn (SEG 5 Ranking with Indexes; SEG 7.1 Boolean retrieval).

- Chỉ mục ngược có vị trí: mỗi từ (âm tiết không dấu) -> danh sách (bài, số lần, các vị trí)
- Nén danh sách: mã hóa hiệu (delta/d-gap), mã byte v-byte, mã bit Elias-gamma; so sánh kích thước
- Con trỏ nhảy (skip pointers) khi giao hai danh sách
- Xử lý truy vấn theo từng tài liệu (DAAT) và theo từng từ (TAAT) với BM25, cho cùng kết quả
- Truy vấn Boolean AND / OR / NOT và cụm từ trong ngoặc kép (dùng vị trí)
"""
from __future__ import annotations

import heapq
import math
import re
import time
from collections import defaultdict

from visong.text import clean, has_diacritics, remove_diacritics


def tokens(text: str) -> list[str]:
    return remove_diacritics(clean(text)).split()


class PositionalIndex:
    def __init__(self, texts: list[str], doc_ids: list[str], k1: float = 0.9, b: float = 0.4):
        self.doc_ids = doc_ids
        self.postings: dict[str, list[tuple[int, list[int]]]] = defaultdict(list)
        self.dl = []
        self.marked = []                  # lời có dấu của từng bài, để lọc khi từ trong truy vấn có dấu
        for d, t in enumerate(texts):
            self.marked.append(" " + clean(t) + " ")
            toks = tokens(t)
            self.dl.append(len(toks))
            pos = defaultdict(list)
            for i, w in enumerate(toks):
                pos[w].append(i)
            for w, ps in pos.items():
                self.postings[w].append((d, ps))
        self.N = len(texts)
        self.avgdl = sum(self.dl) / max(1, self.N)
        self.k1, self.b = k1, b

    # ---------------------------------------------------------------- nén (SEG 5.3)
    @staticmethod
    def vbyte(n: int) -> bytes:
        out = []
        while True:
            out.insert(0, n % 128)
            if n < 128:
                break
            n //= 128
        out[-1] += 128                       # bit cao = 1 đánh dấu byte cuối
        return bytes(out)

    @staticmethod
    def gamma(n: int) -> str:
        """Elias-gamma của n >= 1: (độ dài - 1) số 0, rồi n ở dạng nhị phân."""
        b = bin(n)[2:]
        return "0" * (len(b) - 1) + b

    def posting_view(self, term: str, limit: int = 12) -> dict:
        t = remove_diacritics(clean(term)).replace(" ", "")
        plist = self.postings.get(t, [])
        docs = [d for d, _ in plist]
        gaps = [docs[0]] + [b - a for a, b in zip(docs, docs[1:])] if docs else []
        rows = []
        for i, (d, ps) in enumerate(plist[:limit]):
            rows.append({"doc": self.doc_ids[d], "docnum": d, "gap": gaps[i], "tf": len(ps), "positions": ps[:8],
                         "vbyte": " ".join(f"{x:08b}" for x in self.vbyte(gaps[i])), "gamma": self.gamma(gaps[i] + 1)})
        raw = 4 * len(docs)
        vb = sum(len(self.vbyte(g)) for g in gaps)
        gm = sum(len(self.gamma(g + 1)) for g in gaps) / 8
        return {"term": t, "df": len(docs), "rows": rows, "bytes_raw": raw, "bytes_vbyte": vb,
                "bytes_gamma": round(gm, 1)}

    def compression_stats(self) -> dict:
        raw = vb = gm = n = 0
        raw_pos = vb_pos = 0
        for plist in self.postings.values():
            prev = 0
            for d, ps in plist:
                g = d - prev
                prev = d
                n += 1
                raw += 8                                       # docid + tf, 4 byte mỗi số
                vb += len(self.vbyte(g)) + len(self.vbyte(len(ps)))
                gm += len(self.gamma(g + 1)) + len(self.gamma(len(ps)))
                raw_pos += 4 * len(ps)
                p0 = 0
                for p in ps:
                    vb_pos += len(self.vbyte(p - p0))
                    p0 = p
        return {"terms": len(self.postings), "postings": n, "raw_bytes": raw, "vbyte_bytes": vb,
                "gamma_bytes": round(gm / 8), "raw_position_bytes": raw_pos, "vbyte_position_bytes": vb_pos,
                "ratio_vbyte": round(raw / max(1, vb), 2), "ratio_gamma": round(raw / max(1, gm / 8), 2)}

    # ---------------------------------------------------------------- giao danh sách có con trỏ nhảy
    def intersect(self, a: str, b: str, skips: bool = True) -> dict:
        pa = [d for d, _ in self.postings.get(a, [])]
        pb = [d for d, _ in self.postings.get(b, [])]
        sa, sb = max(1, int(math.sqrt(len(pa)))), max(1, int(math.sqrt(len(pb))))
        i = j = comps = 0
        out = []
        while i < len(pa) and j < len(pb):
            comps += 1
            if pa[i] == pb[j]:
                out.append(pa[i]); i += 1; j += 1
            elif pa[i] < pb[j]:
                if skips and i % sa == 0 and i + sa < len(pa) and pa[i + sa] <= pb[j]:
                    i += sa
                else:
                    i += 1
            else:
                if skips and j % sb == 0 and j + sb < len(pb) and pb[j + sb] <= pa[i]:
                    j += sb
                else:
                    j += 1
        return {"result": len(out), "comparisons": comps, "len_a": len(pa), "len_b": len(pb)}

    # ---------------------------------------------------------------- BM25: DAAT và TAAT (SEG 5.7)
    def _idf(self, df: int) -> float:
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def _w(self, tf: int, d: int) -> float:
        k1, b = self.k1, self.b
        return tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.dl[d] / self.avgdl))

    def taat(self, query: str, k: int = 10) -> dict:
        t0 = time.perf_counter()
        acc = defaultdict(float)
        ops = 0
        for t in set(tokens(query)):
            plist = self.postings.get(t, [])
            idf = self._idf(len(plist))
            for d, ps in plist:
                acc[d] += idf * self._w(len(ps), d)
                ops += 1
        top = heapq.nlargest(k, acc.items(), key=lambda x: (x[1], -x[0]))
        return {"top": [(self.doc_ids[d], round(s, 3)) for d, s in top], "accumulators": len(acc), "ops": ops,
                "ms": round((time.perf_counter() - t0) * 1000, 2)}

    def daat(self, query: str, k: int = 10) -> dict:
        t0 = time.perf_counter()
        lists = [(self.postings.get(t, []), self._idf(len(self.postings.get(t, [])))) for t in set(tokens(query))]
        lists = [(pl, idf) for pl, idf in lists if pl]
        ptr = [0] * len(lists)
        heap: list = []
        ops = 0
        while True:
            cur = [pl[p][0] for (pl, _), p in zip(lists, ptr) if p < len(pl)]
            if not cur:
                break
            d = min(cur)
            s = 0.0
            for i, (pl, idf) in enumerate(lists):
                if ptr[i] < len(pl) and pl[ptr[i]][0] == d:
                    s += idf * self._w(len(pl[ptr[i]][1]), d)
                    ptr[i] += 1
                    ops += 1
            if len(heap) < k:
                heapq.heappush(heap, (s, -d))
            elif (s, -d) > heap[0]:
                heapq.heapreplace(heap, (s, -d))
        top = sorted(heap, reverse=True)
        return {"top": [(self.doc_ids[-d], round(s, 3)) for s, d in top], "heap": k, "ops": ops,
                "ms": round((time.perf_counter() - t0) * 1000, 2)}

    # ---------------------------------------------------------------- Boolean (SEG 7.1)
    def phrase_docs(self, phrase: str) -> set[int]:
        toks = tokens(phrase)
        if not toks:
            return set()
        lists = [dict(self.postings.get(t, [])) for t in toks]
        docs = set(lists[0])
        for l in lists[1:]:
            docs &= set(l)
        out = set()
        for d in docs:
            starts = set(lists[0][d])
            for off, l in enumerate(lists[1:], 1):
                starts &= {p - off for p in l[d]}
                if not starts:
                    break
            if starts:
                out.add(d)
        return out

    def boolean(self, query: str) -> dict:
        """Cú pháp: từ cách nhau là AND; OR; NOT hoặc dấu trừ; "cụm từ" theo đúng thứ tự."""
        parts = re.findall(r'"[^"]+"|\S+', query)
        must, should, must_not, explain = [], [], [], []
        mode = "AND"
        for p in parts:
            up = p.upper()
            if up in ("AND", "OR", "NOT"):
                mode = up
                continue
            neg = p.startswith("-") and len(p) > 1
            term = p[1:] if neg else p
            if term.startswith('"'):
                ph = term.strip('"')
                docs = self.phrase_docs(ph)
                label = f'cụm "{ph}"'
            else:
                ph = term
                t = remove_diacritics(clean(term)).replace(" ", "")
                docs = {d for d, _ in self.postings.get(t, [])}
                label = t
            if has_diacritics(ph):        # gõ có dấu thì khớp đúng dấu: "mưa" không khớp "mùa"
                key = " " + clean(ph) + " "
                docs = {d for d in docs if key in self.marked[d]}
                label = f'cụm "{clean(ph)}"' if term.startswith('"') else clean(ph)
            explain.append({"term": label, "op": "NOT" if (neg or mode == "NOT") else mode, "df": len(docs)})
            if neg or mode == "NOT":
                must_not.append(docs)
            elif mode == "OR" and must:
                must[-1] = must[-1] | docs
            else:
                must.append(docs)
            mode = "AND"
        res = set.intersection(*must) if must else set()
        for s in must_not:
            res -= s
        return {"docs": sorted(res), "explain": explain}
