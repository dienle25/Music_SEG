from tokenizers import Tokenizer, models, trainers, pre_tokenizers
import pandas as pd
import sqlite3
import numpy as np
import re
import unicodedata
from collections import Counter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rank_bm25 import BM25Okapi

conn=sqlite3.connect(r'D:\preprocessing_SEG\music_seg_merged_preprocesing.db')   # thêm r'' để dấu \ ko bị hiểu thành ký tự đặc biệt
# Truyền biến kết nối vào tham số con
query = "SELECT * FROM songs"
df = pd.read_sql(query, con=conn)

######Step 1: Load và EDA######
print(df.info()) #cai nay mang laị thong tin ne
print(df.describe()) # lenh nay no ko mang lai gia trị nhieu trong database songs vì mình đang xử lí dưới dạng text
print('Số dòng, số cột:', df.shape)
print('Missing values:\n', df.isnull().sum())
# xóa thẳng những feature ko liên quan hoặc gây phiền vì nó có nhiều null quá
df.drop(columns=['id','album','genre','crawled_at'], inplace=True)
print(df.info())

# text cleaning vs normalazation
before = len(df)
df = df.dropna(subset=['lyrics'])                 # chỉ bắt buộc có lyrics
print('Xóa null:', before - len(df))

# xem trùng TRƯỚC khi xóa (task: có nên xóa trùng ko)
print('Dòng trùng hoàn toàn:', df.duplicated().sum())
print('Lyrics trùng:', df['lyrics'].duplicated().sum())
dup = df[df.duplicated(subset=['lyrics'], keep=False)]
print(dup.sort_values('lyrics')[['title','artist']].head(10))
print('Số nhóm lyrics trùng:', dup['lyrics'].nunique())
print('Nhóm lyrics trùng nhưng khác artist (bản cover):', (dup.groupby('lyrics')['artist'].nunique() > 1).sum())
# -> đa số là cùng 1 bài bị crawl nhiều link (bản concert, remaster...) nên xóa trùng là hợp lý,
#    nếu ko thì search ra 1 bài lặp lại nhiều lần. Đổi lại mất thông tin mấy bản cover của ca sĩ khác.

before = len(df)
df = df.drop_duplicates(subset=['lyrics'])        # trùng lời
print('Xóa trùng:', before - len(df))

# xóa gần trùng: cùng title + artist nhưng lời khác chút xíu (xuống dòng, dấu câu, viết hoa, bản bị thiếu lời)
# ko xóa thì search ra 1 bài 2-3 lần, vd "Mùa Mưa Ấy - Vũ." và "Mùa mưa ấy - Vũ"
df['title_key'] = df['title'].str.lower().str.replace(r"[^\w\s]", "", regex=True).str.strip()
df['artist_key'] = df['artist'].fillna('').str.lower().str.replace(r"[^\w\s]", "", regex=True).str.strip()
df['lyrics_len'] = df['lyrics'].str.len()
before = len(df)
df = df.sort_values('lyrics_len', ascending=False)          # bản lời dài nhất (đầy đủ nhất) lên đầu
df = df.drop_duplicates(subset=['title_key', 'artist_key'])  # giữ bản đầu tiên = bản dài nhất
df = df.sort_index()                                         # trả lại thứ tự cũ
df = df.drop(columns=['title_key', 'artist_key', 'lyrics_len'])
print('Xóa gần trùng (cùng title + artist):', before - len(df))

df = df.reset_index(drop=True)
print('Còn lại:', len(df))

# data ko có cột category nên dùng artist thay (task: số document mỗi nhóm)
print('Số ca sĩ:', df['artist'].nunique())
print(df['artist'].value_counts().head(10))


