"""Lưu bằng chứng tests + crawl HTTP cục bộ, không gọi Spotify hay sửa crawler.db."""
import io
import json
import platform
import sqlite3
import sys
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_integration import LocalCrawlerIntegrationTests


def main():
    out = ROOT / 'docs' / 'evidence'
    out.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    (out / 'tests.log').write_text(log, encoding='utf-8')
    print(log)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
              'tests_run': result.testsRun, 'passed': result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
              'failed': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'uses_spotify': False, 'integration_transport': 'real HTTP on loopback; synthetic fixtures',
              'successful': result.wasSuccessful()}
    (out / 'test_results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if not result.wasSuccessful():
        return 1
    stages = []
    for limit in (3, 5, 10):
        fixture = LocalCrawlerIntegrationTests('test_real_http_bfs_and_sqlite_with_duplicate_cycle_filtering')
        fixture.setUp()
        try:
            summary = fixture.run_crawler(max_pages=limit)
            with closing(sqlite3.connect(fixture.db_path)) as connection:
                summary['sqlite_integrity'] = connection.execute('PRAGMA integrity_check').fetchone()[0]
                summary['sqlite_rows'] = connection.execute('SELECT COUNT(*), COUNT(DISTINCT url) FROM pages').fetchone()
            summary.update(max_pages=limit, evidence_type='SYNTHETIC_LOCAL_HTTP_ONLY',
                           http_request_order=fixture.requests)
            stages.append(summary)
        finally:
            fixture.doCleanups()
    (out / 'local_integration_stages.json').write_text(
        json.dumps(stages, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'tests': report, 'local_stages': [
        {'max_pages': s['max_pages'], 'pages': s['pages_crawled'], 'database': s['database_counts'],
         'depths': s['depth_counts'], 'stop_reason': s['stop_reason']} for s in stages]}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
