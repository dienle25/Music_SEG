"""Hiểu câu truy vấn bằng văn phạm (TMG 11 CFG và phân tích thành phần, TMG 12 phân tích phụ thuộc,
TMG 13 biểu diễn logic của nghĩa câu).

1. Tách cụm (chunk) bằng từ điển: tên ca sĩ, "bài hát", "của", "có câu", "về", từ tâm trạng, biểu thức
   thời gian... Mỗi cụm có thể mang nhiều nhãn từ loại với xác suất (ví dụ "về" là giới từ P_ABOUT
   hay chỉ là một chữ trong lời bài hát W).
2. Văn phạm phi ngữ cảnh xác suất (PCFG) và thuật toán CKY (phân tích từ dưới lên) chọn cây có xác
   suất cao nhất; quy tắc một con (A -> B) được xử lý bằng bao đóng trong mỗi ô.
3. Quy tắc chọn đầu (head rules) đổi cây thành phần thành cây phụ thuộc; dãy chuyển trạng thái
   arc-standard (SHIFT, LEFT-ARC, RIGHT-ARC) tạo đúng cây đó (oracle của bộ phân tích chuyển trạng thái).
4. Dạng logic bậc nhất (FOL) dựng theo nguyên lý kết hợp (lambda), rồi kiểm tra trên kho bài hát
   (model checking): bài nào thỏa mãn công thức.
"""
from __future__ import annotations

import math
from collections import defaultdict

from visong.text import clean

from . import temporal
from .lexicons import PhraseMatcher, load

# Xác suất nhãn của một cụm theo loại từ điển đã khớp
TAG_PROBS = {
    "ARTIST": {"ARTIST": 0.85, "W": 0.15}, "P_HAS": {"P_HAS": 0.95, "W": 0.05}, "P_ABOUT": {"P_ABOUT": 0.8, "W": 0.2},
    "V_REQ": {"V_REQ": 0.9, "W": 0.1}, "N_SONG": {"N_SONG": 0.95, "W": 0.05}, "N_SINGER": {"N_SINGER": 0.95, "W": 0.05},
    "V_SING": {"V_SING": 0.7, "W": 0.3}, "P_BY": {"P_BY": 0.75, "W": 0.25}, "MOOD": {"ADJ": 0.7, "W": 0.3},
    "TIME": {"TIME": 0.7, "W": 0.3}, "ADV": {"ADV": 0.7, "W": 0.3}, "WH": {"WH": 0.8, "W": 0.2},
    "P_IN": {"P_IN": 0.7, "W": 0.3}, "HEDGE": {"HEDGE": 0.9, "W": 0.1}, "W": {"W": 1.0},
}
PRIORITY = ["ARTIST", "P_HAS", "HEDGE", "V_REQ", "N_SINGER", "N_SONG", "P_ABOUT", "TIME", "MOOD", "V_SING", "P_BY", "WH",
            "ADV", "P_IN"]

RULES = [   # (vế trái, vế phải, xác suất)
    ("S", ("REQ", "SONGQ"), 0.2), ("S", ("SONGQ",), 0.4), ("S", ("LYR",), 0.2), ("S", ("ARTP",), 0.15),
    ("S", ("HEDGE", "SONGQ"), 0.05),     # "hình như có câu ...", "nhớ mang máng bài của ..."
    ("REQ", ("V_REQ",), 1.0),
    ("SONGQ", ("SONG", "MODS"), 0.6), ("SONGQ", ("SONG",), 0.05), ("SONGQ", ("MODS",), 0.25),
    ("SONGQ", ("SONG", "MODS", "WHP"), 0.05), ("SONGQ", ("SONG", "WHP"), 0.05),
    ("SONG", ("N_SONG",), 1.0),
    ("MODS", ("MOD", "MODS"), 0.35), ("MODS", ("MOD",), 0.65),
    ("MOD", ("ARTP",), 0.3), ("MOD", ("HASP",), 0.25), ("MOD", ("ABOUTP",), 0.15), ("MOD", ("TIMEP",), 0.12),
    ("MOD", ("ADJP",), 0.15), ("MOD", ("WHP",), 0.03),
    ("ARTP", ("P_BY", "ARTIST"), 0.35), ("ARTP", ("P_BY", "ARTV"), 0.2), ("ARTP", ("ARTV",), 0.15),
    ("ARTP", ("N_SINGER", "ARTIST"), 0.15), ("ARTP", ("ARTIST",), 0.15),
    ("ARTV", ("ARTIST", "V_SING"), 1.0),
    ("HASP", ("P_HAS", "LYR"), 1.0),
    ("ABOUTP", ("P_ABOUT", "TOPIC"), 1.0),
    ("TOPIC", ("TIMEP",), 0.25), ("TOPIC", ("ADJP",), 0.15), ("TOPIC", ("LYR",), 0.6),
    ("TIMEP", ("TIME",), 0.7), ("TIMEP", ("P_IN", "TIME"), 0.3),
    ("ADJP", ("ADJ",), 0.8), ("ADJP", ("ADV", "ADJ"), 0.2),
    ("WHP", ("WH",), 1.0),
    ("LYR", ("W", "LYR"), 0.7), ("LYR", ("W",), 0.3),
]


