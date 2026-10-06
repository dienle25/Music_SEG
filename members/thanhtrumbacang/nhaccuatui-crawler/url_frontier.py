from collections import deque


class URLFrontier:
    """URL Frontier sử dụng BFS."""

    def __init__(self):
        self.queue = deque()
        self.waiting = set()

    def add(self, url, depth):
        if url in self.waiting:
            return False
        self.queue.append((url, depth))
        self.waiting.add(url)
        return True

    def get_next(self):
        if not self.queue:
            return None
        url, depth = self.queue.popleft()
        self.waiting.discard(url)
        return url, depth

    def empty(self):
        return len(self.queue) == 0

    def __len__(self):
        return len(self.queue)

    def items(self):
        return list(self.queue)
