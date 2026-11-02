"""
ranking.py - Score Ranking bang TF-IDF ket hop BM25 (yeu cau thu 4)

Doc du lieu da crawl trong data/crawler.db, xay chi muc (index) trong bo nho,
cham diem moi tai lieu cho mot truy van bang:
    - TF-IDF (do tuong dong cosine)
    - BM25
roi ket hop hai diem thanh diem cuoi cung va xep hang.

Cach chay (cung thu muc voi main.py):
    python ranking.py "son tung"                 # mot truy van
    python ranking.py "nhạc trẻ" --top 5         # lay 5 ket qua dau
    python ranking.py --demo                     # chay thu nhieu truy van mau
    python ranking.py                            # che do go truy van lien tuc
    python ranking.py "tinh yeu" --explain       # xem diem tung tu cua ket qua dau
    python ranking.py "tinh yeu" --source songs  # xep hang tren bang songs
    python ranking.py "nhac tre" --max-df 0      # khong loc menu/chan trang (de so sanh)

Chi dung thu vien co san cua Python (khong can cai them gi).
"""

import argparse
import math
import re
import sqlite3
import sys
import unicodedata
from collections import Counter

DB_PATH = "data/crawler.db"

# ---- tham so mo hinh ----
K1 = 1.5          # BM25: do bao hoa cua tan suat tu
B = 0.75          # BM25: muc chuan hoa theo do dai tai lieu
ALPHA = 0.5       # trong so BM25 trong diem ket hop (TF-IDF co trong so 1 - ALPHA)
TITLE_BOOST = 3   # tieu de duoc tinh nhu lap lai 3 lan (tieu de quan trong hon noi dung)

# Tu dung pho bien (da bo dau). Chi bo cac tu don, khong bo cum tu.
STOPWORDS = {
    "va", "cua", "la", "co", "nhung", "cac", "mot", "cho", "de", "trong", "voi",
    "duoc", "khi", "nay", "do", "da", "se", "cung", "nhu", "den", "tu", "ve",
    "tren", "the", "ma", "thi", "rat", "nen", "hay", "van", "con", "nhieu",
}