def to_cnf(rules):
    """Đổi về dạng gần chuẩn Chomsky: vế phải dài hơn 2 được tách đôi bằng ký hiệu phụ."""
    binary, unary = [], []
    for lhs, rhs, p in rules:
        if len(rhs) == 1:
            unary.append((lhs, rhs[0], p))
            continue
        cur, prob = lhs, p
        rest = list(rhs)
        while len(rest) > 2:
            new = f"{lhs}|{'_'.join(rest[1:])}"
            binary.append((cur, rest[0], new, prob))
            cur, prob, rest = new, 1.0, rest[1:]
        binary.append((cur, rest[0], rest[1], prob))
    return binary, unary


BINARY, UNARY = to_cnf(RULES)


class QueryGrammar:
    def __init__(self, artist_names: dict[str, str]):
        q = load("query")
        self.moods = q["MOOD"]
        self.matchers = {k: PhraseMatcher(q[k]) for k in ("V_REQ", "N_SONG", "N_SINGER", "P_BY", "V_SING", "P_HAS",
                                                         "P_ABOUT", "P_IN", "ADV", "WH", "HEDGE")}
        self.matchers["MOOD"] = PhraseMatcher(self.moods)
        self.artist_of = artist_names                        # dạng clean -> tên gốc
        self.matchers["ARTIST"] = PhraseMatcher(list(artist_names))
        self.by_right = defaultdict(list)
        for a, b, c, p in BINARY:
            self.by_right[(b, c)].append((a, math.log(p)))
        self.unary_of = defaultdict(list)
        for a, b, p in UNARY:
            self.unary_of[b].append((a, math.log(p)))

    # ------------------------------------------------------------ tách cụm
    def chunk(self, text: str, original: str | None = None) -> list[dict]:
        """original: câu người dùng gõ (không dấu) khi `text` là câu đã được thêm dấu tự động; khớp cả hai,
        vì thêm dấu có thể làm sai tên ca sĩ ("noo phuoc thinh" thành "nô phước thính")."""
        toks = clean(text).split()
        bare = clean(original).split() if original else None
        spans = set()
        for k in PRIORITY:
            if k not in self.matchers:
                continue
            for i, j, w in self.matchers[k].find_all(toks):
                spans.add((i, j, k, w))
            if bare is not None and len(bare) == len(toks):
                for i, j, w in self.matchers[k].find_all(bare):
                    spans.add((i, j, k, w))
        spans = list(spans)
        for x in temporal.tag(" ".join(toks)):
            i = len(" ".join(toks)[: x["start"]].split())
            j = i + len(x["text"].split())
            spans.append((i, j, "TIME", x["text"]))
        spans.sort(key=lambda s: (-(s[1] - s[0]), PRIORITY.index(s[2]), s[0], s[3]))
        taken = [None] * len(toks)
        for i, j, k, w in spans:
            if all(t is None for t in taken[i:j]):
                for t in range(i, j):
                    taken[t] = (i, j, k, w)
        chunks, t = [], 0
        while t < len(toks):
            if taken[t] is None:
                chunks.append({"text": toks[t], "kind": "W", "value": toks[t]})
                t += 1
            else:
                i, j, k, w = taken[t]
                val = w
                if k == "ARTIST":
                    val = self.artist_of.get(w, w)
                elif k == "MOOD":
                    val = self.moods.get(w, w)
                chunks.append({"text": " ".join(toks[i:j]), "kind": k, "value": val})
                t = j
        for c in chunks:
            c["tags"] = TAG_PROBS[c["kind"]]
        return chunks

    # ------------------------------------------------------------ CKY
    def parse(self, text: str, original: str | None = None) -> dict:
        chunks = self.chunk(text, original)
        n = len(chunks)
        if n == 0:
            return {"chunks": [], "tree": None}
        chart = [[dict() for _ in range(n + 1)] for _ in range(n + 1)]     # chart[i][j][A] = (logp, back)

        def closure(cell):
            changed = True
            while changed:
                changed = False
                for b, (lp, _) in list(cell.items()):
                    for a, lpr in self.unary_of.get(b, []):
                        s = lp + lpr
                        if a not in cell or cell[a][0] < s - 1e-12:
                            cell[a] = (s, ("unary", b))
                            changed = True

        for i, c in enumerate(chunks):
            cell = chart[i][i + 1]
            for tag, p in c["tags"].items():
                cell[tag] = (math.log(p), ("leaf", i))
            closure(cell)
        for span in range(2, n + 1):
            for i in range(0, n - span + 1):
                j = i + span
                cell = chart[i][j]
                for k in range(i + 1, j):
                    left, right = chart[i][k], chart[k][j]
                    if not left or not right:
                        continue
                    for b, (lb, _) in left.items():
                        for c2, (lc, _) in right.items():
                            for a, lpr in self.by_right.get((b, c2), []):
                                s = lb + lc + lpr
                                if a not in cell or cell[a][0] < s:
                                    cell[a] = (s, ("bin", k, b, c2))
                closure(cell)
        top = chart[0][n]
        if "S" not in top:
            return {"chunks": chunks, "tree": None, "chart": _chart_view(chart, n)}

        def build(i, j, sym):
            lp, back = chart[i][j][sym]
            if back[0] == "leaf":
                c = chunks[back[1]]
                return {"label": sym, "word": c["text"], "kind": c["kind"], "value": c["value"], "i": back[1]}
            if back[0] == "unary":
                return {"label": sym, "children": [build(i, j, back[1])]}
            _, k, b, c2 = back
            kids = [build(i, k, b), build(k, j, c2)]
            out = []
            for kid in kids:          # bỏ ký hiệu phụ của bước tách đôi
                out.extend(kid["children"] if "|" in kid["label"] else [kid])
            return {"label": sym, "children": out}

        tree = build(0, n, "S")
        alt = {}
        for sym in ("SONGQ", "LYR"):
            if sym in top:
                alt[sym] = round(top[sym][0], 3)
        return {"chunks": chunks, "tree": tree, "logprob": round(top["S"][0], 3), "alternatives": alt,
                "chart": _chart_view(chart, n), "bracket": bracket(tree)}

    # ------------------------------------------------------------ nghĩa
    def understand(self, text: str, kb=None, original: str | None = None) -> dict:
        res = self.parse(text, original)
        tree = res.get("tree")
        slots = {"artist": None, "artist_text": None, "lyric": None, "about": None, "mood": None, "time": None,
                 "intent": "lyric"}
        typed = None          # đoạn người dùng gõ cho từng cụm (khi `text` là câu đã thêm dấu tự động)
        if original:
            otoks, off, typed = clean(original).split(), 0, []
            if len(otoks) == len(clean(text).split()):
                for c in res["chunks"]:
                    n = len(c["text"].split())
                    typed.append(" ".join(otoks[off:off + n]))
                    off += n
            else:
                typed = None
        if tree is not None:
            _collect(tree, slots, None, typed)
            top = tree["children"][0]["label"] if tree.get("children") else ""
            if top == "LYR" and len(tree["children"]) == 1:
                slots["intent"] = "lyric"
                slots["lyric"] = " ".join(_leaves(tree))
            else:
                slots["intent"] = "ask_title" if _has(tree, "WHP") else "find_song"
        res["slots"] = slots
        res["deps"] = dependencies(tree, res["chunks"]) if tree else []
        res["transitions"] = arc_standard(res["deps"], res["chunks"]) if tree else []
        res["logic"] = logical_form(slots)
        if kb is not None:
            res["model_check"] = kb.check(slots)
        return res


