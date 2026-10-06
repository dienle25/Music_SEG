# Vietnamese Music on Spotify Focused Web Crawler

Project Python minh họa đầy đủ pipeline **Seed URLs → URL Frontier → BFS → URL validation → HTTP Request → BeautifulSoup → Extract Data/Links → Filter URLs → SQLite → Statistics**.

**Trạng thái: EXTERNAL BLOCKER.** Phần crawler đã triển khai và vượt qua 47 tests; chưa thu thập trang nội dung Spotify vì chính sách website. `data/crawler.db` là database thật, hiện có **0 trang, 0 liên kết**. Kết quả kiểm thử cục bộ được ghi riêng, không gọi là dữ liệu Spotify.

## Chủ đề, website và đối chiếu đề bài

- Chủ đề: **Nhạc Việt trên Spotify / Vietnamese Music on Spotify**.
- Website mục tiêu: <https://open.spotify.com/>.
- Lý do chọn: nghiên cứu trang nghệ sĩ, album, bài hát và playlist liên quan nhạc Việt; chỉ xét HTML/text/hyperlink, không tải nhạc, video hoặc lời bài hát có đăng nhập.
- Source of truth: assignment PDF 26 trang, tên `21_09_2026___755bde41-6ce6-4515-8cda-704b57491359.pdf`, đã đọc toàn bộ nội dung trích xuất. Bảng đối chiếu nằm trong [docs/ASSIGNMENT_MAPPING.md](docs/ASSIGNMENT_MAPPING.md).

**Nhóm sử dụng Vietnamese Music / Spotify làm custom crawling topic. Topic này cần được giảng viên chấp thuận nếu assignment chỉ cho phép các topic có sẵn trong bảng.** Chưa có bằng chứng giảng viên chấp thuận. Đề trang 1–2 yêu cầu ít nhất 2 domain; project hiện cấu hình 1 domain, nên **SECOND_DOMAIN_REQUIRED**. Không tính hai subdomain Spotify là hai website độc lập. Cấu hình hỗ trợ nhiều domain; chưa tự ý thêm website khác.

## Spotify Technical Audit

Ngày kiểm tra: 22/09/2026. Xem [bằng chứng JSON](docs/evidence/spotify_audit.json) và [báo cáo](docs/CRAWLING_REPORT.md).

