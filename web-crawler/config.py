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
        "rewrite": [
            # /intl-vi/track/ID -> /track/ID
            (r"^/intl-[\w-]+(/.*)$", r"\1"),
        ],
    },

    # ---------- ZING MP3: web app JavaScript, mọi trang gắn noindex,nofollow ----------
    "zingmp3.vn": {
        "name": "Zing MP3",
        "aliases": ["www.zingmp3.vn"],
        "seeds": [
            "https://zingmp3.vn/",
            "https://zingmp3.vn/playlist/Nhung-Bai-Hat-Hay-Nhat-Cua-Son-Tung-M-TP/ZWZAC9BF.html",
        ],
        "allow": [
            r"^/$",
            r"^/(bai-hat|album|playlist|video-clip)/[\w-]+/\w+\.html$",
            r"^/[A-Z][\w-]*$",                               # trang nghệ sĩ: /Son-Tung-M-TP
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
MAX_PAGES = 120            # tổng số trang gửi request
MAX_PAGES_PER_DOMAIN = 40  # để 1 website không chiếm hết lượt crawl

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
