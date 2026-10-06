# =========================================================
# config.py  –  TASK 1: Seed URLs & cấu hình crawler
# =========================================================
# Mọi thông số nằm ở file này, đổi cấu hình không cần sửa logic crawler.

TOPIC = "Music (Âm nhạc) – Sơn Tùng M-TP & nhạc Việt"


# ---------------------------------------------------------
# DOMAINS: mỗi website có seed và luật lọc URL riêng
#
#   name     : tên hiển thị
#   aliases  : host phụ được gộp về host chính (www / không www)
#   seeds    : URL bắt đầu (depth 0)
#   allow    : regex cho PATH được phép crawl
#              (focused crawler: chỉ nhận trang âm nhạc)
#   rewrite  : đổi URL kiểu cũ về một dạng chuẩn để không bị trùng
#   sitemaps : (tuỳ chọn) sitemap do chính website công bố trong
#              robots.txt – nguồn URL bổ sung cho site điều hướng
#              bằng JavaScript (HTML không có thẻ <a href>)
#   song_pages : regex cho PATH của TRANG BÀI HÁT. Trang khớp luật này
#              được tách thành title / artist / album / genre / lyrics
#              và lưu vào bảng songs (ngoài bảng pages)
#   has_lyrics : (tuỳ chọn, mặc định True) False nếu HTML của site không
#              có lời bài hát (Spotify) -> cột lyrics để NULL
# ---------------------------------------------------------

DOMAINS = {

    # ---------- NHAC.VN: HTML render sẵn, có đầy đủ <a href> ----------
    "nhac.vn": {
        "name": "Nhac.vn",
        "aliases": ["www.nhac.vn"],
        "seeds": [
            "https://nhac.vn/bai-hat/chung-ta-cua-hien-tai-son-tung-m-tp-solpo1X",
            "https://nhac.vn/nghe-si/son-tung-m-tp-atbQz5",
            "https://nhac.vn/bang-xep-hang-bai-hat-viet-nam-bxdE",
            "https://nhac.vn/",
        ],
        "allow": [
            r"^/$",                                          # trang chủ
            r"^/bai-hat/[\w-]+-so\w+$",                      # bài hát  ...-soXXXX
            r"^/nghe-si/[\w-]+-at\w+$",                      # nghệ sĩ  ...-atXXXX
            r"^/album/[\w-]+-pl\w+$",                        # album    ...-plXXXX
            r"^/video/[\w-]+-mv\w+$",                        # MV       ...-mvXXXX
            r"^/bang-xep-hang-[\w-]+-bx\w+$",                # BXH      ...-bxXXXX
            r"^/nhung-bai-hat-hay-nhat-cua-[\w-]+-ca\w+$",   # tuyển tập nghệ sĩ
        ],
        "song_pages": [
            r"^/bai-hat/[\w-]+-so\w+$",                      # bài hát  ...-soXXXX
        ],
    },

    # ---------- NHACCUATUI: có nội dung, nhưng link mở bằng JavaScript ----------
    "www.nhaccuatui.com": {
        "name": "NhacCuaTui",
        "aliases": ["nhaccuatui.com"],
        "seeds": [
            "https://www.nhaccuatui.com/playlist/rRmPsafCqIXD",   # Những Bài Hát Hay Nhất Của Sơn Tùng M-TP
            "https://www.nhaccuatui.com/playlist/cDFnrv2kVpFu",   # Top Songs: Sơn Tùng M-TP
            "https://www.nhaccuatui.com/playlist/ZLq1MthiuWE8",   # Sơn Tùng M-TP Full album
            "https://www.nhaccuatui.com/playlist/ecZiHDvBu9PD",   # M-TP
            "https://www.nhaccuatui.com/",
        ],
        "allow": [
            r"^/$",
            r"^/(song|playlist|artist|album|video)/[\w-]+$",
        ],
        "song_pages": [
            r"^/song/[\w-]+$",                               # bài hát (sau khi rewrite)
        ],
        "rewrite": [
            # /bai-hat/lac-troi-son-tung-m-tp.3tvGi3UEZLVT.html -> /song/3tvGi3UEZLVT
            (r"^/bai-hat/[^/]+\.([\w-]+)\.html$", r"/song/\1"),
            (r"^/playlist/[^/]+\.([\w-]+)\.html$", r"/playlist/\1"),
        ],
        "sitemaps": [
            "https://www.nhaccuatui.com/sitemap/sitemap_600.xml",   # các trang /song/...
            "https://www.nhaccuatui.com/sitemap/sitemap_300.xml",   # các trang /playlist/...
        ],
    },

    # ---------- SPOTIFY: link nằm trong thẻ <meta music:...> ----------
    "open.spotify.com": {
        "name": "Spotify",
        "seeds": [
            "https://open.spotify.com/artist/5dfZ5uSmzR7VQK0udbAVpf",     # Sơn Tùng M-TP
            "https://open.spotify.com/playlist/37i9dQZF1DWYPc4oQ0ynkq",   # Mãi Yêu Sơn Tùng M-TP
            "https://open.spotify.com/album/1V77kA4O1MlAUwfyBONfp1",      # Chúng Ta Của Hiện Tại
        ],
        "allow": [
            r"^/(artist|album|track|playlist)/[A-Za-z0-9]{22}$",
        ],
        "song_pages": [
            r"^/track/[A-Za-z0-9]{22}$",                     # bài hát (track)
        ],
        "has_lyrics": False,      # HTML chỉ có tiêu đề + mô tả, không có lời bài hát
        "rewrite": [
            # /intl-vi/track/ID -> /track/ID
            (r"^/intl-[\w-]+(/.*)$", r"\1"),
        ],
    },

    # ---------- HỢP ÂM CHUẨN: HTML render sẵn (lời + hợp âm), có đầy đủ <a href> ----------
    # (thay cho Zing MP3: Zing là web app JavaScript, HTML gần như rỗng)
    "hopamchuan.com": {
        "name": "HopAmChuan",
        "aliases": ["www.hopamchuan.com"],
        "seeds": [
            "https://hopamchuan.com/artist/21205/son-tung-m-tp",
            "https://hopamchuan.com/song/8926/lac-troi",
            "https://hopamchuan.com/song/41085/chung-ta-cua-hien-tai",
            "https://hopamchuan.com/",
        ],
        "allow": [
            r"^/$",                                          # trang chủ
            r"^/song/\d+/[\w-]+$",                           # bài hát   /song/8926/lac-troi
            r"^/artist/\d+/[\w-]+$",                         # nghệ sĩ   /artist/21205/son-tung-m-tp
        ],
        "song_pages": [
            r"^/song/\d+/[\w-]+$",                           # bài hát   /song/8926/lac-troi
        ],
    },
}


