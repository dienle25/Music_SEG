# SEG301 – Focused Web Crawler (Music) – bản Spotify (v2)

Crawler viết bằng Python 3, dùng Requests, BeautifulSoup và SQLite. Bản này **chỉ crawl Spotify** (`open.spotify.com`).
Crawler bắt đầu từ các Seed URL (playlist, album) và đi theo link theo chiến lược **BFS**.
Kết quả được lưu vào `data/spotify.db`:

- trang, link và bài hát theo đúng các bảng mà đề bài yêu cầu;
- metadata đầy đủ của từng bài hát (bảng `tracks`);
- lời bài hát (bảng `lyrics` và cột `songs.lyrics`). HTML Spotify không có lời, nên `lyrics.py` tra lời trên LRCLIB, một kho lời bài hát mở.

`export_c2c.py` xuất dữ liệu sang đúng schema của đề tài nghiên cứu **C2C-VN**.

> Bản này do [@dienle25](https://github.com/dienle25) tách từ [crawler chung 4 website của nhóm](https://github.com/dienle25/Music_TMG/tree/main/web-crawler) để dùng cho đề tài nghiên cứu.
> Trên GitHub, bản này nằm ở [`members/dienle25/spotify-crawler-v2/`](https://github.com/dienle25/Music_TMG/tree/main/members/dienle25/spotify-crawler-v2).
> Crawler chung của nhóm và [bài cá nhân đầu tiên](https://github.com/dienle25/Music_TMG/tree/main/members/dienle25/spotify-crawler) vẫn giữ nguyên.

## Cấu trúc project

```
spotify-crawler-v2/
├── main.py            # chạy chương trình, in cấu hình
├── config.py          # Task 1 – seed, luật lọc, giới hạn crawl, tên file
├── seeds.txt          # seed bổ sung (mỗi dòng 1 link playlist / album)
├── url_frontier.py    # Task 2 – URL Frontier (deque FIFO + visited/queued)
├── crawler.py         # Task 3, 6, 7, 9 – vòng lặp BFS, retry 429/503, thống kê
├── parser.py          # Task 4, 5 – lấy title/content, trích xuất link
├── spotify_parser.py  # tách metadata trang bài hát / album / playlist
├── url_filter.py      # Task 5, 7 – chuẩn hoá URL, luật lọc URL
├── robots.py          # kiểm tra robots.txt (RFC 9309)
├── database.py        # Task 8 – SQLite: pages, links, songs, tracks, listings, meta
├── lyrics.py          # tra lời bài hát (LRCLIB) cho các bài đã crawl
├── check_db.py        # xem nhanh dữ liệu đã crawl
├── export_c2c.py      # xuất dữ liệu theo schema đề tài C2C-VN
├── run.bat            # Windows: bấm đúp để crawl, tra lời, xem và xuất dữ liệu
├── lay_loi_bai_hat.bat    # Windows: chỉ tra lời cho dữ liệu đã crawl
├── tests/
│   ├── test_spotify.py    # 21 kiểm thử crawler (offline)
│   └── test_lyrics.py     # 10 kiểm thử tra lời (offline)
├── data/
│   ├── spotify_summary.txt     # thống kê lần crawl 06/10/2026
│   ├── lyrics_summary.txt      # thống kê lần tra lời 06/10/2026
│   ├── c2c_spotify_report.json # báo cáo xuất cho đề tài
│   ├── spotify.db              # tạo khi chạy (không đưa lên GitHub)
│   └── c2c_spotify.sqlite      # tạo khi chạy export_c2c.py (không đưa lên GitHub)
├── requirements.txt
└── README.md
```

Database (`spotify.db`, `c2c_spotify.sqlite`) **không** được đưa lên GitHub. Lý do: chúng chứa lời bài hát có bản quyền (lấy từ LRCLIB) và dữ liệu crawl từ Spotify.
Thư mục `data/` trên GitHub chỉ có các file thống kê; chạy `run.bat` để tạo lại database.

## Cách chạy

Yêu cầu Python 3.10 trở lên.

```bash
pip install -r requirements.txt
python main.py          # crawl (tối đa 5000 trang hoặc 2,5 giờ)
python main.py --max-pages 30    # chạy thử nhanh, khoảng 1 phút
python lyrics.py        # tra lời bài hát cho các bài đã crawl (LRCLIB)
python check_db.py      # xem dữ liệu trong data/spotify.db
python export_c2c.py    # xuất data/c2c_spotify.sqlite cho đề tài C2C-VN
python -m unittest discover -s tests -v    # chạy kiểm thử offline
```

Trên Windows có thể bấm đúp `run.bat`. File này tự cài thư viện rồi chạy lần lượt `main.py`, `lyrics.py`, `check_db.py` và `export_c2c.py`.
Đã crawl rồi và chỉ cần lấy lời thì bấm đúp `lay_loi_bai_hat.bat`.
Muốn dừng sớm thì nhấn `Ctrl+C`: chương trình vẫn in thống kê, và dữ liệu đã lấy được vẫn nằm trong database.

---

## 1. Chủ đề và phạm vi

```
Topic : Music (Âm nhạc) – nhạc Việt trên Spotify
Domain: Spotify (open.spotify.com)
```

HTML mà Spotify trả về cho Requests (không chạy JavaScript) chỉ có phần `<head>`. Phần `<head>` gồm tiêu đề, mô tả và các thẻ `<meta music:*>`:

| Trang | Thông tin lấy được | Link đi tiếp |
|---|---|---|
| `/playlist/<id>` | tên, mô tả, khoảng 30 bài đầu (`music:song`) | các bài trong playlist |
| `/album/<id>` | tên, nghệ sĩ, ngày phát hành, **toàn bộ** bài trong album | các bài trong album |
| `/track/<id>` | tên bài, nghệ sĩ (tên + id), album, số thứ tự, ngày phát hành, thời lượng | album, trang nghệ sĩ |
| `/artist/<id>` | chỉ có tên và số người nghe, **không có link** | – (mặc định không tải) |

**Lời bài hát không nằm trong HTML Spotify.** HTML công khai mà Spotify trả về cho crawler không chứa lời bài hát. Ở lần chạy 06/10/2026, phần chữ của cả 2194 trang bài hát chỉ dài 109–970 ký tự, gồm tên bài và mô tả.
Lời chỉ hiện qua JavaScript của trình phát, và chủ yếu phải đăng nhập. Crawler không đăng nhập và không vượt qua bước đăng nhập.
Vì vậy lời được lấy ở bước riêng, `lyrics.py` (mục 8), từ LRCLIB. Cột `genre` vẫn để `NULL`, vì Spotify không có thể loại.

**Lưu ý về điều khoản.** Điều khoản sử dụng của Spotify không cho phép crawl / scrape. Đề tài C2C-VN cũng đã loại Spotify khỏi testbed chính vì lý do này.
Bản này chỉ dùng cho môn học và cho phân tích thăm dò (exploratory) và được giới hạn như sau:

- chỉ đọc trang công khai mà robots.txt cho phép, mỗi giây tối đa một request;
- chỉ lưu metadata, không lưu dữ liệu người dùng;
- không phát tán dữ liệu thô.

## 2. Seed URLs

Seed lấy từ hai nơi:

- 2 seed cố định trong `config.py`: playlist *Mãi Yêu Sơn Tùng M-TP* và album *Chúng Ta Của Hiện Tại*;
- các link trong `seeds.txt`:
  - bảng xếp hạng nhạc Việt (Top 50 - Vietnam, Top Bài Hát 2024 / 2025…);
  - 14 playlist “This Is <nghệ sĩ>” do Spotify biên tập;
  - 3 album tuyển tập bolero.

`seeds.txt` chỉ dùng playlist do Spotify biên tập (id bắt đầu bằng `37i9dQZ`) và album, không dùng playlist cá nhân.

Muốn thêm seed: trên Spotify mở playlist hoặc album, chọn *Chia sẻ → Sao chép liên kết*, rồi dán link vào `seeds.txt`, mỗi link một dòng.
Phần `?si=…` và `/intl-vi/` trong link được crawler tự bỏ.
Link sai hoặc đã bị gỡ chỉ trả HTTP 404 và bị bỏ qua, không làm dừng crawler.

## 3. Cấu hình (`config.py`)

```
Maximum pages     : 5000      (số trang LƯU vào database)
Maximum time      : 2.5 giờ   (MAX_HOURS)
Maximum depth     : 6
Request timeout   : 10 giây
Crawl delay       : 1 giây sau mỗi request
Retry             : HTTP 429 / 503, tối đa 3 lần, chờ theo Retry-After (tối đa 60 giây)
Dừng khi lỗi      : 10 request lỗi liên tiếp (mạng, 403, 429, 5xx)
Page types        : playlist, album, track (FETCH_ARTIST_PAGES = False)
```

- **Crawl delay 1 giây:** robots.txt của Spotify không khai báo `Crawl-delay`. Crawler nghỉ 1 giây sau mỗi request, nên gửi tối đa khoảng 1 request/giây.
- **429 / 503:** crawler chờ đúng số giây trong header `Retry-After` (nếu không có header thì chờ 5, 10, 20 giây) rồi thử lại.
  Nếu server yêu cầu chờ lâu hơn 60 giây thì crawler **dừng hẳn** thay vì gửi tiếp.
- **10 lỗi liên tiếp:** khi gặp liên tiếp lỗi mạng, 403, 429 hoặc 5xx, crawler hiểu là có thể đang bị chặn và dừng. Lỗi 404 (link hỏng) không tính vào số lỗi này.
- **Không tải trang nghệ sĩ:** trang `/artist/` không có link và không có thông tin mới, vì tên và id nghệ sĩ đã có trong trang bài hát.

## 4. Chiến lược crawl

**BFS** dùng URL Frontier (`url_frontier.py`):

- `deque` lưu cặp `(url, depth)`;
- `queued` và `visited` để không thêm / không crawl một URL hai lần;
- link tìm thấy ở trang depth `d` được thêm với depth `d + 1`.

Đồ thị link của Spotify khi không chạy JavaScript:

```
playlist (depth 0) ──> track (1) ──> album (2) ──> các track khác trong album (3) ──> ...
```

Mỗi playlist “This Is <nghệ sĩ>” dẫn tới khoảng 30 bài của nghệ sĩ đó. Từ mỗi bài, crawler đi tiếp tới album rồi tới các bài còn lại của album.
Nhờ vậy số bài của từng nghệ sĩ tăng lên, đúng thứ đề tài cần.

Crawler dừng khi gặp một trong các điều kiện sau, và lý do dừng được in trong thống kê:

- đã lưu đủ `MAX_PAGES` trang;
- chạy quá `MAX_HOURS`;
- URL Frontier rỗng;
- 10 request lỗi liên tiếp;
- server yêu cầu chờ quá lâu (`Retry-After`);
- người dùng nhấn `Ctrl+C`.

## 5. Luật lọc URL

**Chuẩn hoá** (`normalize_url`):

- relative URL được đổi thành absolute;
- bỏ `#fragment` và `?query` (ví dụ `?si=`);
- luôn dùng `https`, bỏ dấu `/` ở cuối URL;
- `/intl-vi/track/ID` được đổi thành `/track/ID`.

**Loại bỏ** (`check_url_rules`, kiểm tra theo thứ tự):

| Luật | Lý do bị loại |
|---|---|
| depth > `MAX_DEPTH` | `MAX_DEPTH` |
| không phải `http/https` | `INVALID_PROTOCOL` |
| host khác `open.spotify.com` | `OUTSIDE_DOMAIN` |
| đuôi `.jpg .css .js .mp3 …` | `NON_HTML_RESOURCE` |
| path không phải `/playlist/<id>`, `/album/<id>` hoặc `/track/<id>` với id 22 ký tự (ví dụ `/artist/`, `/user/`, `/embed/`) | `NOT_MUSIC_PAGE` |
| robots.txt không cho phép | `ROBOTS_TXT` |
| đã crawl hoặc đang trong hàng đợi | duplicate |

**robots.txt** (`robots.py`):

- mỗi domain chỉ đọc một lần;
- nội dung được chuẩn hoá theo RFC 9309 trước khi kiểm tra, vì robots.txt của Spotify có `Allow: /` đứng trước các dòng `Disallow: /embed/`…;
- mã HTTP, SHA-256 của file và các dòng `Content-Signal` (nếu có) được ghi vào bảng `meta` và in trong thống kê.

**Sau khi tải trang:**

- chỉ xử lý response `200` có `Content-Type: text/html`;
- nếu trang redirect, crawler kiểm tra lại URL cuối;
- trang có `noindex` thì không lưu, trang có `nofollow` thì không đi theo link.

**Lấy link** (`parser.extract_links`):

- lấy từ thẻ `<a href>`, từ thẻ `<meta music:*>` và từ URL / URI `spotify:track:…` nằm trong `<script>`;
- với thẻ `<meta music:*>`, chỉ nhận **giá trị là URL**.
  Bản 4 website từng lưu nhầm số thứ tự bài, ngày phát hành, thời lượng và tên ca sĩ thành link dạng `/track/1`, `/track/2018-05-12`, `/track/273`; lỗi này đã được sửa ở bản Spotify.
- không lấy link trang người dùng (`/user/…`, ví dụ người tạo playlist).

## 6. Thiết kế database (`data/spotify.db`)

Ba bảng theo đề bài, giữ nguyên tên và thứ tự cột như bản 4 website:

| Bảng | Nội dung |
|---|---|
| `pages` | `id, url, domain, title, content, depth, status_code, crawled_at`: mỗi trang HTML crawl thành công |
| `links` | `id, source_url, target_url`: đồ thị liên kết, `UNIQUE (source_url, target_url)` |
| `songs` | `id, url, title, artist, album, genre, lyrics, crawled_at`: mỗi trang bài hát. `lyrics` do `lyrics.py` điền (mục 8); Spotify không có thể loại nên `genre` = `NULL` |

Bốn bảng thêm cho đề tài:

**`tracks`** – một dòng / bài hát:

| Cột | Ý nghĩa |
|---|---|
| track_id | id Spotify (khoá chính) |
| url, title | link và tên bài |
| artists_json, artist_ids_json | tên và id nghệ sĩ trình bày, cùng thứ tự. Bài có nhiều nghệ sĩ mà không tách được tên thì `artists_json = []` |
| n_artists, artist_text | số nghệ sĩ; chuỗi tên gốc trên trang |
| album, album_id, track_number | album và số thứ tự bài trong album |
| release_date, year, duration_sec | ngày phát hành, năm, thời lượng (giây) |
| source_url | playlist / album mà crawler tìm thấy bài này |
| html_sha256, crawled_utc | SHA-256 của HTML (truy vết), thời điểm crawl (UTC) |

**`listings`** – album / playlist đã crawl:

- `kind, title, description, artist_ids_json, release_date`;
- `track_ids_json`: danh sách bài theo thứ tự;
- `n_tracks`, `n_new_tracks`: số bài, và số bài lần đầu được tìm thấy từ trang này.

Người tạo playlist **không** được lưu.

**`meta`** – thông tin lần chạy:

- phiên bản crawler, thời gian bắt đầu / kết thúc, lý do dừng;
- danh sách seed và cấu hình;
- thông tin robots.txt;
- số liệu thống kê.

**`lyrics`** – kết quả tra lời, một dòng / bài:

- `status`: `found` (có lời), `not_found` hoặc `instrumental` (nhạc không lời);
- `source`, `source_id`: `lrclib` + id trên LRCLIB, hoặc `db:<file>` + url khi lấy từ database khác;
- `match`: kiểu khớp (mục 8);
- `matched_title`, `matched_artist`, `matched_duration`: tên bài, ca sĩ, thời lượng ở nguồn lời;
- `plain_lyrics`: lời thường; `synced_lyrics`: lời có mốc thời gian (LRC), nếu nguồn có.

Khi crawl lại (`RESET_DB = True`), bảng `lyrics` **không** bị xoá, và lời đã tra được chép lại vào `songs.lyrics`. Nhờ vậy mỗi bài chỉ phải tra lời một lần.

## 7. Kết quả crawl

Sau mỗi lần chạy, thống kê được in ra màn hình và lưu vào `data/spotify_summary.txt`. File thống kê gồm:

- số trang theo loại (playlist / album / track);
- số bài hát đã lưu, số bài có album / ngày phát hành / thời lượng;
- số bài chỉ có một nghệ sĩ, số nghệ sĩ có từ 30 (và 24) bài trở lên, top nghệ sĩ;
- mã HTTP, các lần retry, lỗi và URL bị loại theo từng lý do.

`python check_db.py` in nhanh nội dung của từng bảng.

`data/spotify_summary.txt` được ghi ngay khi crawl xong, **trước** bước tra lời, nên dòng về lyrics trong file đó ghi `NULL`.
Số bài có lời xem ở `data/lyrics_summary.txt` (mục 8).

Lần chạy đầy đủ ngày 06/10/2026 (26 seed, 1 giờ 1 phút, dừng vì URL Frontier rỗng):

```
Pages Saved (DB)            : 2654  (21 playlist, 439 album, 2194 track)
Failed Requests             : 3     (1 seed 404, 2 track HTTP 500)
Retries (HTTP 429/503)      : 0
Tracks saved                : 2194  (đủ album, ngày phát hành, thời lượng)
single-artist tracks        : 1297
distinct artists            : 606
artists >= 30 tracks        : 22
```

## 8. Lời bài hát (`lyrics.py`)

```bash
python lyrics.py                  # tra mọi bài chưa có kết quả
python lyrics.py --limit 30       # chạy thử 30 bài
python lyrics.py --retry          # tra lại cả các bài lần trước không tìm thấy
python lyrics.py --from-db ../nhom/nhaccuatui.db   # lấy thêm lời từ database crawl của thành viên khác
```

Nguồn lời là [LRCLIB](https://lrclib.net): kho lời bài hát mở, có API công khai, không cần tài khoản.
Với mỗi bài, `lyrics.py` tìm theo thứ tự sau và dừng ở bước đầu tiên tìm thấy:

1. `/api/search` với tên bài + ca sĩ chính. Chỉ nhận kết quả có **cùng tên bài** và **cùng ca sĩ**, khi so khớp không phân biệt hoa thường và dấu tiếng Việt (“Bich Phuong” = “Bích Phương”). Nếu có nhiều kết quả, lấy kết quả có thời lượng gần nhất.
2. `/api/search` với tên gốc của bài, tức là bỏ phần ghi phiên bản như “- Live in Đà Lạt”, “(feat. …)”, “- Remix”.
3. `/api/get`: LRCLIB tự khớp theo tên bài + ca sĩ + album + thời lượng (lệch tối đa ±2 giây).

| `match` | Ý nghĩa |
|---|---|
| `exact`, `search` | cùng tên bài và ca sĩ, thời lượng lệch ≤ 3 giây: cùng bản thu |
| `other_version` | cùng tên bài và ca sĩ, thời lượng lệch ≤ 30 giây (bản edit / bản khác) |
| `base_title` | lời của bản gốc, khi bài trên Spotify là bản live / remix / feat. |
| `title_artist` | lấy từ database khác (`--from-db`), cùng tên bài và ca sĩ |

Nếu cần lời chắc chắn đúng bản thu thì chỉ dùng `exact` và `search`.

Các quy tắc khác:

- Bài có tên chứa “Instrumental”, “Karaoke” hoặc “(Beat)” được ghi là nhạc không lời, không gửi request.
- **Lịch sự với server:** nghỉ 0,5 giây trước mỗi request và gửi `User-Agent` có tên dự án.
  Khi LRCLIB báo quá tải (429 / 5xx), chương trình chậm lại và chờ theo `Retry-After`. Nếu lỗi liên tiếp 10 lần thì dừng.
- **Chạy tiếp được:** bài đã tra (có lời / không lời / không tìm thấy) được bỏ qua ở lần sau. Bài bị lỗi mạng sẽ được tra lại.
  Tra hơn 2000 bài mất khoảng 30–80 phút; có thể nhấn `Ctrl+C` bất cứ lúc nào rồi chạy lại.
- **`--from-db`** đọc bảng `songs(title, artist, lyrics)` theo đề bài từ database crawl của thành viên khác (NhacCuaTui, Nhac.vn, Hợp Âm Chuẩn…). Lời được lấy khi trùng tên bài và ca sĩ. Bước này chạy trước LRCLIB.
- Thống kê được lưu vào `data/lyrics_summary.txt`.

Kết quả tra lời cho 2194 bài hát. Lần chạy ngày 06/10/2026 tra hết; lần chạy ngày 07/10/2026 tra lại 15 bài bị lỗi mạng ở lần đầu:

```
Có lời (songs.lyrics)  : 1572 (72%)
    search             : 1497   (cùng tên bài, ca sĩ, thời lượng)
    base_title         : 58     (lời bản gốc của bản live / remix / feat.)
    exact              : 10
    other_version      : 7
Nhạc không lời         : 71
Không tìm thấy         : 551
Chưa tra               : 0
```

> Lời bài hát có bản quyền: chỉ lưu trong máy để học / nghiên cứu, không đăng lại.
> `export_c2c.py` **không** đưa lời vào file xuất cho đề tài (testbed chỉ dùng metadata); bài báo chỉ báo số đếm.

## 9. Xuất dữ liệu cho đề tài C2C-VN (`export_c2c.py`)

```bash
python export_c2c.py                       # data/spotify.db -> data/c2c_spotify.sqlite
python export_c2c.py --db A.db --out B.sqlite
```

File xuất ra có đúng 3 bảng `songs`, `listings`, `meta` như crawler HopAmChuan của đề tài (`c2c_crawl.py`), nên dùng được với `scripts/build_testbed.py` và `scripts/data_quality.py`.
Khi chạy, nên đặt `C2C_HOME` là một thư mục riêng để không ghi đè dữ liệu HopAmChuan:

```bash
python scripts/build_testbed.py --db <đường dẫn>/c2c_spotify.sqlite
```

| Cột C2C | Lấy từ | Ghi chú |
|---|---|---|
| song_id | `int(sha1(track_id)[:15], 16)` | số nguyên 60 bit, giữ nguyên giữa các lần chạy |
| url, title | tracks | |
| authors_json, author_ids_json, n_authors | tên + id nghệ sĩ | là **nghệ sĩ trình bày (performer)**, không phải tác giả sáng tác |
| genre, rhythm | `NULL` | Spotify không có |
| record_json | toàn bộ metadata + `"role": "performer"` | |
| html_sha256, crawled_utc, source_listing | tracks | `http_status = 200` |

Sau khi xuất, chương trình in và lưu (`data/c2c_spotify_report.json`) bản **ước tính theo đúng luật `prepare_records()` của testbed**:

- số bài chỉ có một nghệ sĩ, sau khi gộp các bài trùng tên bài + nghệ sĩ;
- số nghệ sĩ;
- số nghệ sĩ có ≥ 30 bài và ≥ 24 bài.

Lần xuất ngày 06/10/2026 (`data/c2c_spotify_report.json`):

```
Bài hát (bảng songs)           : 2194
Bỏ: nhiều nghệ sĩ / trùng bài  : 897 / 110
Bản ghi dùng được              : 1187  (bài 1 nghệ sĩ, đã gộp bài trùng)
Số nghệ sĩ                     : 139
Nghệ sĩ có >= 30 bài           : 9
Nghệ sĩ có >= 24 bài           : 10
```

Testbed cần nhiều nghệ sĩ có ≥ 30 bài; bản HopAmChuan có 56 tác giả như vậy. Với 9 nghệ sĩ, dữ liệu Spotify hiện **chỉ đủ cho phân tích thăm dò**.
Muốn tăng số này, hãy thêm playlist “This Is <nghệ sĩ>” vào `seeds.txt` rồi crawl lại.

Lưu ý khi dùng cho bài báo:

- Testbed chính (đã đăng ký trước, PREREG) vẫn là HopAmChuan. Dữ liệu Spotify chỉ dùng cho phân tích thăm dò (exploratory / post hoc) và phải được ghi rõ như vậy.
- Câu hỏi của testbed dùng chữ “tác giả / sáng tác”. Với Spotify, `authors` là ca sĩ trình bày, nên phải đổi câu hỏi (ví dụ “do … trình bày”) trước khi chạy.
- Spotify có nhiều phiên bản của cùng một bài (remix, live, “softer version”). Export giữ nguyên các phiên bản này. Testbed chỉ gộp các bài trùng **tên** + nghệ sĩ.

## 10. Kiểm thử

```bash
python -m unittest discover -s tests -v
```

31 kiểm thử chạy offline (không gửi request thật). HTML mẫu dựng lại đúng thứ tự thẻ `<meta>` mà Spotify trả về; LRCLIB được giả lập. Các kiểm thử bao gồm:

- tách metadata trang bài hát / album / playlist; tên nghệ sĩ có dấu phẩy hoặc dấu chấm (“Onionn.”, “Tyler, The Creator”);
- không còn link rác từ `<meta music:*>`, không lấy link `/user/`;
- luật lọc URL; mọi seed trong `config.py` và `seeds.txt` đều hợp lệ;
- crawl từ đầu tới cuối với website giả:
  - retry 429 theo `Retry-After`, dừng khi `Retry-After` quá dài;
  - giới hạn `MAX_PAGES` / `MAX_HOURS`, dừng sau nhiều lỗi liên tiếp;
  - trang `noindex`, trang 404;
- bảng `songs` giữ đúng schema đề bài; file xuất có đúng schema C2C-VN và chạy được câu `SELECT` của `build_testbed.py`;
- tra lời:
  - khớp theo thời lượng, ca sĩ không dấu, bản live (`base_title`), lời có mốc thời gian;
  - không nhận nhầm ca sĩ (“MIN” ≠ “Minh Tuyết”), bỏ qua nhạc không lời;
  - thử lại khi gặp 503, dừng khi lỗi liên tiếp, chạy tiếp / `--retry`, `--from-db`;
  - bảng `lyrics` còn nguyên sau khi crawl lại.
