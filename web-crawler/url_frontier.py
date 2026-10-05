# =========================================================
# url_frontier.py  –  TASK 2: URL Frontier (BFS)
# =========================================================
# Hàng đợi FIFO: URL vào trước được crawl trước
#   -> crawl hết depth 0, rồi depth 1, rồi depth 2 ... (BFS)

from collections import deque


class URLFrontier:

    def __init__(self):
        self.queue = deque()     # (url, depth) đang chờ crawl
        self.queued = set()      # URL đang nằm trong queue
        self.visited = set()     # URL đã lấy ra để crawl

    def add(self, url, depth):
        """Thêm URL nếu chưa crawl và chưa có trong hàng đợi."""
        if url in self.visited or url in self.queued:
            return False

        self.queue.append((url, depth))
        self.queued.add(url)
        return True

    def pop(self):
        url, depth = self.queue.popleft()
        self.queued.discard(url)
        return url, depth

    def mark_visited(self, url):
        self.visited.add(url)

    def is_visited(self, url):
        return url in self.visited

    def is_queued(self, url):
        return url in self.queued

    def is_empty(self):
        return not self.queue

    def __len__(self):
        return len(self.queue)

    def show(self, limit=15):
        print()
        print("=" * 10, "URL FRONTIER", "=" * 10)
        print()

        for i, (url, depth) in enumerate(list(self.queue)[:limit], start=1):
            print(f"[{i}] {url}   (depth {depth})")

        if len(self.queue) > limit:
            print(f"... và {len(self.queue) - limit} URL khác")

        print()
