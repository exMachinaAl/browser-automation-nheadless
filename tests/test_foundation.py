"""v0.4.1 regression coverage: configuration, routing safeguards, record identity."""
import json
import tempfile
import unittest
from pathlib import Path

from gateway.core import Settings, Store, origin
from gateway.security import (
    validate_cdp_endpoint, valid_gateway_host_header, valid_browser_origin,
)

BASE = json.loads((Path(__file__).resolve().parents[1] / 'config.example.json').read_text(encoding='utf-8'))


class GuardTests(unittest.TestCase):
    def test_origin_invalid_port_and_uri(self):
        for raw in ('http://example.com:99999', 'http://[not-valid/',
                    'https://user:pass@example.com/', 'data:text/plain,hi'):
            self.assertEqual(origin(raw), '')

    def test_cdp_validates_parsed_hostname_not_text_prefix(self):
        for value in ('http://localhost:9222@evil.example',
                      'http://127.0.0.1:9222.evil.test',
                      'ws://127.0.0.1:9222@evil.test/devtools',
                      'http://0.0.0.0:9222',
                      'http://127.0.0.1:99999',
                      'https://127.0.0.1:9222',
                      'http://127.0.0.1:9222/path?token=secret',
                      'http://127.0.0.1:9222#fragment'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_cdp_endpoint(value)
        self.assertEqual(validate_cdp_endpoint('http://127.0.0.1:9222'), 'http://127.0.0.1:9222')
        self.assertEqual(validate_cdp_endpoint('ws://[::1]:9222/devtools/browser/id'), 'ws://[::1]:9222/devtools/browser/id')

    def test_host_header_and_origin_no_dns_rebinding(self):
        self.assertTrue(valid_gateway_host_header('127.0.0.1:8788', 8788))
        self.assertTrue(valid_gateway_host_header('localhost:8788', 8788))
        for value in ('localhost:8789', '127.0.0.1:8788@evil.test',
                      '127.0.0.1.evil.test:8788', 'evil.test:8788', 'localhost'):
            self.assertFalse(valid_gateway_host_header(value, 8788), value)
        self.assertTrue(valid_browser_origin('http://localhost:8788', 8788))
        self.assertTrue(valid_browser_origin(None, 8788))
        for value in ('null', 'https://localhost:8788', 'http://evil.test:8788',
                      'http://localhost:8789', 'http://localhost:8788@evil.test'):
            self.assertFalse(valid_browser_origin(value, 8788), value)

    def test_settings_validate_no_assert_dependencies(self):
        cfg = Settings.from_dict(BASE).raw
        self.assertEqual(cfg['allowed_origins'], ['http://127.0.0.1:8787'])
        for field, value in [('allowed_origins', []), ('start_url','file:///etc/passwd'),
                             ('host','0.0.0.0'), ('port',1), ('viewport',{'width':0,'height':700}),
                             ('cdp_endpoint','http://localhost:9222@external.example')]:
            bad = dict(BASE)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                Settings.from_dict(bad)

    def test_deduplication_separates_sessions(self):
        with tempfile.TemporaryDirectory() as dir:
            db=Store(Path(dir)/'store.sqlite')
            self.assertTrue(db.change('response', 'Hello', 's1'))
            self.assertFalse(db.change('response', 'Hello', 's1'))
            self.assertTrue(db.change('response', 'Hello', 's2'))
            self.assertEqual(len(db.changes()),2)
            db.db.close()


if __name__ == '__main__':
    unittest.main()
