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
python main.py        # crawl, mất khoảng 2–4 phút
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
- Zing MP3      (zingmp3.vn)
```

Trước khi crawl, nhóm đã kiểm tra robots.txt và HTML thô (khi không chạy JavaScript) của từng site:

| Website | robots.txt | HTML trả về cho Requests | Cách crawler xử lý |
|---|---|---|---|
| Nhac.vn | Cấm `/ajax/`, `/tag/`, `/search?q=`… | Render sẵn, có đầy đủ thẻ `<a href>` | BFS bình thường |
| NhacCuaTui | Cấm `/api/`, `/ajax/`, `/m2/`…; có khai báo sitemap | Có tên bài hát, nghệ sĩ, nhưng **link được mở bằng JavaScript** (gần như không có `<a href>`) | Đọc thêm **sitemap** mà NCT công bố trong robots.txt để lấy URL bài hát / playlist |
| Spotify | Cấm `/embed/`, `/download/`, `/local/` | Trang nghệ sĩ gần như rỗng; trang album / playlist / track có link trong thẻ `<meta name="music:song">`, `music:album`, `music:musician` | Lấy link từ thẻ `<meta>`; **không** dùng `/embed/` vì robots.txt cấm |
| Zing MP3 | Cấm `/api/`, `/xhr/`… | Mọi URL đều trả về cùng một “vỏ” JavaScript (“You need to enable JavaScript to run this app”) kèm `<meta name="robots" content="noindex, nofollow">` | Crawler vẫn gửi request tới seed nhưng **tôn trọng noindex/nofollow**: không lưu, không đi theo link |

> Theo đề bài: *“If a selected website cannot reasonably be crawled, choose another domain from the same topic.”*
> Zing MP3 thuộc trường hợp này với Requests + BeautifulSoup. Nhóm giữ Zing MP3 trong cấu hình để minh hoạ việc crawler tôn trọng meta robots; có thể thay bằng domain khác trong `config.py`.

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
| Zing MP3 | https://zingmp3.vn/ |
| | https://zingmp3.vn/playlist/Nhung-Bai-Hat-Hay-Nhat-Cua-Son-Tung-M-TP/ZWZAC9BF.html |

Seed bổ sung từ sitemap (bật/tắt bằng `USE_SITEMAPS`), mỗi file lấy 8 URL đầu tiên hợp lệ:

- https://www.nhaccuatui.com/sitemap/sitemap_600.xml (trang `/song/...`)
- https://www.nhaccuatui.com/sitemap/sitemap_300.xml (trang `/playlist/...`)

## 3. Cấu hình (Crawling configuration)

```
Maximum pages            : 120   (tối đa 40 trang / domain)
Maximum depth            : 2
Request timeout          : 10 seconds
Crawl delay              : 1 second
Sitemap URLs per sitemap : 8
```

- **Crawl delay 1 giây:** crawler nghỉ 1 giây sau mỗi request nên gửi tối đa khoảng 1 request/giây. Không site nào khai báo `Crawl-delay` trong robots.txt. Vì crawl xen kẽ 4 domain, mỗi website thực tế nhận ít hơn 1 request/giây. Đây là mức lịch sự với server mà 120 trang vẫn chạy xong trong vài phút.
- **40 trang / domain:** Nhac.vn có rất nhiều link nên nếu không giới hạn, một site sẽ chiếm hết lượt crawl và các site khác không được crawl.
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

- đã crawl `MAX_PAGES` trang;
- URL Frontier rỗng;
- người dùng nhấn `Ctrl+C` (vẫn in thống kê).

Ngoài ra, khi một domain đã đủ `MAX_PAGES_PER_DOMAIN` trang, các URL còn lại của domain đó bị bỏ qua.

Trong cùng một trang, link được thêm theo **thứ tự xuất hiện**, nên kết quả crawl ổn định giữa các lần chạy.

## 5. Luật lọc URL (URL filtering rules)

**Bước 1 – Chuẩn hoá URL** (`normalize_url`):

- Chuyển relative URL thành absolute URL bằng `urljoin`.
- Bỏ `#fragment` và `?query` (ví dụ `?si=` của Spotify, `?st=` của Nhac.vn).
- Viết thường host và gộp host phụ về host chính (`www.nhac.vn` thành `nhac.vn`, `nhaccuatui.com` thành `www.nhaccuatui.com`).
- Domain đang crawl luôn dùng `https`, và bỏ dấu `/` thừa ở cuối URL.
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
| domain đã đủ số trang | `DOMAIN_LIMIT` |

