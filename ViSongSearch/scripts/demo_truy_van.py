"""Demo cho Người 2: tạo vi_du_truy_van.csv (70 truy vấn mẫu) và so_luong_truy_van_test.json.

Chạy từ thư mục ViSongSearch (cần thư mục work_full/queries và data/hac.jsonl, không nằm trong git):
    python scripts/demo_truy_van.py --queries work_full/queries --songs data/hac.jsonl work_pilot/corpus.jsonl --out docs/nguoi2_truy_van_danh_gia

--queries: thư mục chứa rule.jsonl, llm.jsonl, llm_unmarked.jsonl (và variants.jsonl nếu có)
--songs:   các file jsonl chứa bài hát (doc_id, title, artist, url) để ghi tên bài đúng
"""
import argparse, collections, csv, json, os, random

TYPES = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q4u", "Q5u"]
FILES = ["rule.jsonl", "llm.jsonl", "llm_unmarked.jsonl", "variants.jsonl"]
EXP = {
    "Q1": "Câu chính xác: chép nguyên 5–12 âm tiết liền nhau trong lời bài hát, có đủ dấu (người dùng nhớ đúng một câu).",
    "Q2": "Không dấu: câu như Q1 nhưng gõ không dấu; khoảng 15% còn sót 1–2 âm tiết gõ kiểu Telex.",
    "Q3": "Nghe nhầm / gõ sai: câu như Q1 nhưng 10–40% âm tiết bị sai (nghe nhầm thanh điệu, phụ âm đầu, gõ sai phím).",
    "Q4": "Diễn đạt lại: mô hình ngôn ngữ viết lại một đoạn lời bằng từ ngữ khác nhưng giữ nguyên ý (nhớ ý, không nhớ chữ).",
    "Q5": "Mô tả: mô hình ngôn ngữ đóng vai người quên tên bài, kể lại nội dung/cảm xúc của bài (nhớ mà không gọi được tên).",
    "Q4u": "Q4 gõ không dấu: đúng câu diễn đạt lại của Q4 nhưng bỏ hết dấu.",
    "Q5u": "Q5 gõ không dấu: đúng câu mô tả của Q5 nhưng bỏ hết dấu.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--songs", nargs="+", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--per-type", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()

    docs = {}
    for f in a.songs:
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            docs[d["doc_id"]] = d

    count = collections.defaultdict(collections.Counter)   # loại -> split -> số câu
    test = collections.defaultdict(dict)                    # cluster -> loại -> [truy vấn]
    for name in FILES:
        path = os.path.join(a.queries, name)
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            count[q["type"]][q["split"]] += 1
            if q["split"] == "test":
                test[q["cluster"]].setdefault(q["type"], []).append(q)

    # 1) so_luong_truy_van_test.json
    stats = {
        "giai_thich": "Số truy vấn mỗi loại trong tập kiểm tra (test); mỗi truy vấn có đúng một bài đúng.",
        "test": {t: c["test"] for t, c in count.items()},
        "dev": {t: c["dev"] for t, c in count.items() if c["dev"]},
        "train": {t: c["train"] for t, c in count.items() if c["train"]},
    }
    stats["tong_test_7_loai_chinh"] = sum(stats["test"].get(t, 0) for t in TYPES)
    with open(os.path.join(a.out, "so_luong_truy_van_test.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    # 2) vi_du_truy_van.csv: cùng một nhóm bài cho cả 7 loại, để so sánh từng bài
    ok = sorted(c for c, v in test.items() if all(t in v for t in TYPES)
                and all(docs.get(q["doc_id"], {}).get("title") for t in TYPES for q in v[t]))
    pick = random.Random(a.seed).sample(ok, a.per_type)
    rows = []
    for t in TYPES:
        for c in pick:
            q = test[c][t][0]
            d, m = docs[q["doc_id"]], q.get("meta", {})
            note = ""
            if t == "Q3":
                ops = ", ".join(f"{k}:{v}" for k, v in m.get("ops", {}).items())
                note = f"tỉ lệ nhiễu {round(m.get('rate', 0) * 100)}%, {m.get('changed')} âm tiết bị đổi ({ops})"
            elif t in ("Q4", "Q5"):
                note = f"sinh bởi {m.get('generator')}"
            elif t in ("Q4u", "Q5u"):
                note = f"bỏ dấu từ {m.get('from')}"
            rows.append([len(rows) + 1, t, EXP[t], q["text"], m.get("source_fragment", ""), d["title"],
                         d.get("artist", "").replace(" | NHAC.VN", ""), q["doc_id"], q["cluster"],
                         d.get("url", ""), q["qid"], note])
    header = ["STT", "Loại truy vấn", "Giải thích loại (ngắn)", "Truy vấn", "Đoạn lời gốc dùng để sinh truy vấn",
              "Bài đúng: tên bài", "Tác giả/ca sĩ (theo nguồn)", "Bài đúng: doc_id", "Cụm phiên bản (cluster)",
              "Link bài", "Mã truy vấn (qid)", "Ghi chú"]
    with open(os.path.join(a.out, "vi_du_truy_van.csv"), "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([header] + rows)   # utf-8-sig: Excel mở đúng dấu tiếng Việt

    print("Số truy vấn test:", json.dumps(stats["test"], ensure_ascii=False))
    print("Tổng 7 loại chính:", stats["tong_test_7_loai_chinh"])
    print(f"Đã ghi vi_du_truy_van.csv ({len(rows)} dòng) và so_luong_truy_van_test.json vào {a.out}")


if __name__ == "__main__":
    main()