# Dạng danh sách giống ví dụ trong đề bài (tự sinh từ DOMAINS)
SEED_URLS = [url for site in DOMAINS.values() for url in site["seeds"]]
ALLOWED_DOMAINS = list(DOMAINS)
DOMAIN_ALIASES = {
    alias: host
    for host, site in DOMAINS.items()
    for alias in site.get("aliases", [])
}


# ---------------------------------------------------------
# GIỚI HẠN CRAWL
# ---------------------------------------------------------

MAX_DEPTH = 2              # seed = depth 0
MAX_PAGES = 160            # tổng số trang LƯU vào database (4 domain x 40)
MAX_PAGES_PER_DOMAIN = 40  # mỗi domain lưu tối đa 40 trang
# Trang lỗi / trang noindex không được lưu nên không tính vào 2 giới hạn trên.

REQUEST_TIMEOUT = 10       # giây
CRAWL_DELAY = 1.0          # giây nghỉ sau mỗi request

USE_SITEMAPS = True        # đọc sitemap trong DOMAINS[...]["sitemaps"]
SITEMAP_URL_LIMIT = 8      # số URL lấy từ mỗi sitemap


# File không phải trang HTML
BLOCKED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".zip", ".rar", ".pdf",
    ".mp3", ".mp4", ".m4a", ".flac", ".wav",
)


# ---------------------------------------------------------
# BẢNG songs: cách tách thông tin bài hát (song_parser.py)
# ---------------------------------------------------------
# Bảng songs có đúng 8 cột theo yêu cầu đề bài:
#   id, url, title, artist, album, genre, lyrics, crawled_at

# Nhãn chữ trong trang, dạng "Thể loại: Nhạc Trẻ" (không phân biệt hoa/thường).
# Bắt buộc có dấu ":" để không nhầm với mục menu như "Album", "Nghệ sĩ".
SONG_LABELS = {
    "artist": ["Ca sĩ", "Nghệ sĩ", "Trình bày", "Thể hiện", "Singer", "Artist"],
    "author": ["Tác giả", "Sáng tác", "Nhạc sĩ", "Composer"],   # chỉ dùng khi chưa biết ca sĩ
    "album":  ["Album"],
    "genre":  ["Thể loại", "Thể loại nhạc", "Genre"],
}

# Tiêu đề trang có dạng "Tên bài - Ca sĩ - Tên website", ví dụ:
#   "Lạc Trôi - Sơn Tùng M-TP - mp3 download | lyric - NhacCuaTui"
# Các cụm dưới đây (khớp CẢ cụm, không phân biệt hoa/thường) bị bỏ đi,
# phần còn lại là tên bài hát và ca sĩ.
TITLE_NOISE = [
    # tên website
    "Nhac.vn", "Nhạc.vn", "NhacCuaTui", "NhacCuaTui.com", "Nhạc Của Tui",
    "Hợp Âm Chuẩn", "HopAmChuan", "HopAmChuan.com", "Spotify",
    # cụm chữ chung chung
    "mp3", "mp3 download", "download", "lyric", "lyrics", "lời bài hát",
    "lời nhạc", "hợp âm", "chord", "chords", "nghe nhạc", "tải nhạc",
]

# Từ website thêm vào ĐẦU tên bài:  "Hợp âm Lạc Trôi"  ->  "Lạc Trôi"
TITLE_PREFIXES = ["Lời bài hát", "Lời nhạc", "Hợp âm", "Lyrics", "Chord"]


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

BOT_NAME = "SEG301-MusicCrawler"   # tên dùng khi kiểm tra robots.txt

# Một số site trả trang rỗng cho User-Agent lạ, nên dùng UA trình duyệt
# và vẫn gắn tên bot để website biết đây là crawler.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"(compatible; {BOT_NAME}/1.0)"
)


# ---------------------------------------------------------
# DATABASE & LOG
# ---------------------------------------------------------

DB_FILE = "data/crawler.db"             # tính từ thư mục project
SUMMARY_FILE = "data/crawl_summary.txt"
RESET_DB = True                         # xoá dữ liệu cũ mỗi lần chạy

VERBOSE = False   # True: in ACCEPT / SKIP cho từng link (Task 7)
