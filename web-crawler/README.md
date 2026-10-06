# SEG301 – Focused Web Crawler (Music)

Crawler viết bằng Python 3 + Requests + BeautifulSoup + SQLite.
Bắt đầu từ các Seed URL, đi theo hyperlink theo chiến lược **BFS**, lọc URL,
lưu trang, link và bài hát (title, artist, album, genre, lyrics) vào `data/crawler.db`,
cuối cùng in thống kê.

Bảng đối chiếu từng yêu cầu của đề bài với code nằm ở [mục 8](#8-đối-chiếu-yêu-cầu-đề-bài).

## Cấu trúc project

```
web-crawler/
├── main.py           # chạy chương trình, in cấu hình
├── config.py         # Task 1 – seed, domain, giới hạn, luật lọc từng site
├── url_frontier.py   # Task 2 – URL Frontier (deque FIFO + visited/queued)
├── crawler.py        # Task 3, 6, 7, 9 – vòng lặp BFS + thống kê
├── parser.py         # Task 4, 5 – lấy title/content, trích xuất link
├── song_parser.py    # bảng songs – tách title/artist/album/genre/lyrics của trang bài hát
├── url_filter.py     # Task 5, 7 – chuẩn hoá URL, luật lọc URL, nhận diện trang bài hát
├── robots.py         # kiểm tra robots.txt
├── database.py       # Task 8 – SQLite (pages, links, songs)
├── check_db.py       # xem nhanh dữ liệu đã crawl (gồm cả bảng songs)
├── run.bat           # Windows: bấm đúp để cài thư viện, crawl và xem dữ liệu
├── tests/
│   └── test_crawler.py   # 31 test offline (không gửi request ra Internet)
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
python main.py        # crawl, mất khoảng 4–5 phút
python check_db.py    # xem dữ liệu trong data/crawler.db (pages, songs, links)
```

Trên Windows có thể bấm đúp `run.bat`: tự cài thư viện, chạy `main.py` rồi `check_db.py`.

Chạy kiểm thử (offline, vài giây; GitHub Actions cũng tự chạy mỗi lần push):

```bash
python -m unittest discover -s tests -v
```

Kiểm tra một trang bài hát đã lưu từ trình duyệt (xem `song_parser.py` tách ra những gì):

```bash
python song_parser.py trang.html https://nhac.vn/bai-hat/chung-ta-cua-hien-tai-son-tung-m-tp-solpo1X
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

- **Crawl delay 1 giây:** crawler nghỉ 1 giây sau mỗi request nên gửi tối đa khoảng 1 request/giây. Không site nào khai báo `Crawl-delay` trong robots.txt. Vì crawl xen kẽ 4 domain, mỗi website thực tế nhận ít hơn 1 request/giây. Đây là mức lịch sự với server mà 160 trang vẫn chạy xong trong khoảng 4–5 phút.
- **160 trang = 4 domain × 40 trang:** Nhac.vn có rất nhiều link nên nếu không giới hạn theo domain, một site sẽ chiếm hết lượt crawl và các site khác không được crawl.
- **Giới hạn tính theo số trang đã lưu:** trang có `noindex` hoặc request bị lỗi thì không được lưu, nên không tính vào giới hạn; crawler lấy URL kế tiếp trong hàng đợi để bù. Ở lần chạy cuối, NhacCuaTui có 14 trang gắn `noindex` nên crawler gửi 54 request để lưu đủ 40 trang.
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
- Trang được lưu vào `pages` mà URL khớp luật trang bài hát (`song_pages` trong `config.py`) thì được tách thêm title / artist / album / genre / lyrics và lưu vào bảng `songs` (mục 6).

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

**`songs`** – mỗi dòng là một **bài hát** (chỉ trang bài hát, không gồm trang nghệ sĩ / album / playlist). Tên bảng, tên cột và thứ tự cột theo đúng yêu cầu đề bài:

| Cột | Ý nghĩa |
|---|---|
| id | khoá chính (tự tăng) |
| url | URL đã chuẩn hoá của trang bài hát (UNIQUE) |
| title | tên bài hát (đã bỏ tên website, tên ca sĩ, “mp3 download”, “Hợp âm”… khỏi tiêu đề) |
| artist | ca sĩ / nghệ sĩ |
| album | album |
| genre | thể loại |
| lyrics | lời bài hát (đã bỏ hợp âm `[Am]`, giữ xuống dòng) |
| crawled_at | thời điểm crawl |

Cột nào trang không có thì để `NULL`, crawler không điền giá trị đoán. Trang bài hát chỉ vào bảng `songs` khi được lưu vào `pages` (trang `noindex` không lưu vào cả hai bảng). Vì `songs` lấy từ các trang đã lưu, số bài hát của mỗi domain không vượt quá `MAX_PAGES_PER_DOMAIN`; muốn nhiều bài hơn thì tăng giới hạn trong `config.py`.

Trang nào là **trang bài hát** do `song_pages` trong `config.py` quyết định:

| Domain | Trang bài hát |
|---|---|
| Nhac.vn | `/bai-hat/...-so…` |
| NhacCuaTui | `/song/<id>` |
| Spotify | `/track/<ID 22 ký tự>` |
| HopAmChuan | `/song/<id>/<slug>` |

`song_parser.py` thử lần lượt nhiều nguồn cho từng cột, nguồn nào có dữ liệu trước thì dùng:

| Cột | Nguồn (theo thứ tự ưu tiên) |
|---|---|
| title | JSON-LD; `<h1>`; `og:title`; `<title>`. Chỉ nhận `<h1>` khi tên đó cũng nằm trong `<title>` / `og:title`, để không lấy nhầm logo hay khẩu hiệu của website |
| artist | JSON-LD; mô tả Spotify; nhãn `Ca sĩ:`; phần `Tên bài - Ca sĩ` trong tiêu đề; nhãn `Tác giả:` |
| album | JSON-LD; mô tả Spotify; nhãn `Album:` |
| genre | JSON-LD; nhãn `Thể loại:` |
| lyrics | JSON-LD; khối có `id` / `class` chứa chữ `lyric`; khối nằm sau tiêu đề `Lời bài hát` / `Lyrics` / `Chord by …`; khối chứa hợp âm `[Am]` |

Cách tách theo từng site:

- **Nhac.vn:** tiêu đề dạng `Tên bài - Ca sĩ - Nhac.vn` nên tách được cả tên bài và ca sĩ; thể loại lấy từ nhãn `Thể loại:`; lời nằm sau tiêu đề `Lời bài hát`.
- **NhacCuaTui:** tiêu đề dạng `Lạc Trôi - Sơn Tùng M-TP - mp3 download | lyric - NhacCuaTui`; phần “mp3 download”, “lyric” và tên website bị bỏ; lời nằm sau tiêu đề `Lyrics`.
- **Hợp Âm Chuẩn:** tiêu đề dạng `Hợp âm Lạc Trôi - Sơn Tùng M-TP (Hợp âm cơ bản) - Hợp Âm Chuẩn`; thể loại lấy từ nhãn `Thể loại:`; lời có hợp âm dạng `[Am]` nên hợp âm bị bỏ, chỉ giữ lời.
- **Spotify:** tên bài lấy từ `og:title`; ca sĩ và album lấy từ mô tả `Ca sĩ · Album · Song · Năm` trong thẻ `<meta>`; HTML không có lời bài hát và thể loại nên hai cột này để `NULL` (`has_lyrics: False` trong `config.py`).

Nhãn chữ (`Ca sĩ`, `Thể loại`…) phải có dấu `:` để không nhầm với mục menu như “Album”, “Nghệ sĩ”. Danh sách nhãn và các cụm chữ bị bỏ khỏi tiêu đề (`SONG_LABELS`, `TITLE_NOISE`, `TITLE_PREFIXES`) nằm trong `config.py`, muốn thêm nhãn mới thì sửa ở đó, không cần sửa code.

## 7. Kết quả crawl (Crawling results)

Sau khi chạy, thống kê được in ra màn hình và lưu vào `data/crawl_summary.txt` (gồm cả số bài hát lưu vào bảng `songs` của từng domain).
Kết quả lần chạy cuối (05/10/2026, 16:08–16:13, khoảng 5 phút), đúng với `data/crawler.db` và `data/crawl_summary.txt` trong repo:

```
========== CRAWLING SUMMARY ==========

Topic                       : Music (Âm nhạc) – Sơn Tùng M-TP & nhạc Việt
Stop reason                 : MAX_PAGES reached

Seed URLs                   : 16 (+16 từ sitemap)
Pages Crawled               : 174
Pages Saved (DB)            : 160
Not saved (noindex)         : 14
Songs Saved (DB)            : 95
Unique URLs Discovered      : 2956
Skipped URLs                : 2541
Duplicate URLs Skipped      : 2345
Failed Requests             : 0
Links Found                 : 10175
Links Saved (DB)            : 10175
Avg Response Time           : 0.63 sec

Maximum Depth               : 2

Depth 0                     : 32 pages
Depth 1                     : 136 pages
Depth 2                     : 6 pages

HTTP 200                    : 174

Pages per domain (crawled / saved):
    Nhac.vn                 : 40 / 40
    NhacCuaTui              : 54 / 40
    Spotify                 : 40 / 40
    HopAmChuan              : 40 / 40

Songs per domain (saved | artist / album / genre / lyrics):
    Nhac.vn                 : 10 | 10 / 4 / 10 / 10
    NhacCuaTui              : 27 | 27 / 0 / 0 / 27
    Spotify                 : 31 | 31 / 31 / 0 / 0
    HopAmChuan              : 27 | 27 / 0 / 18 / 27

Skipped URLs by reason:
    DOMAIN_LIMIT            : 1631
    NOT_MUSIC_PAGE          : 855
    MAX_DEPTH               : 28
    OUTSIDE_DOMAIN          : 27
======================================
```

| Chỉ số | Giá trị |
|---|---|
| Pages Crawled (số trang đã gửi request) | 174 |
| Pages Saved (DB) | 160 |
| Not saved (noindex) | 14 (đều là trang NhacCuaTui) |
| Songs Saved (DB) | 95 |
| Unique URLs Discovered | 2956 |
| Skipped URLs | 2541 |
| Failed Requests | 0 |
| Links Saved (DB) | 10175 |
| Depth 0 / 1 / 2 (trang đã crawl) | 32 / 136 / 6 |

Số trang theo domain (`python check_db.py`) và độ dài cột `content`:

| Domain | Đã crawl | Lưu vào DB | Content (ký tự): min / trung bình / max |
|---|---|---|---|
| Nhac.vn | 40 | 40 | 2130 / 3376 / 4764 |
| NhacCuaTui | 54 | 40 | 459 / 1905 / 5377 |
| Spotify | 40 | 40 | 93 / 181 / 298 |
| HopAmChuan | 40 | 40 | 1232 / 5237 / 12438 |

Số bài hát trong bảng `songs` theo domain (số bài có giá trị ở từng cột, cột thiếu để `NULL`):

| Domain | Bài hát | artist | album | genre | lyrics |
|---|---|---|---|---|---|
| Nhac.vn | 10 | 10 | 4 | 10 | 10 |
| NhacCuaTui | 27 | 27 | 0 | 0 | 27 |
| Spotify | 31 | 31 | 31 | 0 | 0 |
| HopAmChuan | 27 | 27 | 0 | 18 | 27 |
| **Tổng** | **95** | 95 | 35 | 28 | 64 |

`python check_db.py` in ra 40 trang cho mỗi domain (`nhac.vn`, `www.nhaccuatui.com`, `open.spotify.com`, `hopamchuan.com`), tổng cộng 160 trang, 95 bài hát và 10175 link.

**Nhận xét:**

- Nhac.vn cho nhiều dữ liệu (bài hát có lời, nghệ sĩ, BXH) vì HTML render sẵn.
- NhacCuaTui không có thẻ `<a href>`: crawler lấy link trong `<script>` / JSON và sitemap. 14 trang gắn `noindex` được tôn trọng (không lưu), crawler tự lấy trang khác trong hàng đợi để đủ 40 trang.
- Spotify chỉ lấy được tiêu đề và mô tả (93–298 ký tự/trang) vì phần còn lại render bằng JavaScript; link đi theo thẻ `<meta music:*>`. 3 seed chỉ dẫn tới 31 trang ở depth 1, nên 6 trang còn lại nằm ở depth 2.
- Hợp Âm Chuẩn có HTML render sẵn, trang bài hát chứa lời + hợp âm nên content dài nhất (trung bình khoảng 5.200 ký tự/trang).

## 8. Đối chiếu yêu cầu đề bài

| Yêu cầu | Cách nhóm thực hiện | File / hàm |
|---|---|---|
| Python 3, Requests, BeautifulSoup, SQLite | `requests.Session`, `BeautifulSoup(html, "html.parser")`, `sqlite3` (thư viện chuẩn) | `requirements.txt`, `crawler.py`, `database.py` |
| Chọn chủ đề, ít nhất 2 domain | Music, 4 domain: Nhac.vn, NhacCuaTui, Spotify, Hợp Âm Chuẩn | `config.DOMAINS` |
| Kiểm tra robots.txt và HTML trước khi crawl, đổi domain nếu không crawl được | Bảng kiểm tra ở mục 1; Zing MP3 (web app JavaScript) được thay bằng Hợp Âm Chuẩn | mục 1 |
| **Task 1** – Seed URLs, cấu hình tách khỏi logic, in cấu hình khi chạy | 16 seed + 16 URL từ sitemap; mọi thông số nằm trong `config.py` | `config.py`, `main.print_config()` |
| **Task 2** – URL Frontier | `deque` chứa `(url, depth)`, `append()` / `popleft()` (FIFO), tập `queued` và `visited` | `url_frontier.URLFrontier` |
| **Task 3** – Gửi HTTP request | Timeout 10 giây, đo thời gian phản hồi, nghỉ 1 giây sau mỗi request; HTTP 403/404/500, timeout, lỗi kết nối được ghi nhận, crawler không dừng | `Crawler.fetch()`, `Crawler.crawl_page()` |
| **Task 4** – Lấy thông tin trang | `url, domain, title, content, depth, status_code, crawled_at`; content chưa tiền xử lý NLP | `parser.extract_page_data()` |
| **Task 5** – Trích xuất và lọc hyperlink | `<a href>` + `urljoin`; thêm `<meta music:*>` (Spotify) và URL trong `<script>` (NhacCuaTui); lọc protocol, domain, đuôi file, trang không phải âm nhạc | `parser.extract_links()`, `url_filter.check_url_rules()` |
| **Task 6** – Giới hạn độ sâu | Seed ở depth 0, link tìm thấy ở depth `d` có depth `d + 1`, chỉ nhận khi ≤ `MAX_DEPTH` (2) | `Crawler.try_add()`, `check_url_rules()` |
| **Task 7** – Chống trùng URL | Chuẩn hoá URL trước khi so sánh; `visited` / `queued`; `UNIQUE` trên `pages.url`, `songs.url`, `(source_url, target_url)` | `url_filter.normalize_url()`, `url_frontier.py`, `database.py` |
| **Task 8** – Lưu SQLite | Bảng `pages`, `links` và `songs` trong `data/crawler.db` (160 trang, 10175 link, 95 bài hát) | `database.py` |
| **Task 9** – Crawler hoàn chỉnh, điều kiện dừng | Dừng khi lưu đủ `MAX_PAGES`, khi URL Frontier rỗng hoặc khi nhấn Ctrl+C (vẫn in thống kê) | `Crawler.run()` |
| robots.txt | Đọc một lần cho mỗi domain, chuẩn hoá theo RFC 9309; tôn trọng `noindex` / `nofollow` | `robots.py`, `parser.read_meta_robots()` |
| Thống kê crawl | Tự đếm trong lúc crawl: số trang, URL duy nhất, URL bị bỏ qua (theo lý do), request lỗi, số trang theo depth, theo mã HTTP, theo domain | `Crawler.print_summary()` → `data/crawl_summary.txt` |
| README | Chủ đề, seed, cấu hình, chiến lược, luật lọc URL, database, kết quả | mục 1–7 |
| Kiểm thử (bổ sung) | 31 test offline cho chuẩn hoá / lọc URL, frontier, robots.txt, parser, bảng songs, SQLite và dữ liệu đã nộp; GitHub Actions chạy trên Python 3.10 và 3.13 | `tests/test_crawler.py` |
