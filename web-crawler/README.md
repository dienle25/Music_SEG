# SEG301 – Focused Web Crawler (Music)

Crawler viết bằng Python 3 + Requests + BeautifulSoup + SQLite.
Bắt đầu từ các Seed URL, đi theo hyperlink theo chiến lược **BFS**, lọc URL,
lưu trang và link vào `data/crawler.db`, cuối cùng in thống kê.

## Cấu trúc project

```
web-crawler/
├── main.py           # chạy chương trình, in cấu hình
├── config.py         # Task 1 – seed, domain, giới hạn, luật lọc từng site
├── url_frontier.py   # Task 2 – URL Frontier (deque FIFO + visited/queued)
├── crawler.py        # Task 3, 6, 7, 9 – vòng lặp BFS + thống kê
├── parser.py         # Task 4, 5 – lấy title/content, trích xuất link
├── url_filter.py     # Task 5, 7 – chuẩn hoá URL, luật lọc URL
├── robots.py         # kiểm tra robots.txt
├── database.py       # Task 8 – SQLite (pages, links)
├── check_db.py       # xem nhanh dữ liệu đã crawl
├── data/
│   ├── crawler.db
│   └── crawl_summary.txt
├── requirements.txt
└── README.md
```

## Cách chạy

Yêu cầu Python 3.10 trở lên.

```bash
pip install -r requirements.txt
python main.py        # crawl, mất khoảng 4 phút
python check_db.py    # xem dữ liệu trong data/crawler.db
```

---

## 1. Chủ đề (Selected topic)

```
Topic: Music (Âm nhạc) – Sơn Tùng M-TP & nhạc Việt
Domains:
- Nhac.vn       (nhac.vn)
- NhacCuaTui    (www.nhaccuatui.com)
- Spotify       (open.spotify.com)
- HopAmChuan    (hopamchuan.com – Hợp Âm Chuẩn)
```

Trước khi crawl, nhóm đã kiểm tra robots.txt và HTML thô (khi không chạy JavaScript) của từng site:

| Website | robots.txt | HTML trả về cho Requests | Cách crawler xử lý |
|---|---|---|---|
| Nhac.vn | Cấm `/ajax/`, `/tag/`, `/search?q=`… | Render sẵn, có đầy đủ thẻ `<a href>` | BFS bình thường |
| NhacCuaTui | Cấm `/api/`, `/ajax/`, `/m2/`…; có khai báo sitemap | Có tên bài hát, nghệ sĩ, nhưng **không có thẻ `<a href>`**: link nằm trong `<script>` / JSON. Một số trang bài hát gắn `<meta name="robots" content="noindex, nofollow">` | Lấy URL trong `<script>` / JSON, đọc thêm **sitemap** mà NCT công bố trong robots.txt; trang `noindex` không lưu, trang `nofollow` không đi theo link |
| Spotify | Cấm `/embed/`, `/download/`, `/local/` | Trang nghệ sĩ gần như rỗng; trang album / playlist / track có link trong thẻ `<meta name="music:song">`, `music:album`, `music:musician` | Lấy link từ thẻ `<meta>`; **không** dùng `/embed/` vì robots.txt cấm |
| Hợp Âm Chuẩn | Không có dòng `User-agent` / `Disallow` nào (file chỉ có các dòng chú thích về “content signals”) → được phép crawl | Render sẵn trên server: lời bài hát kèm hợp âm, tiểu sử nghệ sĩ, đầy đủ thẻ `<a href>` (trang chủ khoảng 330 link, trang bài hát khoảng 120 link); meta robots `index, follow` | BFS bình thường, chỉ nhận trang bài hát và trang nghệ sĩ |

> **Thay domain:** ban đầu nhóm chọn Zing MP3, nhưng Zing là web app JavaScript: HTML trả về cho Requests chỉ có
> “You need to enable JavaScript to run this app”, nên lần chạy thử chỉ lấy được 6 trang gần như rỗng.
> Theo đề bài (*“If a selected website cannot reasonably be crawled, choose another domain from the same topic.”*),
> nhóm thay Zing MP3 bằng **Hợp Âm Chuẩn** – cùng chủ đề nhạc Việt, có trang riêng của Sơn Tùng M-TP.
> Khi chọn domain thay thế, nhóm cũng loại keeng.vn vì HTML thô của site này không có chữ và không có thẻ `<a href>` nào.

## 2. Seed URLs

