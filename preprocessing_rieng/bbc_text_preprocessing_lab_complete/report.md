# Báo cáo Assignment — Text Preprocessing and Search Engine Lab

## 1. Mục tiêu
Thực hiện các bước tiền xử lý văn bản trên BBC News dataset, sau đó so sánh hai phương pháp truy hồi thông tin: TF-IDF kết hợp cosine similarity và BM25. Các phép so sánh được thực hiện trên cùng bộ truy vấn và cùng chiến lược tiền xử lý.

## 2. Môi trường và dữ liệu
- Dataset: `bbc-text.csv`
- Số dòng ban đầu: **2225**
- Số cột: **2** (`category`, `text`)
- Giá trị thiếu ở cột `text`: **0**
- Dòng trùng lặp hoàn toàn: **99**
- Bài viết trùng văn bản: **99**
- Văn bản trùng nhưng khác nhãn category: **0**
- Sau khi loại bài viết trùng văn bản: **2126 tài liệu**
- Số tài liệu theo category ban đầu:
  - sport: 511
  - business: 510
  - politics: 417
  - tech: 401
  - entertainment: 386

**Quyết định xử lý trùng lặp:** Dataset có 99 văn bản trùng, và kiểm tra cho thấy không có trường hợp văn bản trùng nhưng category khác nhau. Vì vậy, các bài viết trùng văn bản được loại khỏi tập truy hồi để tránh một nội dung lặp lại xuất hiện nhiều lần trong kết quả. Báo cáo vẫn ghi rõ số liệu ban đầu trước khi loại trùng.

## 3. Làm sạch và chuẩn hóa văn bản
Các thao tác được dùng:
1. Xóa HTML tags.
2. Xóa URL.
3. Chuẩn hóa khoảng trắng.
4. Chuyển chữ về lowercase.

Không xóa dấu câu và số trong bước cleaning cơ bản vì chúng có thể mang thông tin, ví dụ năm, tỷ lệ, số tiền hoặc cách viết tên riêng. Phiên bản xóa dấu câu chỉ nên được xem như một thí nghiệm riêng. Kết quả mẫu raw/clean được lưu ở `cleaning_samples.csv`.

## 4. Tokenization
Văn bản được tách thành token bằng biểu thức chính quy để script có thể chạy trong môi trường không có kết nối tải tài nguyên NLTK. Cách tách này giữ các cụm chữ/số và một số dạng từ có apostrophe hoặc dấu gạch nối. Đây là một khác biệt triển khai so với `nltk.word_tokenize` được gợi ý trong handout.

- Tổng token sau cleaning, trước lọc stop words: **828,397**
- Tổng token sau stop-word removal: **446,635**
- Mức giảm token: **46.08%**

## 5. Stop-word removal
Sử dụng danh sách stop words tiếng Anh của scikit-learn để không cần tải corpus NLTK. Tổng token giảm khoảng **46.08%**. Loại stop words có thể giảm nhiễu và kích thước biểu diễn, nhưng không phải lúc nào cũng tốt: các từ như `not` có thể thay đổi ý nghĩa. Trong một hệ thống tìm kiếm, nên thử nghiệm có và không loại stop words rồi đánh giá kết quả thay vì mặc định rằng loại bỏ luôn tốt hơn.

## 6. Stemming và lemmatization
- **Stemming:** dùng Porter Stemmer; áp dụng quy tắc cắt từ, kết quả có thể không phải từ điển hợp lệ.
- **Lemmatization:** trong lần chạy này dùng bộ quy tắc hình thái đơn giản làm fallback offline. Đây **không phải** lemmatization đầy đủ dựa trên WordNet/POS như phần hướng dẫn NLTK; vì vậy kết quả lemmatized chỉ có tính minh họa và cần chạy lại bằng WordNet + POS tagging trong môi trường có tài nguyên NLTK để báo cáo học thuật chính thức.

