# Dữ liệu của crawler chung

| File | Nội dung |
|---|---|
| `crawler.db` | Database của crawler chung, tạo bằng `python main.py`: 160 trang (40 trang mỗi website), 10175 link, 95 bài hát |
| `crawl_summary.txt` | Thống kê lần crawl cuối của crawler chung |
| `music_seg_merged.db` | **Database gộp của cả nhóm**: `crawler.db` và database của từng thành viên, 997 trang, 63557 link, 602 bài hát |

## Database gộp `music_seg_merged.db`

Tạo bằng [`merge_db.py`](../merge_db.py). Form giống `crawler.db`: ba bảng `pages`, `links`, `songs` tạo bằng
`database.create_tables()`, cùng tên cột, thứ tự cột và chỉ mục, nên câu truy vấn viết cho `crawler.db` chạy được trên file này.

Cách gộp:

- đọc các database nguồn theo thứ tự trong bảng dưới, trong mỗi nguồn đọc theo `id` tăng dần;
- trùng `url` (bảng `pages`, `songs`) hoặc trùng cặp `(source_url, target_url)` (bảng `links`) thì giữ dòng của nguồn đứng trước;
- `id` được đánh lại từ 1 theo thứ tự đọc; database nguồn chỉ được mở ở chế độ đọc.

| # | Nguồn | `pages` | `links` | `songs` |
|---|---|---|---|---|
| 1 | @thanhtrumbacang, NhacCuaTui (bản đã làm sạch): `members/thanhtrumbacang/nhaccuatui-crawler-v2/music_clean.db` | 306/306 | 1638/1638 | 300/300 |
| 2 | @quy205, Hợp Âm Chuẩn: `members/quy205/hopamchuan_crawler_quy205_lyrics_pending/hopamchuan_phuquy/data/hopamchuan_phuquy.db` | 249/249 | 23041/23041 | 200/200 |
| 3 | @TranKhoaDang, nhac.vn: `members/TranKhoaDang/nhacvn-crawler/data/crawler.db` | 300/300 | 29911/29911 | 12/12 |
| 4 | crawler chung (4 website, có Spotify): `web-crawler/data/crawler.db` | 142/160 | 8967/10175 | 90/95 |

Mỗi ô là số dòng được thêm / số dòng có trong nguồn. Ở nguồn 4, phần chênh lệch là các dòng đã có từ nguồn 1–3.

| Website | `pages` | `songs` |
|---|---|---|
| www.nhaccuatui.com | 341 | 322 |
| nhac.vn | 327 | 22 |
| hopamchuan.com | 289 | 227 |
| open.spotify.com | 40 | 31 |
| **Tổng** | **997** | **602** |

Bảng `links` có 63557 dòng.

### Chưa gộp

| Database | Lý do |
|---|---|
| `members/quy205/zingmp3-crawler/data/music.db` | khác form: chỉ có bảng `songs` với các cột riêng (`category`, `lyric_excerpt`, `lyric_hash`, …), không có `pages`, `links` |
| `members/quy205/musicbrainz-crawler/data/crawler.db` | không thuộc 4 website của crawler chung (musicbrainz.org, freemusicarchive.org) và không có bảng `songs` |
| `members/dienle25/spotify-crawler/data/crawler.db` | rỗng; dữ liệu Spotify trong bản gộp lấy từ `crawler.db` |
| `members/thanhtrumbacang/nhaccuatui-crawler-v2/data/music.db` | bản cũ, thay bằng `music_clean.db` |
| bảng `thumbnails` trong database Hợp Âm Chuẩn | không có trong form chung |

### Tạo lại và kiểm tra

```bash
cd web-crawler
python merge_db.py           # tạo lại data/music_seg_merged.db
python merge_db.py --check   # kiểm tra file đã nộp, không ghi đè
```

`--check` gộp lại từ các database nguồn vào một file tạm rồi so với file đã nộp: `PRAGMA integrity_check`, bảng / cột / chỉ mục
giống `crawler.db`, và nội dung từng dòng giống nhau. Phép so sánh dựa trên nội dung chứ không so từng byte, vì SQLite ở máy khác
có thể xếp trang trong file khác đi.

Khi một thành viên cập nhật hoặc chuyển chỗ database của mình, sửa đường dẫn trong `SOURCES` ở đầu `merge_db.py`
rồi chạy lại `python merge_db.py`.
