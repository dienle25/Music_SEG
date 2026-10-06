# Báo cáo: Focused Web Crawler cho chủ đề Music (nhac.vn)

Assignment 1 – SEG301 (Crawls and Feeds)

Project Python thực hiện đầy đủ pipeline:
**Seed URL → URL Frontier (BFS) → kiểm tra URL → robots.txt → HTTP Request → BeautifulSoup → trích xuất dữ liệu và liên kết → lọc URL → SQLite → thống kê.**

**Trạng thái:** hoàn thành phần crawl. Lần chạy gần nhất thu thập 300 trang từ `nhac.vn`, lưu vào `data/crawler.db` (3 bảng: `pages`, `links`, `songs`). Phần xử lý TF-IDF + BM25 là bước tiếp theo, chưa nằm trong báo cáo này.

---

## 1. Chủ đề và website

| Mục | Nội dung |
|---|---|
| Chủ đề | Music (nhạc Việt) |
| Website | https://nhac.vn/ |
| Số domain | 1 (`nhac.vn`) |
| Nội dung thu thập | Tiêu đề, văn bản trang, liên kết, thông tin bài hát (tên bài, ca sĩ, album, thể loại) và lời bài hát (tùy chọn, xem mục 9) |

**Lý do chọn nhac.vn.** Các domain khác đã được thử nhưng không crawl được: `nhaccuatui.com` hiển thị nội dung bằng JavaScript (chỉ tìm thấy 4 liên kết tĩnh khi chạy `check_domain.py`), `chiasenhac.vn` không kết nối được. `nhac.vn` trả HTML đầy đủ từ server nên chỉ cần `requests` và `BeautifulSoup`, không cần trình duyệt giả lập.

**Lưu ý về số domain.** Đề bài khuyến nghị ít nhất 2 domain. Theo README gốc của dự án, giảng viên đã đồng ý cho dùng riêng `nhac.vn`. Cấu hình vẫn hỗ trợ nhiều domain (thêm vào `SEED_URLS` và `ALLOWED_DOMAINS`).

---

## 2. Quy trình thực hiện

```
config.py ─► khởi tạo Frontier + Database ─► thêm seed (depth 0)
                         │
        ┌────────────────▼─────────────────┐
        │ 1. Lấy URL tiếp theo (BFS, FIFO) │
        │ 2. Kiểm tra robots.txt           │
        │ 3. Gửi HTTP request (timeout)    │
        │ 4. Phân tích HTML (nếu 200)      │
        │ 5. Lưu pages / songs / links     │
        │ 6. Lọc và thêm link mới (d + 1)  │
        │ 7. Nghỉ CRAWL_DELAY giây         │
        └────────────────┬─────────────────┘
                         │ (đủ MAX_PAGES hoặc frontier rỗng)
                         ▼
              In thống kê ─► crawler.db
```

| Bước | File / hàm |
|---|---|
| 0. Kiểm tra website trước khi crawl | `check_domain.py` |
| 1. Cấu hình | `config.py` |
| 2. Khởi tạo, vòng lặp chính | `main.py`, `crawler.py` → `Crawler.run()` |
| 3. Hàng đợi URL, chống trùng | `url_frontier.py` → `URLFrontier` |
| 4. Tải trang, robots.txt | `crawler.py` → `fetch()`, `can_fetch()` |
| 5. Phân tích HTML | `parser.py` → `extract_links`, `extract_song_info`, `extract_page_info` |
| 6. Lưu trữ, thống kê | `database.py` |

---

## 3. Cấu hình

| Tham số | Giá trị | Ý nghĩa |
|---|---|---|
| `MAX_DEPTH` | 3 | Độ sâu tối đa tính từ seed |
| `MAX_PAGES` | 300 | Số trang tối đa trong một lần chạy |
| `REQUEST_TIMEOUT` | 10 giây | Thời gian chờ mỗi request |
| `CRAWL_DELAY` | 1 giây | Nghỉ giữa hai request, tránh làm quá tải server |
| `RESPECT_ROBOTS` | True | Tải và tuân thủ robots.txt |
| `STORE_LYRICS` | True / False | Bật/tắt lưu lời bài hát |
| `MAX_CONTENT_LENGTH` | 20 000 ký tự | Giới hạn văn bản lưu mỗi trang |

