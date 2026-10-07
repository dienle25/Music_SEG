# NhacCuaTui - 300 bài hát + Lyrics

Bản mới giữ form crawler với 3 bảng `links`, `pages`, `songs`.

## songs
`id, url, title, artist, genre, lyrics, crawled_at`

**Đã bỏ hoàn toàn `album`.**

Crawler chỉ tính một bài khi URL bài hát hợp lệ và có title. Bài không tìm được URL sẽ bị bỏ qua và không tăng bộ đếm. Crawler tiếp tục cho tới khi đạt đủ **300 bài hợp lệ** hoặc hết giới hạn crawl.

Lyrics được lấy từ vùng lyric trên trang bài hát và lưu vào `songs.lyrics`; không có lyric thì để trống.

Chạy:
```bash
pip install -r requirements.txt
python main.py
```

Crawler tôn trọng robots.txt và không bypass CAPTCHA/anti-bot.