######Step 2: Text Cleaning và Normalization######
def clean_text(t):
    t = unicodedata.normalize("NFC", str(t))                  # chuẩn hóa dấu tiếng Việt (vài bài bị lưu dấu tách rời)
    t = re.sub(r"<[^>]+>", " ", t)                             # bỏ thẻ HTML
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)               # bỏ URL
    t = re.sub(r"\[[^\]\n]{0,40}\]", " ", t)                   # bỏ nhãn cấu trúc bài: [Chorus], [Verse 1], [ĐK:]...
    t = re.sub(r"(?im)^\s*(verse|pre-chorus|chorus|hook|bridge|rap|intro|outro|điệp khúc|đk)\s*\d*\s*:?\s*$", " ", t)  # dòng chỉ có nhãn "Verse 1:"
    t = re.sub(r"\s+", " ", t)                                 # gộp \n, tab, nhiều dấu cách thành 1 dấu cách
    return t.strip().lower()

df['clean_text'] = df['lyrics'].apply(clean_text)

# bản bỏ dấu câu, để riêng ko ghi đè clean_text
# lab dùng [^a-zA-Z0-9\s] nhưng vậy sẽ xóa luôn chữ có dấu (ơ, ư, đ...) → đổi thành [^\w\s]
df['clean_no_punct'] = (df['clean_text']
                        .str.replace(r"[^\w\s]", " ", regex=True)
                        .str.replace(r"\s+", " ", regex=True)
                        .str.strip())

# task: so sánh raw vs cleaned ít nhất 3 bài
for i in range(3):
    print('\n=====', df['title'].iloc[i], '=====')
    print('RAW:\n', df['lyrics'].iloc[i][:300])
    print('CLEANED:\n', df['clean_text'].iloc[i][:300])
    print('NO PUNCT:\n', df['clean_no_punct'].iloc[i][:300])

print('\nSố bài có URL:', df['lyrics'].str.contains(r"https?://|www\.").sum())
print('Số bài có nhãn [..]:', df['lyrics'].str.contains(r"\[[^\]\n]{0,40}\]").sum())
print('Số bài lỗi dấu (ko phải NFC):', (df['lyrics'] != df['lyrics'].apply(lambda t: unicodedata.normalize("NFC", t))).sum())
# Mất gì nếu bỏ số / gạch nối / nháy / dấu câu:
# - số: năm "1975", tên bài "3107"... → mất từ khóa search được
# - gạch nối, nháy: "m-tp", "let's", "don't" bị tách rời, sai từ
# - dấu câu: ít ảnh hưởng nghĩa nhưng "?" "!" mang cảm xúc
# → search lời bài hát: giữ số + dấu câu ở clean_text, chỉ bỏ HTML / URL / nhãn cấu trúc (ko phải lời hát)


######Step 3: Tokenization (BPE)######
texts = df['clean_text'].tolist()                 # train trên text ĐÃ clean

# 1–2. Khởi tạo + train
tokenizer = Tokenizer(models.BPE(unk_token="[UNK]"))
tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
trainer = trainers.BpeTrainer(vocab_size=3000, min_frequency=2,
                              special_tokens=["[UNK]", "[PAD]"])
tokenizer.train_from_iterator(texts, trainer)

tokenizer.save("bpe_lyrics.json")
# lần sau: tokenizer = Tokenizer.from_file("bpe_lyrics.json")

def tokenize_for_search(text):                   # dùng CHUNG cho bài hát và query
    return tokenizer.encode(clean_text(text)).tokens

df["tokens"] = [tokenizer.encode(t).tokens for t in texts]   # dạng chữ, để xem
df["ids"]    = [tokenizer.encode(t).ids    for t in texts]   # dạng số, để đưa vào model
print(df[["lyrics", "tokens","ids"]].head())

# task: 30 token đầu của 3 bài
for i in range(3):
    print(df['title'].iloc[i], '→', df['tokens'].iloc[i][:30])