Toàn bộ tham số nằm trong `config.py`, không cần sửa logic crawler để thay đổi.

---

## 4. Chiến lược crawl: BFS và URL Frontier

**Vì sao BFS?** BFS duyệt hết các trang ở độ sâu *d* trước khi sang *d + 1*. Với focused crawler, cách này cho độ phủ rộng và cân bằng (trang chủ → danh mục/bảng xếp hạng → bài hát/album/nghệ sĩ) thay vì đi sâu vào một nhánh, đồng thời dễ áp dụng giới hạn độ sâu.

**Cách `URLFrontier` hoạt động:**
- Lưu các cặp `(url, depth)` trong `collections.deque`: `append` để thêm, `popleft` để lấy, nên URL vào trước được crawl trước.
- Mỗi domain có một hàng đợi riêng, `next()` lấy lần lượt theo vòng tròn (round-robin) để một domain lớn không chiếm hết hàng đợi khi crawl nhiều domain. Với một domain thì hoạt động như một hàng đợi BFS bình thường.
- Seed có depth 0; liên kết tìm thấy ở trang depth *d* có depth *d + 1*.
- Ba tập hợp chống trùng: `in_frontier` (đang chờ), `visited` (đã lấy ra crawl), `discovered` (mọi URL đã từng được chấp nhận).

**Điều kiện dừng:** đủ `MAX_PAGES` hoặc frontier rỗng.

---

## 5. HTTP Request và robots.txt

- Dùng `requests.Session` với `User-Agent` rõ ràng và timeout 10 giây.
- `robots.txt` được tải một lần cho mỗi domain, cache lại và phân tích bằng `urllib.robotparser`. URL bị chặn được bỏ qua và đếm vào `Blocked by robots.txt`.
- Quy tắc xử lý robots.txt: 200 → phân tích; 401/403 → coi như chặn toàn bộ; 404 hoặc lỗi khác → cho phép; lỗi mạng → cho phép.
- Chỉ phân tích phản hồi có mã **200** và `Content-Type` chứa `html`. Mã khác (404, 500...) hoặc lỗi kết nối (timeout) vẫn được ghi vào bảng `pages` cùng `status_code` (0 nghĩa là không có phản hồi), crawler tiếp tục với URL kế tiếp.

---

## 6. Phân tích HTML

Ba hàm trong `parser.py`, gọi theo đúng thứ tự vì `extract_page_info` sửa cây HTML (xóa `<script>`, `<style>`):

1. **`extract_links`** – lấy mọi thẻ `<a href>`, đổi liên kết tương đối thành tuyệt đối bằng `urljoin`, lọc, chuẩn hóa và loại trùng.
2. **`extract_song_info`** – chỉ chạy với URL chứa `/bai-hat/`. Lấy tên bài, ca sĩ, album, thể loại từ thẻ meta và lời bài hát từ khối có class/id gợi ý lyric.
3. **`extract_page_info`** – lấy tiêu đề và toàn bộ văn bản hiển thị, cắt tối đa 20 000 ký tự. Chưa tiền xử lý (không tách từ, không bỏ stopword) vì đó là việc của bước TF-IDF/BM25.

---

## 7. Lọc và chuẩn hóa URL

Một liên kết chỉ được nhận khi qua **tất cả** quy tắc:

1. Đổi URL tương đối thành tuyệt đối.
2. Chỉ nhận `http` / `https`; bỏ `mailto:`, `javascript:`, `tel:`, `ftp:`, `sms:`, `data:`.
3. Bỏ tệp không phải trang web: ảnh, CSS, JS, nén, tài liệu, audio/video, font.
4. **Quy tắc domain:** host phải bằng domain cho phép hoặc là subdomain của nó. Các site ngoài (ví dụ `facebook.com`) bị loại.
5. **Chuẩn hóa:** bỏ `#fragment`, viết thường scheme/host, bỏ cổng mặc định, bỏ dấu `/` cuối, nên `/news/1`, `/news/1/` và `/news/1#top` là một URL.
6. **Chống trùng:** bỏ URL đã thăm hoặc đang chờ trong frontier.
7. **Độ sâu:** không thêm liên kết vượt `MAX_DEPTH`.
8. **robots.txt:** URL bị cấm sẽ bị bỏ qua.

---

## 8. Thiết kế cơ sở dữ liệu

