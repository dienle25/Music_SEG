"""FIFO URL frontier cho chiến lược Breadth-First Search (BFS)."""

from __future__ import annotations

from collections import deque

from urls import normalize_url


class URLFrontier:
    """Queue each normalized URL at most once per crawler run."""

    def __init__(self) -> None:
        self._queue: deque[tuple[str, int]] = deque()
        self.queued_urls: set[str] = set()
        self.visited_urls: set[str] = set()

    def add(self, url: str, depth: int) -> bool:
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
            raise ValueError("Crawl depth must be a nonnegative integer")
        normalized = normalize_url(url)
        if normalized is None or normalized in self.queued_urls or normalized in self.visited_urls:
            return False
        self._queue.append((normalized, depth))
        self.queued_urls.add(normalized)
        return True

    def pop(self) -> tuple[str, int]:
        """Select the oldest entry and mark it visited; empty queues raise IndexError."""
        url, depth = self._queue.popleft()
        self.queued_urls.remove(url)
        self.visited_urls.add(url)
        return url, depth

    def __bool__(self) -> bool:
        return bool(self._queue)

    def __len__(self) -> int:
        return len(self._queue)
