"""Nhật ký truy vấn và lượt nhấp, thẻ của người dùng, gợi ý (SEG 9.3 Logging; SEG 11 Social search;
SEG 9.6 Online testing; SEG 6.2 gợi ý truy vấn).

Lưu trong SQLite (webdemo/_runtime/demo.sqlite3), tạo tự động.
- Nhật ký: mỗi truy vấn (phiên, thời điểm, loại truy vấn, số kết quả, thời gian xử lý) và mỗi lượt nhấp
- Làm sạch nhật ký: bỏ các tiền tố khi người dùng đang gõ dở (tìm khi đang gõ)
- CTR theo vị trí; truy vấn phổ biến; truy vấn không có kết quả
- Lọc cộng tác (collaborative filtering) từ lượt nhấp: bài được nhấp cùng phiên
- Thẻ (tag) do người dùng gắn; đám mây thẻ; suy ra thẻ còn thiếu từ bài tương tự
- Xen kẽ team-draft (interleaving) để so sánh hai cách xếp hạng bằng lượt nhấp thật
"""
from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import threading
import time
from collections import Counter, defaultdict

SCHEMA = """
create table if not exists queries(id text primary key, ts real, session text, query text, model text,
    qtype text, n_results integer, ms real, extra text);
create table if not exists clicks(query_id text, ts real, session text, doc_id text, rank integer, team text);
create table if not exists tags(doc_id text, tag text, session text, ts real);
create index if not exists ix_clicks_doc on clicks(doc_id);
create index if not exists ix_tags_doc on tags(doc_id);
"""


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def q(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    # ------------------------------------------------------------ nhật ký
    def log_query(self, qid, session, query, model, qtype, n, ms, extra=None):
        self._exec("insert or replace into queries values(?,?,?,?,?,?,?,?,?)",
                   (qid, time.time(), session, query, model, qtype, n, ms, json.dumps(extra or {}, ensure_ascii=False)))

    def log_click(self, qid, session, doc_id, rank, team=""):
        self._exec("insert into clicks values(?,?,?,?,?,?)", (qid, time.time(), session, doc_id, rank, team))

    def cleaned_queries(self, window: float = 8.0):
        """Bỏ truy vấn là tiền tố của truy vấn kế tiếp cùng phiên trong vòng `window` giây."""
        rows = self.q("select id, ts, session, query, model, qtype, n_results, ms from queries order by session, ts")
        out = []
        for i, r in enumerate(rows):
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt and nxt[2] == r[2] and nxt[1] - r[1] < window and nxt[3].startswith(r[3]) and nxt[3] != r[3]:
                continue
            out.append(r)
        return out

    def analytics(self) -> dict:
        qs = self.cleaned_queries()
        raw = self.q("select count(*) from queries")[0][0]
        clicks = self.q("select rank, count(*) from clicks group by rank order by rank")
        n_q = len(qs) or 1
        by_rank = {r: c for r, c in clicks}
        ctr = [{"rank": r, "clicks": by_rank.get(r, 0), "ctr": round(by_rank.get(r, 0) / n_q, 3)} for r in range(1, 11)]
        top = Counter(r[3] for r in qs if r[6] > 0).most_common(12)
        zero = Counter(r[3] for r in qs if r[6] == 0).most_common(8)
        types = Counter(r[5] for r in qs)
        ms = sorted(r[7] for r in qs)
        clicked = {r[0] for r in self.q("select distinct query_id from clicks")}
        firsts = self.q("select min(rank) from clicks group by query_id")
        hours = Counter(time.strftime("%H:00", time.localtime(r[1])) for r in qs)
        return {"raw_queries": raw, "queries": len(qs), "sessions": len({r[2] for r in qs}),
                "clicks": sum(by_rank.values()), "ctr": ctr, "top": top, "zero": zero, "types": dict(types),
                "abandon": round(1 - len(clicked & {r[0] for r in qs}) / n_q, 3),
                "click_mrr": round(sum(1.0 / f[0] for f in firsts if f[0]) / n_q, 3),
                "p50_ms": ms[len(ms) // 2] if ms else 0, "p95_ms": ms[int(len(ms) * 0.95)] if ms else 0,
                "by_hour": sorted(hours.items())}

    def suggestions(self, prefix: str, titles: list[str], k: int = 8) -> list[str]:
        from visong.text import clean, remove_diacritics
        p = remove_diacritics(clean(prefix))
        if not p:
            return []
        pop = Counter(r[3] for r in self.cleaned_queries() if r[6] > 0)
        cands = [(c, q) for q, c in pop.items() if remove_diacritics(clean(q)).startswith(p)]
        cands.sort(key=lambda x: -x[0])
        out = [q for _, q in cands]
        for t in titles:
            if len(out) >= k:
                break
            if remove_diacritics(clean(t)).startswith(p) and t not in out:
                out.append(t)
        return out[:k]

    # ------------------------------------------------------------ lọc cộng tác
    def co_clicked(self, doc_id: str, k: int = 6) -> list[tuple[str, int]]:
        sess = [r[0] for r in self.q("select distinct session from clicks where doc_id=?", (doc_id,))]
        c = Counter()
        for s in sess:
            for (d,) in self.q("select distinct doc_id from clicks where session=? and doc_id<>?", (s, doc_id)):
                c[d] += 1
        return c.most_common(k)

    # ------------------------------------------------------------ thẻ
    def add_tag(self, doc_id, tag, session):
        tag = " ".join(tag.lower().split())[:30]
        if tag:
            self._exec("insert into tags values(?,?,?,?)", (doc_id, tag, session, time.time()))

    def tags_of(self, doc_id) -> list[tuple[str, int]]:
        return self.q("select tag, count(*) from tags where doc_id=? group by tag order by count(*) desc", (doc_id,))

    def tag_cloud(self) -> list[dict]:
        rows = self.q("select tag, count(*) from tags group by tag order by count(*) desc limit 80")
        if not rows:
            return []
        hi = math.log(1 + rows[0][1])
        return [{"tag": t, "count": c, "size": round(0.9 + 1.4 * math.log(1 + c) / hi, 2)} for t, c in rows]

    def docs_with_tag(self, tag) -> list[tuple[str, int]]:
        return self.q("select doc_id, count(*) from tags where tag=? group by doc_id order by count(*) desc", (tag,))

    # ------------------------------------------------------------ interleaving (SEG 9.6)
    def interleave_wins(self) -> dict:
        rows = self.q("select id, extra from queries where model='interleave'")
        wins = Counter()
        for qid, extra in rows:
            teams = Counter(t for (t,) in self.q("select team from clicks where query_id=?", (qid,)) if t)
            if not teams:
                wins["không nhấp"] += 1
            elif teams["A"] > teams["B"]:
                wins["A"] += 1
            elif teams["B"] > teams["A"]:
                wins["B"] += 1
            else:
                wins["hòa"] += 1
        return dict(wins)


def team_draft(a: list[str], b: list[str], k: int = 20, seed: int = 0) -> list[tuple[str, str]]:
    """Xen kẽ team-draft (Radlinski và cộng sự): mỗi vòng, đội ít bài hơn (hoặc tung đồng xu khi bằng)
    chọn bài tốt nhất của mình chưa có trong danh sách."""
    rng = random.Random(seed)
    out, used, na, nb = [], set(), 0, 0
    ia = ib = 0
    while len(out) < k and (ia < len(a) or ib < len(b)):
        pick_a = na < nb or (na == nb and rng.random() < 0.5)
        if pick_a and ia >= len(a):
            pick_a = False
        if not pick_a and ib >= len(b):
            pick_a = True
        src, idx = (a, ia) if pick_a else (b, ib)
        while idx < len(src) and src[idx] in used:
            idx += 1
        if idx < len(src):
            out.append((src[idx], "A" if pick_a else "B"))
            used.add(src[idx])
            if pick_a:
                na += 1
            else:
                nb += 1
        if pick_a:
            ia = idx + 1
        else:
            ib = idx + 1
    return out
