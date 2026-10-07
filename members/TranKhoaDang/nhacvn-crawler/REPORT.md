# Báo cáo: Focused Web Crawler và Score Ranking cho chủ đề Music (nhac.vn)

Assignment 1 – SEG301 (Crawls and Feeds)

Project Python gồm hai phần:

1. **Crawl dữ liệu:** Seed URL → URL Frontier (BFS) → kiểm tra URL → robots.txt → HTTP Request → BeautifulSoup → trích xuất dữ liệu và liên kết → lọc URL → SQLite → thống kê.
2. **Score Ranking:** từ database, tiền xử lý văn bản rồi tính điểm bằng **TF-IDF kết hợp BM25** để xếp hạng trang theo truy vấn.

**Trạng thái:** hoàn thành cả hai phần. Lần chạy crawl gần nhất thu thập 300 trang từ `nhac.vn`, lưu vào `data/crawler.db` (3 bảng: `pages`, `links`, `songs`). Script `ranking.py` đọc database này và xếp hạng 287 trang có nội dung.

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

## 2. Quy trình crawl

```
config.py ─► khởi tạo Frontier + Database ─► thêm seed (depth 0)
                         │
        ┌────────────────▼─────────────────┐
        │ 1. Lấy URL tiếp theo (BFS, FIFO) │
        │ 2. Kiểm tra robots.txt           │
        │ 3. Gửi HTTP request (requests)   │
        │ 4. Phân tích HTML (BeautifulSoup)│
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
| 7. Xếp hạng (TF-IDF + BM25) | `ranking.py` |

---

## 3. Cấu hình

| Tham số | Giá trị | Ý nghĩa |
|---|---|---|
| `MAX_DEPTH` | 3 | Độ sâu tối đa tính từ seed |
| `MAX_PAGES` | 300 | Số trang tối đa trong một lần chạy |
| `REQUEST_TIMEOUT` | 10 giây | Thời gian chờ mỗi request |
| `CRAWL_DELAY` | 1 giây | Nghỉ giữa hai request, tránh làm quá tải server |
| `RESPECT_ROBOTS` | True | Tải và tuân thủ robots.txt |
| `STORE_LYRICS` | True | Lưu lời bài hát (đặt False để xóa khối lời trước khi lưu) |
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
2. **`extract_song_info`** – chạy với URL chứa `/bai-hat/`. Tên bài và ca sĩ lấy từ thẻ `og:title` (tách tại dấu ` - `), album và thể loại từ thẻ `music:album`, `music:genre`, lời bài hát từ khối có class/id gợi ý lyric.
3. **`extract_page_info`** – lấy tiêu đề và toàn bộ văn bản hiển thị, cắt tối đa 20 000 ký tự. Chưa tiền xử lý (không tách từ, không bỏ stopword); việc đó thực hiện ở bước xếp hạng (mục 11).

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

`pages` là nguồn dữ liệu cho bước TF-IDF/BM25. `songs` cung cấp các trường có cấu trúc để thống nhất schema (modal) trong nhóm. `links` giữ đồ thị web phục vụ phân tích liên kết.

---

## 9. Bản quyền và đạo đức crawl

- Dữ liệu chỉ phục vụ **mục đích học tập**, lưu cục bộ, không dùng cho kinh doanh.
- Lời bài hát thuộc bản quyền của tác giả. Database bản hiện tại (có cột `lyrics`) **không được đưa lên GitHub công khai**; nếu giảng viên cần xem dữ liệu thì nộp file `crawler.db` riêng qua kênh của lớp.
- Có thể tắt việc lưu lời bài hát bằng `STORE_LYRICS = False`; khi đó khối lyric bị xóa trước khi lưu.
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
| Dòng trong bảng `songs` | 47 |
| Trang bài hát thật (URL kết thúc `-so...`) | 12, cả 12 đều có ca sĩ và lời |
| Trang danh sách bị nhận nhầm là bài hát (URL kết thúc `-gr...`) | 35 |

### Phân bố theo độ sâu

| Độ sâu | Số trang |
|---|---|
| 0 | 1 |
| 1 | 162 |
| 2 | 137 |
| 3 | 0 |

---

## 11. Score Ranking: TF-IDF kết hợp BM25

### 11.1. Mục tiêu

Từ database, nhận một câu truy vấn và trả về danh sách trang xếp theo mức độ liên quan. Phần này nằm trong file `ranking.py`, chỉ dùng thư viện chuẩn của Python.

### 11.2. Các bước

| Bước | Việc làm |
|---|---|
| 1. Nạp dữ liệu | Đọc bảng `pages`, chỉ lấy trang mã 200 có nội dung: **287 tài liệu** |
| 2. Tiền xử lý | Chữ thường, bỏ dấu, tách theo âm tiết, bỏ từ dừng, thêm cặp hai từ liền nhau (ví dụ `nhac_tre`) |
| 3. Loại nội dung lặp | Bỏ khỏi phần nội dung các từ xuất hiện ở hơn 50 % số trang (menu, chân trang): **548 từ/cụm từ** |
| 4. Lập chỉ mục | Đếm tần suất từ (tf), số trang chứa từ (df), độ dài từng trang; tiêu đề tính nặng gấp 3 lần nội dung |
| 5. Chấm điểm | Tính TF-IDF và BM25 cho từng trang |
| 6. Kết hợp | Chuẩn hóa hai điểm về thang 0–1 rồi lấy trung bình |
| 7. Xếp hạng | Sắp theo điểm kết hợp giảm dần |

Kích thước chỉ mục: 23 164 từ/cụm từ khác nhau, độ dài trung bình 380 mục mỗi trang (trước khi loại nội dung lặp là 1 301).

### 11.3. Công thức

**TF-IDF (độ tương đồng cosine):**
- Trọng số một từ: `(1 + log tf) × idf`, với `idf = log((N + 1) / (df + 1)) + 1`
- Điểm = cosine giữa vector truy vấn và vector tài liệu.

**BM25:**
- Với mỗi từ của truy vấn: `idf × tf × (k1 + 1) / (tf + k1 × (1 − b + b × dl / avgdl))`, trong đó `idf = log(1 + (N − df + 0,5) / (df + 0,5))`
- `k1 = 1,5`, `b = 0,75`; `dl` là độ dài tài liệu, `avgdl` là độ dài trung bình.
- Điểm tài liệu là tổng điểm của các từ truy vấn.

**Kết hợp:** mỗi điểm được chia cho điểm lớn nhất trong lần tìm để về thang 0–1, sau đó:

`điểm cuối = 0,5 × BM25 + 0,5 × TF-IDF` (đổi tỷ lệ bằng tham số `--alpha`).

Lý do kết hợp: hai mô hình có thể xếp hạng khác nhau (TF-IDF ưu tiên độ tương đồng tổng thể, BM25 ưu tiên tần suất có bão hòa và chuẩn hóa độ dài); điểm kết hợp chọn trang được cả hai đánh giá tốt.

### 11.4. Kết quả

| Truy vấn | Số trang khớp | Kết quả đứng đầu |
|---|---|---|
| bảng xếp hạng | 19 / 287 | Các trang "Bảng xếp hạng ... tuần 40/2026" (cả 10 kết quả đầu đều là trang BXH) |
| nhạc trẻ | 222 / 287 | Ba trang "Nhạc Trẻ HOT" (album, bài hát, video) |
| tình yêu | 78 / 287 | "Tình Yêu Giản Đơn", "Yêu Thương Không Là Mãi Mãi", "Một Khi Đã Yêu" |
| nhạc hot | 265 / 287 | "Nhạc Hot - V.A", "Nhạc HOT 2015" |
| sơn tùng | 61 / 287 | "Âm Nhạc Năm 2015", "Nhạc Việt Remix HOT", trang bài hát "Muộn Rồi Mà Sao Còn - Sơn Tùng M-TP" (hạng 3) |

### 11.5. Ảnh hưởng của việc loại nội dung lặp

Mọi trang của nhac.vn đều chứa menu và chân trang, nên các từ như "nhạc", "trẻ", "bảng xếp hạng" xuất hiện ở hầu hết các trang.

| Truy vấn "nhạc trẻ" | Chưa loại | Đã loại (mặc định) |
|---|---|---|
| Số trang khớp | 285 / 287 | 222 / 287 |
| Giá trị idf của từ "nhac" | 0,01 | cao hơn đáng kể |
| Hạng 1–3 | Nhạc Trẻ HOT, Nhạc Phim Việt Nam, Nhạc Hàn HOT | Nhạc Trẻ HOT (album), Nhạc Trẻ HOT (bài hát), Nhạc Trẻ HOT (MV) |

Khi chưa loại, idf gần 0 nên điểm gần như chỉ phụ thuộc độ dài trang. Sau khi loại, ba kết quả đầu đều đúng chủ đề. Điều này cho thấy chất lượng dữ liệu đầu vào quyết định kết quả của TF-IDF và BM25. Có thể kiểm tra bằng `python ranking.py "nhạc trẻ" --max-df 0`.

---

## 12. Phân tích kết quả crawl

1. **Không có trang ở độ sâu 3.** `MAX_DEPTH = 3` nhưng crawl dừng ở độ sâu 2. Độ sâu 1 đã có 162 trang. Vì BFS hoàn thành từng tầng, giới hạn `MAX_PAGES = 300` đạt được giữa tầng 2 (137 trang).

2. **Tỷ lệ trùng lặp rất cao.** 29 911 liên kết được lưu nhưng chỉ 3 695 URL duy nhất; 26 217 liên kết bị bỏ qua. Trung bình mỗi trang chứa khoảng 100 liên kết, phần lớn là menu, header và footer lặp lại.

3. **Lỗi HTTP 500.** 12 trang (4 %) trả lỗi 500 từ phía server; được ghi lại với `status_code = 500` và không có nội dung.

4. **Dữ liệu bài hát.** Bảng `songs` có 47 dòng nhưng chỉ **12 dòng là trang bài hát thật** (URL dạng `...-soXXXX`); cả 12 đều có ca sĩ và lời. 35 dòng còn lại là trang danh sách nằm dưới `/bai-hat/` (URL dạng `...-grXXXX`, ví dụ `/bai-hat/nhac-hot-gr3m`) bị nhận nhầm là bài hát vì quy tắc chỉ kiểm tra chuỗi `/bai-hat/`. Hai cột `album` và `genre` trống ở cả 47 dòng vì nhac.vn không có thẻ `music:album`, `music:genre` như dự đoán. Cột `artist` của bài hát thật còn dính hậu tố "| NHAC.VN".

5. **robots.txt.** Có 1 URL bị chặn và 1 request không có phản hồi; crawler ghi nhận cả hai rồi tiếp tục.

---

## 13. Hạn chế

| Vấn đề | Chi tiết / hướng xử lý |
|---|---|
| Chỉ 1 domain | `nhaccuatui.com` cần JavaScript, `chiasenhac.vn` không truy cập được. |
| Bảng `songs` lẫn trang danh sách | Đổi quy tắc nhận diện bài hát thành URL kết thúc `-so...`; cần crawl lại để cập nhật. |
| `album`, `genre` trống | Cần tìm đúng thẻ HTML chứa thông tin này (xem "Inspect" trên một trang bài hát). |
| Menu, chân trang lẫn vào `content` | Hiện xử lý ở bước xếp hạng (loại từ lặp); cách tốt hơn là chỉ lấy khối nội dung chính khi crawl. |
| Xếp hạng chỉ đọc tiêu đề và nội dung | Tên nghệ sĩ nằm trong URL (ví dụ `son-tung-m-tp`) chưa được đưa vào chỉ mục nên tìm tên người chưa tốt; một số trang nghệ sĩ chỉ có tiêu đề "Nhac.vn". |
| Trang trùng nội dung | Nhiều URL khác nhau có cùng tiêu đề và nội dung cho cùng điểm; chưa gộp. |
| Chưa dùng thư viện tách từ tiếng Việt | Thay bằng tách âm tiết và cặp từ liền nhau. |
| Một số trang không phải nội dung nhạc | Các URL kỹ thuật (ví dụ `/auth`) vẫn qua quy tắc domain; có thể thêm danh sách đường dẫn bị loại. |
| Chỉ crawl 300 trang | Chưa phủ hết website (hơn 3 600 URL còn trong frontier khi dừng). |

---

## 14. Cách chạy

```bash
pip install -r requirements.txt
python check_domain.py     # (tùy chọn) kiểm tra website trước khi crawl
python main.py             # crawl và in thống kê (xóa dữ liệu cũ trong crawler.db)
python ranking.py --demo   # xếp hạng thử 5 truy vấn mẫu
python ranking.py "nhạc trẻ" --top 5
python ranking.py "nhạc trẻ" --explain
```

Lưu ý: `python main.py` xóa dữ liệu cũ trước khi crawl; nên sao lưu `data/crawler.db` nếu muốn giữ.

Các tham số của `ranking.py`: `--top` (số kết quả), `--alpha` (trọng số BM25, 0 là chỉ TF-IDF, 1 là chỉ BM25), `--max-df` (ngưỡng loại từ lặp, 0 là không loại), `--source pages|songs`, `--explain` (xem điểm từng từ).

---

## 15. Cấu trúc project

```
music_crawler/
├── main.py            # điểm vào chương trình
├── crawler.py         # vòng lặp BFS, robots.txt, HTTP request, thống kê
├── url_frontier.py    # URL frontier, chuẩn hóa URL, chống trùng
├── parser.py          # trích xuất trang, bài hát, liên kết và lọc URL
├── database.py        # bảng SQLite và các truy vấn
├── config.py          # toàn bộ tham số crawl
├── check_domain.py    # kiểm tra robots.txt và khả năng crawl
├── ranking.py         # xếp hạng TF-IDF + BM25 trên dữ liệu trong database
├── requirements.txt
└── data/crawler.db    # CSDL đầu ra (không đưa lên GitHub công khai)
```

---

## 16. Hướng phát triển

1. Sửa quy tắc nhận diện trang bài hát (`-so...`) và tìm thẻ chứa album, thể loại để bảng `songs` đầy đủ hơn.
2. Chỉ lấy khối nội dung chính của trang khi crawl, bỏ menu và chân trang ngay từ đầu.
3. Đưa chữ trong URL vào chỉ mục để tìm tên nghệ sĩ tốt hơn; gộp các trang trùng nội dung.
4. Thử thư viện tách từ tiếng Việt chuyên dụng và so sánh kết quả với cách tách âm tiết hiện tại.
5. Thống nhất schema (modal) với nhóm dựa trên bảng `songs` và gộp dữ liệu nhiều website.
