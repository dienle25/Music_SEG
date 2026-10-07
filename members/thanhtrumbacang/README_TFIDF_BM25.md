# NhacCuaTui — TF-IDF + BM25 Score Ranking

## Mục tiêu
Tiền xử lý 300 bài hát trong `music.db` để phục vụ tìm kiếm/xếp hạng theo:
- TF-IDF cosine similarity
- BM25
- Hybrid Score = 0.5 * TF-IDF + 0.5 * BM25

Điểm TF-IDF và BM25 đều được chuẩn hóa theo max score của từng query trước khi kết hợp.

## Dữ liệu
Bảng gốc `songs` giữ nguyên. Bảng `songs_ranked` bổ sung:
- `clean_lyrics`: lyrics đã loại bỏ một số text giao diện crawl.
- `search_text`: văn bản chuẩn hóa dùng cho tìm kiếm.
- `doc_length`: độ dài document.

`genre` hiện đang trống trong DB gốc nên không ảnh hưởng ranking.

## Trọng số trường
- title: x3
- artist: x2
- lyrics: x1

Điều này giúp khi tìm tên bài hát/nghệ sĩ, kết quả phù hợp được ưu tiên hơn các từ chỉ xuất hiện trong lyric.

## Công thức
BM25:
`score(D,Q) = Σ IDF(t) * [tf*(k1+1)] / [tf + k1*(1-b+b*|D|/avgdl)]`

với:
- k1 = 1.5
- b = 0.75

Hybrid:
`Score = 0.5 * TFIDF_norm + 0.5 * BM25_norm`

## Chạy tìm kiếm
```bash
pip install -r requirements_ranking.txt
python ranking/ranker.py "yêu em" --top-k 10
```

Có thể thay trọng số:
```bash
python ranking/ranker.py "quang hùng" --top-k 10 --tfidf-weight 0.6 --bm25-weight 0.4
```

## Ý nghĩa cho project web nhạc
Pipeline này phù hợp để xây search box:
1. User nhập từ khóa.
2. Query được chuẩn hóa.
3. TF-IDF đo độ tương đồng ngữ nghĩa/từ vựng.
4. BM25 đo mức độ phù hợp theo tần suất và độ dài document.
5. Hai score được chuẩn hóa và cộng thành Hybrid Score.
6. Sắp xếp giảm dần để trả về Top-K bài hát.

Lưu ý: BM25/TF-IDF là query-dependent, vì vậy không nên tạo một "score ranking" cố định cho 300 bài khi chưa có query.
