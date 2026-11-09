"""Đánh giá tự động, chạy nền khi web khởi động (SEG 9 Evaluating Search Engines; TMG 03/04 đánh giá
bộ phân loại; TMG 06 đánh giá phân cụm; TMG 09 BLEU).

Tập kiểm thử tự sinh từ chính file .db (SEG 9.2): mỗi bài kiểm thử cho 3 truy vấn Q1 (đoạn lời đúng),
Q2 (bỏ dấu), Q3 (thêm nhiễu); đáp án là cụm của bài đó (mọi bản trùng đều đúng). Chia train/dev/test
theo cụm 70/10/20 nên không bài nào lọt giữa các tập.
- Dò trọng số trộn RRF trên tập dev cho từng loại (SEG 9.6 Setting parameter values), rồi đo trên test
- Độ đo: MRR@10, Success@1, Recall@10, NDCG@10, MAP, thời gian mỗi truy vấn (SEG 9.4, 9.5)
- Kiểm định ngẫu nhiên hóa cặp đôi và hiệu chỉnh Holm (SEG 9.6 Significance tests)
- Đường cong độ bền khi tăng nhiễu; truy vấn tên ca sĩ có nhiều bài đúng: P@10, MAP, đường P-R 11 điểm
- Phân loại loại truy vấn: Naive Bayes và hồi quy logistic, P/R/F1, ma trận nhầm lẫn, đặc trưng chi bình phương
- Phục hồi dấu xem như dịch máy: độ đúng âm tiết, khớp cả câu, BLEU-4 với mô hình 1/2/3-gram
- Phân cụm: silhouette theo K, so sánh k-means / phân cấp / DBSCAN; MinHash so với Jaccard thật
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time
import traceback
from collections import Counter, defaultdict
from itertools import product

import numpy as np

from visong.dedup import jaccard, minhash, shingles
from visong.diacritics import DiacriticRestorer, syllable_accuracy
from visong.evaluate import holm, randomization_test, target_rank
from visong.fusion import WeightTuner
from visong.noise import corrupt
from visong.qtype import QueryTypeClassifier
from visong.queries import AmbiguityChecker, generate_rule_queries
from visong.text import clean, remove_diacritics

from . import clustering
from .core import PRESET_WEIGHTS, RUNS
from .lexicons import HERE

SYSTEMS = ["boolean", "tfidf", "lsa", "bm25", "bm25_nodiac", "char5x", "phon12", "ql", "rm3",
           "fusion_equal", "fusion_preset", "fusion_tuned"]
SYSTEM_NAMES = {"boolean": "Boolean AND", "tfidf": "VSM tf-idf", "lsa": "LSA", "bm25": "BM25 âm tiết+cặp (phục hồi dấu)",
                "bm25_nodiac": "BM25 không dấu+cặp", "char5x": "BM25 5-gram ký tự", "phon12": "BM25 khóa phát âm",
                "ql": "Query likelihood (Dirichlet)", "rm3": "RM3", "fusion_equal": "Trộn RRF trọng số đều",
                "fusion_preset": "Trộn RRF đặt tay theo loại", "fusion_tuned": "Trộn RRF dò trên dev theo loại (hệ thống chính)"}
TUNABLE = ("syl12", "nodiac12", "char5x", "phon12", "spell")


def bleu(hyps: list[str], refs: list[str], n_max: int = 4) -> float:
    """BLEU-4 cấp kho (Papineni và cộng sự): trung bình nhân độ chính xác n-gram có cắt ngọn x phạt ngắn."""
    import math as m
    match, total = [0] * n_max, [0] * n_max
    hl = rl = 0
    for h, r in zip(hyps, refs):
        h, r = h.split(), r.split()
        hl += len(h)
        rl += len(r)
        for n in range(1, n_max + 1):
            hc = Counter(tuple(h[i:i + n]) for i in range(len(h) - n + 1))
            rc = Counter(tuple(r[i:i + n]) for i in range(len(r) - n + 1))
            match[n - 1] += sum(min(c, rc[g]) for g, c in hc.items())
            total[n - 1] += max(0, len(h) - n + 1)
    if min(match) == 0:
        return 0.0
    logp = sum(m.log(match[i] / total[i]) for i in range(n_max)) / n_max
    bp = 1.0 if hl > rl else m.exp(1 - rl / max(1, hl))
    return round(100 * bp * m.exp(logp), 2)


PAPER_SYSTEMS = [
    ("bm25_syl12", "BM25 âm tiết + cặp âm tiết"),
    ("bm25_char5x", "BM25 n-gram 5 ký tự"),
    ("restoretrain_bm25_syl12", "Phục hồi dấu (LM học trên train) + BM25"),
    ("rm3_syl12", "RM3 phản hồi giả"),
    ("lexall_adaptive", "Trộn RRF các danh sách từ vựng, trọng số theo loại"),
    ("dense_naft_e5base_s1_rest", "Dense e5-base tinh chỉnh chịu nhiễu"),
    ("hybrid_naft_e5base_s3_rest_adaptive", "Lai từ vựng + dense, theo loại"),
    ("rerank_bgererank_hybrid_naft_e5base_s3_rest_adaptive", "Lai + xếp hạng lại bge-reranker"),
]


class Evaluator:
    def __init__(self, eng, out_path: str, test_songs: int = 150, dev_songs: int = 100, sweep: int = 120):
        self.eng = eng
        self.path = out_path
        self.n_test, self.n_dev, self.n_sweep = test_songs, dev_songs, sweep
        self.state = {"status": "chưa chạy", "progress": []}
        if os.path.exists(out_path):
            try:
                with open(out_path, encoding="utf-8") as f:
                    self.state = json.load(f)
                if self.state.get("status") == "xong" and self.state.get("tuned_weights"):
                    self._apply_weights(self.state["tuned_weights"])
            except Exception:
                self.state = {"status": "chưa chạy", "progress": []}

    def start(self):
        if self.state.get("status") == "xong":
            return
        threading.Thread(target=self._safe_run, daemon=True).start()

    def _safe_run(self):
        try:
            self.run()
        except Exception as e:
            self.state["status"] = "lỗi"
            self.state["error"] = f"{e}\n{traceback.format_exc()}"

    def _apply_weights(self, tuned):
        for t, w in tuned.items():
            self.eng.weights[t] = {**PRESET_WEIGHTS[t], **w}
        self.eng.weights_source = "dò trên tập dev (Q1-Q3), đặt tay (Q4, Q5)"

    def note(self, msg):
        self.state["progress"].append(f"{time.strftime('%H:%M:%S')}  {msg}")

    # ------------------------------------------------------------------ chạy
    def run(self):
        eng = self.eng
        t_all = time.time()
        self.state = {"status": "đang chạy", "progress": [], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        rng = random.Random(0)
        checker = AmbiguityChecker(eng.songs, eng.cluster_of)
        test_pool = [s for s in eng.songs if eng.split[s.doc_id] == "test"]
        dev_pool = [s for s in eng.songs if eng.split[s.doc_id] == "dev"]
        test_s = rng.sample(test_pool, min(self.n_test, len(test_pool)))
        dev_s = rng.sample(dev_pool, min(self.n_dev, len(dev_pool)))
        per = {"Q1": 1, "Q2": 1, "Q3": 1}
        test_q = generate_rule_queries(test_s, eng.cluster_of, {"test": per}, seed=7, checker=checker,
                                       one_per_cluster=("test",))
        dev_q = generate_rule_queries(dev_s, eng.cluster_of, {"dev": per}, seed=11, checker=checker,
                                      one_per_cluster=("dev",))
        self.state["collection"] = {"test_queries": len(test_q), "dev_queries": len(dev_q),
                                    "by_type": dict(Counter(q["type"] for q in test_q)),
                                    "examples": [{"type": q["type"], "text": q["text"], "song": eng.by_id[q["doc_id"]].title}
                                                 for q in test_q[:9]]}
        self.note(f"Tạo {len(dev_q)} truy vấn dev và {len(test_q)} truy vấn test từ file .db")

        # 1. dò trọng số trên dev
        lists, target, qtype_of = {}, {}, {}
        for q in dev_q:
            a = eng.analyze(q["text"])
            runs = eng.run_lists(a, RUNS)
            lists[q["qid"]] = [runs.get(n, []) for n in TUNABLE] + [runs.get("meta", []), runs.get("lsa", [])]
            target[q["qid"]] = q["cluster"]
            qtype_of[q["qid"]] = q["type"]
        grid = [w + (m, l) for w in product((0.0, 0.5, 1.0, 2.0), repeat=len(TUNABLE)) if any(w)
                for m in (0.0, 0.25, 0.5) for l in (0.0, 0.25)]
        tuned, tune_rows = {}, []
        for t in ("Q1", "Q2", "Q3"):
            sub = {k: v for k, v in lists.items() if qtype_of[k] == t}
            if len(sub) < 10:
                continue
            tuner = WeightTuner(sub, {k: target[k] for k in sub}, eng.cluster_of)
            best, mrr = tuner.best(grid)
            tuned[t] = {n: float(x) for n, x in zip(TUNABLE + ("meta", "lsa"), best)}
            preset = tuner.exact_mrr([PRESET_WEIGHTS[t][n] for n in TUNABLE + ("meta", "lsa")])
            tune_rows.append({"type": t, "n": len(sub), "weights": tuned[t], "dev_mrr_tuned": round(mrr, 4),
                              "dev_mrr_preset": round(preset, 4)})
        self.state["tuning"] = {"grid_size": len(grid), "rows": tune_rows}
        self.state["tuned_weights"] = tuned
        self.note(f"Dò trọng số RRF trên dev: lưới {len(grid)} bộ trọng số cho mỗi loại")

        # 2. các hệ thống trên test
        per_q = {s: {} for s in SYSTEMS}
        ms = defaultdict(list)
        for i, q in enumerate(test_q):
            a = eng.analyze(q["text"])
            for sysname in SYSTEMS:
                t0 = time.perf_counter()
                if sysname == "fusion_equal":
                    ranking, _ = eng.rank(q["text"], "fusion", a, weights={n: 1.0 for n in RUNS}, boosts=False)
                elif sysname == "fusion_preset":
                    ranking, _ = eng.rank(q["text"], "fusion", a, weights=PRESET_WEIGHTS[a["type"]])
                elif sysname == "fusion_tuned":
                    w = {**PRESET_WEIGHTS[a["type"]], **tuned.get(a["type"], {})}
                    ranking, _ = eng.rank(q["text"], "fusion", a, weights=w)
                else:
                    ranking, _ = eng.rank(q["text"], sysname, a)
                ms[sysname].append((time.perf_counter() - t0) * 1000)
                r = target_rank(ranking, q["cluster"], eng.cluster_of)
                per_q[sysname][q["qid"]] = r
            if i % 100 == 99:
                self.note(f"Đã chạy {i + 1}/{len(test_q)} truy vấn test qua {len(SYSTEMS)} hệ thống")
        types = ["Q1", "Q2", "Q3"]
        table = []
        rr = {}
        for sysname in SYSTEMS:
            row = {"system": sysname, "name": SYSTEM_NAMES[sysname],
                   "ms": round(float(np.median(ms[sysname])), 1)}
            vals = {q["qid"]: per_q[sysname][q["qid"]] for q in test_q}
            rr[sysname] = np.array([1.0 / vals[q["qid"]] if vals[q["qid"]] and vals[q["qid"]] <= 10 else 0.0
                                    for q in test_q])
            for t in types + ["all"]:
                qs = [q for q in test_q if t == "all" or q["type"] == t]
                ranks = [vals[q["qid"]] for q in qs]
                row[t] = metrics(ranks)
            table.append(row)
        pvals = {s: randomization_test(rr["fusion_tuned"], rr[s], 5000) for s in SYSTEMS if s != "fusion_tuned"}
        adj = holm(pvals)
        for row in table:
            if row["system"] in pvals:
                row["p"] = round(pvals[row["system"]], 4)
                row["p_holm"] = round(adj[row["system"]], 4)
                row["delta"] = round(float(rr["fusion_tuned"].mean() - rr[row["system"]].mean()), 4)
        self.state["systems"] = table
        self._apply_weights(tuned)
        self.note("Xong bảng so sánh các mô hình; đã dùng trọng số dò được cho trang tìm kiếm")

        # 3. độ bền theo nhiễu
        q1 = [q for q in test_q if q["type"] == "Q1"][: self.n_sweep]
        srng = random.Random(5)
        sweep = []
        for rate in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
            row = {"rate": rate}
            texts = [corrupt(q["text"], rate, srng)[0] if rate > 0 else q["text"] for q in q1]
            for sysname in ("bm25", "char5x", "phon12", "fusion_tuned"):
                ranks = []
                for q, text in zip(q1, texts):
                    a = eng.analyze(text)
                    if sysname == "fusion_tuned":
                        w = {**PRESET_WEIGHTS[a["type"]], **tuned.get(a["type"], {})}
                        ranking, _ = eng.rank(text, "fusion", a, weights=w)
                    else:
                        ranking, _ = eng.rank(text, sysname, a)
                    ranks.append(target_rank(ranking, q["cluster"], eng.cluster_of))
                row[sysname] = metrics(ranks)["mrr10"]
            sweep.append(row)
        self.state["noise_sweep"] = sweep
        self.note("Xong đường cong độ bền theo tỉ lệ nhiễu 0-50%")

        # 4. truy vấn ca sĩ: nhiều bài đúng
        self.state["adhoc"] = self.adhoc_artists()
        self.note("Xong đánh giá truy vấn tên ca sĩ (nhiều bài đúng, đường P-R)")

        # 5. phân loại loại truy vấn
        self.state["classifier"] = self.classifier_eval(test_q)
        self.note("Xong đánh giá bộ phân loại loại truy vấn (NB và hồi quy logistic)")

        # 6. phục hồi dấu như dịch máy, sửa chính tả
        self.state["restoration"] = self.restoration_eval(test_q)
        self.note("Xong đánh giá phục hồi dấu (BLEU) và sửa chính tả")

        # 7. phân cụm, MinHash
        self.state["clustering"] = self.clustering_eval()
        self.state["minhash"] = self.minhash_eval()
        self.state["index"] = {"compression": eng.pindex.compression_stats(),
                               "vocab": {k: len(v.vocab) for k, v in eng.ix.items()}}
        self.note("Xong phân cụm, MinHash và thống kê nén chỉ mục")
        self.state["paper"] = self.paper_results()
        self.state["seconds"] = round(time.time() - t_all, 1)
        self.state["status"] = "xong"
        self.note(f"Hoàn tất sau {self.state['seconds']} giây")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------ các phần
    def adhoc_artists(self) -> dict:
        eng = self.eng
        artists = [(n, c) for n, c in eng.artist_count.most_common() if c >= 6 and len(clean(n).split()) >= 2][:25]
        systems = {"meta": "BM25 trường tên bài/ca sĩ", "bm25": "BM25 trên lời", "fusion": "ViSongSearch"}
        out = {s: {"ap": [], "p10": [], "rprec": [], "ndcg10": []} for s in systems}
        curves = {s: np.zeros(11) for s in systems}
        for name, _ in artists:
            key = remove_diacritics(clean(name))
            rel = {d for d in eng.ids if key in eng._artist_plain[d]}
            q = key
            for s in systems:
                if s == "meta":
                    ranking = [d for d, _ in eng.ix["meta"].search(q, 200)]
                else:
                    ranking = [d for d, _ in eng.rank(q, s)[0][:200]]
                hits, ap, prec_at = 0, 0.0, []
                for i, d in enumerate(ranking, 1):
                    if d in rel:
                        hits += 1
                        ap += hits / i
                    prec_at.append((hits / i, hits / len(rel)))
                out[s]["ap"].append(ap / len(rel))
                out[s]["p10"].append(sum(d in rel for d in ranking[:10]) / 10)
                R = len(rel)
                out[s]["rprec"].append(sum(d in rel for d in ranking[:R]) / R)
                dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(ranking[:10], 1) if d in rel)
                idcg = sum(1 / math.log2(i + 1) for i in range(1, min(R, 10) + 1))
                out[s]["ndcg10"].append(dcg / idcg)
                for j, level in enumerate(np.linspace(0, 1, 11)):
                    ps = [p for p, r in prec_at if r >= level - 1e-9]
                    curves[s][j] += max(ps) if ps else 0.0
        n = max(1, len(artists))
        return {"n_queries": len(artists), "artists": [a for a, _ in artists],
                "rows": [{"system": systems[s], "MAP": round(float(np.mean(v["ap"])), 4),
                          "P@10": round(float(np.mean(v["p10"])), 4), "R-prec": round(float(np.mean(v["rprec"])), 4),
                          "NDCG@10": round(float(np.mean(v["ndcg10"])), 4)} for s, v in out.items()],
                "pr_curve": {systems[s]: [round(float(x / n), 4) for x in c] for s, c in curves.items()}}

    def classifier_eval(self, test_q) -> dict:
        from sklearn.feature_selection import chi2
        from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
        eng = self.eng
        checker = AmbiguityChecker(eng.songs, eng.cluster_of)
        train = generate_rule_queries([s for s in eng.songs if eng.split[s.doc_id] == "train"], eng.cluster_of,
                                      {"train": {"Q1": 1, "Q2": 1, "Q3": 1}}, seed=13, checker=checker)
        llm = [json.loads(l) for l in open(os.path.join(HERE, "data", "llm_queries.jsonl"), encoding="utf-8")]
        X_tr = [q["text"] for q in train] + [q["text"] for q in llm if q["split"] == "dev"]
        y_tr = [q["type"] for q in train] + [q["type"][:2] for q in llm if q["split"] == "dev"]
        X_te = [q["text"] for q in test_q] + [q["text"] for q in llm if q["split"] == "test"]
        y_te = [q["type"] for q in test_q] + [q["type"][:2] for q in llm if q["split"] == "test"]
        labels = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        res = {"train": dict(Counter(y_tr)), "test": dict(Counter(y_te)), "models": []}
        vm, vp = set(eng.ix["syl12"].vocab), set(eng.ix["nodiac12"].vocab)
        for name, model, bal in (("Naive Bayes", "nb", False), ("Hồi quy logistic (cân bằng lớp)", "logreg", True)):
            clf = eng.qtype if model == "logreg" else QueryTypeClassifier(vm, vp, model, bal).fit(X_tr, y_tr)
            pred = clf.predict(X_te)
            p, r, f, s = precision_recall_fscore_support(y_te, pred, labels=labels, zero_division=0)
            acc = float(np.mean([a == b for a, b in zip(pred, y_te)]))
            res["models"].append({"name": name, "accuracy": round(acc, 4), "macro_f1": round(float(np.mean(f)), 4),
                                  "per_class": [{"label": l, "p": round(float(p[i]), 3), "r": round(float(r[i]), 3),
                                                 "f1": round(float(f[i]), 3), "n": int(s[i])} for i, l in enumerate(labels)],
                                  "confusion": confusion_matrix(y_te, pred, labels=labels).tolist()})
        tf = eng.qtype.features.tfidf
        Xt = tf.transform(X_tr)
        names = tf.get_feature_names_out()
        top = {}
        ya = np.array(y_tr)
        for l in labels:
            sc, _ = chi2(Xt, ya == l)
            sc = np.nan_to_num(sc)
            pos = np.asarray(Xt[ya == l].mean(axis=0)).ravel() > np.asarray(Xt[ya != l].mean(axis=0)).ravel()
            sc = np.where(pos, sc, 0)          # chỉ giữ đặc trưng xuất hiện nhiều hơn ở lớp này
            top[l] = [names[i].replace(" ", "␣") for i in np.argsort(-sc)[:8]]
        res["chi2"] = top
        res["labels"] = labels
        return res

    def restoration_eval(self, test_q) -> dict:
        eng = self.eng
        train_texts = [s.lyrics for s in eng.songs if eng.split[s.doc_id] == "train"]
        r = DiacriticRestorer(train_texts)
        golds = [q["text"] for q in test_q if q["type"] == "Q1"]
        rows = []
        from visong.nbest import nbest as nb
        for order in (1, 2, 3):
            r.order = order
            hyps = [r.restore(remove_diacritics(g)) for g in golds]
            ok = tot = exact = 0
            for h, g in zip(hyps, golds):
                a, b = syllable_accuracy(h, g)
                ok += a
                tot += b
                exact += h == clean(g)
            rows.append({"order": order, "syllable_acc": round(ok / max(1, tot), 4),
                         "exact": round(exact / max(1, len(golds)), 4),
                         "bleu": bleu(hyps, [clean(g) for g in golds])})
        r.order = 3
        oracle = sum(clean(g) in [t for t, _ in nb(r, remove_diacritics(g), 5)] for g in golds) / max(1, len(golds))
        examples = []
        for g in golds[:6]:
            examples.append({"input": remove_diacritics(clean(g)), "output": r.restore(remove_diacritics(g)),
                             "gold": clean(g)})
        # sửa chính tả trên Q3
        from .spelling import NoisyChannelSpeller
        sp = NoisyChannelSpeller(r)
        q3 = [q for q in test_q if q["type"] == "Q3"]
        before = after = tot = 0
        sp_examples = []
        for q in q3:
            gold = clean(q["meta"]["source_fragment"]).split()
            noisy = clean(q["text"]).split()
            fix = sp.decode(q["text"], 1)
            fixed = fix[0]["text"].split() if fix else noisy
            if len(noisy) != len(gold) or len(fixed) != len(gold):
                continue
            before += sum(x == y for x, y in zip(noisy, gold))
            after += sum(x == y for x, y in zip(fixed, gold))
            tot += len(gold)
            if len(sp_examples) < 6:
                sp_examples.append({"input": " ".join(noisy), "output": " ".join(fixed), "gold": " ".join(gold)})
        return {"n": len(golds), "rows": rows, "oracle5": round(oracle, 4), "examples": examples,
                "spelling": {"n": len(q3), "syllable_acc_before": round(before / max(1, tot), 4),
                             "syllable_acc_after": round(after / max(1, tot), 4), "examples": sp_examples},
                "note": "Mô hình ngôn ngữ ở đây chỉ học trên lời các bài thuộc tập train, nên lời bài test là văn bản lạ."}

    def clustering_eval(self) -> dict:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        eng = self.eng
        rng = np.random.default_rng(1)
        idx = rng.choice(len(eng.ids), min(400, len(eng.ids)), replace=False)
        X = eng.vsm.Z[idx]
        k = eng.k_choice["best_k"]
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
        from sklearn.cluster import AgglomerativeClustering
        ag = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit(X)
        def sil(labels):
            labels = np.asarray(labels)
            ok = labels >= 0
            if len(set(labels[ok].tolist())) < 2:
                return None
            return round(float(silhouette_score(X[ok], labels[ok], metric="cosine")), 4)
        # DBSCAN nhạy với eps: thử nhiều giá trị như demo của môn, giữ giá trị cho silhouette tốt nhất
        sweep = []
        for eps in (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5):
            r = clustering.dbscan_roles(X, eps=eps, min_samples=4)
            roles = Counter(r["roles"])
            sweep.append({"eps": eps, "clusters": r["n_clusters"], "noise": r["n_noise"], "core": roles.get("lõi", 0),
                          "border": roles.get("biên", 0), "silhouette": sil(r["labels"])})
        ok = [r for r in sweep if r["silhouette"] is not None]
        best = max(ok, key=lambda r: r["silhouette"]) if ok else sweep[-1]
        return {"k_choice": eng.k_choice, "sample": len(idx), "dbscan_sweep": sweep,
                "compare": [{"method": f"K-means (K={k})", "silhouette": sil(km.labels_)},
                            {"method": f"Phân cấp average-link (K={k})", "silhouette": sil(ag.labels_)},
                            {"method": f"DBSCAN (eps={best['eps']}): {best['clusters']} cụm, {best['noise']} điểm nhiễu",
                             "silhouette": best["silhouette"]}],
                "topics": {str(k): v for k, v in eng.topic_names.items()},
                "topic_sizes": dict(Counter(eng.topic.values()))}

    def minhash_eval(self) -> dict:
        eng = self.eng
        rng = random.Random(3)
        by_c = defaultdict(list)
        for d in eng.ids:
            by_c[eng.cluster_of[d]].append(d)
        multi = [v for v in by_c.values() if len(v) > 1]
        pairs = [tuple(rng.sample(v, 2)) for v in rng.sample(multi, min(150, len(multi)))]
        pairs += [tuple(rng.sample(eng.ids, 2)) for _ in range(150)]
        err, rows = [], []
        for a, b in pairs:
            la, lb = eng.by_id[a].lyrics, eng.by_id[b].lyrics
            j = jaccard(shingles(la), shingles(lb))
            est = minhash(la).jaccard(minhash(lb))
            err.append(abs(j - est))
            rows.append((round(j, 3), round(est, 3)))
        curve = [{"j": round(j, 2), "p": round(1 - (1 - j ** 3) ** 42, 4)} for j in np.linspace(0.05, 1, 20)]
        return {"pairs": len(pairs), "mae": round(float(np.mean(err)), 4), "max_err": round(float(np.max(err)), 4),
                "sample": rows[:12], "lsh_curve": curve, "stats": eng.dedup_stats}

    def paper_results(self) -> list | None:
        """Kết quả của bài báo (paper/generated/summary.json, chạy trên Kaggle GPU với tập truy vấn đầy đủ,
        có cả Q4, Q5 do LLM viết). Demo chỉ đọc lại để so sánh, không chạy lại các mô hình dense."""
        path = os.path.join(HERE, "..", "paper", "generated", "summary.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        rows = []
        for key, name in PAPER_SYSTEMS:
            v = data.get(key)
            if isinstance(v, dict):
                rows.append({"key": key, "name": name, **{k: v.get(k) for k in
                             ("Q1", "Q2", "Q3", "Q4", "Q5", "macro", "N0", "N10", "N20", "N30", "N40", "N50")}})
        return rows or None


def metrics(ranks) -> dict:
    n = max(1, len(ranks))
    rr10 = [1.0 / r if r and r <= 10 else 0.0 for r in ranks]
    return {"n": len(ranks), "mrr10": round(sum(rr10) / n, 4),
            "s1": round(sum(1 for r in ranks if r == 1) / n, 4),
            "r10": round(sum(1 for r in ranks if r and r <= 10) / n, 4),
            "ndcg10": round(sum(1 / math.log2(r + 1) for r in ranks if r and r <= 10) / n, 4),
            "map": round(sum(1.0 / r for r in ranks if r) / n, 4)}
