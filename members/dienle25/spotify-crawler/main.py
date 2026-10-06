"""Điểm vào CLI; audit, crawl được phép và kiểm tra SQLite."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from audit import spotify_audit
from config import Config, ROOT, TOPIC
from crawler import Crawler
from database import Database


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Focused crawler - Nhạc Việt trên Spotify')
    parser.add_argument('--audit-only', action='store_true', help='Chỉ kiểm tra robots/chính sách Spotify')
    parser.add_argument('--verify-db', action='store_true', help='Đếm và kiểm tra URL duy nhất trong SQLite')
    parser.add_argument('--seed', action='append', help='Seed website đã kiểm tra quyền crawl; lặp để thêm')
    parser.add_argument('--allow-domain', action='append', help='Tên host chính xác; lặp để thêm domain')
    parser.add_argument('--max-pages', type=int, default=Config.max_pages)
    parser.add_argument('--max-depth', type=int, default=Config.max_depth)
    parser.add_argument('--timeout', type=float, default=Config.request_timeout)
    parser.add_argument('--delay', type=float, default=Config.crawl_delay)
    parser.add_argument('--db', type=Path, default=Config.db_path)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'docs' / 'evidence')
    args = parser.parse_args()
    if args.verify_db:
        if not args.db.is_file():
            parser.error('Database chưa tồn tại; chạy crawler trước.')
        with Database(args.db) as database:
            counts = database.counts()
            counts['unique_url_check'] = counts['pages'] == counts['unique_urls']
            counts['integrity_check'] = database.connection.execute('PRAGMA integrity_check').fetchone()[0]
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0 if counts['unique_url_check'] and counts['integrity_check'] == 'ok' else 1
    if args.audit_only:
        result = spotify_audit(args.output_dir, args.timeout)
        print('SPOTIFY PAGE DIAGNOSTIC')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if args.seed and not args.allow_domain:
        parser.error('--seed yêu cầu --allow-domain để khai báo phạm vi tường minh.')
    try:
        config = Config(seed_urls=tuple(args.seed) if args.seed else Config.seed_urls,
                        allowed_domains=tuple(args.allow_domain) if args.allow_domain else Config.allowed_domains,
                        max_depth=args.max_depth, max_pages=args.max_pages,
                        request_timeout=args.timeout, crawl_delay=args.delay, db_path=args.db)
    except ValueError as exc:
        parser.error(str(exc))
    print('========== CRAWLER CONFIGURATION ==========')
    print(json.dumps({'topic': TOPIC, **asdict(config)}, ensure_ascii=False, indent=2, default=str))
    result = Crawler(config).run()
    result['configuration'] = {key: value for key, value in asdict(config).items() if key != 'db_path'}
    result['topic'] = TOPIC
    if not config.seed_urls:
        result['blockers'] = ['SPOTIFY_POLICY_RESTRICTION', 'SEED_URL_VALIDATION_PENDING',
                              'CUSTOM_TOPIC_REQUIRES_APPROVAL', 'SECOND_DOMAIN_REQUIRED']
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'crawl_summary.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('========== CRAWLING SUMMARY ==========')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['status'] == 'EXTERNAL_BLOCKER' else (1 if result['status'] == 'PARTIAL' else 0)


if __name__ == '__main__':
    raise SystemExit(main())