| Domain | Seed URL |
|---|---|
| Nhac.vn | https://nhac.vn/bai-hat/chung-ta-cua-hien-tai-son-tung-m-tp-solpo1X |
| | https://nhac.vn/nghe-si/son-tung-m-tp-atbQz5 |
| | https://nhac.vn/bang-xep-hang-bai-hat-viet-nam-bxdE |
| | https://nhac.vn/ |
| NhacCuaTui | https://www.nhaccuatui.com/playlist/rRmPsafCqIXD |
| | https://www.nhaccuatui.com/playlist/cDFnrv2kVpFu |
| | https://www.nhaccuatui.com/playlist/ZLq1MthiuWE8 |
| | https://www.nhaccuatui.com/playlist/ecZiHDvBu9PD |
| | https://www.nhaccuatui.com/ |
| Spotify | https://open.spotify.com/artist/5dfZ5uSmzR7VQK0udbAVpf |
| | https://open.spotify.com/playlist/37i9dQZF1DWYPc4oQ0ynkq |
| | https://open.spotify.com/album/1V77kA4O1MlAUwfyBONfp1 |
| HopAmChuan | https://hopamchuan.com/artist/21205/son-tung-m-tp |
| | https://hopamchuan.com/song/8926/lac-troi |
| | https://hopamchuan.com/song/41085/chung-ta-cua-hien-tai |
| | https://hopamchuan.com/ |

Seed bổ sung từ sitemap (bật/tắt bằng `USE_SITEMAPS`), mỗi file lấy 8 URL đầu tiên hợp lệ:

- https://www.nhaccuatui.com/sitemap/sitemap_600.xml (trang `/song/...`)
- https://www.nhaccuatui.com/sitemap/sitemap_300.xml (trang `/playlist/...`)

## 3. Cấu hình (Crawling configuration)

```
Maximum pages            : 160   (số trang LƯU vào database, tối đa 40 trang / domain)
Maximum depth            : 2
Request timeout          : 10 seconds
Crawl delay              : 1 second
Sitemap URLs per sitemap : 8
```

- **Crawl delay 1 giây:** crawler nghỉ 1 giây sau mỗi request nên gửi tối đa khoảng 1 request/giây. Không site nào khai báo `Crawl-delay` trong robots.txt. Vì crawl xen kẽ 4 domain, mỗi website thực tế nhận ít hơn 1 request/giây. Đây là mức lịch sự với server mà 160 trang vẫn chạy xong trong khoảng 4 phút.
- **160 trang = 4 domain × 40 trang:** Nhac.vn có rất nhiều link nên nếu không giới hạn theo domain, một site sẽ chiếm hết lượt crawl và các site khác không được crawl.
- **Giới hạn tính theo số trang đã lưu:** trang có `noindex` hoặc request bị lỗi thì không được lưu, nên không tính vào giới hạn; crawler lấy URL kế tiếp trong hàng đợi để bù. Ở lần chạy cuối, NhacCuaTui có 14 trang gắn `noindex` nên crawler gửi 54 request để lưu đủ 40 trang; Spotify có 1 request lỗi HTTP 504 nên gửi 41 request.
- **Timeout 10 giây:** quá thời gian này request được ghi nhận là thất bại và crawler chuyển sang URL tiếp theo, không dừng chương trình.

## 4. Chiến lược crawl (Crawling strategy)

**BFS (Breadth-First Search):** crawl hết các trang depth 0 (seed), rồi tới depth 1, depth 2…
Cách này ưu tiên các trang gần seed (liên quan chủ đề nhất). Nó cũng dàn đều lượt crawl cho các seed của cả 4 website và tránh việc đi quá sâu vào một nhánh.

**URL Frontier** (`url_frontier.py`):

- `deque` lưu cặp `(url, depth)`. `append()` thêm vào cuối, `popleft()` lấy ở đầu (FIFO), nên các trang được xử lý theo thứ tự BFS.
- `queued`: set các URL đang chờ trong hàng đợi, để không thêm một URL hai lần.
- `visited`: set các URL đã lấy ra crawl, để không crawl lại.
- Link tìm thấy ở trang depth `d` được thêm với depth `d + 1`, và chỉ thêm khi `d + 1 <= MAX_DEPTH`.

Crawler dừng khi gặp một trong các điều kiện sau:

- đã lưu đủ `MAX_PAGES` trang vào database;
- URL Frontier rỗng;
- người dùng nhấn `Ctrl+C` (vẫn in thống kê).

Ngoài ra, khi một domain đã lưu đủ `MAX_PAGES_PER_DOMAIN` trang, các URL còn lại của domain đó bị bỏ qua (`DOMAIN_LIMIT`).

Trong cùng một trang, link được thêm theo **thứ tự xuất hiện**. Nội dung các website thay đổi theo thời gian (BXH cập nhật theo tuần, Hợp Âm Chuẩn gợi ý bài hát ngẫu nhiên mỗi lần tải trang), nên danh sách trang crawl được có thể khác nhau đôi chút giữa các lần chạy.

## 5. Luật lọc URL (URL filtering rules)

**Bước 1 – Chuẩn hoá URL** (`normalize_url`):

