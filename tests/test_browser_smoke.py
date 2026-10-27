"""Optional real Chromium DOM-only smoke tests, no internet or real websites.

BAG_BROWSER_SMOKE=1 BAG_SMOKE_CHROMIUM=/usr/bin/chromium python -m gateway test

We use page.set_content and override the allow/probe only because the fixture URL
is about:blank, not a real website. Navigation, DNS, HTTP and allowlist are NOT tested.
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from gateway.core import Engine, Settings, Store

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

RUN = (os.environ.get('BAG_BROWSER_SMOKE') == '1')
BROWSER = os.environ.get('BAG_SMOKE_CHROMIUM', '/usr/bin/chromium')
ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'config.example.json').read_text())

HTML = '''<!doctype html><meta charset="utf-8"><style>
body{margin:0;font:16px sans-serif}#prompt{position:absolute;top:70px;left:30px;width:290px;height:45px}
#send{position:absolute;top:70px;left:340px;width:120px;height:45px}
.assistant-response{position:absolute;top:170px;left:30px;width:440px;height:100px}
</style>
<textarea id="prompt"></textarea><button id="send">Send</button>
<div class="assistant-response"></div>
<script>
const b=document.getElementById('send'), input=document.getElementById('prompt');
b.addEventListener('click', ()=>{
 const area=document.querySelector('.assistant-response');area.textContent='';
 let words=['Jawaban','Jawaban untuk','Jawaban untuk '+input.value];let i=0;
 const timer=setInterval(()=>{area.textContent=words[i++];if(i===words.length)clearInterval(timer)},90);
});
</script>'''


@unittest.skipUnless(RUN and async_playwright and Path(BROWSER).is_file(),
                     'Set BAG_BROWSER_SMOKE=1 and a valid BAG_SMOKE_CHROMIUM')
class BrowserSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'fixture.sqlite')
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, executable_path=BROWSER)
        self.page = await self.browser.new_page(viewport={'width':1280,'height':900})
        await self.page.set_content(HTML)
        self.cfg=Settings.from_dict(CFG)
        self.engine=Engine(self.cfg,self.store)
        self.engine.page=self.page
        # Only the in-memory fixture is allowed; no navigations are attempted.
        self.engine.allow=lambda url: url == 'about:blank'
        async def ready():return {'status':'ready'}
        self.engine.probe=ready

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.store.db.close()
        self.tmp.cleanup()

    async def map_element(self, selector, role):
        b=await self.page.locator(selector).bounding_box()
        rect={'x1':b['x']+2,'y1':b['y']+2,'x2':b['x']+b['width']-2,'y2':b['y']+b['height']-2}
        return await self.engine.save_map(selector, role, rect)

    async def test_map_fill_click_read(self):
        inp=await self.map_element('#prompt','input')
        send=await self.map_element('#send','send')
        resp=await self.map_element('.assistant-response','response')
        self.assertTrue((await self.engine.verify_map(send['id'],click=True))['ok'])
        await self.engine.fill_map(inp['id'],'BAG test')
        await self.engine.click_map(send['id'])
        await asyncio.sleep(0.35)
        out=await self.engine.read_map(resp['id'])
        self.assertEqual(out['text'],'Jawaban untuk BAG test')

    async def test_refuse_obscured_button(self):
        send=await self.map_element('#send','send')
        await self.page.evaluate('''() => {let d=document.createElement('div');
          d.style='position:fixed;inset:0;background:rgba(0,0,0,0.1);z-index:99999';
          document.body.appendChild(d)}''')
        with self.assertRaisesRegex(ValueError, 'target_obscured_or_shifted'):
            await self.engine.click_map(send['id'])

    async def test_chat_selector_flow_records_session(self):
        result=await self.engine.chat('tes stream')
        self.assertEqual(result['status'],'idle_unconfirmed')
        self.assertEqual(result['text'],'Jawaban untuk tes stream')
        self.assertTrue(self.store.changes())


if __name__ == '__main__':
    unittest.main()