# task: đếm token mỗi bài + so sánh trước/sau cleaning (cùng 1 tokenizer)
df['raw_tokens'] = df['lyrics'].apply(lambda t: tokenizer.encode(str(t).lower()).tokens)
df['clean_tokens'] = df['tokens']
df['n_tokens'] = df['clean_tokens'].apply(len)
print(df[['title', 'n_tokens']].head(10))
print('Mean raw token count:', df['raw_tokens'].apply(len).mean())
print('Mean cleaned token count:', df['clean_tokens'].apply(len).mean())
print(df['clean_tokens'].apply(len).describe())
print('Số token [UNK] (raw / cleaned):',
      sum(tokens.count('[UNK]') for tokens in df['raw_tokens']), '/',
      sum(tokens.count('[UNK]') for tokens in df['clean_tokens']))
# raw nhiều token hơn cleaned chủ yếu do: nhãn [Chorus]/[Verse 1] (mỗi nhãn ra 3-5 token), URL bị chẻ vụn,
# và bài lỗi dấu (ko NFC) → BPE ko nhận ra chữ nên chẻ nhỏ / ra [UNK].
# Còn \n thì ko ảnh hưởng vì pre_tokenizer Whitespace đã tách theo khoảng trắng rồi.


######Step 4: Stop-word Removal######
# NLTK ko có stopword tiếng Việt → tự liệt kê hư từ tiếng Việt + vài từ tiếng Anh / từ đệm hay gặp trong lời bài hát
# CỐ Ý ko bỏ: đại từ (anh, em, ta, mình) và từ phủ định (không, chẳng, chưa...) vì query hay dùng
stop_words = set([
    "là", "và", "của", "thì", "mà", "những", "các", "cái", "một", "có", "được", "cho", "với",
    "này", "đó", "kia", "ấy", "rồi", "đã", "đang", "sẽ", "cũng", "vẫn", "lại", "nữa", "như",
    "nhưng", "nên", "vì", "để", "khi", "trong", "ở", "từ", "tại", "bị", "do", "thế", "vậy",
    "à", "ơi", "nhé", "nha", "ạ", "hả", "hoặc", "chỉ", "rất", "quá", "lắm",
    "i", "you", "me", "my", "the", "a", "to", "and", "is", "it", "in", "of",
    "oh", "yeah", "ah", "uh", "ooh", "hey", "yah", "huh", "hah", "eh", "woah",
])
negations = set(["không", "chẳng", "chả", "chưa", "đừng"])

# xem token hay gặp nhất để kiểm tra lại list stopword
token_freq = Counter(t for tokens in df['clean_tokens'] for t in tokens)
print('30 token hay gặp nhất:', token_freq.most_common(30))

# Experiment A: chỉ bỏ stop words
df['tokens_no_stop'] = df['clean_tokens'].apply(
    lambda tokens: [t for t in tokens if t not in stop_words]
)
# Experiment B: bỏ stop words + token ko phải chữ/số (dấu câu)
df['tokens_no_stop_alnum'] = df['clean_tokens'].apply(
    lambda tokens: [t for t in tokens if t.isalnum() and t not in stop_words]
)

print('Original:', df['clean_tokens'].iloc[0][:30])
print('Stop words removed:', df['tokens_no_stop'].iloc[0][:30])
print('Stop words + non-alphanumeric removed:', df['tokens_no_stop_alnum'].iloc[0][:30])

before = sum(len(tokens) for tokens in df['clean_tokens'])
after_a = sum(len(tokens) for tokens in df['tokens_no_stop'])
after_b = sum(len(tokens) for tokens in df['tokens_no_stop_alnum'])
print(f"Token reduction A (stop words): {(before - after_a) / before * 100:.2f}%")
print(f"Token reduction B (stop words + dấu câu): {(before - after_b) / before * 100:.2f}%")
# phần chênh lệch B - A chính là phần do bỏ dấu câu → tách riêng được ảnh hưởng của từng bước

# task: bỏ từ phủ định có làm đổi nghĩa ko
sample = "anh không còn yêu em nữa"
sample_tokens = tokenize_for_search(sample)
print('Câu gốc:', sample_tokens)
print('Bỏ stop words:', [t for t in sample_tokens if t not in stop_words])
print('Bỏ luôn phủ định:', [t for t in sample_tokens if t not in stop_words | negations])
# → còn "anh còn yêu em": nghĩa bị đảo ngược → KO được cho từ phủ định vào stop word
# stop word phải test chứ ko mặc định là tốt hơn: vd query "người ấy" bỏ stop word chỉ còn "người",
# mà BM25 / TF-IDF đã tự giảm trọng số từ phổ biến qua IDF rồi