## 7. TF-IDF + cosine similarity và BM25
Bốn truy vấn thử nghiệm theo handout:
- `digital video recorders`
- `WorldCom accounting fraud`
- `UK political election`
- `football championship`

TF-IDF biểu diễn tài liệu và truy vấn thành vector có trọng số; cosine similarity xếp hạng theo độ gần hướng giữa hai vector. BM25 tính điểm dựa trên tần suất term, IDF và chuẩn hóa độ dài tài liệu. Trong script này BM25 được cài trực tiếp theo dạng công thức Okapi với `k1=1.5`, `b=0.75`, để không phụ thuộc gói `rank-bm25` trong môi trường offline.

Điểm TF-IDF cosine và BM25 nằm trên thang đo khác nhau, vì vậy không so sánh trực tiếp độ lớn điểm số; cần so sánh thứ tự tài liệu và metric đánh giá.

### Ví dụ Top-3 với baseline
| query                     | method   |   rank |   doc_id | category   |     score |
|:--------------------------|:---------|-------:|---------:|:-----------|----------:|
| digital video recorders   | tfidf    |      1 |      261 | tech       |  0.272459 |
| digital video recorders   | tfidf    |      2 |      886 | tech       |  0.177263 |
| digital video recorders   | tfidf    |      3 |     1166 | tech       |  0.168925 |
| digital video recorders   | bm25     |      1 |      886 | tech       | 17.4007   |
| digital video recorders   | bm25     |      2 |     1166 | tech       | 14.6624   |
| digital video recorders   | bm25     |      3 |      783 | tech       | 13.2353   |
| WorldCom accounting fraud | tfidf    |      1 |     1764 | business   |  0.588346 |
| WorldCom accounting fraud | tfidf    |      2 |        1 | business   |  0.479193 |
| WorldCom accounting fraud | tfidf    |      3 |     1207 | business   |  0.404071 |
| WorldCom accounting fraud | bm25     |      1 |     1764 | business   | 27.1431   |
| WorldCom accounting fraud | bm25     |      2 |        1 | business   | 25.9593   |
| WorldCom accounting fraud | bm25     |      3 |     1524 | business   | 24.956    |
| UK political election     | tfidf    |      1 |     1491 | politics   |  0.250643 |
| UK political election     | tfidf    |      2 |      958 | politics   |  0.232583 |
| UK political election     | tfidf    |      3 |     1983 | tech       |  0.230484 |
| UK political election     | bm25     |      1 |      980 | politics   | 10.5705   |
| UK political election     | bm25     |      2 |     1521 | politics   | 10.4308   |
| UK political election     | bm25     |      3 |     1358 | politics   |  9.6148   |
| football championship     | tfidf    |      1 |     1422 | sport      |  0.189243 |
| football championship     | tfidf    |      2 |      580 | tech       |  0.14361  |
| football championship     | tfidf    |      3 |     1651 | sport      |  0.124297 |
| football championship     | bm25     |      1 |     1422 | sport      | 10.5389   |
| football championship     | bm25     |      2 |      580 | tech       |  7.95851  |
| football championship     | bm25     |      3 |      765 | sport      |  7.53492  |

Kết quả Top-5 đầy đủ theo truy vấn, chiến lược và phương pháp được lưu ở `retrieval_top5_results.csv`.

## 8. So sánh chiến lược tiền xử lý
| Strategy | Vocabulary size | Total tokens |
|---|---:|---:|
| baseline | 32,329 | 828,397 |
| no_stopwords | 32,033 | 446,635 |
| stemmed | 23,280 | 446,635 |
| lemmatized | 26,462 | 446,635 |

Stemming làm giảm vocabulary rõ hơn trong lần chạy này. Tuy nhiên, vocabulary nhỏ hơn **không đồng nghĩa** truy hồi tốt hơn: stemming có thể gộp các từ khác nghĩa hoặc tạo stem khó đọc; loại stop words có thể bỏ mất tín hiệu hữu ích. Cần kiểm tra Top-5 và đánh giá relevance theo từng truy vấn.

