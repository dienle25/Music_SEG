# zingmp3_phuquy

Crawler **Zing MP3 -> SQLite**, mục tiêu 300 bài hát hợp lệ.

Project này được chuyển theo cùng form của project `nhaccuatui_hthanh`:

```text
main.py
config.py
parser.py
crawler.py
database.py
schema.sql
requirements.txt
data/music.db
```

## 1. Website

Website mục tiêu:

```text
https://zingmp3.vn/
```

Zing MP3 hiện là dịch vụ âm nhạc của VNG/Zalo và cung cấp các khu vực như
Bảng xếp hạng và Top 100. 

## 2. Mục tiêu

Crawler cố gắng thu thập 300 bài hát.

Một bài chỉ được lưu nếu có:

- `title`
- `url`
- `thumbnail`

Lyric được phát hiện dưới dạng `lyric_url` và `lyric_status`.

Project không bulk-copy toàn bộ lời bài hát có bản quyền vào database.

## 3. SQLite

Database:

```text
data/music.db
```

Bảng:

```text
songs
```

Các trường:

```text
id
title
artist
category
url
thumbnail
duration
lyric_url
lyric_status
created_at
```

**Không có trường `album`.**

## 4. Cài đặt

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
```

## 5. Chạy

```powershell
python main.py
```

Ví dụ output:

```text
=================================================================
ZINGMP3_PHUQUY CRAWLER
=================================================================
Target: 300 | Existing: 0 | DB: data/music.db

[001] 200 https://zingmp3.vn/top100
  Song links found: ...

[002] 200 ...
  + SONG 1/300 | ...
```

## 6. Các tham số

Mở:

```text
config.py
```

Có thể thay:

```python
TARGET_SONGS = 300
MAX_PAGES = 1500
MAX_DEPTH = 3
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
CRAWL_DELAY = 1.0
```

## 7. Cơ chế crawler

```text
SEED_URLS
     |
     v
Queue (URL, depth)
     |
     v
robots.txt
     |
     v
HTTP request
     |
     +------> Trang thường
     |             |
     |             v
     |       extract_links()
     |             |
     |             v
     |          Queue
     |
     +------> URL bài hát
                   |
                   v
              parse_song()
                   |
                   v
          title + artist + thumbnail
                   |
                   v
                SQLite
```

## 8. Lưu ý về Zing MP3

Zing MP3 hiện sử dụng giao diện web có nhiều dữ liệu được render bằng JavaScript.
Do đó một số trang có thể không đưa toàn bộ danh sách bài hát trực tiếp vào HTML.

Nếu crawler báo:

```text
Song links found: 0
```

thì không phải SQLite bị lỗi; nghĩa là HTML trả về không chứa link bài hát
ở thời điểm crawler đọc trang.

Không bypass CAPTCHA hoặc anti-bot.

## 9. Robots.txt

Crawler có kiểm tra:

```text
https://zingmp3.vn/robots.txt
```

và bỏ qua URL nếu robots.txt không cho phép crawler.

## 10. Kiểm tra database

Có thể mở:

```text
data/music.db
```

bằng DB Browser for SQLite.

SQL kiểm tra số bài:

```sql
SELECT COUNT(*) FROM songs;
```

SQL kiểm tra bài có thumbnail:

```sql
SELECT title, artist, thumbnail
FROM songs
LIMIT 20;
```

SQL kiểm tra lyric:

```sql
SELECT title, lyric_url, lyric_status
FROM songs
LIMIT 20;
```

## 11. Tại sao không có album?

Theo yêu cầu assignment, trường:

```text
album
```

đã được loại bỏ hoàn toàn khỏi:

- parser
- database
- schema
- README

## 12. Quyền sử dụng nội dung

Zing MP3 nêu trong điều khoản rằng nội dung trên dịch vụ có thể thuộc
Zing hoặc được cấp phép hợp pháp cho Zing; vì vậy project chỉ lưu metadata,
thumbnail URL và tham chiếu/trạng thái lyric thay vì sao chép hàng loạt toàn
bộ lời bài hát.



## 13. Phiên bản Lyrics

Phiên bản này nâng cấp parser để:

1. Phát hiện `lyric_url`.
2. Nếu phần lyrics đã xuất hiện trực tiếp trong HTML, nhận diện vùng lyrics.
3. Lưu một `lyric_excerpt` ngắn để kiểm tra parser.
4. Lưu `lyric_chars` để biết có bao nhiêu ký tự lyrics được phát hiện.
5. Lưu `lyric_hash` SHA-256 để có thể so sánh/kiểm tra dữ liệu mà không lưu toàn bộ lời bài hát.

Database mới có thêm:

```text
lyric_excerpt
lyric_chars
lyric_hash
```

Ví dụ:

```sql
SELECT title, lyric_url, lyric_status, lyric_chars
FROM songs
LIMIT 20;
```

### Vì sao không lưu toàn bộ lyrics?

Lyrics là nội dung có thể được bảo hộ bản quyền. Project này phục vụ học tập về
crawler nên chỉ lưu URL, trạng thái, đoạn trích ngắn để kiểm tra parser, độ dài
và hash. Nếu bạn có **nguồn lyrics do bạn sở hữu hoặc được cấp phép**, có thể
thay `extract_lyric_metadata()` bằng parser của nguồn đó và lưu nội dung theo
phạm vi giấy phép.

### Quan trọng với Zing MP3

Nếu lyrics được tải bằng JavaScript sau khi HTML ban đầu trả về, parser HTML
có thể chỉ tìm thấy `lyric_url` hoặc không tìm thấy lyrics. Khi đó cần xác định
nguồn dữ liệu được trang cung cấp hợp pháp; không bypass CAPTCHA/anti-bot.
