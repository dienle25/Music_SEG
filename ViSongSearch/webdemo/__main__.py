"""Chạy web demo ViSongSearch (gộp SEG301 + TMG301).

    python -m webdemo                          # tự tìm file .db, mở trình duyệt
    python -m webdemo --db D:\\duong\\dan\\file.db
    python -m webdemo --port 8001 --no-browser
    python -m webdemo --rebuild                # bỏ bộ nhớ đệm, dựng lại chỉ mục

Lần đầu mất khoảng 1 phút để dựng chỉ mục (lưu vào webdemo/_runtime), các lần sau khoảng 10-15 giây.
Phần đánh giá tự động chạy nền vài phút sau khi mở web; xem kết quả ở trang "Đánh giá".
"""
from __future__ import annotations

import argparse
import glob
import os
import socket
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:                  # để "import visong" chạy được dù đứng ở thư mục nào
    sys.path.insert(0, ROOT)

DB_NAME = "music_seg_merged_preprocesing.db"
NEEDED = {"fastapi": "fastapi", "uvicorn": "uvicorn", "jinja2": "jinja2", "sklearn": "scikit-learn",
          "scipy": "scipy", "numpy": "numpy", "datasketch": "datasketch"}


def find_db(arg: str | None) -> str | None:
    if arg:
        return arg if os.path.isfile(arg) else None
    if os.environ.get("VISONG_DB") and os.path.isfile(os.environ["VISONG_DB"]):
        return os.environ["VISONG_DB"]
    home = os.path.expanduser("~")
    places = [os.path.join(ROOT, "data"), os.path.join(ROOT, "visong"), ROOT, os.getcwd(),
              os.path.join(home, "Downloads", "ViSongSearch_source_latex", "visong"),
              os.path.join(home, "Downloads", "ViSongSearch_source_latex"),
              os.path.join(home, "Downloads", "ViSongSearch_source_latex", "data"),
              os.path.join(home, "Downloads")]
    for p in places:                       # ưu tiên đúng tên file của nhóm
        f = os.path.join(p, DB_NAME)
        if os.path.isfile(f):
            return f
    for p in places[:4]:
        found = sorted(glob.glob(os.path.join(p, "*.db")), key=os.path.getsize, reverse=True)
        if found:
            return found[0]
    return None


def free_port(host: str, port: int) -> int:
    for p in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host if host != "0.0.0.0" else "127.0.0.1", p))
                return p
            except OSError:
                continue
    return port


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")    # cửa sổ lệnh Windows không có bảng mã tiếng Việt
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="python -m webdemo", description="Web demo ViSongSearch (SEG301 + TMG301)")
    ap.add_argument("--db", help=f"đường dẫn file .db (mặc định: tự tìm {DB_NAME})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="không tự mở trình duyệt")
    ap.add_argument("--rebuild", action="store_true", help="xóa bộ nhớ đệm, dựng lại chỉ mục")
    ap.add_argument("--no-eval", action="store_true", help="không chạy phần đánh giá tự động")
    ap.add_argument("--runtime", default=os.path.join(HERE, "_runtime"), help="thư mục bộ nhớ đệm và nhật ký")
    args = ap.parse_args(argv)

    missing = []
    for mod, pkg in NEEDED.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Thiếu thư viện: " + ", ".join(missing))
        print("Cài bằng lệnh:  python -m pip install " + " ".join(missing))
        return 2

    db = find_db(args.db)
    if not db:
        print("Không tìm thấy file .db." + (f" Đường dẫn sai: {args.db}" if args.db else ""))
        print(f"Chép {DB_NAME} vào thư mục {os.path.join(ROOT, 'data')} hoặc chạy:")
        print("    python -m webdemo --db <đường dẫn tới file .db>")
        return 2
    db = os.path.abspath(db)
    os.makedirs(args.runtime, exist_ok=True)
    if args.rebuild:
        for f in glob.glob(os.path.join(args.runtime, "engine_*.pkl")) + glob.glob(os.path.join(args.runtime, "eval_*.json")):
            os.remove(f)

    port = free_port(args.host, args.port)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{port}"
    os.environ.setdefault("VISONG_BASE_URL", url)

    import uvicorn
    from webdemo.core import DemoEngine
    from webdemo.evaluation import Evaluator
    from webdemo.web import create_app

    print(f"File dữ liệu: {db}")
    print("Đang chuẩn bị bộ máy tìm kiếm (lần đầu khoảng 1 phút)...")
    t = time.time()
    eng = DemoEngine.load_or_build(db, args.runtime, print)
    s = eng.summary()
    print(f"Xong sau {time.time() - t:.0f} giây: {s['songs']} bài ({s['clusters']} cụm bản trùng), "
          f"{s['artists']} ca sĩ, {s['pages']} trang trong đồ thị liên kết.")
    ev = Evaluator(eng, os.path.join(args.runtime, f"eval_{eng.sig}.json"))
    if not args.no_eval:
        ev.start()
        if ev.state.get("status") == "xong":
            print("Kết quả đánh giá đã có từ lần chạy trước.")
        else:
            print("Phần đánh giá tự động chạy nền (vài phút); xem ở trang Đánh giá.")
    app = create_app(eng, ev, args.runtime)

    config = uvicorn.Config(app, host=args.host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    def open_when_ready():
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.1)
        print(f"\nWeb demo đang chạy: {url}   (nhấn Ctrl+C để dừng)\n")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