######Step 5: Stemming vs Lemmatization → BỎ QUA######
# Tiếng Việt là ngôn ngữ đơn lập: từ ko biến đổi hình thái (ko có -s, -ed, -ing...),
# "yêu" lúc nào cũng là "yêu" → Porter Stemmer / WordNet Lemmatizer (làm cho tiếng Anh) ko áp dụng được.
# Token BPE lại là mảnh subword chứ ko phải từ trọn vẹn → càng ko stem được.
# Thay vào đó Step 7 so sánh kích thước vocab BPE (1000 / 3000 / 8000) xem độ "chẻ" token ảnh hưởng search ra sao.


######Step 6: Search engine: TF-IDF + cosine và BM25######
docs = df["tokens"].tolist()

# TF-IDF + cosine
tfidf = TfidfVectorizer(analyzer=lambda x: x)    # nhận thẳng list token
X_tfidf = tfidf.fit_transform(docs)
print("Vocab size:", len(tfidf.vocabulary_))

# BM25
bm25 = BM25Okapi(docs, k1=1.5, b=0.75)

def search(query, method="bm25", top_k=5):
    q = tokenize_for_search(query)
    if method == "tfidf":
        scores = cosine_similarity(tfidf.transform([q]), X_tfidf).ravel()
    else:
        scores = bm25.get_scores(q)
    top = np.argsort(scores)[::-1][:top_k]
    return [{"index": int(i), "score": round(float(scores[i]), 4),
             "title": df.iloc[i]["title"], "artist": df.iloc[i]["artist"]} for i in top]

queries = ["mưa buồn", "hà nội mùa thu", "anh nhớ em", "baby"]
for q in queries:
    print("\nQUERY:", q, "→", tokenize_for_search(q))
    for m in ["tfidf", "bm25"]:
        print(f"  {m}:")
        for rank, r in enumerate(search(q, m, 3), 1):
            print("   ", rank, r["score"], r["title"], "-", r["artist"])
# điểm TF-IDF (cosine 0 → 1) và BM25 (ko giới hạn) khác thang đo → chỉ so thứ hạng, ko so điểm trực tiếp

# task: query có từ ko nằm trong vocab
oov = "blockchain metaverse"
print("\nQUERY OOV:", oov, "→", tokenize_for_search(oov))
for rank, r in enumerate(search(oov, "bm25", 3), 1):
    print("   ", rank, r["score"], r["title"], "-", r["artist"])
# BPE ko bị OOV hoàn toàn: từ lạ bị chẻ thành mảnh nhỏ / ký tự → vẫn ra kết quả nhưng là khớp "rác" theo mảnh.
# Nếu tokenizer theo từ thì query này ra điểm 0 hết.


######Step 6.5: Precision@K và Recall@K######
def precision_at_k(ranked_ids, relevant_ids, k=5):
    top_k_ids = ranked_ids[:k]
    return len(set(top_k_ids) & set(relevant_ids)) / k

def recall_at_k(ranked_ids, relevant_ids, k=5):
    if not relevant_ids:
        return None          # ko có bài liên quan → recall ko xác định, KO tính là 0
    top_k_ids = ranked_ids[:k]
    return len(set(top_k_ids) & set(relevant_ids)) / len(relevant_ids)

# Gán nhãn liên quan: lý tưởng là tự đọc rồi gán bằng tay cho từng query.
# Ở đây dùng nhãn TẠM (proxy): bài "liên quan" = title hoặc lyrics chứa NGUYÊN CỤM query
# → ghi rõ trong báo cáo là nhãn tự động, và nên kiểm tra lại bằng tay mấy bài top-5 in ra bên dưới
def judge_relevant(q):
    pattern = r"\b" + re.escape(clean_text(q)) + r"\b"
    mask = (df['clean_text'].str.contains(pattern, regex=True)
            | df['title'].apply(clean_text).str.contains(pattern, regex=True))
    return set(df.index[mask])

