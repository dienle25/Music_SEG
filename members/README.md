# Bài cá nhân của từng thành viên

Ở giai đoạn đầu, mỗi thành viên tự làm một focused crawler cho một website âm nhạc.
Sau đó nhóm gộp lại thành crawler chung ở [`web-crawler/`](../web-crawler/).

Mỗi thư mục con là một project độc lập, giữ nguyên code và README của tác giả.

| Thành viên | Project | Website | Dữ liệu có sẵn trong repo | Trước khi sắp xếp nằm ở |
|---|---|---|---|---|
| [@TranKhoaDang](https://github.com/TranKhoaDang) | [nhacvn-crawler](TranKhoaDang/nhacvn-crawler/) | nhac.vn | `data/crawler.db`: 300 trang, 31127 link; báo cáo `REPORT.md` | `assiment1/` |
| [@thanhtrumbacang](https://github.com/thanhtrumbacang) | [nhaccuatui-crawler](thanhtrumbacang/nhaccuatui-crawler/) | nhaccuatui.com | chưa có database (chạy `main.py` để tạo `data/crawler.db`) | các file ở thư mục gốc repo |
| [@thanhtrumbacang](https://github.com/thanhtrumbacang) | [nhaccuatui-crawler-v2](thanhtrumbacang/nhaccuatui-crawler-v2/) | nhaccuatui.com (bảng xếp hạng) | `data/music.db`: 350 trang, 391 link, 277 bài hát | `nhaccuatui_hoanchinh_hieuthanh.zip` (nhánh `thanhtrumbacang-patch-2`, đã merge vào `main` ở commit [`5ba5f23`](https://github.com/dienle25/Music_SEG/commit/5ba5f23)) |
| [@dienle25](https://github.com/dienle25) | [spotify-crawler](dienle25/spotify-crawler/) | open.spotify.com | `data/crawler.db`: 0 trang (dừng crawl vì điều khoản sử dụng của Spotify); 47 test, tài liệu trong `docs/` | `spotify/` |
| [@dienle25](https://github.com/dienle25) | [spotify-crawler-v2](dienle25/spotify-crawler-v2/) | open.spotify.com (+ lời bài hát từ LRCLIB) | thống kê trong `data/`: 2654 trang, 2194 bài, 1572 bài có lời; database không đưa lên vì chứa lời bài hát có bản quyền; 31 test | mới: tách từ [`web-crawler/`](../web-crawler/) ngày 07/10/2026 |
| [@quy205](https://github.com/quy205) | [hopamchuan_phuquy](quy205/) (các file ở gốc thư mục `quy205/`) | hopamchuan.com | chưa có trên `main`; `hopamchuan_phuquy.db` (249 trang, 23174 link, 200 bài hát) nằm trong `hopamchuan_phuquy.zip` ở nhánh `quy205-patch-6` | mới: tải lên ngày 07/10/2026 (PR #14) |
| [@quy205](https://github.com/quy205) | [zingmp3-crawler](quy205/zingmp3-crawler/) | zingmp3.vn | `data/music.db`: 195 bài hát | `zingmp3_phuquy_lyrics.zip` và nhánh `quy205-patch-3` (đã merge vào `main` ở commit [`1e36688`](https://github.com/dienle25/Music_SEG/commit/1e36688)) |
| [@quy205](https://github.com/quy205) | [musicbrainz-crawler](quy205/musicbrainz-crawler/) | musicbrainz.org, freemusicarchive.org | `data/crawler.db`: 99 trang, 8461 link | `web-crawler-music-assignment-complete/` |

Ghi chú (10/10/2026): crawler Hợp Âm Chuẩn import `url_filter.py` và `url_frontier.py`. Hai file này mới có ở nhánh `quy205-patch-5`, chưa có trên `main`, nên chạy `python main.py` trong `quy205/` lúc này sẽ báo `ModuleNotFoundError`.

## Cách chạy một project

```bash
cd members/<thành viên>/<project>     # ví dụ: cd members/TranKhoaDang/nhacvn-crawler
pip install -r requirements.txt
python main.py
```

Phần lớn các project dùng đường dẫn tương đối `data/...` cho database, nên hãy chạy `main.py` từ chính thư mục của project.

## Lịch sử commit

Các file được chuyển bằng `git mv` nên lịch sử vẫn còn: trên GitHub mở file → **History**,
hoặc `git log --follow -- <đường dẫn file>`.