def _chart_view(chart, n):
    rows = []
    for i in range(n):
        for j in range(i + 1, n + 1):
            cell = chart[i][j]
            if cell:
                best = sorted(cell.items(), key=lambda kv: -kv[1][0])[:5]
                rows.append({"i": i, "j": j, "items": [[a, round(lp, 2)] for a, (lp, _) in best if "|" not in a]})
    return rows


def _leaves(t):
    if "word" in t:
        return [t["word"]]
    return [w for c in t["children"] for w in _leaves(c)]


def _has(t, label):
    if t.get("label") == label:
        return True
    return any(_has(c, label) for c in t.get("children", []))


def _collect(t, slots, parent, typed=None):
    lab = t["label"]
    if lab == "ARTIST" and "word" in t:
        slots["artist"] = t["value"]
        slots["artist_text"] = typed[t["i"]] if typed else t["word"]
    elif lab == "ADJ" and "word" in t:
        slots["mood"] = t["value"]
    elif lab == "TIME" and "word" in t:
        tags = temporal.tag(t["word"])
        slots["time"] = {"text": t["word"], "value": tags[0]["value"] if tags else "",
                         "name": temporal.TIME_TAG_NAMES.get(tags[0]["value"], t["word"]) if tags else t["word"]}
    elif lab == "HASP":
        lyr = [c for c in t["children"] if c["label"] == "LYR"]
        if lyr:
            slots["lyric"] = " ".join(_leaves(lyr[0]))
    elif lab == "TOPIC":
        slots["about"] = " ".join(_leaves(t))
    for c in t.get("children", []):
        _collect(c, slots, lab, typed)


