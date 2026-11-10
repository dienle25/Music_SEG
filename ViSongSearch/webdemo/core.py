"""Bộ máy tìm kiếm của web demo: đọc file .db của nhóm, dựng chỉ mục và các mô hình, trả lời truy vấn.

Luồng xử lý một truy vấn (SEG 2 kiến trúc máy tìm kiếm):
  chuẩn hóa -> phân loại loại truy vấn (Q1..Q5) -> phục hồi dấu (n-best) / sửa chính tả (kênh nhiễu)
  -> hiểu câu (PCFG + dạng logic: ca sĩ, lời, tâm trạng, thời gian) -> khử nhập nhằng + mở rộng (WordNet)
  -> nhiều danh sách xếp hạng (BM25 các bộ phân tích, LSA, mở rộng) -> trộn RRF theo trọng số của loại
  -> tăng điểm theo các ràng buộc ngữ nghĩa và PageRank -> gom bản trùng -> đoạn trích, bộ lọc (facet)
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import sqlite3
import threading
import time
from collections import Counter, OrderedDict, defaultdict

import numpy as np

from visong.corpus import Song, make_doc_id, prepare, source_of
from visong.dedup import cluster_songs
from visong.diacritics import DiacriticRestorer
from visong.fusion import rrf
from visong.lexical import LexicalIndex
from visong.nbest import mixture_scores, nbest, posterior
from visong.qtype import QueryTypeClassifier
from visong.queries import AmbiguityChecker, generate_rule_queries, split_of
from visong.text import analyzer as get_analyzer
from visong.text import clean, has_diacritics, nfc, remove_diacritics

from . import clustering, temporal
from .extraction import EventExtractor, Gazetteer, metadata_relations, split_artists
from .grammar import QueryGrammar
from .graph import LinkGraph, artist_communities
from .indexing import PositionalIndex
from .langmodel import Embeddings, LMTools
from .lexicons import HERE, stopwords
from .retrieval import VectorSpace
from .semantics import TermAssociation, WordNet
from .sentiment import NaiveBayes, SentimentLexicon, features, weak_labels
from .snippets import highlight, snippet
from .spelling import NoisyChannelSpeller

CODE_VERSION = "1.0.9"
TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5")
TYPE_NAMES = {"Q1": "Nhớ đúng lời", "Q2": "Gõ không dấu", "Q3": "Nhớ sai / gõ nhầm", "Q4": "Diễn đạt lại lời",
              "Q5": "Mô tả bài hát"}
# Các danh sách xếp hạng được trộn (SEG 12 combining evidence)
RUNS = ("syl12", "nodiac12", "char5x", "phon12", "spell", "meta", "lsa", "expand")
RUN_NAMES = {"syl12": "BM25 âm tiết có dấu + cặp âm tiết (sau phục hồi dấu)", "nodiac12": "BM25 không dấu + cặp",
             "char5x": "BM25 n-gram 5 ký tự (chịu lỗi gõ)", "phon12": "BM25 khóa phát âm (chịu nghe nhầm)",
             "spell": "BM25 trên câu đã sửa chính tả", "meta": "BM25 tên bài / ca sĩ / album",
             "lsa": "LSA (chủ đề ẩn)", "expand": "BM25 có mở rộng từ đồng nghĩa (WordNet)"}
PRESET_WEIGHTS = {   # đặt tay ban đầu; sau khi đánh giá xong được thay bằng trọng số dò trên tập dev (Q1-Q3)
    "Q1": {"syl12": 1.0, "nodiac12": 0.5, "char5x": 0.5, "phon12": 0.0, "spell": 0.0, "meta": 0.2, "lsa": 0.0, "expand": 0.0},
    "Q2": {"syl12": 1.0, "nodiac12": 1.0, "char5x": 0.5, "phon12": 0.2, "spell": 0.3, "meta": 0.2, "lsa": 0.0, "expand": 0.0},
    "Q3": {"syl12": 0.5, "nodiac12": 0.5, "char5x": 1.0, "phon12": 1.0, "spell": 1.0, "meta": 0.2, "lsa": 0.2, "expand": 0.0},
    "Q4": {"syl12": 0.5, "nodiac12": 0.5, "char5x": 0.5, "phon12": 0.2, "spell": 0.0, "meta": 0.6, "lsa": 1.0, "expand": 1.0},
    "Q5": {"syl12": 0.5, "nodiac12": 0.5, "char5x": 0.3, "phon12": 0.0, "spell": 0.0, "meta": 1.0, "lsa": 1.0, "expand": 1.0},
}
MODELS = {"fusion": "ViSongSearch (trộn RRF theo loại truy vấn)", "bm25": "BM25 âm tiết",
          "bm25_nodiac": "BM25 không dấu", "char5x": "BM25 n-gram ký tự", "ql": "Query likelihood, Dirichlet",
          "rm3": "RM3 phản hồi giả", "tfidf": "Không gian vector tf-idf",
          "lsa": "LSA chủ đề ẩn", "boolean": "Boolean AND/OR/NOT",
          "pagerank": "BM25 + PageRank", "interleave": "So sánh xen kẽ: ViSongSearch vs BM25"}
FUNCTION_KINDS = {"V_REQ", "N_SONG", "N_SINGER", "P_BY", "P_HAS", "P_ABOUT", "P_IN", "WH", "V_SING", "ARTIST", "ADV", "HEDGE"}


class PIndex(LexicalIndex):
    """LexicalIndex lưu được bằng pickle (hàm phân tích được dựng lại khi nạp)."""

    def __getstate__(self):
        d = self.__dict__.copy()
        d.pop("analyze", None)
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.analyze = get_analyzer(self.analyzer_name)


def read_db(path: str):
    con = sqlite3.connect(path)              # đường dẫn thường, không dùng URI (Windows)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    cols = {r[1] for r in con.execute("pragma table_info(songs)")}
    sel = [c if c in cols else f"NULL as {c}" for c in ("url", "title", "artist", "album", "genre", "lyrics")]
    songs, seen = [], set()
    for url, title, artist, album, genre, lyrics in con.execute(f"select {', '.join(sel)} from songs"):
        if not url or url in seen or not lyrics:
            continue
        seen.add(url)
        g = "" if genre in (None, "None") else nfc(genre).strip()
        extra = {"album": nfc(album).strip()} if album not in (None, "", "None") else {}
        songs.append(Song(make_doc_id(url), url, source_of(url), nfc(title or "").strip(), nfc(artist or "").strip(),
                          g, lyrics, extra))
    pages, links = {}, []
    if "pages" in tables:
        for url, title in con.execute("select url, title from pages"):
            pages[url] = title or ""
    if "links" in tables:
        links = con.execute("select source_url, target_url from links").fetchall()
    for s in songs:
        pages.setdefault(s.url, s.title)
    con.close()
    return songs, pages, links


def db_signature(path: str) -> str:
    st = os.stat(path)
    return hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{CODE_VERSION}".encode()).hexdigest()[:12]


def meta_text(s) -> str:
    return " ".join(x for x in (s.title, s.artist, s.extra.get("album", ""), s.genre) if x)


class DemoEngine:
    @classmethod
    def load_or_build(cls, db_path: str, runtime: str, log=print) -> "DemoEngine":
        sig = db_signature(db_path)
        cache = os.path.join(runtime, f"engine_{sig}.pkl")
        if os.path.exists(cache):
            t = time.time()
            try:
                with open(cache, "rb") as f:
                    eng = pickle.load(f)
                eng.runtime = runtime
                eng.load_seconds = round(time.time() - t, 1)
                log(f"Đã nạp chỉ mục từ bộ nhớ đệm ({eng.load_seconds} giây).")
                eng._after_load()
                return eng
            except Exception as e:              # bộ đệm hỏng hoặc khác phiên bản: dựng lại
                log(f"Không đọc được bộ nhớ đệm ({e}); dựng lại.")
        eng = cls(db_path, runtime, log)
        eng.sig = sig
        os.makedirs(runtime, exist_ok=True)
        for old in os.listdir(runtime):
            if old.startswith("engine_") and old.endswith(".pkl"):
                try:
                    os.remove(os.path.join(runtime, old))
                except OSError:
                    pass
        try:
            with open(cache, "wb") as f:
                pickle.dump(eng, f, protocol=pickle.HIGHEST_PROTOCOL)
            log("Đã lưu bộ nhớ đệm, lần sau khởi động nhanh hơn.")
        except Exception as e:
            log(f"Không lưu được bộ nhớ đệm: {e}")
        eng._after_load()
        return eng

    DERIVED = ("_cache", "_rcache", "_lock", "log", "ix", "pindex", "assoc", "lmtools", "X_marked", "terms_marked", "checker")

    def __getstate__(self):
        d = self.__dict__.copy()
        for k in self.DERIVED:              # dựng lại khi nạp (nhanh), để bộ nhớ đệm nhỏ
            d.pop(k, None)
        return d

    def _after_load(self):
        self._cache = OrderedDict()
        self._rcache = OrderedDict()
        self._lock = threading.Lock()
        self.log = print
        if not hasattr(self, "ix"):
            self._build_indexes()
        self._build_derived()

    def _build_indexes(self):
        texts = [s.lyrics for s in self.songs]
        self.ix = {
            "syl12": PIndex(texts, self.ids, "syl12", 0.3, 0.9),
            "nodiac12": PIndex(texts, self.ids, "nodiac12", 0.3, 0.75),
            "char5x": PIndex(texts, self.ids, "char5x", 0.6, 0.9),
            "phon12": PIndex(texts, self.ids, "phon12", 0.6, 0.9),
            "nodiac": PIndex(texts, self.ids, "nodiac", 0.6, 1.0),
            "meta": PIndex([meta_text(s) for s in self.songs], self.ids, "nodiac12", 0.9, 0.75),
        }
        self.pindex = PositionalIndex(texts, self.ids)

    def _build_derived(self):
        if not hasattr(self, "assoc"):
            self.assoc = TermAssociation(self.ix["syl12"])
        if not hasattr(self, "lmtools"):
            self.lmtools = LMTools(self.restorer.lm)
        if not hasattr(self, "X_marked"):
            self.X_marked, self.terms_marked = self._marked_tfidf(self.ix["syl12"])
        self._artist_plain = {s.doc_id: remove_diacritics(clean(s.artist)) for s in self.songs}
        self._lyric_plain = {s.doc_id: " " + remove_diacritics(clean(" ".join(s.lines()))) + " " for s in self.songs}
        self._title_plain = {s.doc_id: remove_diacritics(clean(re.sub(r"\(.*?\)|-.*$", " ", s.title))) for s in self.songs}
        self.members = defaultdict(list)          # cụm bản trùng -> các bài
        for d in self.ids:
            self.members[self.cluster_of[d]].append(d)

    # ================================================================== dựng
    def __init__(self, db_path: str, runtime: str, log=print):
        self.log = log
        self.db_path = db_path
        self.runtime = runtime
        self.timing = {}
        t0 = time.time()

        def step(name):
            now = time.time()
            self.timing[name] = round(now - step.last, 2)
            step.last = now
            log(f"  [{sum(self.timing.values()):5.1f}s] {name}")
        step.last = t0

        raw, pages, links = read_db(db_path)
        self.n_raw = len(raw)
        step("Đọc file .db")
        kept, self.prep_stats = prepare(raw)
        step("Làm sạch lời, bỏ bài quá ngắn / không phải tiếng Việt (SEG 3.8, 4.1)")
        self.cluster_of, self.dedup_stats = cluster_songs(kept)
        step("Gom bản trùng bằng MinHash LSH (SEG 3.7)")
        self.songs = kept
        self.ids = [s.doc_id for s in kept]
        self.by_id = {s.doc_id: s for s in kept}
        self.pos = {d: i for i, d in enumerate(self.ids)}
        self.split = {s.doc_id: split_of(self.cluster_of[s.doc_id]) for s in kept}
        texts = [s.lyrics for s in kept]
        self.cluster_size = Counter(self.cluster_of.values())

        self._build_indexes()
        step("Chỉ mục ngược BM25 với 6 bộ phân tích, chỉ mục có vị trí (SEG 4, 5)")
        self.vsm = VectorSpace(texts, self.ids)
        step("Vector tf-idf và LSA (SEG 7.1, 7.6; TMG 05)")
        self.restorer = DiacriticRestorer(texts)
        self.speller = NoisyChannelSpeller(self.restorer)
        self._build_derived()
        step("Mô hình ngôn ngữ trigram Kneser-Ney, phục hồi dấu, sửa chính tả (TMG 01, 02)")
        self.emb = Embeddings(texts)
        step("Vector từ PPMI + SVD (TMG 05)")
        self.wordnet = WordNet()
        artists = Counter(n for s in kept for n in split_artists(s.artist))
        self.artist_count = artists
        self.gazetteer = Gazetteer(artists)
        self.grammar = QueryGrammar(self._artist_aliases(artists))
        self.events = EventExtractor()
        self.sent = SentimentLexicon()
        step("WordNet, liên kết từ, từ điển tên ca sĩ, văn phạm truy vấn (TMG 11-16)")

        # phân tích từng bài: cảm xúc, nhãn thời gian
        self.mood, self.emotion, self.time_tags, nb_docs, nb_labels = {}, {}, {}, [], []
        for s in kept:
            a = self.sent.analyze_song(s.lyrics)
            self.mood[s.doc_id] = a
            self.emotion[s.doc_id] = a["emotions"]
            self.time_tags[s.doc_id] = temporal.time_tags(s.lyrics)
            if self.split[s.doc_id] == "train":
                docs, ys, _ = weak_labels(self.sent, s.lines())
                nb_docs.extend(docs)
                nb_labels.extend(ys)
        step("Cảm xúc theo từ điển và nhãn thời gian cho từng bài (TMG 15, 17)")
        self.nb = NaiveBayes().fit([features(d) for d in nb_docs], nb_labels)
        self.nb_stats = {"train_lines": len(nb_docs), "labels": dict(Counter(nb_labels))}
        step("Naive Bayes cảm xúc học từ nhãn yếu (TMG 03)")

        # chủ đề: k-means trên vector LSA, chọn K bằng silhouette (TMG 06, SEG 10.2)
        self.k_choice = clustering.choose_k(self.vsm.Z)
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=self.k_choice["best_k"], n_init=8, random_state=0).fit(self.vsm.Z)
        self.topic_of_row = km.labels_
        sx = self.ix["syl12"]
        self.topic_names = clustering.label_clusters(km.labels_, self.X_marked, self.terms_marked, 3)
        self.topic = {d: self.topic_names[int(km.labels_[i])] for i, d in enumerate(self.ids)}
        step("Phân cụm chủ đề k-means, chọn K theo silhouette (TMG 06, SEG 10.2)")

        # phân loại loại truy vấn (TMG 03, 04; SEG 10.1)
        self.checker = AmbiguityChecker(kept, self.cluster_of)
        rule_train = generate_rule_queries([s for s in kept if self.split[s.doc_id] == "train"], self.cluster_of,
                                           {"train": {"Q1": 1, "Q2": 1, "Q3": 1}}, seed=13, checker=self.checker)
        llm = [json.loads(l) for l in open(os.path.join(HERE, "data", "llm_queries.jsonl"), encoding="utf-8")]
        fit = [(q["text"], q["type"]) for q in rule_train] + \
              [(q["text"], q["type"][:2]) for q in llm if q["split"] == "dev"]
        self.qtype_train_size = Counter(t for _, t in fit)
        self.qtype = QueryTypeClassifier(set(sx.vocab), set(self.ix["nodiac12"].vocab), "logreg", True).fit(
            [t for t, _ in fit], [y for _, y in fit])
        del self.checker
        step("Phân loại loại truy vấn bằng hồi quy logistic (TMG 04)")

        # đồ thị liên kết (SEG 4.5)
        self.graph = LinkGraph(pages, links)
        pr = np.array([self.graph.score(s.url) for s in kept])
        order = pr.argsort()
        pct = np.empty(len(pr))
        pct[order] = np.arange(len(pr)) / max(1, len(pr) - 1)
        self.pagerank = {d: float(pr[i]) for i, d in enumerate(self.ids)}
        self.pr_pct = {d: float(pct[i]) for i, d in enumerate(self.ids)}
        self.inlinks = {s.doc_id: int(self.graph.in_deg[self.graph.index[s.url]]) if s.url in self.graph.index else 0
                        for s in kept}
        rel = metadata_relations(kept, self.cluster_of)
        self.relation_counts = rel["counts"]
        self.communities = artist_communities(rel["collab"])
        self.collab = rel["collab"]
        step("PageRank, HITS, quan hệ và cộng đồng ca sĩ (SEG 4.5, 11.3; TMG 14)")
        self.weights = {t: dict(w) for t, w in PRESET_WEIGHTS.items()}
        self.weights_source = "đặt tay"
        self.build_seconds = round(time.time() - t0, 1)
        self.built_at = time.strftime("%Y-%m-%d %H:%M")
        self.sig = ""
        self._cache = OrderedDict()
        self._rcache = OrderedDict()
        self._lock = threading.Lock()

    def _artist_aliases(self, artists: Counter) -> dict[str, str]:
        """Tên ca sĩ (dạng chuẩn hóa) -> tên gốc; thêm bí danh 2 âm tiết đầu cho tên dài ("sơn tùng")."""
        names = {}
        for n, c in artists.most_common():
            names.setdefault(clean(n), n)
        prefix = defaultdict(Counter)
        for key, n in names.items():
            toks = key.split()
            if len(toks) >= 3:
                prefix[" ".join(toks[:2])][n] += artists[n]
        stop = stopwords()
        for p, ns in prefix.items():
            n, c = ns.most_common(1)[0]
            if p not in names and c >= 2 and c >= 0.7 * sum(ns.values()) and not all(t in stop for t in p.split()):
                names[p] = n
        return names

    @staticmethod
    def _marked_tfidf(ix):
        from sklearn.preprocessing import normalize
        X = ix.tf.tocsr().multiply(ix.idf).tocsr()
        X = normalize(X)
        terms = [None] * len(ix.vocab)
        for t, j in ix.vocab.items():
            terms[j] = t
        return X, terms

    # ================================================================== hiểu truy vấn
    EMPTY = {"type": "Q1", "type_proba": {}, "type_source": "", "content": "", "restorations": [], "restored": "",
             "slots": {}, "chunks": [], "grammar": {}, "spell": None, "wsd": [], "expansion": "", "entities": [],
             "timex": [], "sentiment": {}, "ms_analyze": 0.0}

    def analyze(self, query: str) -> dict:
        key = clean(query)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        t0 = time.perf_counter()
        a = {"query": query, "clean": key, "marked": has_diacritics(key)}
        if not key:
            a.update({k: (v.copy() if hasattr(v, "copy") else v) for k, v in self.EMPTY.items()})
            return a
        # hiểu câu; câu không dấu được thêm dấu trước để nhận ra "mùa thu", "của", "có câu"...
        parse_text = key
        if not a["marked"]:
            r = self.restorer.restore(key)
            if len(r.split()) == len(key.split()):
                parse_text = r
        g = self.grammar.understand(parse_text, original=key if parse_text != key else None)
        a["parsed_text"] = parse_text
        a["grammar"] = {k: g.get(k) for k in ("bracket", "logprob", "alternatives", "deps", "logic")}
        a["chunks"] = [{"text": c["text"], "kind": c["kind"]} for c in g["chunks"]]
        slots = g["slots"]
        a["slots"] = slots
        # phần lời giữ đúng chữ người dùng gõ (không dấu thì vẫn không dấu, để phục hồi dấu n-best phía sau)
        orig, off, content_toks, meta_toks = key.split(), 0, [], []
        for c in g["chunks"]:
            n = len(c["text"].split())
            piece = " ".join(orig[off:off + n])
            if slots.get("intent") == "lyric" or c["kind"] not in FUNCTION_KINDS:
                content_toks.append(piece)
            if slots.get("intent") == "lyric" or c["kind"] not in FUNCTION_KINDS or c["kind"] == "ARTIST":
                meta_toks.append(piece)
            off += n
        a["meta_query"] = " ".join(meta_toks) or key     # cho trường tên bài / ca sĩ: bỏ "bài hát", "của", "có câu"...
        same = (lambda t: t) if a["marked"] else remove_diacritics
        a["content"] = " ".join(content_toks) if slots.get("intent") != "lyric" else key
        if slots.get("lyric") and slots.get("intent") != "lyric":
            a["content"] = same(slots["lyric"]) + (" " + same(slots["about"]) if slots.get("about") else "")
        if not a["content"].strip():
            a["content"] = key
        # loại truy vấn: bộ phân loại chạy trên phần lời; câu hỏi có cấu trúc mà không có lời là mô tả (Q5)
        proba = self.qtype.model.predict_proba(self.qtype.features.transform([a["content"]]))[0]
        classes = list(self.qtype.model.classes_)
        a["type_proba"] = {c: round(float(p), 3) for c, p in sorted(zip(classes, proba), key=lambda x: -x[1])}
        a["type"] = max(a["type_proba"], key=a["type_proba"].get)
        a["type_source"] = "hồi quy logistic"
        if slots.get("intent") in ("find_song", "ask_title") and not slots.get("lyric"):
            a["type"], a["type_source"] = "Q5", "văn phạm: câu mô tả, không có lời"
        # phục hồi dấu (n-best) và sửa chính tả
        a["restorations"] = []
        if not has_diacritics(a["content"]):
            nb = nbest(self.restorer, a["content"], 5)
            w = posterior([lp for _, lp in nb], 1.0)
            a["restorations"] = [{"text": t, "logp": round(lp, 2), "p": round(float(p), 3)} for (t, lp), p in zip(nb, w)]
        a["restored"] = a["restorations"][0]["text"] if a["restorations"] else a["content"]
        a["spell"] = self.speller.suggest(a["content"]) if len(a["content"].split()) >= 2 else None
        if a["spell"] and a["type"] == "Q1":
            a["type"], a["type_source"] = "Q3", "sửa chính tả thấy lỗi gõ / nghe nhầm"
        if a["type"] != next(iter(a["type_proba"]), a["type"]):
            # luật (văn phạm, sửa chính tả) quyết định khác bộ phân loại: xác suất hiển thị là kết hợp
            # 0,8 x luật + 0,2 x hồi quy logistic, để thanh xác suất khớp với loại được chọn
            mix = {c: 0.2 * p + (0.8 if c == a["type"] else 0.0) for c, p in a["type_proba"].items()}
            a["type_proba"] = {c: round(p, 3) for c, p in sorted(mix.items(), key=lambda x: -x[1])}
            a["type_source"] += "; kết hợp luật 0,8 + hồi quy logistic 0,2"
        # nghĩa từ và mở rộng (TMG 16)
        exp_text, exp_w, wsd = self.wordnet.expansion(a["restored"])
        a["wsd"] = [{"word": x["word"], "sense": x["sense"], "gloss": x["gloss"], "ambiguous": x["ambiguous"],
                     "synonyms": x["synonyms"][:5]} for x in wsd]
        a["expansion"] = exp_text
        a["entities"] = self.gazetteer.find(key)
        a["timex"] = temporal.tag(a["restored"])
        a["sentiment"] = self.sent.score_line(a["restored"])
        a["ms_analyze"] = round((time.perf_counter() - t0) * 1000, 1)
        with self._lock:
            self._cache[key] = a
            if len(self._cache) > 300:
                self._cache.popitem(last=False)
        return a

    # ================================================================== xếp hạng
    def _scores_to_run(self, scores, k: int = 200, positive=True):
        idx = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in idx if (scores[i] > 0 or not positive)]

    def run_lists(self, a: dict, which=RUNS) -> dict[str, list]:
        content, restored = a["content"], a["restored"]
        runs = {}
        if "syl12" in which:
            ix = self.ix["syl12"]
            if a["restorations"]:
                texts = [r["text"] for r in a["restorations"]]
                runs["syl12"] = self._scores_to_run(mixture_scores(ix, texts, [r["p"] for r in a["restorations"]]))
            else:
                runs["syl12"] = ix.search(content, 200)
        if "nodiac12" in which:
            runs["nodiac12"] = self.ix["nodiac12"].search(content, 200)
        if "char5x" in which:
            runs["char5x"] = self.ix["char5x"].search(content, 200)
        if "phon12" in which:
            runs["phon12"] = self.ix["phon12"].search(restored, 200)
        if "spell" in which and a.get("spell"):
            runs["spell"] = self.ix["syl12"].search(a["spell"]["text"], 200)
        if "meta" in which:
            runs["meta"] = self.ix["meta"].search(a.get("meta_query") or a["clean"], 200)
        if "lsa" in which:
            runs["lsa"] = self._scores_to_run(self.vsm.lsa_scores(restored + " " + a.get("expansion", "")), 200)
        if "expand" in which and a.get("expansion"):
            ix = self.ix["syl12"]
            qv = ix.query_vector(restored)
            for t, c in ix.query_vector(a["expansion"]).items():
                qv[t] = qv.get(t, 0.0) + 0.3 * c
            runs["expand"] = self._scores_to_run(ix.bm25_scores(qv))
        return runs

    def rank(self, query: str, model: str = "fusion", a: dict | None = None, weights: dict | None = None,
             boosts: bool = True) -> tuple[list[tuple[str, float]], dict]:
        a = a or self.analyze(query)
        trace = {"model": model}
        if not a["clean"]:
            order = sorted(self.ids, key=lambda d: -self.pagerank[d])
            return [(d, self.pagerank[d]) for d in order], trace
        if model == "fusion":
            runs = self.run_lists(a)
            w = dict(weights or self.weights.get(a["type"], PRESET_WEIGHTS["Q1"]))
            if a.get("spell"):
                w["spell"] = max(w.get("spell", 0.0), 0.8)
            names = [n for n in RUNS if n in runs and w.get(n, 0) > 0]
            fused = rrf([runs[n] for n in names], 60, [w[n] for n in names], 100)
            trace["runs"] = {n: {"weight": w.get(n, 0), "top": [d for d, _ in runs[n][:5]], "name": RUN_NAMES[n]}
                             for n in runs}
            trace["fused_with"] = names
            if boosts:
                fused = self._boost(fused, a, trace)
            return fused, trace
        restored = a["restored"]
        if model == "bm25":
            return self.ix["syl12"].search(restored, 200), trace
        if model == "bm25_nodiac":
            return self.ix["nodiac12"].search(a["clean"], 200), trace
        if model == "char5x":
            return self.ix["char5x"].search(a["clean"], 200), trace
        if model == "phon12":
            return self.ix["phon12"].search(restored, 200), trace
        if model == "ql":
            return self.ix["nodiac"].search(a["clean"], 200, "ql", mu=100.0), trace
        if model == "rm3":
            return self.ix["nodiac"].search(a["clean"], 200, "rm3", mu=100.0), trace
        if model == "tfidf":
            return self._scores_to_run(self.vsm.tfidf_scores(a["clean"])), trace
        if model == "lsa":
            return self._scores_to_run(self.vsm.lsa_scores(a["clean"])), trace
        if model == "boolean":
            res = self.pindex.boolean(query)
            trace["boolean"] = res["explain"]
            docs = sorted((self.ids[d] for d in res["docs"]), key=lambda d: -self.pagerank[d])
            return [(d, self.pagerank[d]) for d in docs], trace
        if model == "pagerank":
            base = self.ix["nodiac12"].search(a["clean"], 100)
            if not base:
                return [], trace
            hi = base[0][1]
            mix = [(d, 0.8 * s / hi + 0.2 * self.pr_pct[d]) for d, s in base]
            return sorted(mix, key=lambda x: -x[1]), trace
        raise ValueError(model)

    def _boost(self, fused, a, trace):
        """Ràng buộc ngữ nghĩa từ dạng logic của câu truy vấn được dùng như điểm cộng (lọc mềm):
        đúng ca sĩ được hỏi: +M (M = điểm cao nhất), trùng tên bài: +M, đúng tâm trạng / thời gian: +0.25M."""
        slots = a.get("slots", {})
        M = fused[0][1] if fused else 1.0 / 61
        out, applied = [], Counter()
        artist = slots.get("artist") or slots.get("artist_text")     # tên chuẩn trong kho, không phải chữ đã thêm dấu tự động
        art_key = remove_diacritics(clean(artist)) if artist else None
        mood = slots.get("mood")
        tname = (slots.get("time") or {}).get("name")
        qplain = remove_diacritics(a["clean"])
        cplain = remove_diacritics(clean(a["content"]))
        pool = dict(fused)
        if art_key:   # bài của ca sĩ được hỏi mà chưa có trong danh sách: thêm vào để có thể xếp lên
            for d in self.ids:
                if d not in pool and art_key in self._artist_plain[d]:
                    pool[d] = 0.0
        for d, s in pool.items():
            b = 0.0
            if art_key and art_key in self._artist_plain[d]:
                b += M
                applied["ca sĩ"] += 1
            tp = self._title_plain[d]
            if len(tp.split()) >= 2 and (tp == qplain or tp == cplain):
                b += M
                applied["trùng tên bài"] += 1
            elif len(tp.split()) >= 3 and f" {tp} " in f" {qplain} ":
                b += 0.5 * M
                applied["chứa tên bài"] += 1
            if mood and self.mood[d]["label"] == mood:
                b += 0.25 * M
                applied["tâm trạng"] += 1
            if tname and tname in self.time_tags[d]:
                b += 0.25 * M
                applied["thời gian"] += 1
            b += 0.01 * M * self.pr_pct[d]
            out.append((d, s + b))
        trace["boosts"] = dict(applied)
        return sorted(out, key=lambda x: (-x[1], x[0]))

    # ================================================================== tìm kiếm cho giao diện
    def song_facets(self, d: str) -> dict:
        s = self.by_id[d]
        return {"artists": split_artists(s.artist) or ["(không rõ)"], "source": s.source,
                "mood": self.mood[d]["label"], "time_tags": self.time_tags[d][:4] or ["(không nhắc)"],
                "topic": self.topic[d], "album": s.extra.get("album") or "(không có)"}

    def search(self, query: str, model: str = "fusion", page: int = 0, hits_per_page: int = 10,
               facet_filters=None, facets=(), max_values: int = 10, pre="__ais-highlight__",
               post="__/ais-highlight__", collapse: bool = True, seed: int = 0) -> dict:
        t0 = time.perf_counter()
        a = self.analyze(query)
        results, versions, groups, trace = self._ranked(query, model, a, seed, collapse)
        filt = self._parse_filters(facet_filters)
        kept = []
        for d, s in results:
            f = self.song_facets(d)
            f["group"] = groups.get(d, "Khác")
            if all(any(v in (f[attr] if isinstance(f[attr], list) else [f[attr]]) for v in vals)
                   for attr, vals in filt.items() if attr in f):
                kept.append((d, s, f))
        facet_counts = {}
        want = list(facets) if facets and facets != ["*"] else ["artists", "source", "mood", "time_tags", "topic", "group"]
        for attr in want:
            c = Counter()
            for _, _, f in kept:
                vals = f.get(attr)
                for v in (vals if isinstance(vals, list) else [vals]):
                    if v is not None and not (attr == "group" and v == "Khác") and v != "(không nhắc)":
                        c[v] += 1
            facet_counts[attr] = dict(c.most_common(max_values))
        n = len(kept)
        start = page * hits_per_page
        hits = []
        for r, (d, s, f) in enumerate(kept[start:start + hits_per_page], start=start + 1):
            hits.append(self._hit(d, s, f, r, a, pre, post, versions[self.cluster_of[d]], trace.get("teams", {})))
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"hits": hits, "nbHits": n, "page": page, "nbPages": (n + hits_per_page - 1) // hits_per_page if hits_per_page else 0,
                "hitsPerPage": hits_per_page, "processingTimeMS": int(ms), "facets": facet_counts,
                "exhaustiveFacetsCount": True, "query": query, "analysis": a, "trace": trace, "ms": ms}

    def _ranked(self, query, model, a, seed, collapse):
        """Xếp hạng, gom bản trùng và nhóm kết quả; lưu tạm vì InstantSearch gửi nhiều yêu cầu cho một lần gõ
        (mỗi bộ lọc dạng OR một yêu cầu), chỉ khác bộ lọc."""
        key = (query.strip(), model, seed if model == "interleave" else 0, collapse, self.weights_source)
        with self._lock:
            if key in self._rcache:
                self._rcache.move_to_end(key)
                return self._rcache[key]
        trace = {}
        if model == "interleave":
            A, _ = self.rank(query, "fusion", a)
            B, _ = self.rank(query, "bm25", a)
            from .store import team_draft
            mixed = team_draft([d for d, _ in A[:50]], [d for d, _ in B[:50]], 40, seed)
            ranking = [(d, 1.0 / (i + 1)) for i, (d, _) in enumerate(mixed)]
            trace["teams"] = {d: t for d, t in mixed}
        else:
            ranking, trace = self.rank(query, model, a)
        # gom bản trùng (cụm) và gắn nhóm kết quả
        seen, results, versions = set(), [], Counter()
        for d, s in ranking:
            c = self.cluster_of[d]
            versions[c] += 1
            if collapse and c in seen:
                continue
            seen.add(c)
            results.append((d, s))
        results = results[:150]
        groups = {}
        if a["clean"] and len(results) >= 12:
            rows = [self.pos[d] for d, _ in results[:40]]
            excl = set(clean(a["restored"]).split())
            g = clustering.cluster_results(self.vsm.Z, rows, self.X_marked, self.terms_marked, exclude=excl)
            # đặt tên nhóm theo bài xếp cao nhất trong nhóm (dễ hiểu hơn vài từ khóa rời rạc), đánh số theo thứ hạng
            first = {}
            for d, _ in results[:40]:
                name = g.get(self.pos[d])
                if name is not None and name not in first:
                    first[name] = d
            def short(t):
                t = re.split(r"\s+[-–(\[]", t)[0].strip()
                return t if len(t) <= 28 else t[:27].rstrip() + "…"
            rename = {name: f"Nhóm {i}: gần “{short(self.by_id[d].title)}”" for i, (name, d) in enumerate(first.items(), 1)}
            groups = {self.ids[r]: rename[name] for r, name in g.items()}
        val = (results, versions, groups, trace)
        with self._lock:
            self._rcache[key] = val
            if len(self._rcache) > 64:
                self._rcache.popitem(last=False)
        return val

    @staticmethod
    def _parse_filters(ff) -> dict[str, set]:
        out: dict[str, set] = {}
        if not ff:
            return out
        if isinstance(ff, str):
            ff = [ff]
        for group in ff:
            items = group if isinstance(group, list) else [group]
            for it in items:
                if ":" not in it:
                    continue
                attr, val = it.split(":", 1)
                if val.startswith("\\-"):
                    val = val[1:]
                out.setdefault(attr, set()).add(val)
        return out

    def _hit(self, d, s, f, rank, a, pre, post, n_versions, teams):
        song = self.by_id[d]
        q = a["restored"] + " " + a["clean"]
        th, tl, tm = highlight(song.title, q, pre, post)
        ah, al, am = highlight(song.artist, q, pre, post)
        sn = snippet(song.lyrics, a["restored"] if a["restorations"] else a["content"])
        return {"objectID": d, "title": song.title, "artist": song.artist, "artists": f["artists"],
                "album": song.extra.get("album", ""), "source": song.source, "url": song.url,
                "mood": f["mood"], "time_tags": [t for t in f["time_tags"] if t != "(không nhắc)"],
                "topic": f["topic"], "group": f.get("group"), "rank": rank, "score": round(s, 5),
                "versions": n_versions, "pagerank_pct": round(100 * self.pr_pct[d]), "team": teams.get(d, ""),
                "page": f"/bai-hat/{d}",
                "_highlightResult": {"title": {"value": th, "matchLevel": tl, "matchedWords": tm},
                                     "artist": {"value": ah, "matchLevel": al, "matchedWords": am}},
                "_snippetResult": {"lyrics": {"value": sn["tagged"].replace("__ais-highlight__", pre)
                                              .replace("__/ais-highlight__", post), "matchLevel": "partial"}},
                "__position": rank}

    # ================================================================== tiện ích cho các trang
    def facet_values(self, attr: str, prefix: str, k: int = 10) -> list[dict]:
        c = Counter()
        for d in self.ids:
            v = self.song_facets(d).get(attr)
            for x in (v if isinstance(v, list) else [v]):
                c[x] += 1
        p = remove_diacritics(clean(prefix))
        out = [(v, n) for v, n in c.most_common() if p in remove_diacritics(clean(str(v)))]
        return [{"value": v, "highlighted": v, "count": n} for v, n in out[:k]]

    def similar(self, d: str, k: int = 6):
        return [(self.ids[i], round(s, 3)) for i, s in self.vsm.similar(self.pos[d], k + 4)
                if self.cluster_of[self.ids[i]] != self.cluster_of[d]][:k]

    def check(self, slots: dict) -> dict:
        """Kiểm tra dạng logic trên kho (model checking, TMG 13): bài nào làm công thức đúng."""
        preds = []
        if slots.get("artist"):
            key = remove_diacritics(clean(slots["artist"]))
            preds.append((f"TrìnhBày(x, {slots['artist']})", lambda d, k=key: k in self._artist_plain[d]))
        if slots.get("lyric"):
            ph = " " + remove_diacritics(clean(slots["lyric"])) + " "
            preds.append((f"CóLời(x, \"{slots['lyric']}\")", lambda d, p=ph: p in self._lyric_plain[d]))
        if slots.get("about"):
            ws = [w for w in remove_diacritics(clean(slots["about"])).split() if w not in stopwords()]
            preds.append((f"NóiVề(x, \"{slots['about']}\")", lambda d, ws=ws: all(f" {w} " in self._lyric_plain[d] for w in ws)))
        if slots.get("mood"):
            preds.append((f"TâmTrạng(x, {slots['mood']})", lambda d, m=slots["mood"]: self.mood[d]["label"] == m))
        if slots.get("time"):
            nm = slots["time"]["name"]
            preds.append((f"NhắcThờiGian(x, {nm})", lambda d, nm=nm: nm in self.time_tags[d]))
        rows, sat = [], set(self.ids)
        for name, f in preds:
            ok = {d for d in self.ids if f(d)}
            rows.append({"predicate": name, "count": len(ok)})
            sat &= ok
        ex = sorted(sat, key=lambda d: -self.pagerank[d])[:5]
        return {"n_songs": len(self.ids), "predicates": rows, "satisfied": len(sat) if preds else len(self.ids),
                "examples": [{"id": d, "title": self.by_id[d].title, "artist": self.by_id[d].artist} for d in ex]}

    def summary(self) -> dict:
        return {"songs_raw": self.n_raw, "songs": len(self.songs), "clusters": len(self.cluster_size),
                "artists": len(self.artist_count), "pages": len(self.graph.urls), "links": self.graph.n_edges,
                "vocab_syl": len(self.ix["syl12"].vocab), "vocab_plain": len(self.ix["nodiac"].vocab),
                "build_seconds": self.build_seconds, "built_at": self.built_at,
                "split": dict(Counter(self.split.values())), "topics": len(self.topic_names)}
