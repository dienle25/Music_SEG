"""Quy tắc robots, không gọi mạng."""
import unittest
from robots import RobotsRules


class RobotsTests(unittest.TestCase):
    def rules(self, text, agent='SEG301-VietnameseMusicCrawler/1.0'):
        return RobotsRules(text, agent)

    def test_longest_match_overrides_earlier_allow_root(self):
        rules = self.rules('User-agent: *\nAllow: /\nDisallow: /embed/\nAllow: /embed/public')
        self.assertFalse(rules.can_fetch('https://example.com/embed/private'))
        self.assertTrue(rules.can_fetch('https://example.com/embed/public'))

    def test_allow_wins_equal_length_regardless_of_order(self):
        rules = self.rules('User-agent: *\nDisallow: /a\nAllow: /a')
        self.assertTrue(rules.can_fetch('https://example.com/a'))

    def test_specific_agent_overrides_wildcard(self):
        rules = self.rules('User-agent: *\nAllow: /\nUser-agent: SEG301\nDisallow: /')
        self.assertFalse(rules.can_fetch('https://example.com/'))

    def test_equally_specific_groups_are_combined(self):
        rules = self.rules('User-agent: *\nDisallow: /a\nUser-agent: *\nDisallow: /b')
        self.assertFalse(rules.can_fetch('https://example.com/a'))
        self.assertFalse(rules.can_fetch('https://example.com/b'))

    def test_wildcard_end_anchor_and_query(self):
        rules = self.rules('User-agent: *\nDisallow: /*.mp3$\nDisallow: /*?private=1')
        self.assertFalse(rules.can_fetch('https://example.com/a.mp3'))
        self.assertTrue(rules.can_fetch('https://example.com/a.mp3/info'))
        self.assertFalse(rules.can_fetch('https://example.com/a?private=1'))

    def test_percent_encoded_unreserved_path_cannot_bypass(self):
        rules = self.rules('User-agent: *\nDisallow: /blocked')
        self.assertFalse(rules.can_fetch('https://example.com/%62locked'))

    def test_unicode_and_percent_encoding_match(self):
        rules = self.rules('User-agent: *\nDisallow: /nhạc')
        self.assertFalse(rules.can_fetch('https://example.com/nh%E1%BA%A1c'))

    def test_empty_disallow_allows(self):
        self.assertTrue(self.rules('User-agent: *\nDisallow:').can_fetch('https://example.com/a'))

    def test_crawl_delay_and_request_rate_take_stricter_interval(self):
        rules = self.rules('User-agent: *\nCrawl-delay: 1.5\nRequest-rate: 1/4')
        self.assertEqual(rules.delay, 4)
