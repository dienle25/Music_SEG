# NhacCuaTui crawler database

## Nội dung dữ liệu

- `pages`: **306 trang đã crawl**. Trường `content` chỉ chứa văn bản thuần được trích xuất từ HTML bằng `get_text()`; không lưu mã HTML.
- `links`: **1.638 quan hệ liên kết** được phát hiện khi crawl. Mỗi dòng là một cặp `source_url` → `target_url`, tức một liên kết từ trang nguồn sang URL đích. Đây là số bản ghi liên kết, không phải số trang duy nhất. Ràng buộc duy nhất trên `(source_url, target_url)` ngăn lưu trùng cùng một cặp liên kết.
- `songs`: **300 bản ghi bài hát**. Phần lời đã được làm sạch các nhãn đầu trang `Lời bài hát`, `Đăng bởi` và tên người đăng khi chúng xuất hiện ở đầu lời. Bản ghi không có lời bài hát thực tế được để `NULL`.

## Các bảng

### `pages`
`id`, `url`, `domain`, `title`, `content`, `depth`, `status_code`, `crawled_at`

### `links`
`id`, `source_url`, `target_url`

### `songs`
`id`, `url`, `title`, `artist`, `album`, `genre`, `lyrics`, `crawled_at`

Các trường `album` và `genre` không có dữ liệu được lưu dưới dạng SQL `NULL`, không dùng chuỗi rỗng.

## Kích thước

Cơ sở dữ liệu đã được tối ưu bằng `VACUUM` sau khi làm sạch để loại bỏ phần dung lượng SQLite không còn sử dụng.
