"""
url_frontier.py - URL Frontier using BFS (Tasks 2, 6, 7)

The frontier stores URLs that were discovered but not crawled yet.
It is a FIFO queue (deque) => Breadth-First Search.
It also detects duplicate URLs and enforces the maximum depth.
"""

from collections import deque
from urllib.parse import urlparse, urlunparse


def normalize_url(url):
    """
    Normalize a URL so that equivalent URLs are treated as the same:
      - remove #fragment
      - lowercase scheme and host
      - remove default ports
      - remove trailing slash (except for the root path)
    Examples:
      https://example.com/news/1/   -> https://example.com/news/1
      https://EXAMPLE.com/news/1#a  -> https://example.com/news/1
    """
    parsed = urlparse(url.strip())

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # fragment is dropped (last argument = "")
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))

class URLFrontier:
    """
    BFS frontier with per-domain round-robin.

    Why: with a single shared FIFO queue, a domain with many internal
    links (e.g. nhac.vn) can fill the whole queue with its own links
    before the crawler ever reaches URLs from a smaller domain. To make
    sure every allowed domain gets crawled (as required by the
    assignment), URLs are grouped into one queue per domain, and next()
    picks domains in round-robin order (BFS is still respected inside
    each domain's own queue).
    """

    def __init__(self, max_depth):
        self.max_depth = max_depth
        self.queues = {}            # domain -> deque of (url, depth)
        self.domain_order = deque() # order in which domains are visited (round-robin)
        self.in_frontier = set()    # URLs currently waiting in any queue
        self.visited = set()        # URLs already taken out to be crawled
        self.discovered = set()     # every unique URL ever accepted

        # statistics
        self.skipped_count = 0

    # ----------------------------------------------------------
    def add(self, url, depth):
        """
        Try to add a URL to the frontier.
        Returns (accepted: bool, reason: str).
        """
        url = normalize_url(url)

        if depth > self.max_depth:
            self.skipped_count += 1
            return False, "SKIP: depth exceeds MAX_DEPTH"

        if url in self.visited:
            self.skipped_count += 1
            return False, "SKIP: URL already visited"

        if url in self.in_frontier:
            self.skipped_count += 1
            return False, "SKIP: URL already in frontier"

        domain = get_domain(url)
        if domain not in self.queues:
            self.queues[domain] = deque()
            self.domain_order.append(domain)

        self.queues[domain].append((url, depth))
        self.in_frontier.add(url)
        self.discovered.add(url)
        return True, "ACCEPT: URL added to frontier"

    def add_seed(self, url):
        """Seeds always have depth 0."""
        return self.add(url, 0)

    # ----------------------------------------------------------
    def next(self):
        """
        Take the next URL. Domains are visited in round-robin order;
        inside each domain, URLs are taken in FIFO (BFS) order.
        """
        for _ in range(len(self.domain_order)):
            domain = self.domain_order[0]
            self.domain_order.rotate(-1)  # move this domain to the back

            queue = self.queues.get(domain)
            if queue:
                url, depth = queue.popleft()
                self.in_frontier.discard(url)
                self.visited.add(url)
                return url, depth
        raise IndexError("next() called on an empty frontier")

    def is_empty(self):
        return all(len(q) == 0 for q in self.queues.values())

    def __len__(self):
        return sum(len(q) for q in self.queues.values())

    # ----------------------------------------------------------
    def show(self, limit=10):
        """Print the URLs waiting in the frontier."""
        print("========== URL FRONTIER ==========")
        shown = 0
        for domain in self.domain_order:
            for url, depth in self.queues[domain]:
                if shown >= limit:
                    break
                shown += 1
                print(f"[{shown}] (depth {depth}) {url}")
            if shown >= limit:
                break
        remaining = len(self) - shown
        if remaining > 0:
            print(f"... and {remaining} more")
        print("==================================")

    # ----------------------------------------------------------



def get_domain(url):
    """Return the host part of a URL."""
    return urlparse(url).netloc.lower()
