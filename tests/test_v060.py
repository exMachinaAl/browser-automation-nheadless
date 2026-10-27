"""v0.6.0 evidence, snapshot, migration and browser geometry regressions."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gateway.core import Engine, Settings, Store
from gateway.precision import (validate_snapshot, validate_presentation, anchor_evidence, check_anchor)

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'config.example.json').read_text())
BROWSER=Path(os.getenv('BAG_SMOKE_CHROMIUM','/usr/bin/chromium'))
SMOKE=os.getenv('BAG_BROWSER_SMOKE')=='1' and BROWSER.is_file()

class PrecisionPureTests(unittest.TestCase):
    def test_snapshot_change_and_expiry(self):
        expected={'url':'about:blank','viewport':{'width':700},'scroll':{'x':0,'y':0},
                  'presentation':{'device_pixel_ratio':1},'tab_marker':'a'}
        validate_snapshot(expected,expected,elapsed=3)
        changed={**expected,'scroll':{'x':0,'y':18}}
        with self.assertRaisesRegex(ValueError,'snapshot_layout_changed'):
            validate_snapshot(expected,changed,elapsed=3)
        with self.assertRaisesRegex(ValueError,'snapshot_expired'):
            validate_snapshot(expected,expected,elapsed=91)

    def test_dpr_zoom_scroll_mismatch(self):
        recorded={'device_pixel_ratio':1,'visual_scale':1,'visual_offset_x':0,'visual_offset_y':0,
                  'scroll':{'x':0,'y':0}}
        current={'presentation':dict(device_pixel_ratio=1,visual_scale=1,visual_offset_x=0,visual_offset_y=0),
                 'scroll':{'x':0,'y':0}}
        validate_presentation(recorded,current)
        current['presentation']['device_pixel_ratio']=1.25
        with self.assertRaisesRegex(ValueError,'presentation_mismatch'):
            validate_presentation(recorded,current)
        current['presentation']['device_pixel_ratio']=1
        current['scroll']['y']=6
        with self.assertRaisesRegex(ValueError,'scroll_mismatch'):
            validate_presentation(recorded,current)

    def test_anchor_never_stores_response_text(self):
        element={'tag':'div','input_type':'','testid':'response','text':'PRIVATE RESPONSE CONTENT'}
        self.assertNotIn('PRIVATE',str(anchor_evidence(element,'response')))
        with self.assertRaisesRegex(ValueError,'element_identity_mismatch'):
            check_anchor({'testid':'submit-x'},{'testid':'submit-y'})

    def test_revision_round_trip_migration_legacy_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'db.sqlite3'
            d=sqlite3.connect(p)
            d.execute('CREATE TABLE maps(id TEXT PRIMARY KEY,name TEXT,role TEXT,origin TEXT,selector TEXT,rect TEXT,viewport TEXT,tag TEXT,input_type TEXT,created_at TEXT)')
            d.execute('CREATE TABLE changes (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,target TEXT,observed_at TEXT,text TEXT,hash TEXT)')
            d.execute("INSERT INTO changes(session_id,target,observed_at,text,hash) VALUES('s','t','d','KEEP_ME','h')")
            d.execute('PRAGMA user_version=2')
            d.commit();d.close()
            st=Store(p)
            self.assertEqual(st.changes()[0]['text'],'KEEP_ME')
            self.assertEqual(st.db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertTrue(st.migration_backup and Path(st.migration_backup).is_file())
            sample=dict(id='map1',name='send',role='send',origin='https://site.example',selector='#send',
                        rect={'x1':0,'y1':0,'x2':80,'y2':32},viewport={'width':900,'height':700},
                        tag='button',input_type='',created_at='now')
            meta={'presentation':{},'scroll':{},'anchor':{}}
            self.assertEqual(st.save_evidence(sample,meta),1)
            new={**sample,'rect':{'x1':10,'y1':0,'x2':90,'y2':32}}
            self.assertEqual(st.save_evidence(new,meta,update=True),2)
            self.assertEqual(st.get_map('map1')['rect']['x1'],10)
            self.assertEqual([r['revision'] for r in st.mapping_revisions('map1')],[2,1])
            st.close()

@unittest.skipUnless(SMOKE,'Set BAG_BROWSER_SMOKE=1 with Chromium executable for precision DOM smoke')
class PrecisionBrowserSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.tmp=tempfile.TemporaryDirectory()
        self.st=Store(Path(self.tmp.name)/'precision.sqlite3')
        self.pw=await async_playwright().start()
        self.br=await self.pw.chromium.launch(headless=True,executable_path=str(BROWSER))
        self.context=await self.br.new_context(viewport={'width':980,'height':700},device_scale_factor=2)
        self.pg=await self.context.new_page()
        await self.pg.set_content('''<style>body{margin:0}#send{position:absolute;top:40px;left:40px;width:110px;height:50px}#spacer{height:1800px}</style>
          <button id="send" data-testid="primary-action">Submit</button><div id="spacer"></div>''')
        self.eng=Engine(Settings.from_dict(CFG),self.st)
        self.eng.page=self.pg
        self.eng.allow=lambda u:u=='about:blank'
        async def ready():return {'status':'ready'}
        self.eng.probe=ready

    async def asyncTearDown(self):
        await self.br.close();await self.pw.stop();self.st.close();self.tmp.cleanup()

    def button_rect(self):
        return {'x1':42,'y1':42,'x2':148,'y2':88}

    async def test_capture_css_pixels_at_device_scale_2(self):
        # PNG IHDR image width/height must equal CSS viewport, not physical pixels.
        result=await self.eng.capture()
        self.assertEqual(int.from_bytes(result['data'][16:20],'big'),980)
        self.assertEqual(int.from_bytes(result['data'][20:24],'big'),700)
        self.assertEqual(result['presentation']['device_pixel_ratio'],2)

    async def test_remap_revision_and_stale_snapshot(self):
        shot=await self.eng.capture()
        m=await self.eng.save_map('send','send',self.button_rect(),shot['id'])
        self.assertEqual(m['revision'],1)
        self.assertTrue((await self.eng.preview_map(m['id']))['ok'])
        await self.pg.evaluate('window.scrollTo(0, 75)')
        with self.assertRaisesRegex(ValueError,'scroll_mismatch'):
            await self.eng.verify_map(m['id'])
        with self.assertRaisesRegex(ValueError,'snapshot_layout_changed'):
            await self.eng.save_map('other','send',self.button_rect(),shot['id'])
        await self.pg.evaluate('window.scrollTo(0,0)')
        await self.pg.evaluate("document.querySelector('#send').textContent='Changed action'")
        with self.assertRaisesRegex(ValueError,'element_identity_mismatch'):
            await self.eng.verify_map(m['id'])
        again=await self.eng.capture()
        updated=await self.eng.save_map('send','send',self.button_rect(),again['id'],map_id=m['id'])
        self.assertEqual(updated['id'],m['id'])
        self.assertEqual(updated['revision'],2)
        self.assertTrue((await self.eng.verify_map(m['id']))['ok'])

    async def test_covered_target_denies_click(self):
        m=await self.eng.save_map('send','send',self.button_rect())
        await self.pg.evaluate("""() => {const d=document.createElement('div');d.style='position:fixed;inset:0;z-index:90000';document.body.appendChild(d)}""")
        with self.assertRaisesRegex(ValueError,'target_obscured'):
            await self.eng.click_map(m['id'])