- Chuyển relative URL thành absolute URL bằng `urljoin`.
- Bỏ `#fragment` và `?query` (ví dụ `?si=` của Spotify, `?st=` của Nhac.vn, `?offset=` của Hợp Âm Chuẩn).
- Viết thường host và gộp host phụ về host chính (`www.nhac.vn` thành `nhac.vn`, `nhaccuatui.com` thành `www.nhaccuatui.com`, `www.hopamchuan.com` thành `hopamchuan.com`).
- Domain đang crawl luôn dùng `https`, và bỏ dấu `/` thừa ở cuối URL (ví dụ `/song/8926/lac-troi/` thành `/song/8926/lac-troi`).
- Đổi URL kiểu cũ về một dạng duy nhất, ví dụ:
  - `nhaccuatui.com/bai-hat/lac-troi-son-tung-m-tp.3tvGi3UEZLVT.html` thành `/song/3tvGi3UEZLVT`
  - `open.spotify.com/intl-vi/track/ID` thành `/track/ID`

**Bước 2 – Loại bỏ** (`check_url_rules`, kiểm tra theo thứ tự):

| Luật | Lý do bị loại |
|---|---|
| `mailto:`, `javascript:`, `tel:`, `#...` | không phải trang web |
| depth > `MAX_DEPTH` | `MAX_DEPTH` |
| không phải `http/https` | `INVALID_PROTOCOL` |
| host không nằm trong 4 domain đã khai báo (so khớp **chính xác** host, sub-domain khác như `m.nhaccuatui.com` bị loại) | `OUTSIDE_DOMAIN` |
| đuôi `.jpg .png .css .js .zip .pdf .mp3 …` | `NON_HTML_RESOURCE` |
| path không khớp regex trang âm nhạc của domain (focused crawler) | `NOT_MUSIC_PAGE` |
| robots.txt không cho phép | `ROBOTS_TXT` |
| đã crawl hoặc đang trong hàng đợi | duplicate |
| domain đã lưu đủ 40 trang | `DOMAIN_LIMIT` |

Regex trang âm nhạc của từng domain (trong `config.py`):

| Domain | Được crawl |
|---|---|
| Nhac.vn | `/bai-hat/...-so…`, `/nghe-si/...-at…`, `/album/...-pl…`, `/video/...-mv…`, `/bang-xep-hang-...-bx…`, `/nhung-bai-hat-hay-nhat-cua-...` |
| NhacCuaTui | `/song/…`, `/playlist/…`, `/artist/…`, `/album/…`, `/video/…` |
| Spotify | `/artist/`, `/album/`, `/track/`, `/playlist/` + ID 22 ký tự |
| HopAmChuan | `/` (trang chủ), `/song/<id>/<slug>` (bài hát), `/artist/<id>/<slug>` (nghệ sĩ) |

Nhac.vn bỏ qua các trang danh mục lặp lại ở menu mọi trang (`...-gr…` thể loại, `/hot-list/...-tv…`, `/vip`, `/xhrUser/…`). Nhờ vậy BFS đi tới bài hát, nghệ sĩ, album thay vì chỉ crawl menu.

Hợp Âm Chuẩn bỏ qua trang quản lý / duyệt bài (`/manage/…`), trang thành viên (`/profile/…`), playlist do thành viên tạo và các “phiên bản” hợp âm do từng thành viên đăng (`/song/<id>/<slug>/<tên thành viên>`, gần trùng nội dung với trang bài hát).

**Bước 3 – Sau khi tải trang:**

- Chỉ xử lý response `200` có `Content-Type: text/html`.
- Nếu trang redirect, crawler dùng URL cuối cùng và kiểm tra lại domain cũng như duplicate.
- `<meta name="robots">` / header `X-Robots-Tag`:
  - `noindex`: không lưu trang vào database;
  - `nofollow`: không đi theo link trong trang.
  - Ví dụ thực tế: NhacCuaTui gắn `noindex, nofollow` cho một số trang bài hát (như *Remember Me*, *Bình Yên Những Phút Giây*), crawler tải trang nhưng không lưu và không đi theo link.

**robots.txt** (`robots.py`): mỗi domain chỉ đọc một lần rồi lưu cache.
Trước khi đưa cho `RobotFileParser`, nội dung được chuẩn hoá theo RFC 9309: gộp các nhóm `User-agent` trùng nhau và ưu tiên luật có path dài hơn.
Lý do: Python bản cũ chỉ đọc nhóm `User-agent: *` đầu tiên và dùng luật khớp đầu tiên. Khi đó `Allow: /` đứng đầu file robots.txt của Spotify và NhacCuaTui sẽ làm mọi dòng `Disallow` bị bỏ qua.

**Lấy link** (`parser.extract_links`):

1. thẻ `<a href>` (Nhac.vn, Hợp Âm Chuẩn);
2. thẻ `<meta property="music:...">` (Spotify);
3. URL trang âm nhạc nằm trong `<script>` / JSON, dành cho site render bằng JavaScript (NhacCuaTui).

