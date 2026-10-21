# BBC Text Preprocessing Lab

## Chạy nhanh
1. Cài Python 3.10+.
2. Mở terminal tại thư mục này.
3. Cài thư viện: `pip install pandas numpy scikit-learn nltk`
4. Chạy: `python text_preprocessing_bbc_lab.py --data bbc-text.csv --out results`

Các kết quả mẫu từ lần chạy hiện tại nằm ngay trong thư mục gốc:
- `vocabulary_comparison.csv`
- `retrieval_top5_results.csv`
- `cleaning_samples.csv`
- `report.md`

Lưu ý: báo cáo phân biệt rõ kết quả đã chạy và các giới hạn của môi trường offline. Precision/Recall cần tự gán tài liệu liên quan cho từng truy vấn; không nên tự tạo metric khi chưa có relevance judgments.
