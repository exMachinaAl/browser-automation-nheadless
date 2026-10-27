"""Regression tests covering user-reported inability to browse websites."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import AsyncMock, MagicMock

from gateway.core import Settings, Store, Engine
from gateway.security import valid_browse_url
from gateway.demo_server import start_demo_if_needed

BASE=Path(__file__).resolve().parents[1]
CFG=json.loads((BASE/'config.example.json').read_text(encoding='utf-8'))

class NavigationPolicyTests(unittest.TestCase):
    def test_browser_can_visit_web_origins_syntactically(self):
        self.assertTrue(valid_browse_url('https://www.example.org/search?q=hi'))
        self.assertTrue(valid_browse_url('http://127.0.0.1:9888/'))
        self.assertTrue(valid_browse_url('http://localhost:8788/'))
        for url in ('file:///etc/passwd','javascript:alert(1)','https://a.com@evil.com/',
                    'https://u:p@a.com','data:text/html,hello','', 'ftp://example.org'):
            self.assertFalse(valid_browse_url(url),url)

    def test_default_bot_allowlist_does_not_become_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'data.sqlite3')
            eng=Engine(Settings.from_dict(CFG),store,data_root=Path(tmp)/'sessions')
            self.assertTrue(eng.allow('http://127.0.0.1:8787'))
            self.assertFalse(eng.allow('https://www.example.org'))
            store.close()

class NavigatorAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'store.sqlite')
        self.cfg=Settings.from_dict(CFG)
        self.engine=Engine(self.cfg,self.store,data_root=Path(self.tmp.name)/'user-data')

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_legacy_route_never_aborts_navigation(self):
        r=MagicMock()
        r.continue_=AsyncMock()
        r.abort=AsyncMock()
        await self.engine.route(r)
        r.continue_.assert_awaited_once()
        r.abort.assert_not_awaited()

    async def test_trusting_current_site_is_explicit_and_persistent(self):
        p=MagicMock()
        p.url='https://example.org/chat'
        p.is_closed.return_value=False
        self.engine.page=p
        with self.assertRaisesRegex(ValueError,'trusted_origin_mismatch'):
            await self.engine.trust_current('https://another.example')
        self.assertFalse(self.engine.allow(p.url))
        v=await self.engine.trust_current('https://example.org')
        self.assertIn('https://example.org',v['authorized_origins'])
        self.assertTrue(self.engine.allow(p.url))
        new=Engine(self.cfg,self.store,data_root=Path(self.tmp.name)/'user-data')
        self.assertTrue(new.allow('https://example.org/another'))
        self.assertFalse(new.allow('https://other.example.org'))

    async def test_manually_browsed_tab_does_not_auto_grant_origin(self):
        manager=MagicMock()
        page=MagicMock()
        page.url='https://example.org/home'
        manager.page=page
        manager.browse_tab=AsyncMock(return_value={'tabs':[],'active_url':page.url})
        self.engine.manager=manager
        v=await self.engine.browse_tab('https://example.org/home')
        self.assertEqual(v['automation_authorized'],False)
        manager.browse_tab.assert_awaited_once()

    async def test_generic_trusted_site_can_report_ready_without_demo_selector(self):
        p=MagicMock()
        p.url='https://example.org'
        p.is_closed.return_value=False
        p.evaluate=AsyncMock(return_value={'title':'Example','url':p.url,
           'text':'Hello','ready_state':'complete','password':False,'challenge':False})
        self.engine.page=p
        await self.engine.trust_current('https://example.org')
        self.assertEqual((await self.engine.probe())['status'],'ready')

    async def test_untrusted_site_status_distinguishes_browsing_from_automation(self):
        p=MagicMock()
        p.url='https://example.org'
        p.is_closed.return_value=False
        self.engine.page=p
        state=await self.engine.probe()
        self.assertEqual(state['status'],'untrusted_origin')

class DemoServerTests(unittest.TestCase):
    def test_server_condition_matches_only_demo_configuration(self):
        cfg={**CFG,'start_url':'https://example.org'}
        self.assertIsNone(start_demo_if_needed(cfg,BASE))
        # Only test port 8787 if available; otherwise another program is running there.
        import socket
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1',8787))
            except OSError:
                self.skipTest('Port 8787 already occupied')
        inst=start_demo_if_needed(CFG,BASE)
        try:
            self.assertIsNotNone(inst)
            with urlopen('http://127.0.0.1:8787', timeout=3) as reply:
                self.assertEqual(reply.status,200)
                self.assertIn(b'prompt',reply.read())
        finally:
            if inst:inst.close()