| Hạng mục | Kết quả thật |
|---|---|
| GET `https://open.spotify.com/robots.txt` bằng Requests | HTTP 200, `text/plain; charset=utf-8`, 1.859 bytes |
| User-Agent | `SEG301-VietnameseMusicCrawler/1.0` |
| Robots cho root và hai URL ứng viên | Cho phép theo nhóm wildcard |
| Robots cho `/embed/` | Không cho phép với User-Agent trên |
| Chính sách sử dụng | [Spotify User Guidelines, mục 5](https://www.spotify.com/us/legal/user-guidelines/) hạn chế crawling/scraping |
| Request HTML nội dung | Không gửi sau khi phát hiện hạn chế chính sách |
| HTTP nội dung / title / text / link HTML | Chưa đo; không suy ra từ HTTP 200 của robots |
| Phụ thuộc JavaScript | Chưa xác định trực tiếp |
| Quyết định | Dừng crawl nội dung; không tăng lên 3/5/10 trang Spotify |

`robots.txt` và điều khoản sử dụng là hai kiểm tra khác nhau. `robots` cho phép đường dẫn không đủ để kết luận có thể tiến hành dự án này. Crawler chặn Spotify theo kết quả xem xét chính sách đã ghi ngày trong `config.py`; không có tùy chọn dòng lệnh để bỏ qua chặn đó. Không dùng Spotify API, proxy, CAPTCHA solver hay trình duyệt giả danh.

## Seed URLs

**Seed đã trực tiếp kiểm chứng và sử dụng cho crawl Spotify: không có (`SEED_URLS = ()`).** Trạng thái `SEED_URL_VALIDATION_PENDING` do hạn chế chính sách.

Hai **ứng viên** được tìm thấy trong kết quả tìm kiếm trang Spotify ngày 21/09/2026, không phải seed đã crawl:

1. Album *m-tp M-TP*: <https://open.spotify.com/album/1AaxmI2e1HRhbwe9XJGPnT>.
2. Bài hát *Lạc Trôi*: <https://open.spotify.com/track/0XaY8eVU9eZO4OdIV6agx1>.

Chúng nằm trong `CANDIDATE_SEED_URLS`, tách khỏi `SEED_URLS`; không gán trạng thái HTTP 200 chưa quan sát được.

## Cấu hình

| Biến | Mặc định | Lần chạy Spotify kiểm chứng |
|---|---:|---:|
| `MAX_DEPTH` | 2 | 1 |
| `MAX_PAGES` | 30 | 3 |
| `REQUEST_TIMEOUT` | 10 giây | 10 giây |
| `CRAWL_DELAY` | 1 giây | 1 giây |
| `max_requests` | 90 | 90 |
| `max_bytes` | 2.000.000 | 2.000.000 |

Allowed domains: `open.spotify.com`. Giới hạn 3 trang là **trần cấu hình**, không có nghĩa đã crawl 3 trang. `MAX_PAGES` tính số HTML được phân tích/lưu thành công trong lần chạy; `max_requests` chặn tổng request gồm robots và chuyển hướng. Crawl delay áp dụng giữa các request cùng origin và lấy giá trị lớn hơn nếu robots yêu cầu lâu hơn.

## BFS và URL Frontier

`url_frontier.py` dùng `collections.deque`, thêm cuối và lấy đầu bằng `popleft()`. Mỗi phần tử là `(url, depth)`; seed depth 0. `queued_urls` ngăn thêm trùng, `visited_urls` đánh dấu URL đã xử lý; crawler còn theo dõi URL đã request qua chuyển hướng để không tải lại target đang nằm trong queue.

Ví dụ thứ tự: Seed → A → B → A1 → B1. Không thêm con khi trang cha đã đạt `MAX_DEPTH`. Dừng khi đủ trang, frontier rỗng hoặc đạt trần request. Tập visited có hiệu lực trong một lần chạy; SQLite upsert theo URL nên chạy lại không tạo hàng trang trùng.

## Requests và BeautifulSoup

Requests gửi GET có timeout, User-Agent rõ ràng, tắt tự động chuyển hướng. Mỗi đích chuyển hướng được kiểm tra domain, chính sách, robots, vòng lặp và trùng URL trước request tiếp theo. 401/403/429 hoặc trang challenge dừng origin, không thử vượt chặn. 404/500/timeout/connection error được ghi nhận; URL khác vẫn có thể tiếp tục.

Chỉ phân tích response `text/html` hoặc `application/xhtml+xml`. Body đọc theo luồng và có trần dung lượng. BeautifulSoup lấy title, body text và `<a href>`; bỏ script/style/noscript/template/head và phần tử được đánh dấu ẩn. Không chạy JavaScript, không tokenization/TF-IDF. Không có title thì lưu chuỗi rỗng.

HTTP 200 chỉ chứng minh request thành công. Chẩn đoán `html_crawlability` đánh dấu PARTIAL nếu text ít hơn 40 ký tự hoặc không tìm được hyperlink; đây là **heuristic**, không chứng minh nội dung đúng chủ đề hay xác định nguyên nhân JavaScript. Trang lá có thể bị đánh dấu PARTIAL dù có nội dung hợp lệ. `javascript_dependency` luôn ghi chưa xác định nếu không có phép đo trực tiếp.

## URL Filtering và normalization

- Chỉ nhận HTTP/HTTPS; chặn `mailto:`, `javascript:`, `tel:`, `data:` và URL chứa credentials.
- Domain khớp **hostname chính xác**, không tự mở quyền cho subdomain hoặc tên miền có hậu tố giả.
- Chuyển link tương đối bằng `urljoin`, bỏ fragment, chuẩn hóa host/cổng mặc định, bỏ tham số tracking như `utm_*`; tham số nội dung vẫn giữ.
- Giữ khác biệt dấu `/` cuối khi chưa có bằng chứng server coi là cùng tài nguyên.
- Lọc ảnh, CSS, JS, archive, PDF, audio, video; Content-Type là lớp kiểm tra tiếp theo.
- `links` lưu hyperlink HTTP(S) đã chuẩn hóa trước khi lọc frontier, kể cả đích ngoài domain/tài nguyên bị bỏ qua; không tải các đích đó.
- Phạm vi chủ đề được định hướng bằng seed và domain. Chưa có bộ phân loại để đảm bảo mọi link cùng domain đều là nhạc Việt.

## robots.txt

Cache theo origin. Nhóm User-Agent cụ thể được ưu tiên; trong nhóm, quy tắc khớp dài nhất thắng, `Allow` thắng nếu bằng nhau. Hỗ trợ `*`, `$`, percent-encoding, `Crawl-delay` và `Request-rate`. Có tests riêng cho trường hợp `Allow: /` xuất hiện trước `Disallow`.

Robots lỗi mạng/401/403/429/5xx hoặc trả HTML: không crawl. Robots 404/410 được hiểu là không có quy tắc; chính sách sử dụng vẫn được kiểm tra riêng. Redirect của robots được từ chối bảo thủ thay vì tự đi theo URL chưa duyệt.

## SQLite

`data/crawler.db` có hai bảng:

```sql
pages(id, url UNIQUE, domain, title, content, depth, status_code, crawled_at)
links(id, source_url, target_url, UNIQUE(source_url, target_url))
```

Timestamp lưu UTC. SQL dùng tham số để giữ an toàn và đúng Unicode. Trang cùng URL được cập nhật; cặp liên kết được deduplicate. `pages` chỉ chứa HTML HTTP 200 đã qua kiểm tra challenge; lỗi lưu trong thống kê JSON, không tạo dữ liệu trang giả.

## Statistics và kết quả thật

Thống kê tính từ crawler: pages, unique URLs discovered, skipped, failed, phân bố depth/status, thời gian phản hồi, lý do dừng và số hàng SQLite. `unique_urls_discovered` gồm seed/hyperlink HTTP(S) đã chuẩn hóa trước lọc. `database_counts.unique_urls` chỉ là `COUNT(DISTINCT url)` của bảng pages. Skipped là số sự kiện bị bỏ qua, không phải số URL khác nhau.

Spotify: pages **0**, links **0**, discovered **0**, skipped **0**, failed **0**, request nội dung **0**. Không có seed được duyệt nên crawler dừng trước network. GET robots của audit được ghi riêng, không cộng vào thống kê crawl nội dung. `PRAGMA integrity_check = ok`; pages = distinct URLs = **0**.

## Kiểm thử

**47 passed, 0 failed, 0 errors, 0 skipped**, chạy thực tế bằng Python 3.12.10. Có unit tests, mock lỗi mạng, và integration dùng Requests thật với HTTPServer trên loopback. Các database kiểm thử là tạm thời, không ghi vào `data/crawler.db`.

| Trần trang ở máy chủ fixture | Pages | Links SQLite | Unique pages | Lý do dừng |
|---:|---:|---:|---:|---|
| 3 | 3 | 8 | 3 | `max_pages` |
| 5 | 5 | 9 | 5 | `max_pages` |
| 10 | 5 | 9 | 5 | `frontier_empty` |

**Bảng này là dữ liệu tổng hợp cục bộ, không phải crawl Spotify.** Đồ thị fixture chỉ có 5 trang được phép. Workflow GitHub CI đã cấu hình để chạy khi push; trạng thái CI trực tuyến được theo dõi trong tab Actions. Kết quả 47 tests ở trên là kiểm chứng local.

## Cách chạy

Trên máy có Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --audit-only
.\.venv\Scripts\python.exe main.py --max-pages 3 --max-depth 1
.\.venv\Scripts\python.exe main.py --verify-db
.\.venv\Scripts\python.exe scripts/verify.py
```

Linux/macOS dùng `.venv/bin/python` tương ứng. Audit và cấu hình Spotify mặc định trả exit code **2** khi có blocker; code **1** là PARTIAL/lỗi, code **0** là PASS. Lệnh kiểm tra SQLite trả 0 khi dữ liệu nhất quán.

Trong workspace đã bàn giao trên máy này, đã chuẩn bị Python portable và thư viện bên ngoài project. Không cần cài lại để chạy:

```powershell
.\run-local.ps1 test
.\run-local.ps1 audit
.\run-local.ps1 run
.\run-local.ps1 verify
```

Trên website khác đã được chấp thuận, dùng `--seed URL --allow-domain HOST`; có thể lặp hai cờ này cho nhiều domain mà không sửa crawler core. Kiểm tra chính sách từng website trước khi thêm. Cần chỉ định `--db` và `--output-dir` riêng để không lẫn dữ liệu mới với bằng chứng Spotify đã bàn giao.

## Git và GitHub

Repository đích: <https://github.com/dienle25/Music_TMG>, branch **main**, thư mục **spotify/**. Crawler NhacCuaTui có sẵn ở thư mục gốc được giữ lại; module Spotify có cấu hình, database và tests riêng.

Từ thư mục gốc repository, chạy `cd spotify` trước các lệnh Python hoặc `run-local.ps1` trong README này. Workflow `.github/workflows/spotify-tests.yml` tại gốc repository kiểm thử module Spotify bằng Python 3.12 và 3.13.

`scripts/publish.py` kiểm tra tests và đích origin rồi push commit đã có lên `dienle25/Music_TMG/main`; không tạo repository mới hoặc force push. Bằng chứng `github_status.json` và log đi kèm ghi lịch sử lần xuất bản project độc lập trước đó, không phải SHA của lần tích hợp này. Commit tích hợp mới nhất được xem trong lịch sử Music_TMG.

Việc xuất bản không thay đổi hạn chế crawl Spotify hoặc yêu cầu topic/domain của assignment.

## Hạn chế và báo cáo với giảng viên

Không khẳng định đã đáp ứng hoàn toàn assignment: còn quyền crawl Spotify, phê duyệt custom topic và domain thứ hai. Khả năng raw HTML/JavaScript của trang Spotify chưa được đo; không báo “HTML FAIL vì JS” khi chưa có bằng chứng. BFS/parser/storage đã được kiểm chứng độc lập bằng fixtures; điều này không thay thế kết quả crawl live.

Xem [báo cáo đầy đủ và 15 ý thuyết trình](docs/CRAWLING_REPORT.md), [đối chiếu đề](docs/ASSIGNMENT_MAPPING.md) và các JSON/log trong `docs/evidence/`.