## 6. Thiết kế database (Database design)

File: `data/crawler.db`

**`pages`** – mỗi dòng là một trang HTML crawl thành công (dữ liệu cho bước index ở bài sau):

| Cột | Ý nghĩa |
|---|---|
| id | khoá chính |
| url | URL đã chuẩn hoá (UNIQUE, không lưu trùng) |
| domain | website |
| title | `<title>`; nếu quá chung chung thì lấy `og:title` / `<h1>` |
| content | mô tả trong thẻ `<meta>` + chữ hiển thị trên trang (chưa tiền xử lý) |
| depth | độ sâu crawl |
| status_code | mã HTTP |
| crawled_at | thời điểm crawl |

**`links`** – đồ thị liên kết: mỗi dòng là một hyperlink `source_url → target_url` lấy được từ trang đã crawl (gồm cả link ra ngoài domain).
Có `UNIQUE INDEX (source_url, target_url)` để không lưu trùng.

## 7. Kết quả crawl (Crawling results)

Sau khi chạy, thống kê được in ra màn hình và lưu vào `data/crawl_summary.txt`.
Kết quả lần chạy cuối (05/10/2026, khoảng 4 phút):

```
========== CRAWLING SUMMARY ==========

Topic                       : Music (Âm nhạc) – Sơn Tùng M-TP & nhạc Việt
Stop reason                 : MAX_PAGES reached

Seed URLs                   : 16 (+16 từ sitemap)
Pages Crawled               : 175
Pages Saved (DB)            : 160
Not saved (noindex)         : 14
Unique URLs Discovered      : 2942
Skipped URLs                : 2569
Duplicate URLs Skipped      : 2377
Failed Requests             : 1
Links Found                 : 10189
Links Saved (DB)            : 10189
Avg Response Time           : 0.30 sec

Maximum Depth               : 2

Depth 0                     : 32 pages
Depth 1                     : 136 pages
Depth 2                     : 7 pages

HTTP 200                    : 174
HTTP 504                    : 1

Pages per domain (crawled / saved):
    Nhac.vn                 : 40 / 40
    NhacCuaTui              : 54 / 40
    Spotify                 : 41 / 40
    HopAmChuan              : 40 / 40

Skipped URLs by reason:
    DOMAIN_LIMIT            : 1689
    NOT_MUSIC_PAGE          : 847
    OUTSIDE_DOMAIN          : 29
    MAX_DEPTH               : 4

Failed requests:
    [HTTP 504] https://open.spotify.com/album/5hxm3ulOLVvjFdZNFO3n4M
======================================
```

| Chỉ số | Giá trị |
|---|---|
| Pages Crawled (số trang đã gửi request) | 175 |
| Pages Saved (DB) | 160 |
| Not saved (noindex) | 14 (đều là trang NhacCuaTui) |
| Unique URLs Discovered | 2942 |
| Skipped URLs | 2569 |
| Failed Requests | 1 (HTTP 504, Spotify) |
| Links Saved (DB) | 10189 |
| Depth 0 / 1 / 2 (trang đã crawl) | 32 / 136 / 7 |

Số trang theo domain (`python check_db.py`) và độ dài cột `content`:

| Domain | Đã crawl | Lưu vào DB | Content (ký tự): min / trung bình / max |
|---|---|---|---|
| Nhac.vn | 40 | 40 | 2130 / 3373 / 4764 |
| NhacCuaTui | 54 | 40 | 648 / 1911 / 5442 |
| Spotify | 41 | 40 | 93 / 180 / 298 |
| HopAmChuan | 40 | 40 | 1160 / 5096 / 12434 |

```
===== PAGES PER DOMAIN =====
www.nhaccuatui.com     40
open.spotify.com       40
nhac.vn                40
hopamchuan.com         40
...
===== LINKS =====
Total links: 10189
```

**Nhận xét:**

- Nhac.vn cho nhiều dữ liệu (bài hát có lời, nghệ sĩ, BXH) vì HTML render sẵn.
- NhacCuaTui không có thẻ `<a href>`: crawler lấy link trong `<script>` / JSON và sitemap. 14 trang gắn `noindex` được tôn trọng (không lưu), crawler tự lấy trang khác trong hàng đợi để đủ 40 trang.
- Spotify chỉ lấy được tiêu đề và mô tả (93–298 ký tự/trang) vì phần còn lại render bằng JavaScript; link đi theo thẻ `<meta music:*>`. 3 seed chỉ dẫn tới 31 trang ở depth 1, nên 6 trang còn lại nằm ở depth 2.
- Hợp Âm Chuẩn có HTML render sẵn, trang bài hát chứa lời + hợp âm nên content dài nhất (trung bình khoảng 5.100 ký tự/trang).
