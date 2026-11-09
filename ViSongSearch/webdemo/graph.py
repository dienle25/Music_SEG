"""Phân tích liên kết (SEG 4.5 Link analysis, PageRank; SEG 11.3 tìm cộng đồng; HITS).

Đồ thị lấy từ bảng `links` của file .db do nhóm thu thập: mỗi trang là một đỉnh, mỗi liên kết là
một cạnh có hướng. PageRank tính bằng lặp lũy thừa với hệ số giảm chấn d = 0.85; trang không có
liên kết ra (dangling) chia đều xác suất cho mọi trang. HITS tính điểm hub và authority.
Cộng đồng ca sĩ: đồ thị "cùng hát một bài", gom nhóm bằng lan truyền nhãn (label propagation).
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

import numpy as np
from scipy import sparse


class LinkGraph:
    def __init__(self, pages: dict[str, str], links: list[tuple[str, str]]):
        self.urls = list(pages)
        self.titles = pages
        self.index = {u: i for i, u in enumerate(self.urls)}
        rows, cols = [], []
        seen = set()
        for s, t in links:
            a, b = self.index.get(s), self.index.get(t)
            if a is None or b is None or a == b or (a, b) in seen:
                continue
            seen.add((a, b))
            rows.append(a)
            cols.append(b)
        n = len(self.urls)
        self.A = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
        self.out_deg = np.asarray(self.A.sum(axis=1)).ravel()
        self.in_deg = np.asarray(self.A.sum(axis=0)).ravel()
        self.n_edges = len(rows)
        self.pagerank, self.pr_iters, self.pr_trace = self._pagerank()
        self.hub, self.auth = self._hits()

    def _pagerank(self, d: float = 0.85, tol: float = 1e-9, max_iter: int = 100):
        n = len(self.urls)
        if n == 0:
            return np.zeros(0), 0, []
        inv = np.where(self.out_deg > 0, 1.0 / np.maximum(self.out_deg, 1), 0.0)
        M = sparse.diags(inv) @ self.A                    # hàng i: xác suất đi từ i sang j
        dangling = self.out_deg == 0
        r = np.full(n, 1.0 / n)
        trace = []
        for it in range(1, max_iter + 1):
            new = d * (M.T @ r) + d * r[dangling].sum() / n + (1 - d) / n
            delta = float(np.abs(new - r).sum())
            trace.append(delta)
            r = new
            if delta < tol:
                break
        return r / r.sum(), it, trace

    def _hits(self, iters: int = 50):
        n = len(self.urls)
        if n == 0:
            return np.zeros(0), np.zeros(0)
        h = np.ones(n)
        a = np.ones(n)
        for _ in range(iters):
            a = self.A.T @ h
            a /= np.linalg.norm(a) or 1.0
            h = self.A @ a
            h /= np.linalg.norm(h) or 1.0
        return h, a

    def score(self, url: str) -> float:
        i = self.index.get(url)
        return float(self.pagerank[i]) if i is not None else 0.0

    def top(self, k: int = 15, by: str = "pagerank", only=None) -> list[dict]:
        arr = {"pagerank": self.pagerank, "authority": self.auth, "hub": self.hub, "indegree": self.in_deg}[by]
        idx = np.argsort(-arr)
        out = []
        for i in idx:
            u = self.urls[i]
            if only and not only(u):
                continue
            out.append({"url": u, "title": self.titles.get(u) or u, "pagerank": float(self.pagerank[i]),
                        "authority": float(self.auth[i]), "hub": float(self.hub[i]),
                        "in": int(self.in_deg[i]), "out": int(self.out_deg[i])})
            if len(out) >= k:
                break
        return out

    def stats(self) -> dict:
        indeg = Counter(int(x) for x in self.in_deg)
        return {"pages": len(self.urls), "edges": self.n_edges, "iterations": self.pr_iters,
                "dangling": int((self.out_deg == 0).sum()),
                "indegree_hist": sorted(indeg.items())[:40], "converge": [round(x, 8) for x in self.pr_trace[:30]]}


def artist_communities(collab: Counter, seed: int = 0, rounds: int = 20) -> dict:
    """Lan truyền nhãn trên đồ thị hợp tác: mỗi ca sĩ lấy nhãn phổ biến nhất (có trọng số) trong hàng xóm."""
    nbr = defaultdict(Counter)
    for (a, b), w in collab.items():
        nbr[a][b] += w
        nbr[b][a] += w
    label = {v: v for v in nbr}
    rng = random.Random(seed)
    nodes = list(nbr)
    for _ in range(rounds):
        rng.shuffle(nodes)
        changed = 0
        for v in nodes:
            c = Counter()
            for u, w in nbr[v].items():
                c[label[u]] += w
            best = max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if best != label[v]:
                label[v] = best
                changed += 1
        if not changed:
            break
    groups = defaultdict(list)
    for v, l in label.items():
        groups[l].append(v)
    comms = sorted(groups.values(), key=len, reverse=True)
    degree = {v: sum(nbr[v].values()) for v in nbr}
    return {"communities": [sorted(g, key=lambda v: -degree[v]) for g in comms if len(g) >= 2],
            "n_artists": len(nbr), "n_edges": len(collab)}
