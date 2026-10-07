# hopamchuan_phuquy - HopAmChuan Crawler

Focused Web Crawler cho **HopAmChuan.com**.

## Mục tiêu

Crawler thu thập tối đa **300 bài hát hợp lệ**. Website Hợp Âm Chuẩn có các trang điệu như `/rhythm/v/ballad`, trong đó mỗi bài dẫn tới trang `/song/<id>/<slug>/`. Trang bài hát chứa tên bài, nghệ sĩ, điệu, phần lời + hợp âm và có thể có video YouTube.

Một record chỉ được đưa vào SQLite khi có:

- URL bài hát hợp lệ
- title
- artist
- lyrics/lời + hợp âm tối thiểu 30 ký tự
- thumbnail URL đầy đủ (`http://` hoặc `https://`)

Không lưu trường `album`.

## Schema SQLite

```text
songs
├── id
├── url
├── title
├── artist
├── genre
├── thumbnail_url
├── lyrics
└── crawled_at
```

Có thêm hai bảng phục vụ assignment:

```text
pages
links
```

## Cơ chế

```text
rhythm pages
     ↓
URL Frontier (BFS)
     ↓
filter URL
     ↓
song page
     ↓
BeautifulSoup parser
     ↓
quality gate
     ↓
SQLite
```

Crawler dùng `requests + BeautifulSoup`, không cần Selenium vì các trang rhythm/song có thể được đọc từ HTML đã trả về. Pagination của rhythm sử dụng `offset`, ví dụ `?offset=10`.

## Thumbnail

Parser ưu tiên `og:image`/`twitter:image`. Nếu trang có YouTube iframe, parser lấy video ID và tạo URL thumbnail chuẩn:

```text
https://i.ytimg.com/vi/<VIDEO_ID>/hqdefault.jpg
```

Nếu không tìm được thumbnail URL đầy đủ, bài bị loại theo quality gate.

## Chạy

```powershell
python -m pip install -r requirements.txt
python main.py
```

Nên test trước bằng cách đổi trong `config.py`:

```python
MAX_SONGS = 5
```

Khi test OK thì đổi lại:

```python
MAX_SONGS = 300
```

Database:

```text
data/hopamchuan_phuquy.db
```

Summary:

```text
data/crawl_summary.txt
```

## Lưu ý bản quyền

Dữ liệu lời bài hát có thể thuộc quyền tác giả/bản quyền. Chỉ sử dụng crawler cho mục đích học tập, nghiên cứu hoặc dữ liệu mà bạn có quyền sử dụng; không tái phân phối kho lyrics thu thập được nếu không có quyền phù hợp. Hợp Âm Chuẩn cũng công bố quy định bản quyền riêng trên website.