def bracket(t) -> str:
    if t is None:
        return ""
    if "word" in t:
        return f"[{t['label']} {t['word']}]"
    return f"[{t['label']} " + " ".join(bracket(c) for c in t["children"]) + "]"


# ------------------------------------------------------------ phụ thuộc (TMG 12)

HEAD = {"S": ["REQ", "SONGQ", "LYR", "ARTP"], "REQ": ["V_REQ"], "SONGQ": ["SONG", "MODS", "WHP"], "SONG": ["N_SONG"],
        "MODS": ["MOD"], "MOD": ["ARTP", "HASP", "ABOUTP", "TIMEP", "ADJP", "WHP"],
        "ARTP": ["ARTV", "ARTIST"], "ARTV": ["V_SING"], "HASP": ["P_HAS"], "ABOUTP": ["TOPIC"],
        "TOPIC": ["LYR", "TIMEP", "ADJP"], "TIMEP": ["TIME"], "ADJP": ["ADJ"], "WHP": ["WH"], "LYR": ["W"]}
LABEL = {("S", "SONGQ"): "obj", ("S", "LYR"): "obj", ("ARTP", "P_BY"): "case", ("ARTP", "N_SINGER"): "compound",
         ("ARTV", "ARTIST"): "nsubj", ("HASP", "LYR"): "obj", ("ABOUTP", "P_ABOUT"): "case",
         ("TIMEP", "P_IN"): "case", ("ADJP", "ADV"): "advmod", ("LYR", "LYR"): "flat", ("ARTP", "ARTV"): "acl",
         ("S", "HEDGE"): "advmod"}
MOD_LABEL = {"ARTP": "nmod:poss", "HASP": "acl", "ABOUTP": "nmod", "TIMEP": "nmod:tmod", "ADJP": "amod", "WHP": "det"}


def dependencies(tree, chunks) -> list[dict]:
    """Cây phụ thuộc trên các cụm: [{i, word, head, rel}] (head = -1 là ROOT)."""
    heads = {}

    def lex_head(t):
        if "word" in t:
            return t["i"]
        kids = t["children"]
        if t["label"] in ("SONGQ", "MODS"):
            mods = _flatten_mods(t)
            noun = [m for m in mods if m["label"] == "SONG"]
            h = lex_head(noun[0]) if noun else lex_head(mods[0])
            for m in mods:
                mh = lex_head(m)
                if mh != h:
                    lab = m["label"]
                    if lab == "MOD":
                        lab = m["children"][0]["label"]
                    heads[mh] = (h, "nmod:poss" if lab == "ARTP" and _has(m, "ARTV") is False else
                                 ("acl" if lab == "ARTP" else MOD_LABEL.get(lab, "dep")))
            return h
        prefs = HEAD.get(t["label"], [])
        hk = None
        for p in prefs:
            for k in kids:
                if k["label"] == p:
                    hk = k
                    break
            if hk:
                break
        hk = hk or kids[0]
        h = lex_head(hk)
        for k in kids:
            if k is hk:
                continue
            kh = lex_head(k)
            rel = LABEL.get((t["label"], k["label"]))
            if rel is None and t["label"] == "LYR":
                rel = "flat"
            heads[kh] = (h, rel or "dep")
        return h

    root = lex_head(tree)
    heads[root] = (-1, "root")
    return [{"i": i, "word": c["text"], "kind": c["kind"], "head": heads.get(i, (root, "dep"))[0],
             "rel": heads.get(i, (root, "dep"))[1]} for i, c in enumerate(chunks)]


