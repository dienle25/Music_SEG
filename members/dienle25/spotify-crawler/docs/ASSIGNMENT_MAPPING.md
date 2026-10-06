# Đối chiếu source of truth

Đã đọc nội dung trích xuất của cả 26 trang file `21_09_2026___755bde41-6ce6-4515-8cda-704b57491359.pdf` (692.032 bytes). Tên tài liệu bên trong: `2026-09-09-seg301_04_crawls_and_feeds_practice.md`; bản xuất PDF ghi 21/09/2026. Không đưa nguyên PDF của lớp vào repository công khai. SHA256 nguồn được lưu trong `evidence/source_provenance.json`.

| Yêu cầu đề và trang | Hiện thực/bằng chứng | Đánh giá |
|---|---|---|
| Python, Requests, BeautifulSoup, SQLite (tr. 1) | Source và requirements.txt, runtime Python 3.12.10 | Đạt phần kỹ thuật |
| Chọn 1 topic trong bảng, ít nhất 2 domain (tr. 1–2) | User chọn Nhạc Việt/Spotify; cấu hình 1 domain | **Chưa đạt; custom topic cần chấp thuận, SECOND_DOMAIN_REQUIRED** |
| Kiểm tra robots, điều khoản, khả năng truy cập (tr. 2) | robots HTTP 200; chính sách Spotify mục 5 hạn chế crawling; HTML chưa request | Đã audit và dừng đúng phạm vi; live data bị chặn |
| Đổi domain cùng topic nếu không crawl được (tr. 2) | Chưa tự đổi chủ đề/domain do yêu cầu riêng của user | Cần chọn domain thay thế được chấp thuận |
| Pipeline tổng thể (tr. 3–4, 19–22) | main/crawler/frontier/parser/urls/database/statistics | Đạt trên integration HTTP cục bộ |
| Config riêng, hiển thị cấu hình (tr. 4–5) | config.py và CLI main.py | Đạt |
| Frontier deque, BFS, visited (tr. 5–7) | url_frontier.py; kiểm tra thứ tự request thực tế | Đạt |
| HTTP 200/404/403/500, timeout, lỗi kết nối (tr. 7–9) | Requests có timeout; lỗi ghi JSON; tests đầy đủ | Đạt |
| Response time và delay (tr. 8–9) | response_time_seconds, processing_time_seconds; delay theo origin/robots | Đạt |
| url/domain/title/content/depth/status/timestamp (tr. 9–11) | parser.py, bảng pages, unit tests Unicode/title thiếu | Đạt |
| Chưa làm NLP preprocessing (tr. 10–11) | Chỉ làm sạch HTML/whitespace, không NLP | Đạt |
| Extract `<a href>` và link tương đối (tr. 11–14) | BeautifulSoup + urljoin | Đạt |
| Lọc protocol/domain/file type (tr. 12–14) | urls.py + Content-Type + kiểm tra từng redirect | Đạt |
| Kiểm soát depth (tr. 14–16) | Seed depth 0; không thêm con vượt max_depth | Đạt |
| Normalization và chống trùng (tr. 16–17) | queued/visited/processed + unique trong SQLite | Đạt |
| Hai bảng pages và links (tr. 17–19) | data/crawler.db đã tạo, schema đúng; dữ liệu live bằng 0 | Đạt schema, chưa có dữ liệu Spotify |
| Điều kiện dừng (tr. 22) | max_pages, frontier_empty, max_requests | Đạt |
| robots.txt (tr. 22–23) | robots.py, audit.py, tests rules và deny | Đạt; từ chối redirect robots bảo thủ |
| Thống kê tự tính, depth/status (tr. 23) | statistics.py; summary JSON từ mỗi lần chạy | Đạt |
| Cấu trúc file (tr. 23–24) | Đủ các file yêu cầu, thêm tests/docs/scripts | Đạt |
| README topic, seeds, config, strategy, filters, DB, results (tr. 24–26) | README tiếng Việt và CRAWLING_REPORT.md | Đạt nội dung; nêu rõ phần chưa đạt |

Các yêu cầu thêm của user: không giả dữ liệu, không thay bằng Spotify API, unit/integration tests, audit seed trước crawl lớn, Git commit và GitHub push. Tests có bằng chứng; Spotify bị chặn trước bước HTML; GitHub đã xuất bản thành công sau khi xác thực CLI qua trình duyệt. Không coi tests thành công là bằng chứng hoàn thành phần dữ liệu live hoặc toàn bộ assignment.
