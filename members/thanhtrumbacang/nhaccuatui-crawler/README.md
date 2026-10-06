# FOCUSED WEB CRAWLER – MUSIC

## 1. Selected Topic

**Topic: Music (Âm nhạc)**

**Selected domain: NhacCuaTui**

Website được sử dụng trong project:

```text
https://www.nhaccuatui.com/
```

Project này chỉ crawl **NhacCuaTui**. Không thêm domain thứ hai vì domain còn lại đã được thành viên khác trong nhóm thực hiện.

## 2. Seed URL

```text
https://www.nhaccuatui.com/
```

## 3. Crawling Configuration

```text
Maximum pages  : 50
Maximum depth  : 2
Request timeout: 10 seconds
Crawl delay    : 1 second
Allowed domain : nhaccuatui.com
```

Có thể thay đổi các thông số trong `config.py`.

## 4. Crawling Strategy

Crawler sử dụng **Breadth-First Search (BFS)**.

URL Frontier được cài đặt bằng `collections.deque`.

Mỗi phần tử có dạng:

```text
(url, depth)
```

Crawler lấy URL đầu hàng đợi bằng `popleft()` và thêm URL mới vào cuối hàng đợi bằng `append()`.

Ví dụ:

```text
Depth 0
  NhacCuaTui homepage

Depth 1
  Page A
  Page B
  Page C

Depth 2
  Page A1
  Page A2
  Page B1
  Page B2
```

## 5. URL Filtering Rules

Crawler chỉ nhận:

- HTTP hoặc HTTPS
- Domain `nhaccuatui.com`
- URL chưa được crawl
- URL chưa nằm trong Frontier
- Depth không vượt quá `MAX_DEPTH`

Crawler bỏ qua:

- `mailto:`
- `javascript:`
- `tel:`
- file ảnh
- CSS
- JavaScript
- file nén
- file âm thanh/video
- PDF và một số file tài liệu

URL tương đối được chuyển thành URL tuyệt đối bằng `urljoin()`.

Crawler kiểm tra `robots.txt` trước khi fetch URL.

## 6. Page Information

Mỗi trang HTML được lưu:

```text
url
domain
title
content
depth
status_code
crawled_at
```

BeautifulSoup được sử dụng để:

- lấy `<title>`
- lấy text hiển thị
- tìm các thẻ `<a href="...">`

## 7. Database Design

Database:

```text
data/crawler.db
```

### Table: pages

```text
id
url
domain
title
content
depth
status_code
crawled_at
```

### Table: links

```text
id
source_url
target_url
```

Bảng `pages` lưu thông tin các trang đã crawl.

Bảng `links` lưu quan hệ giữa URL nguồn và URL đích được phát hiện.

## 8. Duplicate URL Handling

Crawler sử dụng:

```python
visited = set()
```

để tránh crawl cùng URL nhiều lần.

`URLFrontier` cũng có `waiting` để tránh đưa một URL vào hàng đợi nhiều lần.

## 9. Error Handling

Crawler xử lý các trường hợp:

- HTTP 404
- HTTP 403
- HTTP 500
- timeout
- connection error

Request lỗi không làm dừng toàn bộ chương trình.

## 10. Crawling Statistics

Sau khi hoàn thành, chương trình hiển thị:

- Pages Crawled
- Unique URLs Discovered
- Skipped URLs
- Failed Requests
- Maximum Depth
- số trang theo depth
- số response theo HTTP status

Các số liệu được tính từ kết quả crawler.

## 11. Project Structure

```text
music_web_crawler/
│
├── main.py
├── crawler.py
├── url_frontier.py
├── parser.py
├── database.py
├── config.py
├── requirements.txt
├── README.md
│
└── data/
    └── crawler.db
```

`crawler.db` được tự tạo sau khi chạy.

## 12. How to Run

Mở folder project bằng VS Code.

Cài thư viện:

```powershell
pip install -r requirements.txt
```

Chạy:

```powershell
python main.py
```

## 13. Expected Output

```text
=======================================================
          FOCUSED WEB CRAWLER - MUSIC
=======================================================
Topic            : Music
Seed URLs:
  1. https://www.nhaccuatui.com/
Allowed Domains:
  - nhaccuatui.com
Maximum Pages    : 50
Maximum Depth    : 2
Request Timeout  : 10 seconds
Crawl Delay      : 1 second(s)
Database         : data/crawler.db
=======================================================

[Crawl #001]
  Depth : 0
  URL   : https://www.nhaccuatui.com/
  Status: 200
  Title : ...
  Links : ...
  Time  : ...

...

==================================================
 CRAWLING SUMMARY
==================================================
Topic                 : Music
Pages Crawled         : ...
Unique URLs Discovered: ...
Skipped URLs          : ...
Failed Requests       : ...
Maximum Depth         : 2
...
```

## 14. Note

Website structure and crawling policies can change. Before submitting, check the current `robots.txt` and terms of use of NhacCuaTui.

Không tăng `MAX_PAGES` quá lớn. Project sử dụng giới hạn nhỏ và crawl delay để phù hợp với bài thực hành.
