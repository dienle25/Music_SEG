# =========================================================
# url_frontier.py - BFS URL Frontier
# =========================================================

from collections import deque


class URLFrontier:
    def __init__(self):
        self.queue = deque()
        self.queued = set()
        self.visited = set()

    def add(self, url, depth):
        if url in self.queued or url in self.visited:
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

    def is_empty(self):
        return not self.queue

    def is_visited(self, url):
        return url in self.visited

    def is_queued(self, url):
        return url in self.queued

    def __len__(self):
        return len(self.queue)