# =====================================================================
# 1. Tien xu ly van ban
# =====================================================================
def strip_accents(text):
    """'Nhạc Trẻ' -> 'Nhac Tre' (de truy van khong dau van khop)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def tokenize(text):
    """
    Chu thuong -> bo dau -> tach tu (am tiet) -> bo stopword -> them bigram.
    Tieng Viet viet tach tung am tiet, nen bigram ('nhac_tre') giup bat duoc
    cum tu ma khong can thu vien tach tu ngoai.
    """
    text = strip_accents((text or "").lower())
    words = re.findall(r"[a-z0-9]+", text)
    words = [w for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


# =====================================================================
# 2. Doc tai lieu tu database
# =====================================================================
def clean_artist(artist):
    return re.sub(r"\s*\|\s*NHAC\.VN\s*$", "", artist or "", flags=re.IGNORECASE).strip()


def load_documents(db_path, source):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    docs = []

    if source == "songs":
        rows = conn.execute("SELECT url, title, artist, lyrics FROM songs").fetchall()
        for r in rows:
            artist = clean_artist(r["artist"])
            title_tokens = tokenize(f"{r['title']} {artist}")
            body_tokens = tokenize(r["lyrics"] or "")
            docs.append({
                "url": r["url"],
                "title": f"{r['title']} - {artist}" if artist else r["title"],
                "title_tokens": title_tokens,
                "body_tokens": body_tokens,
            })
    else:
        rows = conn.execute(
            "SELECT url, title, content FROM pages "
            "WHERE status_code = 200 AND content != ''"
        ).fetchall()
        for r in rows:
            docs.append({
                "url": r["url"],
                "title": r["title"] or "(khong co tieu de)",
                "title_tokens": tokenize(r["title"]),
                "body_tokens": tokenize(r["content"]),
            })

    conn.close()
    return docs


# =====================================================================
# 3. Chi muc va cham diem
# =====================================================================
class Ranker:
    def __init__(self, docs, max_df_ratio=None):
        """
        max_df_ratio: bo cac tu trong NOI DUNG xuat hien o hon ty le nay cua so
        tai lieu (vi du 0.5). Moi trang cua nhac.vn deu chua menu / chan trang,
        nen cac tu do khong giup phan biet tai lieu. Tieu de luon duoc giu lai.
        """
        self.docs = docs
        self.N = len(docs)

        # ---- tien xu ly: loai tu lap lai o hau het cac trang ----
        self.boilerplate = set()
        if max_df_ratio:
            body_df = Counter()
            for d in docs:
                body_df.update(set(d["body_tokens"]))
            self.boilerplate = {
                t for t, n in body_df.items() if n / self.N > max_df_ratio
            }
        for d in docs:
            body = [t for t in d["body_tokens"] if t not in self.boilerplate]
            d["tokens"] = d["title_tokens"] * TITLE_BOOST + body

        self.tf = [Counter(d["tokens"]) for d in docs]      # tan suat tu trong tung tai lieu
        self.dl = [len(d["tokens"]) for d in docs]          # do dai tung tai lieu
        self.avgdl = sum(self.dl) / self.N

        # df: so tai lieu chua tu
        self.df = Counter()
        for c in self.tf:
            self.df.update(c.keys())

        # IDF cho hai mo hinh
        self.idf_tfidf = {t: math.log((self.N + 1) / (n + 1)) + 1 for t, n in self.df.items()}
        self.idf_bm25 = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in self.df.items()
        }

        # Do dai vector TF-IDF cua tung tai lieu (de tinh cosine)
        self.norms = []
        for c in self.tf:
            s = sum(((1 + math.log(f)) * self.idf_tfidf[t]) ** 2 for t, f in c.items())
            self.norms.append(math.sqrt(s) or 1.0)

    # ---- cham diem mot truy van ----
    def score(self, query, alpha=ALPHA):
        q_all = Counter(tokenize(query))
        unknown = [t for t in q_all if t not in self.df]
        q = {t: f for t, f in q_all.items() if t in self.df}
        if not q:
            return [], unknown

        # vector truy van cho TF-IDF
        qvec = {t: (1 + math.log(f)) * self.idf_tfidf[t] for t, f in q.items()}
        qnorm = math.sqrt(sum(w * w for w in qvec.values())) or 1.0

        raw = []  # (doc_index, tfidf, bm25)
        for i, c in enumerate(self.tf):
            tfidf = bm25 = 0.0
            for t, qw in qvec.items():
                f = c.get(t)
                if not f:
                    continue
                tfidf += qw * (1 + math.log(f)) * self.idf_tfidf[t]
                bm25 += self.idf_bm25[t] * f * (K1 + 1) / (
                    f + K1 * (1 - B + B * self.dl[i] / self.avgdl)
                )
            if bm25 > 0:
                raw.append((i, tfidf / (self.norms[i] * qnorm), bm25))

        if not raw:
            return [], unknown

        # chuan hoa moi diem ve [0, 1] bang cach chia cho diem lon nhat
        max_tfidf = max(r[1] for r in raw) or 1.0
        max_bm25 = max(r[2] for r in raw) or 1.0

        results = []
        for i, tfidf, bm25 in raw:
            tfidf_n = tfidf / max_tfidf
            bm25_n = bm25 / max_bm25
            final = alpha * bm25_n + (1 - alpha) * tfidf_n
            results.append({
                "index": i,
                "doc": self.docs[i],
                "tfidf": tfidf_n,
                "bm25": bm25_n,
                "score": final,
            })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results, unknown

    # ---- giai thich diem BM25 cua mot tai lieu ----
    def explain(self, query, doc_index):
        c = self.tf[doc_index]
        rows = []
        for t in sorted(set(tokenize(query))):
            if t not in self.df:
                continue
            f = c.get(t, 0)
            part = 0.0
            if f:
                part = self.idf_bm25[t] * f * (K1 + 1) / (
                    f + K1 * (1 - B + B * self.dl[doc_index] / self.avgdl)
                )
            rows.append((t, f, self.df[t], self.idf_bm25[t], part))
        return rows


# =====================================================================
# 4. Hien thi
# =====================================================================
def short(text, n):
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 3] + "..."


def show_results(ranker, query, top, alpha, explain=False):
    results, unknown = ranker.score(query, alpha)
    print()
    print("=" * 96)
    print(f'Truy van: "{query}"   (alpha BM25 = {alpha}, TF-IDF = {1 - alpha:.2f})')
    if unknown:
        print("Tu khong co trong du lieu:", ", ".join(unknown))
    if not results:
        print("Khong tim thay tai lieu nao.")
        return
    print(f"So tai lieu khop: {len(results)} / {ranker.N}")
    print("-" * 96)
    print(f"{'Hang':<5}{'Diem':>7}{'BM25':>7}{'TF-IDF':>8}   {'Tieu de':<44}{'URL'}")
    print("-" * 96)
    for rank, r in enumerate(results[:top], 1):
        d = r["doc"]
        print(
            f"{rank:<5}{r['score']:>7.3f}{r['bm25']:>7.3f}{r['tfidf']:>8.3f}   "
            f"{short(d['title'], 42):<44}{short(d['url'].replace('https://', ''), 40)}"
        )
    if explain:
        best = results[0]
        print()
        print(f"Giai thich diem BM25 cua ket qua dau: {short(best['doc']['title'], 60)}")
        print(f"{'Tu':<22}{'tf':>5}{'df':>6}{'idf':>8}{'diem':>9}")
        for t, f, df, idf, part in ranker.explain(query, best["index"]):
            print(f"{t:<22}{f:>5}{df:>6}{idf:>8.2f}{part:>9.3f}")


# =====================================================================
# 5. Chuong trinh chinh
# =====================================================================
DEMO_QUERIES = ["sơn tùng", "nhạc trẻ", "bảng xếp hạng", "tình yêu", "nhạc hot"]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # in tieng Viet tren Windows
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Score Ranking: TF-IDF + BM25")
    parser.add_argument("query", nargs="*", help="cau truy van")
    parser.add_argument("--db", default=DB_PATH, help="duong dan crawler.db")
    parser.add_argument("--source", choices=["pages", "songs"], default="pages")
    parser.add_argument("--top", type=int, default=10, help="so ket qua hien thi")
    parser.add_argument("--alpha", type=float, default=ALPHA,
                        help="trong so BM25 (0 = chi TF-IDF, 1 = chi BM25)")
    parser.add_argument("--max-df", type=float, default=0.5,
                        help="bo tu trong noi dung co mat o hon ty le nay cua cac trang "
                             "(0 = khong bo; chi ap dung cho --source pages)")
    parser.add_argument("--explain", action="store_true", help="giai thich diem")
    parser.add_argument("--demo", action="store_true", help="chay cac truy van mau")
    args = parser.parse_args()

    docs = load_documents(args.db, args.source)
    if not docs:
        print("Khong co du lieu trong bang", args.source)
        return
    max_df = args.max_df if args.source == "pages" else None
    ranker = Ranker(docs, max_df_ratio=max_df)
    print(f"Da nap {ranker.N} tai lieu tu bang '{args.source}', "
          f"{len(ranker.df)} tu/cum tu khac nhau, do dai trung binh {ranker.avgdl:.0f}.")
    if ranker.boilerplate:
        print(f"Da bo {len(ranker.boilerplate)} tu/cum tu lap lai o hon "
              f"{int(args.max_df * 100)}% so trang (menu, chan trang) khoi noi dung.")

    if args.demo:
        for q in DEMO_QUERIES:
            show_results(ranker, q, args.top, args.alpha, args.explain)
    elif args.query:
        show_results(ranker, " ".join(args.query), args.top, args.alpha, args.explain)
    else:
        print("Go truy van roi Enter (de trong de thoat).")
        while True:
            q = input("\nTruy van> ").strip()
            if not q:
                break
            show_results(ranker, q, args.top, args.alpha, args.explain)


if __name__ == "__main__":
    main()