def _flatten_mods(t):
    out = []
    for k in t["children"]:
        if k["label"] == "MODS":
            out.extend(_flatten_mods(k))
        else:
            out.append(k)
    return out


def arc_standard(deps, chunks) -> list[dict]:
    """Dãy chuyển trạng thái arc-standard tạo ra cây phụ thuộc đã cho (oracle tĩnh)."""
    if not deps:
        return []
    head = {d["i"] + 1: d["head"] + 1 for d in deps}            # 0 = ROOT
    rel = {d["i"] + 1: d["rel"] for d in deps}
    words = {0: "ROOT", **{d["i"] + 1: d["word"] for d in deps}}
    n_kids = defaultdict(int)
    for d, h in head.items():
        n_kids[h] += 1
    attached = defaultdict(int)
    stack, buf, steps = [0], list(range(1, len(deps) + 1)), []
    guard = 0
    while (buf or len(stack) > 1) and guard < 200:
        guard += 1
        conf = {"stack": [words[x] for x in stack], "buffer": [words[x] for x in buf]}
        if len(stack) >= 2 and stack[-2] != 0 and head.get(stack[-2]) == stack[-1]:
            dep = stack.pop(-2)
            attached[stack[-1]] += 1
            steps.append({**conf, "action": f"LEFT-ARC({rel[dep]})", "arc": f"{words[stack[-1]]} → {words[dep]}"})
        elif len(stack) >= 2 and head.get(stack[-1]) == stack[-2] and attached[stack[-1]] == n_kids[stack[-1]]:
            dep = stack.pop()
            attached[stack[-1]] += 1
            steps.append({**conf, "action": f"RIGHT-ARC({rel[dep]})", "arc": f"{words[stack[-1]]} → {words[dep]}"})
        elif buf:
            stack.append(buf.pop(0))
            steps.append({**conf, "action": "SHIFT", "arc": ""})
        else:
            break
    return steps


# ------------------------------------------------------------ dạng logic (TMG 13)

def _q(s):
    return '"' + s.replace('"', "'") + '"'


def logical_form(slots) -> dict:
    preds, lambdas = [], []
    if slots.get("artist"):
        preds.append(f"TrìnhBày(x, {_q(slots['artist'])})")
        lambdas.append(f"λx.TrìnhBày(x, {_q(slots['artist'])})")
    if slots.get("lyric"):
        preds.append(f"CóLời(x, {_q(slots['lyric'])})")
        lambdas.append(f"λx.CóLời(x, {_q(slots['lyric'])})")
    if slots.get("about"):
        preds.append(f"NóiVề(x, {_q(slots['about'])})")
        lambdas.append(f"λx.NóiVề(x, {_q(slots['about'])})")
    if slots.get("mood"):
        preds.append(f"TâmTrạng(x, {slots['mood'].replace(' ', '_')})")
        lambdas.append(f"λx.TâmTrạng(x, {slots['mood'].replace(' ', '_')})")
    if slots.get("time"):
        preds.append(f"NhắcThờiGian(x, {slots['time']['name'].replace(' ', '_')})")
        lambdas.append(f"λx.NhắcThờiGian(x, {slots['time']['name'].replace(' ', '_')})")
    q = "?x" if slots.get("intent") == "ask_title" else "∃x"
    body = " ∧ ".join(["BàiHát(x)"] + preds)
    steps = []
    if lambdas:
        steps.append("Mỗi bổ ngữ là một vị từ một ngôi: " + ";  ".join(lambdas))
        steps.append("Ghép các bổ ngữ bằng giao (predicate modification): λx." + " ∧ ".join(
            p for p in preds))
        steps.append(f"Danh từ 'bài hát' cho λP.{q}[BàiHát(x) ∧ P(x)]; áp dụng (β-rút gọn) được: {q}[{body}]")
    return {"formula": f"{q}[{body}]", "predicates": preds, "steps": steps}