Regex trang âm nhạc của từng domain (trong `config.py`):

| Domain | Được crawl |
|---|---|
| Nhac.vn | `/bai-hat/...-so…`, `/nghe-si/...-at…`, `/album/...-pl…`, `/video/...-mv…`, `/bang-xep-hang-...-bx…`, `/nhung-bai-hat-hay-nhat-cua-...` |
| NhacCuaTui | `/song/…`, `/playlist/…`, `/artist/…`, `/album/…`, `/video/…` |
| Spotify | `/artist/`, `/album/`, `/track/`, `/playlist/` + ID 22 ký tự |
| Zing MP3 | `/bai-hat/`, `/album/`, `/playlist/`, `/video-clip/`, trang nghệ sĩ |

Nhac.vn bỏ qua các trang danh mục lặp lại ở menu mọi trang (`...-gr…` thể loại, `/hot-list/...-tv…`, `/vip`, `/xhrUser/…`). Nhờ vậy BFS đi tới bài hát, nghệ sĩ, album thay vì chỉ crawl menu.

**Bước 3 – Sau khi tải trang:**

- Chỉ xử lý response `200` có `Content-Type: text/html`.
- Nếu trang redirect, crawler dùng URL cuối cùng và kiểm tra lại domain cũng như duplicate.
- `<meta name="robots">` / header `X-Robots-Tag`:
  - `noindex`: không lưu trang vào database;
  - `nofollow`: không đi theo link trong trang.

**robots.txt** (`robots.py`): mỗi domain chỉ đọc một lần rồi lưu cache.
Trước khi đưa cho `RobotFileParser`, nội dung được chuẩn hoá theo RFC 9309: gộp các nhóm `User-agent` trùng nhau và ưu tiên luật có path dài hơn.
Lý do: Python bản cũ chỉ đọc nhóm `User-agent: *` đầu tiên và dùng luật khớp đầu tiên. Khi đó `Allow: /` đứng đầu file robots.txt của Spotify và NhacCuaTui sẽ làm mọi dòng `Disallow` bị bỏ qua.

**Lấy link** (`parser.extract_links`):

1. thẻ `<a href>`;
2. thẻ `<meta property="music:...">` (Spotify);
3. URL trang âm nhạc nằm trong `<script>` / JSON, dành cho site render bằng JavaScript.

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
Dán kết quả lần chạy cuối vào đây:

```
(dán nội dung data/crawl_summary.txt)
```

| Chỉ số | Giá trị |
|---|---|
| Pages Crawled | |
| Pages Saved (DB) | |
| Unique URLs Discovered | |
| Skipped URLs | |
| Failed Requests | |
| Depth 0 / 1 / 2 | |
| Nhac.vn / NhacCuaTui / Spotify / Zing MP3 | |

**Nhận xét:**

- Nhac.vn cho nhiều dữ liệu nhất (bài hát có lời, nghệ sĩ, BXH) vì HTML render sẵn.
- NhacCuaTui có nội dung nhưng phải nhờ sitemap mới tìm được thêm URL.
- Spotify chủ yếu lấy được tiêu đề và mô tả, link đi theo thẻ `<meta music:*>`.
- Zing MP3 không lấy được dữ liệu bằng Requests vì là web app JavaScript và gắn noindex/nofollow.
