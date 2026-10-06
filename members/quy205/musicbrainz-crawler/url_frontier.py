from collections import deque


class URLFrontier:
    """FIFO queue used to implement Breadth-First Search (BFS)."""

    def __init__(self):
        self.queue = deque()
        self.queued = set()

    def add(self, url, depth):
        """Add a URL only once."""
        if url in self.queued:
            return False

        self.queue.append((url, depth))
        self.queued.add(url)
        return True

    def pop(self):
        """Remove and return the oldest (URL, depth) pair."""
        if not self.queue:
            return None

        return self.queue.popleft()

    def empty(self):
        return not self.queue

    def size(self):
        return len(self.queue)