File: `data/crawler.db` (SQLite).

**Bảng `pages`** – mỗi URL đã crawl một dòng.

| Cột | Mô tả |
|---|---|
| `id` | Khóa chính |
| `url` | Địa chỉ trang (UNIQUE) |
| `domain` | Tên miền |
| `title` | Tiêu đề trang |
| `content` | Văn bản hiển thị của trang (chưa tiền xử lý) |
| `depth` | Độ sâu crawl |
| `status_code` | Mã HTTP (0 = không có phản hồi) |
| `crawled_at` | Thời điểm crawl |

**Bảng `links`** – đồ thị liên kết (nguồn → đích).

| Cột | Mô tả |
|---|---|
| `id` | Khóa chính |
| `source_url` | Trang chứa liên kết |
| `target_url` | URL đích đã chuẩn hóa |

**Bảng `songs`** – dữ liệu có cấu trúc của trang bài hát.

| Cột | Mô tả |
|---|---|
| `id` | Khóa chính |
| `url` | Địa chỉ trang bài hát (UNIQUE) |
| `title` | Tên bài hát |
| `artist` | Ca sĩ |
| `album` | Album |
| `genre` | Thể loại |
| `lyrics` | Lời bài hát (rỗng nếu `STORE_LYRICS = False` hoặc không tìm thấy) |
| `crawled_at` | Thời điểm crawl |

`pages` là nguồn dữ liệu cho bước TF-IDF/BM25. `songs` cung cấp các trường có cấu trúc để thống nhất schema (modal) trong nhóm và để xếp hạng theo từng trường. `links` giữ đồ thị web phục vụ phân tích liên kết.

---

## 9. Bản quyền và đạo đức crawl

- Dữ liệu chỉ phục vụ **mục đích học tập**, lưu cục bộ, không dùng cho kinh doanh.
- **Không đưa `crawler.db` và thư mục `data/` lên GitHub** (đã khai báo trong `.gitignore`), vì cơ sở dữ liệu có thể chứa lời bài hát thuộc bản quyền của tác giả.
- Có thể tắt hoàn toàn việc lưu lời bài hát bằng `STORE_LYRICS = False`; khi đó khối lyric bị xóa trước khi lưu.
- Giữ `CRAWL_DELAY = 1` giây và tuân thủ robots.txt (`RESPECT_ROBOTS = True`).

---

## 10. Kết quả crawl thực tế

Số liệu lấy từ phần `CRAWLING SUMMARY` của lần chạy gần nhất.

```
========== CRAWLING SUMMARY ==========
Topic                  : Music
Seed URLs              : 1
Stop reason            : MAX_PAGES reached
Pages Crawled          : 300
Unique URLs Discovered : 3695
Skipped URLs           : 26217
Blocked by robots.txt  : 1
Failed Requests        : 1
Links stored           : 29911
Songs stored           : 47
Songs with lyrics      : 12
Maximum Depth (config) : 3
Pages per depth:
  Depth 0 : 1 pages
  Depth 1 : 162 pages
  Depth 2 : 137 pages
Pages per domain:
  nhac.vn : 300
HTTP status:
  No response : 1
  HTTP 200 : 287
  HTTP 500 : 12
=======================================
```

### Bảng tổng hợp

| Chỉ số | Giá trị |
|---|---|
| Trang đã crawl | 300 |
| URL duy nhất phát hiện | 3 695 |
| Liên kết bị bỏ qua (trùng / vượt độ sâu) | 26 217 |
| Liên kết lưu trong DB | 29 911 |
| Trang trả HTTP 200 | 287 (95,7 %) |
| Trang trả HTTP 500 | 12 (4,0 %) |
| Request không có phản hồi | 1 (0,3 %) |
| URL bị robots.txt chặn | 1 |
| Bài hát lưu trong `songs` | 47 |
| Bài hát có lời | 12 (khoảng 25,5 % số bài) |

### Phân bố theo độ sâu

| Độ sâu | Số trang |
|---|---|
| 0 | 1 |
| 1 | 162 |
| 2 | 137 |
| 3 | 0 |

---

## 11. Phân tích kết quả

