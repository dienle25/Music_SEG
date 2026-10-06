"""Crawl counters derived from actual events, ready for JSON reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any


@dataclass
class CrawlStatistics:
    pages_crawled: int = 0
    links_found: int = 0
    skipped_urls: int = 0
    failed_requests: int = 0
    discovered_urls: set[str] = field(default_factory=set)
    depth_counts: Counter[int] = field(default_factory=Counter)
    http_status_counts: Counter[int] = field(default_factory=Counter)
    errors: list[Any] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _started_monotonic: float = field(default_factory=monotonic, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe values; status/depth dictionary keys are strings."""
        return {
            "started_at": self.started_at.isoformat(),
            "elapsed_seconds": round(max(0.0, monotonic() - self._started_monotonic), 3),
            "pages_crawled": self.pages_crawled,
            "links_found": self.links_found,
            "unique_urls_discovered": len(self.discovered_urls),
            "skipped_urls": self.skipped_urls,
            "failed_requests": self.failed_requests,
            "depth_counts": {str(depth): count for depth, count in sorted(self.depth_counts.items())},
            "http_status_counts": {str(status): count for status, count in sorted(self.http_status_counts.items())},
            "errors": list(self.errors),
        }
