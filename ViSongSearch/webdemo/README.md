# Web demo ViSongSearch (SEG301 + TMG301)

Một web demo chung cho cả hai môn: máy tìm kiếm bài hát tiếng Việt khi chỉ nhớ lời, nhớ sai hoặc chỉ mô tả được bài hát,
cộng các trang cho thấy từng kỹ thuật trong slide chạy trên chính kho bài hát của nhóm.

Giao diện tìm kiếm lấy từ dự án công khai [typesense/showcase-songs-search](https://github.com/typesense/showcase-songs-search)
(Apache-2.0, InstantSearch.js + Bootstrap) và được chỉnh lại: thay Typesense bằng bộ máy tìm kiếm của nhóm (FastAPI),
giao diện tiếng Việt, thêm bộ lọc tâm trạng / chủ đề / thời gian, chọn mô hình xếp hạng và bảng "Vì sao ra kết quả này?".

## Chạy trên Windows

1. Cài thư viện (một lần): `python -m pip install fastapi uvicorn jinja2 numpy scipy scikit-learn datasketch`
   (đã có sẵn nếu bạn từng chạy `pip install -r requirements.txt` của dự án).
2. Bấm đúp `run_demo.bat` trong thư mục `ViSongSearch`, hoặc mở cửa sổ lệnh tại thư mục đó và gõ:

```
python -m webdemo
```

Demo tự tìm file `music_seg_merged_preprocesing.db` ở `ViSongSearch\data`, `ViSongSearch\visong`, `ViSongSearch`
hoặc `Downloads\ViSongSearch_source_latex\visong`. File để chỗ khác thì chỉ đường dẫn:

```
python -m webdemo --db "C:\duong\dan\music_seg_merged_preprocesing.db"
```

Lần đầu mất khoảng 1 phút để dựng chỉ mục (lưu vào `webdemo\_runtime`), các lần sau khoảng 10-15 giây.
Trình duyệt tự mở http://127.0.0.1:8000. Phần đánh giá chạy nền vài phút, xem ở trang Đánh giá.

Tùy chọn: `--port 8001` (đổi cổng), `--no-browser`, `--no-eval` (không chạy đánh giá), `--rebuild` (dựng lại chỉ mục).

## Các trang

| Trang | Nội dung | Môn |
|---|---|---|
| `/` Tìm kiếm | 11 mô hình xếp hạng, bộ lọc, gom bản trùng, nhóm kết quả, gợi ý truy vấn, bảng giải thích từng bước | SEG 1-7, 10, 12; TMG 01-04, 11-17 |
| `/bai-hat/...` | Trang bài hát chuẩn SEO (title, meta, H1, canonical, Open Graph, JSON-LD), tự chấm 13 tiêu chí SEO; cảm xúc, thời gian, thực thể, bài tương tự, thẻ | SEG 8, 11; TMG 05, 14, 15, 17 |
| `/tro-ly` | Trợ lý hội thoại theo khung: NLU, theo dõi trạng thái, chính sách, sinh câu | TMG 10 |
| `/phong-lab` | 18 ô thử từng kỹ thuật: khoảng cách sửa, kênh nhiễu, n-gram, phục hồi dấu, Naive Bayes, logistic, PPMI-SVD, phân cụm, self-attention, masked LM, PCFG/CKY, phụ thuộc, dạng logic, trích xuất, thời gian, WordNet/Lesk, chỉ mục và nén, DAAT/TAAT, Boolean, liên kết từ | TMG 01-17; SEG 5-7 |
| `/danh-gia` | So sánh 12 mô hình (MRR@10, S@1, R@10, NDCG@10, MAP, kiểm định ý nghĩa), dò trọng số trên dev, độ bền theo nhiễu, P-R cho truy vấn ca sĩ, P/R/F1 + ma trận nhầm lẫn, BLEU, silhouette, MinHash, nén chỉ mục, kết quả bài báo | SEG 3, 5, 9, 10, 12; TMG 02-06, 09 |
| `/thong-ke` | Nhật ký truy vấn, CTR theo vị trí, xen kẽ (interleaving), PageRank, HITS, cộng đồng ca sĩ, chấm SEO nhiều trang | SEG 4, 6, 8, 9, 11; TMG 14 |
| `/the` | Thẻ người dùng, đám mây thẻ, suy thẻ cho bài tương tự | SEG 11 |
| `/doi-chieu` | Bảng đối chiếu từng bài giảng của hai môn với mã nguồn và trang demo | cả hai |

`/sitemap.xml`, `/robots.txt` và `/api/docs` (tài liệu API) cũng có sẵn.

## Gợi ý trình bày (khoảng 10 phút)

1. Trang Tìm kiếm: bấm lần lượt các ví dụ Q1 → Q5 phía dưới ô tìm kiếm; mở "Vì sao ra kết quả này?" để thầy thấy loại truy vấn,
   phục hồi dấu, sửa chính tả, cây cú pháp, dạng logic và trọng số trộn. Đổi "Mô hình" sang BM25 / Boolean / LSA để so sánh.
2. Mở một bài hát: chỉ phần chấm SEO, dữ liệu cấu trúc và phân tích cảm xúc / thời gian.
3. Trợ lý: gõ "bài hát buồn về mùa thu", trả lời câu hỏi của trợ lý, xem bảng NLU / DST / policy bên phải.
4. Phòng lab: chọn vài ô theo slide thầy hỏi (bảng quy hoạch động, CKY, Lesk, self-attention...).
5. Đánh giá: bảng 12 mô hình, đường độ bền theo nhiễu, ma trận nhầm lẫn, BLEU.
6. Đối chiếu môn học: bảng tổng hợp bài giảng nào dùng ở đâu.

## Mã nguồn

- `core.py`: đọc file .db, dựng chỉ mục và các mô hình, xử lý truy vấn, xếp hạng, trả kết quả dạng InstantSearch.
- `web.py`: máy chủ FastAPI và các trang; `templates/`, `static/`: giao diện.
- `evaluation.py`: đánh giá tự động chạy nền.
- Từng kỹ thuật: `spelling.py`, `langmodel.py`, `sentiment.py`, `semantics.py`, `grammar.py`, `temporal.py`, `extraction.py`,
  `graph.py`, `indexing.py`, `retrieval.py`, `clustering.py`, `snippets.py`, `seo.py`, `store.py`, `dialog.py`; từ điển ở `lexicon/`.
- Dùng lại các mô-đun của bài báo trong `visong/` (làm sạch, MinHash, BM25, phục hồi dấu, phân loại truy vấn, sinh truy vấn).
- `coverage.py`: bảng đối chiếu ở trang `/doi-chieu`.

## Xử lý lỗi thường gặp

- "Thiếu thư viện": chạy lệnh pip mà demo in ra.
- "Không tìm thấy file .db": chép file vào `ViSongSearch\data\` hoặc dùng `--db`.
- Cổng 8000 đang bận: demo tự chuyển sang cổng trống kế tiếp và in địa chỉ ra cửa sổ lệnh.
- Sửa file .db: demo tự nhận ra và dựng lại chỉ mục ở lần chạy sau.

Giấy phép của phần giao diện mượn: xem `NOTICE` và `static/vendor/LICENSE-*.txt`.
