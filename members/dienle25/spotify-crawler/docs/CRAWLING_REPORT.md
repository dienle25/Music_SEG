# Báo cáo Focused Web Crawler: Nhạc Việt trên Spotify

Ngày hoàn tất kiểm chứng local: **22/09/2026**. Trạng thái tổng thể: **EXTERNAL BLOCKER / PARTIAL**, chưa phải COMPLETE. Source of truth là assignment PDF 26 trang; [bảng đối chiếu](ASSIGNMENT_MAPPING.md) chỉ rõ từng yêu cầu.

## Phân tích khả năng crawl Spotify

Đã gửi GET thật bằng Requests với User-Agent `SEG301-VietnameseMusicCrawler/1.0` đến `https://open.spotify.com/robots.txt`. Kết quả ngày 22/09/2026: **HTTP 200**, URL không chuyển hướng, Content-Type `text/plain; charset=utf-8`, **1.859 bytes**. Bản nội dung và SHA256 nằm trong [robots.txt](evidence/robots.txt) và [spotify_audit.json](evidence/spotify_audit.json).

Nhóm wildcard cho phép root và các đường dẫn album/track ứng viên; cấm `/local/`, `/download/`, `/embed/`. Quy tắc được đánh giá theo đường dẫn khớp dài nhất, không chọn `Allow: /` trước rồi bỏ qua Disallow. Không thay User-Agent thành bot tìm kiếm để lấy quyền riêng của bot đó.

