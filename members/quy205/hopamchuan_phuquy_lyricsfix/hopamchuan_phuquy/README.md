# hopamchuan_phuquy — Hợp Âm Chuẩn Crawler

Crawler phục vụ bài tập thu thập dữ liệu từ **hopamchuan.com**, mục tiêu **200 bài hát hợp lệ**.

## Dữ liệu lưu

- `pages`: `url`, `title`, `domain`, `content`, `depth`, `status_code`, `crawled_at`.
- `songs`: `url`, `title`, `artist`, `album` (để `NULL`), `genre`, `lyrics`, `crawled_at`.
- `thumbnails`: `song_url`, `thumbnail_url` — thumbnail được tách khỏi `songs`.
- `links`: các liên kết phát hiện được giữa những trang.

Parser lấy `genre` từ ô giá trị ngay sau nhãn **Thể loại:**; lấy `artist` từ liên kết trong ô ngay sau **Tác giả:**. Lyrics được lấy từ khối nội dung lời bài hát, bỏ các hợp âm dạng `[C]`, `[G/B]`, `[A7#5]` và các dòng thông tin người đăng / **Báo lỗi**.

## Cơ chế crawler

```text
Seed URLs (rhythm)
       ↓
URL Frontier (BFS)
       ↓
lọc URL + kiểm tra robots.txt
       ↓
requests tải HTML
       ↓
BeautifulSoup phân tích DOM
       ↓
lọc chất lượng bài hát
       ↓
SQLite (pages, links, songs, thumbnails)
```

Crawler giới hạn độ sâu, số URL khám phá và số bài hát hợp lệ. Delay giữa các request tối thiểu 1 giây (cấu hình hiện tại 1.5 giây). User-Agent nhận diện bot là `hopamchuan_phuquy-StudentCrawler/1.0`.

## Seed URLs

- `https://hopamchuan.com/rhythm/v/ballad`
- `https://hopamchuan.com/rhythm/v/pop`
- `https://hopamchuan.com/rhythm/v/slow`
- `https://hopamchuan.com/rhythm/v/rock`

Seed `/rhythm/v/blues` đã được bỏ vì trả về 404 trong lần kiểm tra trước.

## Cài đặt và chạy

```powershell
python -m pip install -r requirements.txt
python main.py
```

Mỗi lần chạy mặc định `RESET_DB = True`, nên database được tạo lại trước khi crawl. Đích là **200 bài**, không phải 300. Khi crawler kết thúc, thống kê được ghi vào `data/crawl_summary.txt`.

- Database: `data/hopamchuan_phuquy.db`
- Summary: `data/crawl_summary.txt`

## Lưu ý

Chỉ sử dụng dữ liệu theo điều khoản website và trong phạm vi bạn có quyền sử dụng. Lyrics có thể được bảo hộ bản quyền; không tái phân phối dữ liệu thu thập được nếu chưa có quyền phù hợp.


### Trích xuất lyrics (bản cập nhật)

Parser ưu tiên các phần tử `.hopamchuan_lyric` trong từng `.chord_lyric_line`. Vì hợp âm nằm ở các phần tử anh em riêng (`.hopamchuan_chord_inline`), parser chỉ ghép văn bản lời bài hát, giữ xuống dòng và khoảng trống giữa các đoạn khi HTML cung cấp. Nếu trang không có khối này hoặc khối quá ngắn, parser mới dùng các nguồn dự phòng.