## 9. Precision@5 và Recall@5
Handout yêu cầu xác định thủ công các tài liệu liên quan cho từng truy vấn. Nhãn `category` chỉ là nhãn chủ đề rộng, không đủ để khẳng định một tài liệu cụ thể có liên quan tới truy vấn cụ thể hay không. Do đó, báo cáo này **không tự tạo Precision@5/Recall@5** khi chưa có relevance judgments đã được kiểm tra.

Để hoàn thành phần metric:
1. Mở `retrieval_top5_results.csv`.
2. Với mỗi truy vấn, đọc các tài liệu được xếp hạng và lập tập `relevant_doc_ids` dựa trên nội dung thật.
3. Dùng cùng tập nhãn liên quan để đánh giá cả TF-IDF và BM25, và giữ cố định cùng strategy khi so sánh hai ranking methods.
4. Báo cáo Precision@5 = số tài liệu liên quan trong Top-5 / 5; Recall@5 = số tài liệu liên quan trong Top-5 / tổng tài liệu liên quan đã biết.

## 10. Trả lời câu hỏi thảo luận
1. **Stop-word removal có luôn cải thiện chất lượng tìm kiếm không?** Không. Nó giảm từ phổ biến nhưng có thể loại mất từ phủ định hoặc từ mang ý nghĩa trong một truy vấn cụ thể.
2. **Stemming khác lemmatization thế nào?** Stemming cắt từ theo quy tắc; lemmatization tìm dạng từ vựng chuẩn và thường cần thông tin từ loại.
3. **Vì sao chiến lược tiền xử lý đổi Top-ranked documents?** Vì nó thay đổi token, vocabulary và trọng số, nên độ tương đồng/điểm BM25 thay đổi.
4. **BM25 xử lý độ dài tài liệu ra sao?** Dùng thành phần chuẩn hóa dựa trên độ dài tài liệu và độ dài trung bình; tài liệu dài không tự động được lợi chỉ vì có nhiều từ.
5. **Vì sao không so sánh trực tiếp điểm BM25 và cosine?** Công thức và thang điểm khác nhau; nên so sánh thứ hạng hoặc metric chung.
6. **Nếu truy vấn có từ ngoài vocabulary thì sao?** TF-IDF không có đặc trưng cho từ đó, nên từ này không đóng góp vào vector truy vấn. BM25 cũng không cộng điểm cho term không có trong corpus.
7. **Bigram có thể giúp gì?** Bigram giữ cụm từ như `prime minister` hoặc `video recorder` như một đơn vị, giúp thể hiện ý nghĩa cụm từ; cần thử `ngram_range=(1,2)`.
8. **Cần gì để đánh giá đúng chất lượng truy hồi?** Các relevance judgments theo từng truy vấn, được gán dựa trên nội dung tài liệu, không chỉ dựa vào category.

## 11. Giới hạn
- Bộ tách token regex không hoàn toàn tương đương `nltk.word_tokenize`.
- Stop words lấy từ scikit-learn thay vì NLTK.
- Lemmatization là fallback đơn giản, không thay thế WordNet/POS tagging.
- BM25 được cài theo công thức Okapi trực tiếp; điểm có thể không trùng hoàn toàn với implementation `rank-bm25`.
- Precision/Recall chưa báo cáo vì cần relevance judgments đã được xác nhận.

## 12. Files đầu ra
- `text_preprocessing_bbc_lab.py`: script thực thi các bước và tạo bảng kết quả.
- `vocabulary_comparison.csv`: vocabulary size và số token theo strategy.
- `retrieval_top5_results.csv`: Top-5 cho mọi truy vấn/strategy/method.
- `cleaning_samples.csv`: ví dụ raw/clean/token.
- `report.md`: báo cáo này.
