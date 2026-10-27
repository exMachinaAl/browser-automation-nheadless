"""New v0.5.0 coverage: additive migrations, site profiles, serial jobs, profile isolation, tabs."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from gateway.browser_manager import BrowserManager, profile_dir
from gateway.core import Settings, Store
from gateway.jobs import JobQueue
from gateway.migrations import SCHEMA_VERSION
from gateway.site_profiles import validate_profile

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT/'config.example.json').read_text())


class MigrationAndProfileTests(unittest.TestCase):
    def test_additive_upgrade_retains_legacy_maps_and_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/'gateway.sqlite3'
            db=sqlite3.connect(dbpath)
            db.execute('''CREATE TABLE maps (id TEXT PRIMARY KEY,name TEXT,role TEXT,origin TEXT,
                          selector TEXT,rect TEXT,viewport TEXT,tag TEXT,input_type TEXT,created_at TEXT)''')
            db.execute('''CREATE TABLE changes (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,
                          target TEXT,observed_at TEXT,text TEXT,hash TEXT)''')
            db.execute('INSERT INTO changes(session_id,target,observed_at,text,hash) VALUES(?,?,?,?,?)',
                       ('S','response','now','PRESERVE-ME','h'))
            db.commit(); db.close()
            store=Store(dbpath)
            self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(store.changes()[0]['text'],'PRESERVE-ME')
            self.assertTrue(store.migration_backup and Path(store.migration_backup).is_file())
            before=store.migration_backup
            store.close()
            repeat=Store(dbpath)
            self.assertIsNone(repeat.migration_backup)
            self.assertTrue(Path(before).exists())
            repeat.close()

    def test_refuses_future_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/'gateway.sqlite3'
            db=sqlite3.connect(dbpath)
            db.execute('PRAGMA user_version = 99')
            db.close()
            with self.assertRaisesRegex(ValueError, 'newer_than_code'):
                Store(dbpath)

    def test_profile_validation_never_expands_origin_allowlist(self):
        allowed=['http://127.0.0.1:8787']
        payload={'origin':allowed[0],'name':'Demo','selectors':{'input':'#prompt','send':'#send'}}
        profile=validate_profile(payload,allowed)
        self.assertEqual(profile['name'],'Demo')
        for change in ({'origin':'https://evil.example'}, {'selectors':{'script':'javascript:alert(1)'}},
                       {'origin':'http://127.0.0.1:8787/sneaky'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_profile({**payload,**change},allowed)

    def test_site_profile_round_trip_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite')
            p={'origin':'http://127.0.0.1:8787','name':'test','selectors':{'ready':'#prompt'}}
            store.put_site_profile(p)
            self.assertEqual(store.site_profile(p['origin'])['selectors']['ready'],'#prompt')
            p['selectors']['ready']='#new-prompt'
            store.put_site_profile(p)
            self.assertEqual(len(store.site_profiles()),1)
            store.close()

    def test_profile_paths_reject_traversal_and_preserve_default(self):
        root=Path('/tmp/bag-fixture')
        self.assertEqual(profile_dir(root,'default'),root/'browser-profile')
        self.assertEqual(profile_dir(root,'work'),root/'browser-profiles'/'work')
        for name in ('../secrets','My Folder','.hidden','', 'a'*100,'a/b'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                profile_dir(root,name)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'jobs.sqlite')

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_serial_order_and_transient_results_not_persisted(self):
        sequence=[]
        async def execute(action,params):
            sequence.append(params['prompt'])
            await asyncio.sleep(.01)
            return {'status':'ok','text':'PRIVATE-' + params['prompt']}
        queue=JobQueue(self.store,execute)
        await queue.start()
        first=queue.submit('chat',{'prompt':'one'})
        second=queue.submit('chat',{'prompt':'two'})
        await queue.queue.join()
        self.assertEqual(sequence,['one','two'])
        self.assertEqual(self.store.get_job(first['id'])['status'],'completed')
        self.assertEqual(queue.results[second['id']]['text'],'PRIVATE-two')
        filetext=' '.join(str(x) for x in self.store.jobs())
        self.assertNotIn('PRIVATE-',filetext)
        await queue.stop()
        self.assertEqual(queue.results,{})

    async def test_cancel_before_start_and_interrupt_on_shutdown(self):
        called=[]
        async def execute(action,params):
            called.append(action)
            await asyncio.sleep(30)
        queue=JobQueue(self.store,execute,max_pending=2)
        a=queue.submit('click',{'map_id':'one'})
        b=queue.submit('click',{'map_id':'two'})
        with self.assertRaisesRegex(ValueError,'job_queue_full'):
            queue.submit('click',{'map_id':'three'})
        queue.cancel(b['id'])
        self.assertEqual(self.store.get_job(b['id'])['status'],'cancelled')
        await queue.start()
        await asyncio.sleep(.04)
        self.assertEqual(self.store.get_job(a['id'])['status'],'running')
        with self.assertRaisesRegex(ValueError,'cannot_be_safely_cancelled'):
            queue.cancel(a['id'])
        await queue.stop()
        self.assertEqual(self.store.get_job(a['id'])['status'],'interrupted')
        # Running actions are not silently restarted on the next queue.start().
        await queue.start()
        await asyncio.sleep(.05)
        self.assertEqual(called,['click'])
        await queue.stop()

    async def test_restart_marks_unfinished_jobs_interrupted(self):
        self.store.new_job('oldjob','read')
        self.store.update_job('oldjob','running')
        path=Path(self.tmp.name)/'jobs.sqlite'
        self.store.close()
        self.store=Store(path)
        self.assertEqual(self.store.get_job('oldjob')['status'],'running')
        # Schema migration does not run twice, but process startup must explicitly
        # mark stale jobs interrupted before new workers begin.
        self.store.interrupt_jobs()
        self.assertEqual(self.store.get_job('oldjob')['status'],'interrupted')


@unittest.skipUnless(os.environ.get('BAG_BROWSER_SMOKE') == '1' and Path(os.environ.get('BAG_SMOKE_CHROMIUM','/usr/bin/chromium')).exists(),
                     'Set BAG_BROWSER_SMOKE=1 with a Chromium executable')
class BrowserManagerSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
        from threading import Thread
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<html><title>Fixture</title><input id="prompt"><button id="send">send</button></html>'
                self.send_response(200)
                self.send_header('Content-Type','text/html')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.tmp=tempfile.TemporaryDirectory()
        cfg={**CFG,'start_url':self.url,'allowed_origins':[self.url],
             'headless':True,'chromium_executable':os.environ.get('BAG_SMOKE_CHROMIUM','/usr/bin/chromium')}
        self.settings=Settings.from_dict(cfg)
        self.store=Store(Path(self.tmp.name)/'test.sqlite')
        from gateway.core import Engine
        self.engine=Engine(self.settings,self.store, data_root=Path(self.tmp.name)/'user-data')
        # Skip sandbox-restricted HTTP; exercise real Chromium tabs and lifecycle.
        self.engine.allow=lambda url: url == 'about:blank' or url == self.url
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.close()
        self.tmp.cleanup()
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    async def test_multi_tab_selection_profile_and_restart(self):
        # In sandbox CI Chromium HTTP navigation can be blocked; DOM/tab lifecycle
        # is tested on about:blank separately from the network domain-probe tests.
        self.engine.allow=lambda url: url == 'about:blank' or url == self.url
        await self.engine.page.set_content('<main id=fixture>Browser fixture</main>')
        self.assertEqual(await self.engine.page.locator('#fixture').inner_text(),'Browser fixture')
        one=self.engine.manager.tabs()[0]['id']
        opened=await self.engine.new_tab('about:blank')
        self.assertEqual(len(opened['tabs']),2)
        two=[t['id'] for t in opened['tabs'] if t['selected']][0]
        await self.engine.select_tab(one)
        self.assertEqual(self.engine.manager.tabs()[0]['selected'],True)
        await self.engine.close_tab(two)
        self.assertEqual(len(self.engine.manager.tabs()),1)
        result=await self.engine.restart('qa')
        self.assertEqual(result['state'],'running')
        self.assertEqual(result['profile'],'qa')
        self.assertTrue((Path(self.tmp.name)/'user-data/browser-profiles/qa').is_dir())
        with self.assertRaisesRegex(ValueError,'invalid_browser_profile_name'):
            await self.engine.restart('../evil')

# Additional browser regression appended after primary smoke tests.
@unittest.skipUnless(os.environ.get('BAG_BROWSER_SMOKE') == '1' and Path(os.environ.get('BAG_SMOKE_CHROMIUM','/usr/bin/chromium')).exists(),
                     'Browser smoke is opt-in')
class ClosedBrowserSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_closed_page_reported_and_restart_recovers(self):
        from gateway.core import Engine
        with tempfile.TemporaryDirectory() as temp:
            c={**CFG,'headless':True,'chromium_executable':os.environ.get('BAG_SMOKE_CHROMIUM','/usr/bin/chromium')}
            cfg=Settings.from_dict(c)
            store=Store(Path(temp)/'store.sqlite')
            engine=Engine(cfg,store, data_root=Path(temp)/'profiles')
            engine.allow=lambda u: u == 'about:blank' or u == cfg['start_url']
            try:
                await engine.start()
                await engine.page.close()
                self.assertEqual((await engine.browser_status())['state'],'page_closed')
                await engine.restart()
                self.assertEqual((await engine.browser_status())['state'],'running')
            finally:
                await engine.close()
                store.close()

class AdditionalStorageSafetyTests(unittest.TestCase):
    def test_new_database_does_not_backup_empty_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'fresh.sqlite3')
            self.assertIsNone(store.migration_backup)
            self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            store.close()
            self.assertFalse((Path(directory)/'backups').exists())

    def test_symlink_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'browser-profiles').mkdir()
            try:
                (root/'browser-profiles'/'qa').symlink_to(root, target_is_directory=True)
            except (OSError,NotImplementedError):
                self.skipTest('Symlink creation unavailable on this platform')
            with self.assertRaisesRegex(ValueError,'browser_profile_symlink_not_allowed'):
                profile_dir(root,'qa')
