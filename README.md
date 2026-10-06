# Music_TMG – Focused Web Crawler chủ đề Âm nhạc

Bài **Assignment 1 – SEG301 (Crawls and Feeds)** của nhóm 4 thành viên, chủ đề **Music (Âm nhạc)**.
Mỗi thành viên làm một crawler cho một website âm nhạc, sau đó nhóm gộp và thống nhất thành **một crawler chung cho 4 website**.

| Thư mục | Nội dung |
|---|---|
| [`web-crawler/`](web-crawler/) | **Sản phẩm cuối của nhóm**: crawler chung cho Nhac.vn, NhacCuaTui, Spotify và Hợp Âm Chuẩn, kèm báo cáo chi tiết |
| [`members/`](members/) | Bài cá nhân của từng thành viên (giai đoạn làm riêng, trước khi gộp) |

## Thành viên và phân công

| Thành viên (GitHub) | Domain trong crawler chung | Bài cá nhân |
|---|---|---|
| [@TranKhoaDang](https://github.com/TranKhoaDang) | Nhac.vn | [nhacvn-crawler](members/TranKhoaDang/nhacvn-crawler/) |
| [@thanhtrumbacang](https://github.com/thanhtrumbacang) | NhacCuaTui | [nhaccuatui-crawler](members/thanhtrumbacang/nhaccuatui-crawler/), [nhaccuatui-crawler-v2](members/thanhtrumbacang/nhaccuatui-crawler-v2/) |
| [@dienle25](https://github.com/dienle25) | Spotify | [spotify-crawler](members/dienle25/spotify-crawler/) |
| [@quy205](https://github.com/quy205) | Hợp Âm Chuẩn (thay cho Zing MP3) | [zingmp3-crawler](members/quy205/zingmp3-crawler/), [musicbrainz-crawler](members/quy205/musicbrainz-crawler/) |

Zing MP3 được thay bằng Hợp Âm Chuẩn trong crawler chung vì Zing MP3 render bằng JavaScript, HTML trả về cho Requests gần như rỗng
(xem mục 1 trong [web-crawler/README.md](web-crawler/README.md)).

Chi tiết từng bài cá nhân (website, dữ liệu, cách chạy): [members/README.md](members/README.md).

## Crawler chung – `web-crawler/`

Python 3 + Requests + BeautifulSoup + SQLite. Crawl theo **BFS** từ 16 seed URL (+16 URL từ sitemap NhacCuaTui),
kiểm tra robots.txt, lọc URL theo luật riêng của từng website, lưu `pages`, `links` và `songs` vào `data/crawler.db`.

| Cấu hình | Giá trị |
|---|---|
| Maximum pages | 160 (40 trang / domain) |
| Maximum depth | 2 |
| Request timeout | 10 giây |
| Crawl delay | 1 giây |

Kết quả lần chạy cuối (05/10/2026):

| Domain | Trang lưu vào DB | Bài hát (bảng `songs`) |
|---|---|---|
| Nhac.vn | 40 | 10 |
| NhacCuaTui | 40 | 27 |
| Spotify | 40 | 31 |
| Hợp Âm Chuẩn | 40 | 27 |
| **Tổng** | **160** | **95** |

Tổng cộng 174 trang đã crawl (14 trang NhacCuaTui gắn `noindex` nên không lưu), 10175 link, 0 request lỗi.

### Cách chạy

Yêu cầu Python 3.10 trở lên.

```bash
cd web-crawler
pip install -r requirements.txt
python main.py        # crawl khoảng 4–5 phút, ghi vào data/crawler.db
python check_db.py    # xem dữ liệu: pages, songs, links
```

Trên Windows có thể bấm đúp `web-crawler/run.bat`.

Báo cáo đầy đủ (seed URL, chiến lược BFS, luật lọc URL, thiết kế database, thống kê): [web-crawler/README.md](web-crawler/README.md).

## Cấu trúc repo

```
Music_TMG/
├── README.md                          # trang tổng quan (file này)
├── web-crawler/                       # crawler chung của nhóm (bản nộp cuối)
│   ├── main.py                        # chạy chương trình
│   ├── config.py                      # seed, domain, giới hạn, luật lọc từng site
│   ├── url_frontier.py                # URL Frontier (BFS, FIFO)
│   ├── crawler.py                     # vòng lặp crawl + thống kê
│   ├── parser.py                      # title, content, link
│   ├── song_parser.py                 # title / artist / album / genre / lyrics
│   ├── url_filter.py                  # chuẩn hoá và lọc URL
│   ├── robots.py                      # robots.txt
│   ├── database.py                    # SQLite: pages, links, songs
│   ├── check_db.py                    # xem nhanh dữ liệu đã crawl
│   ├── run.bat                        # Windows: cài thư viện, crawl, xem dữ liệu
│   ├── requirements.txt
│   ├── README.md                      # báo cáo chi tiết
│   └── data/
│       ├── crawler.db
│       └── crawl_summary.txt
├── members/                           # bài cá nhân của từng thành viên
│   ├── README.md
│   ├── TranKhoaDang/nhacvn-crawler/
│   ├── thanhtrumbacang/nhaccuatui-crawler/
│   ├── thanhtrumbacang/nhaccuatui-crawler-v2/
│   ├── dienle25/spotify-crawler/
│   ├── quy205/zingmp3-crawler/
│   └── quy205/musicbrainz-crawler/
└── .github/workflows/spotify-tests.yml  # CI: chạy test của spotify-crawler
```

Lịch sử commit của từng file vẫn được giữ sau khi sắp xếp lại thư mục: trên GitHub mở file → **History**,
hoặc dùng `git log --follow -- <đường dẫn file>`.