eval_queries = queries + ["quê hương", "người yêu cũ"]
relevant_by_query = {q: judge_relevant(q) for q in eval_queries}

K = 5
for q in eval_queries:
    rel = relevant_by_query[q]
    print(f"\nQUERY: {q} | số bài liên quan: {len(rel)}")
    if not rel:
        print('  → ko có bài liên quan, bỏ qua query này (recall ko xác định)')
        continue
    for m in ["tfidf", "bm25"]:
        results = search(q, m, K)
        ranked = [r["index"] for r in results]
        print(f"  {m}: P@{K} = {precision_at_k(ranked, rel, K):.2f} | R@{K} = {recall_at_k(ranked, rel, K):.3f}")
        for r in results:
            print("     ", "[+]" if r["index"] in rel else "[ ]", r["title"], "-", r["artist"])
# R@5 thấp với query phổ biến ("baby" có hàng trăm bài liên quan) vì top 5 tối đa chỉ lấy được 5 bài


######Step 7: So sánh các chiến lược tiền xử lý######
# train thêm 2 tokenizer BPE vocab nhỏ / lớn để thay cho stemming / lemmatization
tokenizers_by_size = {3000: tokenizer}
for size in [1000, 8000]:
    tok = Tokenizer(models.BPE(unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.train_from_iterator(texts, trainers.BpeTrainer(vocab_size=size, min_frequency=2,
                                                       special_tokens=["[UNK]", "[PAD]"]))
    tokenizers_by_size[size] = tok

def preprocess_tokens(text, strategy="baseline"):
    if strategy == "bpe_1000":
        return tokenizers_by_size[1000].encode(clean_text(text)).tokens
    if strategy == "bpe_8000":
        return tokenizers_by_size[8000].encode(clean_text(text)).tokens

    tokens = tokenize_for_search(text)           # BPE 3000
    if strategy == "baseline":
        return tokens
    if strategy == "no_stopwords":
        return [t for t in tokens if t not in stop_words]
    if strategy == "no_stop_alnum":
        return [t for t in tokens if t.isalnum() and t not in stop_words]
    raise ValueError(f"Unknown strategy: {strategy}")

strategies = ["baseline", "no_stopwords", "no_stop_alnum", "bpe_1000", "bpe_8000"]

def build_strategy_models(strategy):
    tokenized_docs = [preprocess_tokens(t, strategy) for t in df['lyrics']]
    vectorizer = TfidfVectorizer(analyzer=lambda x: x)
    matrix = vectorizer.fit_transform(tokenized_docs)
    bm25_model = BM25Okapi(tokenized_docs, k1=1.5, b=0.75)
    return {"tokenized_docs": tokenized_docs, "vectorizer": vectorizer,
            "tfidf_matrix": matrix, "bm25": bm25_model}

# đặt tên strategy_models chứ KO đặt "models" như lab, vì sẽ đè mất models của thư viện tokenizers
strategy_models = {s: build_strategy_models(s) for s in strategies}

for s, model in strategy_models.items():
    print(s, "| vocabulary size:", len(model["vectorizer"].vocabulary_),
          "| TF-IDF matrix shape:", model["tfidf_matrix"].shape)

def search_with_strategy(q, strategy, method="tfidf", top_k=5):
    model = strategy_models[strategy]
    query_tokens = preprocess_tokens(q, strategy)   # query xử lý GIỐNG HỆT bài hát
    if method == "tfidf":
        scores = cosine_similarity(model["vectorizer"].transform([query_tokens]), model["tfidf_matrix"]).ravel()
    elif method == "bm25":
        scores = model["bm25"].get_scores(query_tokens)
    else:
        raise ValueError("method must be 'tfidf' or 'bm25'")
    top = np.argsort(scores)[::-1][:top_k]
    return [{"index": int(i), "score": round(float(scores[i]), 4),
             "title": df.iloc[i]["title"]} for i in top]

# task: so sánh Top-5 ID giữa các chiến lược (đổi q để xem query khác)
q = eval_queries[0]
print("\nTop-5 ID cho query:", q)
for s in strategies:
    for m in ["tfidf", "bm25"]:
        print(f"  {s:14} / {m:5}:", [r["index"] for r in search_with_strategy(q, s, m, 5)])


######Step 8: Bảng kết quả######
base_total = sum(len(tokens) for tokens in strategy_models["baseline"]["tokenized_docs"])
rows = []
for s in strategies:
    model = strategy_models[s]
    total = sum(len(tokens) for tokens in model["tokenized_docs"])
    for m in ["tfidf", "bm25"]:
        p_list, r_list = [], []
        for q in eval_queries:
            rel = relevant_by_query[q]
            if not rel:                          # query ko có nhãn → ko tính
                continue
            ranked = [r["index"] for r in search_with_strategy(q, s, m, K)]
            p_list.append(precision_at_k(ranked, rel, K))
            r_list.append(recall_at_k(ranked, rel, K))
        rows.append({
            "Strategy": s,
            "Method": m,
            "Vocabulary Size": len(model["vectorizer"].vocabulary_),
            "Token Reduction (%)": round((base_total - total) / base_total * 100, 2),   # so với baseline
            f"MeanP@{K}": round(np.mean(p_list), 4),
            f"MeanR@{K}": round(np.mean(r_list), 4),
        })

result_df = pd.DataFrame(rows)
print(result_df.to_string(index=False))
result_df.to_csv("ket_qua_lab.csv", index=False, encoding="utf-8-sig")   # utf-8-sig để Excel đọc được tiếng Việt
# Token Reduction âm (bpe_1000) = nhiều token hơn baseline vì vocab nhỏ → chẻ vụn hơn
# vocab nhỏ hơn KO có nghĩa là search tốt hơn → nhìn cột MeanP@5 / MeanR@5 để kết luận
# khi so preprocessing thì giữ cố định method; khi so TF-IDF vs BM25 thì giữ cố định strategy


######Step 9: Câu hỏi thảo luận (ghi vào báo cáo)######
# 1. Bỏ stop word có luôn tốt hơn? Ko. IDF đã giảm trọng số từ phổ biến; bỏ sai từ (phủ định, "người ấy")
#    làm đổi nghĩa / query rỗng → phải đo bằng P@K, R@K.
# 2. Stemming vs lemmatization: stemming cắt hậu tố theo luật, nhanh, có thể ra từ ko có thật;
#    lemmatization tra từ điển + từ loại ra từ gốc thật, chậm hơn. Tiếng Việt ko cần cả 2.
# 3. 2 chiến lược ra top khác nhau vì token khác → TF, DF, IDF, độ dài bài đều khác → điểm khác.
# 4. BM25 xử lý độ dài bài bằng b * |d| / avgdl: bài dài bị giảm điểm (ko thắng chỉ vì nhiều chữ);
#    k1 làm TF bão hòa (từ lặp 20 lần ko gấp 20 lần điểm).
# 5. Ko so điểm BM25 với cosine trực tiếp: khác thang đo (cosine 0-1, BM25 ko giới hạn) → so thứ hạng + P@K.
# 6. Query có từ ngoài vocab: tokenizer theo từ → bỏ qua, điểm 0; BPE → chẻ thành mảnh, vẫn khớp nhưng nhiễu.
# 7. Bigram: tiếng Việt tách theo âm tiết nên "hà nội" thành "hà" + "nội" → thêm cặp token liền kề
#    ("hà nội", "mùa thu") làm token mới thì khớp đúng cụm, ko khớp bài chỉ có "hà" hoặc "nội" riêng lẻ.
# 8. Cần nhãn liên quan do người gán cho từng query (nhiều người gán càng tốt), ko dùng nhãn tự động như Step 6.5.