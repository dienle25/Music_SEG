"""Bảng đối chiếu từng bài giảng của hai môn với mã nguồn (hiển thị ở trang /doi-chieu).

status: "có sẵn" = đã có trong mã của bài báo (visong/, app/, scripts/, crawler/);
        "bổ sung" = phần thiếu, được viết thêm trong webdemo/; "có sẵn + bổ sung"; "chưa làm".
"""
S, A, SA, N = "có sẵn", "bổ sung", "có sẵn + bổ sung", "chưa làm"

COVERAGE = [
    # ------------------------------------------------------------------ SEG301
    ("SEG301", "1", "Information Retrieval and Search Engines", [
        ("Bài toán truy hồi, máy tìm kiếm, tìm kiếm theo lời (known-item search)", S,
         "cả dự án; visong/pipeline.py", "/", "Bài toán chính: quên tên bài, chỉ nhớ lời / mô tả"),
    ]),
    ("SEG301", "2", "Architecture of a Search Engine", [
        ("Thu thập, biến đổi văn bản, tạo chỉ mục, tương tác, xếp hạng, đánh giá", S,
         "crawler/, visong/corpus.py, visong/lexical.py, visong/pipeline.py", "/thong-ke",
         "Demo in từng bước dựng khi khởi động (xem cửa sổ lệnh)"),
    ]),
    ("SEG301", "3", "Crawls and Feeds", [
        ("Web crawler, lưu tài liệu (SQLite)", S, "crawler/, file .db của nhóm (bảng songs, pages, links)", "/thong-ke", ""),
        ("Sitemaps", A, "webdemo/seo.py: sitemap()", "/sitemap.xml", ""),
        ("Phát hiện trùng lặp, gần trùng (fingerprint, MinHash)", SA, "visong/dedup.py; webdemo/evaluation.py: minhash_eval",
         "/danh-gia#minhash", "Demo đo sai số MinHash so với Jaccard thật và vẽ đường S của LSH"),
        ("Lọc nhiễu, mã hóa ký tự", S, "visong/corpus.py: clean_lyrics, fix_homoglyphs; visong/text.py: nfc", "", ""),
    ]),
    ("SEG301", "4", "Processing Text", [
        ("Tách từ (âm tiết), n-gram, chuẩn hóa dấu", S, "visong/text.py: syllables, with_bigrams, char_ngrams_across", "/phong-lab#index", ""),
        ("Thống kê văn bản (Zipf, Heaps)", S, "scripts/collection_stats.py", "", ""),
        ("Từ dừng (stopping)", A, "webdemo/lexicon/query.json (STOP); dùng cho đoạn trích, nhãn cụm, liên kết từ", "/phong-lab#assoc", ""),
        ("Phân tích liên kết: PageRank, chất lượng liên kết", A, "webdemo/graph.py: LinkGraph._pagerank", "/thong-ke#pagerank",
         "Đồ thị lấy từ bảng links của file .db; PageRank dùng làm điểm phụ khi xếp hạng"),
        ("Trích xuất thông tin (IE)", A, "webdemo/extraction.py", "/phong-lab#ie", "Dùng từ điển và mẫu; chưa làm HMM"),
    ]),
    ("SEG301", "5", "Ranking with Indexes", [
        ("Chỉ mục ngược (đếm, trọng số)", S, "visong/lexical.py: LexicalIndex (ma trận thưa = danh sách posting)", "", ""),
        ("Chỉ mục có vị trí, truy vấn cụm từ", A, "webdemo/indexing.py: PositionalIndex, phrase_docs", "/phong-lab#index", ""),
        ("Nén: delta, mã bit Elias-gamma, mã byte v-byte", A, "webdemo/indexing.py: vbyte, gamma, compression_stats",
         "/phong-lab#index", "Bảng so sánh kích thước trên toàn chỉ mục"),
        ("Con trỏ nhảy (skip pointers)", A, "webdemo/indexing.py: intersect", "/phong-lab#index", ""),
        ("Xử lý truy vấn DAAT và TAAT", A, "webdemo/indexing.py: daat, taat", "/phong-lab#index", ""),
    ]),
    ("SEG301", "6", "Queries and Interfaces", [
        ("Kiểm tra chính tả và gợi ý", SA, "visong/diacritics.py, visong/nbest.py; webdemo/spelling.py (kênh nhiễu)",
         "/phong-lab#spell", "Thêm sửa lỗi gõ nhầm / nghe nhầm, gợi ý \"Có phải bạn muốn tìm\""),
        ("Mở rộng truy vấn", SA, "visong/lexical.py: rm3_vector; webdemo/semantics.py: WordNet.expansion, TermAssociation",
         "/phong-lab#assoc", "Thêm liên kết từ Dice / MIM / EMIM / chi bình phương"),
        ("Trang kết quả, đoạn trích (snippet)", SA, "app/engine.py; webdemo/snippets.py (Luhn)", "/", ""),
        ("Phân cụm kết quả", A, "webdemo/clustering.py: cluster_results", "/", "Bộ lọc \"Nhóm kết quả\" bên trái"),
        ("Gợi ý truy vấn từ nhật ký", A, "webdemo/store.py: suggestions", "/", ""),
    ]),
    ("SEG301", "7", "Retrieval Models", [
        ("Boolean", A, "webdemo/indexing.py: boolean", "/phong-lab#boolean", "Chọn mô hình \"Boolean\" ở ô Xếp hạng"),
        ("Không gian vector tf-idf", A, "webdemo/retrieval.py: VectorSpace.tfidf_scores", "/", ""),
        ("BM25", S, "visong/lexical.py: bm25_scores", "/", ""),
        ("Query likelihood, RM3 (phản hồi giả)", S, "visong/lexical.py: ql_scores, rm3_vector", "/", ""),
        ("Learning to rank, mô hình dày (dense)", S, "visong/ltr.py, visong/dense.py, visong/rerank.py (chạy trên Kaggle GPU)",
         "/danh-gia#paper", "Demo hiện kết quả của bài báo, không chạy mô hình nơ-ron trên máy"),
        ("Chủ đề ẩn và lệch từ vựng (LSA)", A, "webdemo/retrieval.py: lsa_scores", "/", ""),
        ("Kết hợp bằng chứng (RRF)", S, "visong/fusion.py: rrf, WeightTuner", "/", ""),
    ]),
    ("SEG301", "8", "Search Engine Optimization", [
        ("JSON-LD schema.org, sitemap, robots", SA, "app/main.py; webdemo/seo.py", "/bai-hat", ""),
        ("Kiểm tra on-page / technical / off-page, CTR theo vị trí", A, "webdemo/seo.py: audit; webdemo/store.py: analytics",
         "/thong-ke#seo", ""),
    ]),
    ("SEG301", "9", "Evaluating Search Engines", [
        ("Bộ dữ liệu đánh giá, truy vấn có đáp án", S, "visong/queries.py", "/danh-gia", ""),
        ("MRR, Success@k, Recall", S, "visong/evaluate.py", "/danh-gia", ""),
        ("NDCG, MAP, P@10, R-precision, đường P-R 11 điểm", A, "webdemo/evaluation.py: metrics, adhoc_artists", "/danh-gia#adhoc", ""),
        ("Kiểm định ý nghĩa, Holm", S, "visong/evaluate.py: randomization_test, holm", "/danh-gia", ""),
        ("Dò tham số trên tập dev", S, "visong/fusion.py: WeightTuner", "/danh-gia#tuning", ""),
        ("Hiệu năng (ms / truy vấn)", SA, "scripts/bench_efficiency.py; webdemo/evaluation.py", "/danh-gia", ""),
        ("Nhật ký, thử nghiệm trực tuyến (interleaving)", A, "webdemo/store.py: log_query, team_draft, interleave_wins",
         "/thong-ke#log", "Chọn mô hình \"So sánh xen kẽ\" rồi nhấp kết quả"),
    ]),
    ("SEG301", "10", "Classification and Clustering", [
        ("Naive Bayes, hồi quy logistic, đánh giá P/R/F1", S, "visong/qtype.py", "/danh-gia#classifier", ""),
        ("Chọn đặc trưng (chi bình phương)", A, "webdemo/evaluation.py: classifier_eval", "/danh-gia#classifier", ""),
        ("Phân loại cảm xúc", A, "webdemo/sentiment.py", "/phong-lab#sentiment", ""),
        ("Phân cụm phân cấp, K-means, chọn K", A, "webdemo/clustering.py", "/phong-lab#cluster", ""),
    ]),
    ("SEG301", "11", "Social Search", [
        ("Thẻ người dùng, đám mây thẻ, tìm theo thẻ, suy ra thẻ thiếu", A, "webdemo/store.py: tags; web.py: tags_page", "/the", ""),
        ("Cộng đồng (lan truyền nhãn), HITS", A, "webdemo/graph.py: artist_communities, _hits", "/thong-ke#community", ""),
        ("Lọc cộng tác (collaborative filtering)", A, "webdemo/store.py: co_clicked", "/bai-hat", "Mục \"Người nghe bài này cũng mở\""),
    ]),
    ("SEG301", "12", "Beyond Bag of Words", [
        ("Phụ thuộc giữa các từ (cặp âm tiết, n-gram ký tự qua ranh giới)", S, "visong/text.py: with_bigrams, char_ngrams_across", "", ""),
        ("Mô hình dựa trên đặc trưng", S, "visong/ltr.py", "", ""),
        ("Tìm thực thể (ca sĩ), cấu trúc câu hỏi", A, "webdemo/grammar.py, webdemo/core.py: _boost", "/", ""),
        ("Tìm nhạc (lời bài hát)", S, "cả dự án", "/", ""),
    ]),
    # ------------------------------------------------------------------ TMG301
    ("TMG301", "01", "Regular Expressions, Text Normalization, Edit Distance", [
        ("Biểu thức chính quy, chuẩn hóa văn bản", S, "visong/text.py, visong/corpus.py", "", ""),
        ("Khoảng cách sửa (quy hoạch động, truy vết)", A, "webdemo/spelling.py: edit_distance", "/phong-lab#edit", ""),
        ("Mô hình lỗi gõ / nghe nhầm", S, "visong/noise.py", "/phong-lab#spell", ""),
    ]),
    ("TMG301", "02", "N-Gram Language Models", [
        ("Trigram Kneser-Ney, beam search", S, "visong/diacritics.py", "/phong-lab#restore", ""),
        ("Độ hỗn loạn, sinh câu, đoán từ tiếp", A, "webdemo/langmodel.py: LMTools", "/phong-lab#lm", ""),
    ]),
    ("TMG301", "03", "Naive Bayes, Text Classification, Sentiment", [
        ("Naive Bayes phân loại truy vấn", S, "visong/qtype.py", "/danh-gia#classifier", ""),
        ("Naive Bayes cảm xúc (tự cài, Laplace)", A, "webdemo/sentiment.py: NaiveBayes", "/phong-lab#sentiment", ""),
    ]),
    ("TMG301", "04", "Logistic Regression", [
        ("Hồi quy logistic phân loại truy vấn", S, "visong/qtype.py", "/phong-lab#qtype", ""),
    ]),
    ("TMG301", "05", "Vector Semantics and Embeddings", [
        ("tf-idf, cosine", SA, "visong/lexical.py; webdemo/retrieval.py", "", ""),
        ("Vector dày (E5, BGE-M3)", S, "visong/dense.py (Kaggle GPU)", "/danh-gia#paper", ""),
        ("PPMI + SVD, láng giềng gần, phép tương tự", A, "webdemo/langmodel.py: Embeddings", "/phong-lab#embed", ""),
    ]),
    ("TMG301", "06", "Clustering for Text Similarity", [
        ("Gom bản trùng", S, "visong/dedup.py", "", ""),
        ("Độ đo khoảng cách, K-means, phân cấp, DBSCAN, silhouette", A, "webdemo/clustering.py", "/phong-lab#cluster", ""),
    ]),
    ("TMG301", "07", "Transformers and Pre-trained Language Models", [
        ("Mô hình E5, BGE-M3, reranker", S, "visong/dense.py, visong/rerank.py", "/danh-gia#paper", ""),
        ("Self-attention minh họa", A, "webdemo/langmodel.py: self_attention", "/phong-lab#attn", ""),
    ]),
    ("TMG301", "08", "Fine-Tuning and Masked Language Models", [
        ("Tinh chỉnh mô hình truy hồi (NAFT)", S, "visong/finetune.py", "/danh-gia#paper", ""),
        ("Điền chỗ trống (ý tưởng masked LM)", A, "webdemo/langmodel.py: fill_mask", "/phong-lab#mask", ""),
    ]),
    ("TMG301", "09", "Machine Translation", [
        ("Phục hồi dấu như dịch máy thống kê (kênh nhiễu + LM + giải mã)", S, "visong/diacritics.py, visong/nbest.py",
         "/phong-lab#restore", ""),
        ("Đánh giá BLEU", A, "webdemo/evaluation.py: bleu", "/danh-gia#restoration", ""),
    ]),
    ("TMG301", "10", "Chatbot and Dialogue Systems", [
        ("Hệ hội thoại theo khung: NLU, DST, chính sách, NLG", A, "webdemo/dialog.py", "/tro-ly", ""),
    ]),
    ("TMG301", "11", "Context-Free Grammars and Constituency Parsing", [
        ("PCFG, CKY (từ dưới lên), giải quyết nhập nhằng", A, "webdemo/grammar.py: QueryGrammar.parse", "/phong-lab#parse", ""),
    ]),
    ("TMG301", "12", "Dependency Parsing", [
        ("Cây phụ thuộc (head rules), chuyển trạng thái arc-standard", A, "webdemo/grammar.py: dependencies, arc_standard",
         "/phong-lab#parse", "Chưa làm phân tích dựa trên đồ thị (MST)"),
    ]),
    ("TMG301", "13", "Logical Representations of Sentence Meaning", [
        ("Logic bậc nhất, lambda, kiểm tra trên kho", A, "webdemo/grammar.py: logical_form; webdemo/core.py: check",
         "/phong-lab#parse", ""),
    ]),
    ("TMG301", "14", "Relation and Event Extraction", [
        ("Quan hệ từ dữ liệu và từ mẫu; sự kiện và vai", A, "webdemo/extraction.py", "/phong-lab#ie", ""),
    ]),
    ("TMG301", "15", "Time and Temporal Reasoning", [
        ("Nhận diện, chuẩn hóa biểu thức thời gian; mạch thời gian của bài", A, "webdemo/temporal.py", "/phong-lab#time", ""),
    ]),
    ("TMG301", "16", "Word Senses and WordNet", [
        ("WordNet thu nhỏ, Lesk, mở rộng nghĩa", A, "webdemo/semantics.py, webdemo/lexicon/wordnet.json", "/phong-lab#wsd", ""),
    ]),
    ("TMG301", "17", "Lexicons for Sentiment, Affect, and Connotation", [
        ("Từ điển cảm xúc, cảm xúc theo loại, hàm ý; kết hợp học máy", A, "webdemo/sentiment.py, webdemo/lexicon/*.json",
         "/phong-lab#sentiment", ""),
    ]),
]


def counts():
    c = {}
    for course, _, _, rows in COVERAGE:
        for _, st, _, _, _ in rows:
            c.setdefault(course, {}).setdefault(st, 0)
            c[course][st] += 1
    return c