1. **Không có trang ở độ sâu 3.** `MAX_DEPTH = 3` nhưng crawl dừng ở độ sâu 2. Trang chủ liên kết tới rất nhiều danh mục, bảng xếp hạng và bài hát nên riêng độ sâu 1 đã có 162 trang. Vì BFS hoàn thành từng tầng, giới hạn `MAX_PAGES = 300` đạt được giữa tầng 2 (137 trang), trước khi lấy bất kỳ trang tầng 3 nào.

2. **Tỷ lệ trùng lặp rất cao.** Có 29 911 liên kết được lưu nhưng chỉ 3 695 URL duy nhất được phát hiện; 26 217 liên kết bị bỏ qua. Trung bình mỗi trang chứa khoảng 100 liên kết, phần lớn là menu, header và footer lặp lại. Nếu không có các tập `visited` và `in_frontier`, crawler sẽ tải đi tải lại cùng một trang.

3. **Lỗi HTTP 500.** 12 trang (4 %) trả lỗi 500 từ phía server. Những trang này được ghi lại với `status_code = 500` và không có nội dung. Đây là lỗi của website, không phải của crawler.

4. **Dữ liệu bài hát.** Có 47 trang bài hát được lưu vào `songs`, trong đó 12 trang tìm được lời. 35 bài còn lại có thể do trang không có lời, hoặc lời nằm trong khối có tên class khác với danh sách gợi ý (`LYRIC_CONTAINER_HINTS`). Các trường `artist`, `album`, `genre` dựa vào thẻ meta và chưa được kiểm tra đầy đủ trên toàn bộ 47 bài; cần mở bảng `songs` để xác nhận trước khi dùng cho bước tiếp theo.

5. **robots.txt.** Có 1 URL bị chặn và 1 request thất bại do không có phản hồi; crawler ghi nhận cả hai rồi tiếp tục bình thường.

---

## 12. Hạn chế và vấn đề gặp phải

| Vấn đề | Chi tiết / hướng xử lý |
|---|---|
| Chỉ 1 domain | `nhaccuatui.com` cần JavaScript, `chiasenhac.vn` không truy cập được. |
| Có trang không phải nội dung nhạc | Một số URL kỹ thuật hoặc tài khoản (ví dụ `/auth`) vẫn qua quy tắc domain. Có thể thêm danh sách đường dẫn bị loại. |
| URL có tham số `?` | Được giữ lại vì có thể trỏ tới nội dung khác, nhưng một số có thể trùng gần giống nhau. |
| Tiêu đề chung chung | Một số trang chỉ có tiêu đề "Nhac.vn", nên cần dùng thêm `content`. |
| Selector bài hát dựa trên phỏng đoán | Cấu trúc HTML của nhac.vn có thể thay đổi; cần kiểm tra lại `extract_song_info` khi cột bị rỗng. |
| Chỉ crawl 300 trang | Chưa phủ hết website (hơn 3 600 URL còn trong frontier khi dừng). |

---

## 13. Cách chạy

```bash
pip install -r requirements.txt
python check_domain.py     # (tùy chọn) kiểm tra website trước khi crawl
python main.py             # crawl và in thống kê
```

Cơ sở dữ liệu được tạo tại `data/crawler.db`. Có thể mở bằng *DB Browser for SQLite* để xem các bảng `pages`, `links`, `songs`.

---

## 14. Cấu trúc project

```
music_crawler/
├── main.py            # điểm vào chương trình
├── crawler.py         # vòng lặp BFS, robots.txt, HTTP request, thống kê
├── url_frontier.py    # URL frontier, chuẩn hóa URL, chống trùng
├── parser.py          # trích xuất trang, bài hát, liên kết và lọc URL
├── database.py        # bảng SQLite và các truy vấn
├── config.py          # toàn bộ tham số crawl
├── check_domain.py    # kiểm tra robots.txt và khả năng crawl
├── requirements.txt
├── .gitignore         # bỏ qua data/, *.db, __pycache__/
└── data/crawler.db    # CSDL đầu ra (không đưa lên Git)
```

---

## 15. Bước tiếp theo

1. Thống nhất schema (modal) với nhóm dựa trên bảng `songs`.
2. Tiền xử lý văn bản từ `pages.content` và `songs`: tách từ tiếng Việt, loại stopword.
3. Cài đặt TF-IDF kết hợp BM25 trên dữ liệu trong DB.
4. Nhận truy vấn và trả danh sách kết quả xếp hạng theo điểm (Score Ranking).