Assignment trang 2 yêu cầu kiểm tra cả điều khoản sử dụng. Đã xem [Spotify User Guidelines](https://www.spotify.com/us/legal/user-guidelines/) ngày 21 và 22/09/2026: mục 5 hạn chế crawling/scraping. Vì vậy quyết định là **SPOTIFY CRAWLING RESTRICTION DETECTED** và dừng trước khi tải trang nội dung. Đây là hạn chế chính sách được quan sát, không phải kết luận đã gặp CAPTCHA, 403 hay lỗi JavaScript.

| Chỉ số trang nội dung | Kết quả |
|---|---|
| HTTP access trang Spotify | NOT_TESTED_FOR_CONTENT |
| HTTP status/final URL/Content-Type của seed | Chưa đo |
| HTML received/HTML size/title | Chưa đo |
| Visible text/links extracted | Chưa đo |
| JavaScript dependency | NOT_DETERMINED |
| HTML crawlability | NOT_TESTED |
| Requests + BeautifulSoup khả thi kỹ thuật trên Spotify? | Chưa kết luận; dừng do chính sách |
| Số request nội dung Spotify | 0 |

Không dùng HTTP 200 của robots để khẳng định HTML nội dung crawl được. Không thay crawler bằng Spotify API và không dùng công cụ vượt restriction.

## Seed URLs và phạm vi chủ đề

Seed đã trực tiếp kiểm chứng và đưa vào crawl: **không có**. `SEED_URLS = ()`; trạng thái `SEED_URL_VALIDATION_PENDING`.

Hai URL ứng viên có nguồn từ kết quả tìm kiếm Spotify ngày 21/09/2026:

- <https://open.spotify.com/album/1AaxmI2e1HRhbwe9XJGPnT> — album *m-tp M-TP*.
- <https://open.spotify.com/track/0XaY8eVU9eZO4OdIV6agx1> — bài *Lạc Trôi*.

Không ghi chúng là URL đã GET thành công. `open.spotify.com/` được kiểm tra quy tắc robots nhưng cũng không GET HTML trang chủ.

Music/Spotify không có trong bảng topic mặc định. Chưa có phê duyệt custom topic của giảng viên. Assignment yêu cầu ít nhất 2 domain; hiện chỉ có `open.spotify.com`, đánh dấu **SECOND_DOMAIN_REQUIRED**. Core hỗ trợ thêm domain mà không đổi thuật toán; chưa tự thêm domain hoặc dùng subdomain để lách số lượng.

## Hiện thực

Config → frontier deque/BFS → chuẩn hóa/lọc URL → kiểm tra chính sách và robots → Requests có timeout/delay → BeautifulSoup → SQLite → trích link/lọc/thêm frontier → thống kê.

Trang có trường url, domain, title, content, depth, status_code, crawled_at UTC. Database có unique URL và unique cặp link. URL được lọc trước request, kể cả mỗi bước redirect. Lỗi không tạo trang giả; phản hồi không phải HTML không được đọc thành nội dung trang. Không chạy JavaScript hoặc tải media.

Mặc định: depth 2, pages 30, timeout 10 giây, delay 1 giây. Lần kiểm chứng production dùng **depth 1, pages 3** nhưng bị chặn trước request nội dung. Tổng request tối đa 90 và body tối đa 2 MB giúp giới hạn công việc ngoài max_pages.

## Kết quả crawl Spotify và SQLite

Nguồn số liệu: [crawl_summary.json](evidence/crawl_summary.json) và [sqlite_verification.json](evidence/sqlite_verification.json).

| Đại lượng | Giá trị |
|---|---:|
| Pages crawled | 0 |
| Links found | 0 |
| Unique URLs discovered | 0 |
| Skipped URLs | 0 |
| Failed requests trong crawl | 0 |
| HTTP requests trong crawl | 0 |
| Pages rows | 0 |
| Links rows | 0 |
| COUNT(DISTINCT url) trong pages | 0 |

`PRAGMA integrity_check`: **ok**. `COUNT(*) = COUNT(DISTINCT url)`: **true**. Không có phân bố depth/status vì chưa crawl trang. Robots audit tách riêng khỏi vòng crawl; số 0 lỗi nội dung không có nghĩa đã crawl thành công. Dừng vì `no_verified_seed_urls` và các blocker chính sách/domain/topic.

Các truy vấn đã kiểm tra:

```sql
SELECT COUNT(*) FROM pages;
SELECT COUNT(*) FROM links;
SELECT COUNT(DISTINCT url) FROM pages;
PRAGMA integrity_check;
```

## Unit test và integration test

**47 tests PASS; 0 FAIL; 0 ERROR; 0 SKIP**, Python **3.12.10**. Bằng chứng: [test_results.json](evidence/test_results.json), [tests.log](evidence/tests.log).

Tests kiểm tra BFS, duplicate/cycle, URL tương đối và normalization, hostname chính xác, extension/media, depth, page/request limits, title thiếu, Unicode, SQLite/SQL parameters, 404/500, timeout/connection error, robots denied/unavailable/longest match, delay, redirect ngoài domain/robots denied/vòng lặp/target đã queued, 403/429, challenge HTTP 200, HTML rỗng và policy block không gửi request.

Integration chạy HTTP thật trên `127.0.0.1` với HTML fixture tổng hợp và SQLite tạm. Không kết nối Spotify; không đưa fixture vào database production.

| Giai đoạn local | Pages | Links rows | Unique pages | Depth 0/1/2 | Dừng |
|---|---:|---:|---:|---|---|
| max_pages 3 | 3 | 8 | 3 | 1 / 2 / 0 | max_pages |
| max_pages 5 | 5 | 9 | 5 | 1 / 2 / 2 | max_pages |
| max_pages 10 | 5 | 9 | 5 | 1 / 2 / 2 | frontier_empty |

Bằng chứng được tạo từ chương trình ở [local_integration_stages.json](evidence/local_integration_stages.json). Mốc 10 chỉ có 5 trang vì frontier đã hết. Đây **không phải** các giai đoạn live Spotify. Giai đoạn live 1 seed và 3/5/10 trang đều không chạy vì hạn chế chính sách.

## Git và GitHub

**Đích tích hợp do người dùng chỉ định:** <https://github.com/dienle25/Music_TMG>, branch `main`, module `spotify/`. Mã NhacCuaTui và lịch sử có sẵn được giữ lại. Chạy các lệnh của báo cáo bên trong thư mục `spotify`.

### Lịch sử xuất bản project độc lập trước khi tích hợp

**GITHUB PUSH: SUCCESS.** Repository public: <https://github.com/dienle25/vietnamese-music-spotify-crawler>, branch **main**. Đã tạo repository mới và push bằng GitHub CLI đã xác thực tài khoản `dienle25`; SHA trên remote được đối chiếu với local. Không force push và không xóa repository nào.

Commit đầu tiên được xác minh khi xuất bản: `2ce6717c5b185bdb327af90fcbefb4748c05cadd`. Các commit cập nhật tài liệu sau đó được thể hiện trong lịch sử Git; lấy SHA cuối bằng `git rev-parse HEAD` hoặc trang GitHub, tránh tự tham chiếu SHA trong chính commit đó.

Trở ngại xác thực trước đây đã được giải quyết qua đăng nhập trình duyệt và thư mục cấu hình CLI riêng. [github_status.json](evidence/github_status.json) ghi nhận kết quả thành công; log giữ lịch sử lần bị chặn và lần xuất bản thành công. Phần Spotify vẫn có external blocker như báo cáo ở trên.

## 15 ý cần hiểu khi báo cáo với giảng viên

1. Seed URL là điểm bắt đầu; tìm thấy URL trên search không chứng minh crawler đã truy cập được URL đó.
2. Frontier quản lý URL đang chờ, còn visited ghi URL đã xử lý.
3. BFS dùng queue FIFO nên duyệt các trang theo từng tầng độ sâu.
4. Seed ở depth 0; không đưa liên kết con vượt max_depth vào queue.
5. max_pages giới hạn trang được lưu; max_requests giới hạn tổng request, kể cả lỗi và redirect.
6. Chuẩn hóa URL và tập queued/visited ngăn crawl trùng; UNIQUE của SQLite ngăn hàng trùng.
7. Host phải khớp allowlist chính xác, tránh nhận tên miền giả chỉ có chứa chuỗi “spotify”.
8. Link tương đối phải chuyển thành tuyệt đối theo URL nguồn trước khi lọc.
9. Lọc extension và Content-Type giúp tránh phân tích hoặc tải nội dung media ngoài phạm vi.
10. Robots cần chọn nhóm User-Agent và quy tắc khớp dài nhất; điều khoản sử dụng là kiểm tra riêng.
11. HTTP 200 không chứng minh HTML có đủ text/link, cũng không chứng minh nội dung thuộc nhạc Việt.
12. Requests không chạy JavaScript; muốn kết luận JS gây thiếu dữ liệu phải có bằng chứng thực tế.
13. Bảng pages lưu nội dung; links lưu cạnh đồ thị, gồm cả đích được phát hiện nhưng không crawl.
14. Unit/integration local kiểm chứng thuật toán; fixture không được gắn nhãn dữ liệu Spotify thật.
15. Cần trình bày trung thực các phần chưa đạt: quyền crawl Spotify, topic tùy chọn, domain thứ hai và phân biệt xuất bản GitHub thành công với crawl live còn bị chặn.

## Hạn chế còn lại

Chưa có dữ liệu live Spotify, chưa đo raw HTML/JS của trang nội dung, chưa có phê duyệt topic và domain thứ hai. Bộ lọc chủ đề hiện dựa vào seed/domain, chưa phân loại ngữ nghĩa mọi trang. Heuristic HTML có thể đánh dấu PARTIAL cho trang lá ít text/link; không phải phép đánh giá hoàn chỉnh. Robots redirect được chặn bảo thủ. Những thống kê và chẩn đoán đã lưu phản ánh đúng phạm vi kiểm chứng, không thay thế dữ liệu live.

## Chạy lại

Từ thư mục project trên máy bàn giao: `./run-local.ps1 test`, `./run-local.ps1 audit`, `./run-local.ps1 run`, `./run-local.ps1 verify`. Xem README để cài thư viện và chạy bằng Python thông thường. Audit/run mặc định trả exit code 2 khi gặp blocker, không báo thành công giả.
