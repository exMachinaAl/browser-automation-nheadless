"""Local, allowlisted DOM-first browser control with verified viewport mapping.

This is not a CAPTCHA/Cloudflare bypasser. There are no stealth flags or challenge solvers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .security import validate_cdp_endpoint, navigation_message, valid_browse_url

from playwright.async_api import TimeoutError as PlaywrightTimeout
from .browser_manager import BrowserManager
from .migrations import migrate
from .precision import capture_signature, validate_snapshot, validate_presentation, anchor_evidence, check_anchor, movement_px

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def origin(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return ''
    default = 80 if parsed.scheme == 'http' else 443
    host = parsed.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
    return f'{parsed.scheme}://{host}{":" + str(port) if port and port != default else ""}'


def overlap(a, b):
    x = max(0, min(a['x2'], b['x2']) - max(a['x1'], b['x1']))
    y = max(0, min(a['y2'], b['y2']) - max(a['y1'], b['y1']))
    return x * y


def center(rect):
    return ((rect['x1'] + rect['x2']) / 2, (rect['y1'] + rect['y2']) / 2)


def valid_rect(r, vw, vh):
    if not isinstance(r, dict):
        return False
    for k in ('x1','x2','y1','y2'):
        if not isinstance(r.get(k), (float,int)) or isinstance(r[k], bool):
            return False
    return 0 <= r['x1'] < r['x2'] <= vw and 0 <= r['y1'] < r['y2'] <= vh and (r['x2']-r['x1']) >= 4 and (r['y2']-r['y1']) >= 4


@dataclass
class Settings:
    raw: dict

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError('config_must_be_object')
        raw = dict(values)
        allowed = raw.get('allowed_origins')
        if not isinstance(allowed, list) or not allowed:
            raise ValueError('allowed_origins_must_be_nonempty_array')
        normalized = [origin(x) for x in allowed]
        if not all(normalized):
            raise ValueError('invalid_allowed_origin')
        raw['allowed_origins'] = sorted(set(normalized))
        if origin(raw.get('start_url', '')) not in raw['allowed_origins']:
            raise ValueError('start_url_must_be_allowlisted')
        if raw.get('host') not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('gateway_must_bind_loopback')
        if not isinstance(raw.get('port'), int) or isinstance(raw['port'], bool) or not 1024 <= raw['port'] <= 65535:
            raise ValueError('invalid_gateway_port')
        vp = raw.get('viewport')
        if not isinstance(vp, dict) or any(not isinstance(vp.get(k), int) or isinstance(vp[k], bool)
             for k in ('width', 'height')):
            raise ValueError('invalid_viewport')
        if not (320 <= vp['width'] <= 4000 and 240 <= vp['height'] <= 2400):
            raise ValueError('invalid_viewport')
        raw['cdp_endpoint'] = validate_cdp_endpoint(raw.get('cdp_endpoint', ''))
        if not isinstance(raw.get('chromium_executable', ''), str):
            raise ValueError('invalid_chromium_executable')
        if not isinstance(raw.get('headless'), bool):
            raise ValueError('headless_must_be_boolean')
        for k in ('idle_ms', 'max_wait_ms'):
            v = raw.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v < 100 or v > 300000:
                raise ValueError('invalid_' + k)
        for k in ('input_selector', 'send_selector', 'response_selector', 'ready_selector'):
            if not isinstance(raw.get(k), str) or len(raw[k]) > 512:
                raise ValueError('invalid_' + k)
        return cls(raw)

    @classmethod
    def load(cls):
        cfg = ROOT / 'config.json'
        if not cfg.exists():
            # Created once, never overwritten by an upgrade package.
            cfg.write_text((ROOT / 'config.example.json').read_text(encoding='utf-8'), encoding='utf-8')
        raw = json.loads(cfg.read_text(encoding='utf-8'))
        if os.environ.get('BAG_HEADLESS') in ('0', '1'):
            raw['headless'] = os.environ['BAG_HEADLESS'] == '1'
        if os.environ.get('BAG_CHROMIUM'):
            raw['chromium_executable'] = os.environ['BAG_CHROMIUM']
        return cls.from_dict(raw)

    def __getitem__(self, k):
        return self.raw[k]


class Store:
    def __init__(self, filename=DATA/'gateway.sqlite3'):
        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)
        database_preexisted = filename.exists()
        self.db = sqlite3.connect(str(filename), check_same_thread=False)
        if os.name != 'nt':
            try: os.chmod(filename, 0o600)
            except OSError: pass
        self.db.row_factory = sqlite3.Row
        existing_schema = self.db.execute('PRAGMA user_version').fetchone()[0]
        if existing_schema > 3:
            self.db.close()
            raise ValueError('database_schema_newer_than_code')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS maps (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
            origin TEXT NOT NULL, selector TEXT NOT NULL,
            rect TEXT NOT NULL, viewport TEXT NOT NULL,
            tag TEXT NOT NULL, input_type TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
            target TEXT NOT NULL, observed_at TEXT NOT NULL,
            text TEXT NOT NULL, hash TEXT NOT NULL)''')
        self.db.commit()
        self.migration_backup = migrate(self.db, filename, backup_existing=database_preexisted)

    def put_map(self, m):
        self.db.execute('''INSERT INTO maps VALUES (?,?,?,?,?,?,?,?,?,?)''',
           (m['id'], m['name'], m['role'], m['origin'], m['selector'], json.dumps(m['rect']),
            json.dumps(m['viewport']), m['tag'], m['input_type'], m['created_at']))
        self.db.commit()

    def maps(self):
        rows = self.db.execute('SELECT * FROM maps ORDER BY created_at DESC').fetchall()
        return [{**dict(r), 'rect':json.loads(r['rect']), 'viewport':json.loads(r['viewport'])} for r in rows]

    def get_map(self, id):
        return next((m for m in self.maps() if m['id'] == id), None)

    def mapping_evidence(self, map_id):
        row=self.db.execute('SELECT revision,metadata FROM map_evidence WHERE map_id=?',(map_id,)).fetchone()
        return {'revision':row['revision'], 'metadata':json.loads(row['metadata'])} if row else None

    def mapping_revisions(self, map_id):
        return [{'revision':row['revision'], 'changed_at':row['changed_at'],
                 'snapshot':json.loads(row['snapshot'])} for row in
                 self.db.execute('SELECT * FROM map_revisions WHERE map_id=? ORDER BY revision DESC',(map_id,))]

    def save_evidence(self, map_data, metadata, *, update=False):
        existing=self.get_map(map_data['id'])
        if update and not existing: raise ValueError('unknown_map')
        if not update and existing: raise ValueError('map_exists')
        previous=self.mapping_evidence(map_data['id']) if update else None
        rev=(previous['revision']+1 if previous else (2 if update else 1))
        with self.db:
            if update and not previous:
                self.db.execute('INSERT INTO map_revisions VALUES (?,?,?,?)',
                    (map_data['id'],1,json.dumps(existing),utc_now()))
            if update:
                self.db.execute('''UPDATE maps SET name=?,role=?,origin=?,selector=?,rect=?,viewport=?,
                    tag=?,input_type=?,created_at=? WHERE id=?''',
                    (map_data['name'],map_data['role'],map_data['origin'],map_data['selector'],
                    json.dumps(map_data['rect']),json.dumps(map_data['viewport']),map_data['tag'],
                    map_data['input_type'],map_data['created_at'],map_data['id']))
            else:
                self.db.execute('INSERT INTO maps VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (map_data['id'],map_data['name'],map_data['role'],map_data['origin'],
                    map_data['selector'],json.dumps(map_data['rect']),json.dumps(map_data['viewport']),
                    map_data['tag'],map_data['input_type'],map_data['created_at']))
            self.db.execute('''INSERT INTO map_evidence VALUES (?,?,?,?) ON CONFLICT(map_id) DO UPDATE SET
                revision=excluded.revision,metadata=excluded.metadata,updated_at=excluded.updated_at''',
                (map_data['id'],rev,json.dumps(metadata),utc_now()))
            self.db.execute('INSERT INTO map_revisions VALUES (?,?,?,?)',
                (map_data['id'],rev,json.dumps(map_data),utc_now()))
        return rev

    def change(self, target, text, session_id):
        h = hashlib.sha256(text.encode('utf-8')).hexdigest()
        row = self.db.execute('SELECT hash FROM changes WHERE target=? AND session_id=? ORDER BY id DESC LIMIT 1', (target,session_id)).fetchone()
        if row and row['hash'] == h:
            return False
        self.db.execute('INSERT INTO changes(session_id,target,observed_at,text,hash) VALUES(?,?,?,?,?)',
                        (session_id,target,utc_now(),text,h))
        self.db.commit()
        return True

    def changes(self, limit=100):
        return [dict(r) for r in self.db.execute('SELECT id, session_id, target, observed_at, text FROM changes ORDER BY id DESC LIMIT ?', (max(1,min(int(limit),500)),))]


    def put_site_profile(self, profile):
        self.db.execute('''INSERT INTO site_profiles(origin,name,selectors,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(origin) DO UPDATE SET
            name=excluded.name, selectors=excluded.selectors, updated_at=excluded.updated_at''',
            (profile['origin'], profile['name'],json.dumps(profile['selectors']),utc_now()))
        self.db.commit()

    def site_profiles(self):
        return [dict(origin=r['origin'],name=r['name'],selectors=json.loads(r['selectors']),updated_at=r['updated_at'])
                for r in self.db.execute('SELECT * FROM site_profiles ORDER BY origin')]

    def site_profile(self, site_origin):
        r=self.db.execute('SELECT * FROM site_profiles WHERE origin=?',(site_origin,)).fetchone()
        return dict(origin=r['origin'],name=r['name'],selectors=json.loads(r['selectors']),updated_at=r['updated_at']) if r else None

    def new_job(self, job_id, action):
        stamp=utc_now()
        self.db.execute('INSERT INTO jobs(id,action,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                        (job_id,action,'queued',stamp,stamp))
        self.db.commit()

    def update_job(self, job_id, status, result=None, error=None):
        self.db.execute('UPDATE jobs SET status=?,updated_at=?,result=?,error=? WHERE id=?',
                       (status,utc_now(),json.dumps(result) if result is not None else None,error,job_id))
        self.db.commit()

    def get_job(self, job_id):
        r=self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        return {**dict(r),'result':json.loads(r['result']) if r['result'] else None} if r else None

    def jobs(self, limit=30):
        return [self.get_job(r['id']) for r in self.db.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?',
                      (max(1,min(100,int(limit))),))]

    def interrupt_jobs(self):
        self.db.execute("UPDATE jobs SET status='interrupted',error='gateway_stopped',updated_at=? WHERE status IN ('running','queued')",(utc_now(),))
        self.db.commit()

    def has_pending_jobs(self):
        return self.db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None

    def close(self):
        self.db.close()


INSPECT_JS = r'''({x,y})=>{
  const root = document.elementFromPoint(x,y);
  if (!root) return {error:'no_element_at_point'};
  const isUseful = e=>e && (e.matches('button,input,textarea,[contenteditable="true"],[role="button"],[role="textbox"]') || e.id || e.getAttribute('data-testid'));
  const el = root.closest('button,input,textarea,[contenteditable="true"],[role="button"],[role="textbox"]') || root;
  function selector(e){
    if(e.id && document.querySelectorAll('#'+CSS.escape(e.id)).length===1)return '#'+CSS.escape(e.id);
    for (const k of ['data-testid','aria-label','name']) {
      const v=e.getAttribute(k); if(!v) continue;
      const s=e.tagName.toLowerCase()+'['+k+'='+JSON.stringify(v)+']';
      try{if(document.querySelectorAll(s).length===1)return s;}catch{}
    }
    const parts=[];let n=e;
    for(let i=0;n && n!==document.documentElement && i<12;i++,n=n.parentElement){
      let part=n.tagName.toLowerCase();
      const parent=n.parentElement;
      if(parent){const sib=Array.from(parent.children).filter(c=>c.tagName===n.tagName);
        if(sib.length>1)part+=':nth-of-type('+(sib.indexOf(n)+1)+')';}
      parts.unshift(part);
      const s=parts.join(' > ');
      if(document.querySelectorAll(s).length===1)return s;
    }
    return parts.join(' > ');
  }
  const r=el.getBoundingClientRect();const s=selector(el);
  return {selector:s,count:document.querySelectorAll(s).length,
     tag:el.tagName.toLowerCase(),input_type:(el.getAttribute('type')||'').toLowerCase(),
     text:(el.innerText||el.getAttribute('aria-label')||'').trim().slice(0,150),
     box:{x1:r.left,y1:r.top,x2:r.right,y2:r.bottom},
     editable:el.matches('input:not([type="hidden"]),textarea,[contenteditable="true"],[role="textbox"]'),
     clickable:el.matches('button,[role="button"],a,input[type="submit"]'),
     viewport:{width:innerWidth,height:innerHeight},hit_tag:root.tagName.toLowerCase(),
     testid:el.getAttribute('data-testid')||'',aria_label:el.getAttribute('aria-label')||'',
     placeholder:el.getAttribute('placeholder')||'',dom_role:el.getAttribute('role')||''};
}'''


class Engine:
    def __init__(self, settings, store, data_root=DATA):
        self.cfg, self.store = settings, store
        self.data_root = Path(data_root)
        self.browser, self.context, self.page, self.pw = None, None, None, None
        self.lock = asyncio.Lock()
        self.watch_task = None
        self.watch_session = None
        self.watch_error = None
        self.last_nav_error = None
        self.last_nav_status = None
        self.last_http_status = None
        self.manager = None
        self.profile = 'default'
        self.last_snapshot = None
        self.trust_file = self.data_root / 'trusted-origins.json'
        self.trusted_origins = set()
        if self.trust_file.exists():
            if self.trust_file.is_symlink():
                raise ValueError('trusted_origins_symlink_denied')
            loaded = json.loads(self.trust_file.read_text(encoding='utf-8'))
            if (not isinstance(loaded, list) or len(loaded) > 64
                    or any(not isinstance(item, str) or origin(item) != item for item in loaded)):
                raise ValueError('invalid_trusted_origins_file')
            self.trusted_origins = set(loaded)

    def authorized_origins(self):
        return sorted(set(self.cfg['allowed_origins']) | self.trusted_origins)

    def allow(self, url):
        return origin(url) in self.authorized_origins()

    async def trust_current(self, expected_origin):
        # Explicit user approval via token-protected local dashboard only.
        async with self.lock:
            self.ensure_browser()
            current = origin(self.page.url)
            if (not current or not isinstance(expected_origin,str)
                    or current != expected_origin or expected_origin != origin(expected_origin)):
                raise ValueError('trusted_origin_mismatch')
            if current not in self.trusted_origins:
                if len(self.trusted_origins) >= 64:
                    raise ValueError('trusted_origin_limit')
                new_origins = sorted(self.trusted_origins | {current})
                self.trust_file.parent.mkdir(parents=True, exist_ok=True)
                if self.trust_file.is_symlink():
                    raise ValueError('trusted_origins_symlink_denied')
                new_path = self.trust_file.with_suffix('.tmp')
                if new_path.is_symlink():
                    raise ValueError('trusted_origins_symlink_denied')
                new_path.write_text(json.dumps(new_origins,indent=2)+'\n',encoding='utf-8')
                if os.name != 'nt':
                    os.chmod(new_path,0o600)
                os.replace(new_path,self.trust_file)
                self.trusted_origins.add(current)
            return {'origin':current,'authorized_origins':self.authorized_origins(),
                    'note':'Only explicitly approved origins allow DOM automation.'}

    def ensure_browser(self):
        if not self.page or self.page.is_closed():
            raise ValueError('browser_page_unavailable')

    async def start(self):
        async with self.lock:
            if self.manager is None:
                self.manager = BrowserManager(self.cfg, self.data_root, self.route)
            self.page = await self.manager.start(self.profile)
            # Clear stale failed-network status when a human navigates using Chromium.
            self.manager.context.on('page', self._bind_page_navigation)
            for tab in self.manager.context.pages:
                self._bind_page_navigation(tab)
            self.last_snapshot = None
            self.browser, self.context, self.pw = self.manager.browser, self.manager.context, self.manager.pw
            if not self.allow(self.page.url):
                await self._navigate(self.cfg['start_url'])

    def _bind_page_navigation(self, page):
        if getattr(page, '_bag_nav_bound', False):
            return
        setattr(page, '_bag_nav_bound', True)
        page.on('framenavigated', self._manual_navigation_reset)

    def _manual_navigation_reset(self, frame):
        if frame.page is self.page and frame == frame.page.main_frame:
            self.last_nav_error = self.last_nav_status = self.last_http_status = None
            self.last_snapshot = None

    async def route(self, route):
        """Legacy hook retained; interactive navigation must not be blocked."""
        await route.continue_()

    async def close(self):
        await self.stop_watch()
        async with self.lock:
            if self.manager:
                await self.manager.stop()
            self.page = self.browser = self.context = self.pw = None

    async def restart(self, profile=None):
        if profile is not None:
            from .browser_manager import profile_dir
            profile_dir(self.data_root,profile)  # validate before stopping active browser
            if self.cfg['cdp_endpoint'] and profile != 'default':
                raise ValueError('remote_cdp_cannot_switch_profile')
        await self.close()
        if profile is not None:
            self.profile = profile
        await self.start()
        return await self.browser_status()

    async def browser_status(self):
        state = self.manager.state if self.manager else 'stopped'
        page = self.page
        if state == 'running' and (not page or page.is_closed()):
            state = 'page_closed'
        return {'state':state,'profile':self.profile,
                'url':page.url if page and not page.is_closed() else None,
                'headless':self.cfg['headless'],
                'tabs':self.manager.tabs() if self.manager and self.manager.state == 'running' else [],
                'failure_type':self.manager.failure if self.manager else None}

    async def new_tab(self,url):
        async with self.lock:
            if not self.manager: raise ValueError('browser_not_started')
            tabs = await self.manager.new_tab(url,self.allow)
            self.page = self.manager.page
            self.last_snapshot = None
            return {'tabs':tabs,'active_url':self.page.url}

    async def browse_tab(self, url):
        if not valid_browse_url(url):
            raise ValueError('invalid_browse_url')
        async with self.lock:
            if not self.manager:
                raise ValueError('browser_not_started')
            tabs = await self.manager.browse_tab(url)
            self.page = self.manager.page
            self.last_snapshot = None
            self.last_nav_error = self.last_nav_status = self.last_http_status = None
            return {**tabs, 'automation_authorized':self.allow(self.page.url)}

    async def select_tab(self, tab_id):
        async with self.lock:
            if not self.manager: raise ValueError('browser_not_started')
            tabs=self.manager.select_tab(tab_id,self.allow)
            self.page=self.manager.page
            self.last_snapshot = None
            return {'tabs':tabs,'active_url':self.page.url}

    async def close_tab(self, tab_id):
        async with self.lock:
            if not self.manager: raise ValueError('browser_not_started')
            tabs=await self.manager.close_tab(tab_id,self.allow)
            self.page=self.manager.page
            self.last_snapshot = None
            return {'tabs':tabs,'active_url':self.page.url}

    def browser_profiles(self):
        from .browser_manager import profile_dir
        if self.manager:
            return self.manager.profiles()
        return {'active':self.profile,'profiles':['default'],'remote':bool(self.cfg['cdp_endpoint'])}

    def site_selectors(self):
        site = self.store.site_profile(origin(self.page.url)) if self.page else None
        return site['selectors'] if site else {}

    def ensure_page(self):
        if not self.page or self.page.is_closed(): raise ValueError('browser page is closed')
        if not self.allow(self.page.url): raise ValueError('browser is outside allowlist')

    async def dimensions(self):
        return await self.page.evaluate('({width:innerWidth,height:innerHeight,scale:devicePixelRatio})')

    async def _navigate(self, url):
        self.last_nav_error = None
        self.last_nav_status = None
        self.last_http_status = None
        try:
            response = await self.page.goto(url,wait_until='domcontentloaded',timeout=25000)
            self.last_http_status = response.status if response else None
        except PlaywrightTimeout as ex:
            self.last_nav_status = 'timeout'
            self.last_nav_error = navigation_message(ex)
        except Exception as ex:
            self.last_nav_status = 'network_error'
            self.last_nav_error = navigation_message(ex)
        return await self.probe()

    async def goto(self, url):
        if not self.allow(url): raise ValueError('origin_not_allowed')
        async with self.lock:
            return await self._navigate(url)

    async def probe(self):
        if not self.page or self.page.is_closed():
            return {'status':'page_error','message':'browser page closed'}
        if not self.allow(self.page.url):
            if self.last_nav_status and self.page.url in ('about:blank','chrome-error://chromewebdata/'):
                return {'status':self.last_nav_status, 'url':self.page.url,
                        'navigation_error':self.last_nav_error,
                        'note':'Initial navigation failed; run python -m gateway network-check.'}
            return {'status':'untrusted_origin', 'url':self.page.url,
                    'note':'Manual browsing permitted; explicitly approve this origin before automated actions.'}
        try:
            state = await self.page.evaluate('''() => ({title:document.title,url:location.href,
              text:(document.body?.innerText||'').slice(0,2500),
              ready_state:document.readyState, password:!!document.querySelector('input[type=password]'),
              challenge:!!document.querySelector('iframe[src*="challenges.cloudflare"],iframe[src*="recaptcha"],.cf-turnstile,[data-sitekey]')})''')
            status=self.last_nav_status or 'not_ready'
            text=(state['title']+' '+state['text']).lower()
            if self.last_nav_status == 'navigation_denied':
                status = 'navigation_denied'
            elif state['challenge'] or any(s in text for s in ('just a moment...', 'verify you are human', 'checking your browser', 'attention required!')):
                status='challenge_required'
            elif self.last_http_status in (401,403):
                status='access_denied'
            elif self.last_http_status and self.last_http_status>=400:
                status='http_error'
            elif state['password'] or 'sign in to continue' in text:
                status='login_required'
            elif any(s in text for s in ('access denied', 'error 403', 'forbidden')):
                status='access_denied'
            else:
                site_ready = self.site_selectors().get('ready')
                ready_selector = site_ready if site_ready else (
                    self.cfg['ready_selector'] if origin(self.page.url) == origin(self.cfg['start_url']) else '')
                if not self.last_nav_status and state['ready_state'] in ('interactive','complete'):
                    if not ready_selector or await self.page.locator(ready_selector).count():
                        status='ready'
            return { 'status':status, 'url':state['url'], 'title':state['title'],
                'http_status':self.last_http_status,'navigation_error':self.last_nav_error,'note':'Heuristic status, not a guarantee of ongoing access'}
        except Exception as ex:
            return {'status':'page_error','message':str(ex)[:200]}

    async def presentation_state(self):
        self.ensure_page()
        measured=await self.page.evaluate('''() => ({url:location.href,viewport:{width:innerWidth,height:innerHeight},
          scroll:{x:scrollX,y:scrollY},presentation:{device_pixel_ratio:devicePixelRatio,
          visual_scale:visualViewport?.scale??1,visual_offset_x:visualViewport?.offsetLeft??0,
          visual_offset_y:visualViewport?.offsetTop??0}})''')
        # Page identity: a different tab with same URL cannot reuse snapshot.
        measured['tab_marker']=str(id(self.page))
        return measured

    async def capture(self):
        self.ensure_page()
        async with self.lock:
            before=await self.presentation_state()
            image=await self.page.screenshot(type='png',full_page=False,animations='disabled',scale='css')
            after=await self.presentation_state()
            if capture_signature(before)!=capture_signature(after):
                raise ValueError('screenshot_state_changed_retry')
            ref=uuid.uuid4().hex
            self.last_snapshot={'id':ref,'state':after,'created':time.monotonic()}
            return {'data':image,'id':ref,'viewport':after['viewport'],'presentation':after['presentation']}

    async def screenshot(self):
        return (await self.capture())['data']

    async def require_snapshot(self, snapshot_id):
        if not snapshot_id: return  # API backward compatibility; dashboard always sends one.
        snap=self.last_snapshot
        if not snap or not secrets.compare_digest(str(snapshot_id),snap['id']):
            raise ValueError('snapshot_missing_refresh_required')
        now=await self.presentation_state()
        validate_snapshot(snap['state'],now,elapsed=time.monotonic()-snap['created'])


    async def inspect(self, rect):
        self.ensure_page()
        size = await self.dimensions()
        if not valid_rect(rect, size['width'],size['height']): raise ValueError('invalid_rect_for_current_viewport')
        x,y=center(rect)
        result=await self.page.evaluate(INSPECT_JS,{'x':x,'y':y})
        if result.get('error'): raise ValueError(result['error'])
        return result

    async def save_map(self,name,role,rect,snapshot_id=None,map_id=None):
        if not isinstance(name,str) or not 1<=len(name.strip())<=80: raise ValueError('invalid_map_name')
        if role not in ('response','input','send','action'): raise ValueError('invalid_role')
        async with self.lock:
            await self.require_snapshot(snapshot_id)
            probe=await self.probe()
            if probe['status']!='ready': raise ValueError('page_not_ready_'+probe['status'])
            i=await self.inspect(rect)
            if i['count']!=1: raise ValueError('target_not_unique')
            if overlap(rect,i['box'])<.30*((rect['x2']-rect['x1'])*(rect['y2']-rect['y1'])):
                raise ValueError('selected_area_not_on_target')
            if i['input_type'] in ('password','hidden','file'): raise ValueError('sensitive_field_blocked')
            if role=='input' and not i['editable']: raise ValueError('input_role_requires_editable_element')
            if role in ('send','action') and not i['clickable']: raise ValueError('click_role_requires_interactive_element')
            state=await self.presentation_state()
            if snapshot_id: await self.require_snapshot(snapshot_id)
            old=self.store.get_map(map_id) if map_id else None
            if map_id and not old: raise ValueError('unknown_map')
            if old and (old['origin']!=origin(self.page.url) or old['role']!=role):
                raise ValueError('remap_origin_or_role_mismatch')
            map_data=dict(id=map_id or str(uuid.uuid4()),name=name.strip(),role=role,origin=origin(self.page.url),
                selector=i['selector'],rect=rect,viewport=state['viewport'],tag=i['tag'],
                input_type=i['input_type'],created_at=utc_now())
            meta={'presentation':state['presentation'], 'scroll':state['scroll'],
                  'anchor':anchor_evidence(i,role),'capture_based':bool(snapshot_id)}
            revision=self.store.save_evidence(map_data,meta,update=bool(map_id))
            return {**map_data,'revision':revision,'precision':meta}

    async def verify_map(self,id,click=False):
        self.ensure_page()
        m=self.store.get_map(id)
        if not m: raise ValueError('unknown_map')
        if origin(self.page.url)!=m['origin']: raise ValueError('map_origin_mismatch')
        state=await self.presentation_state()
        if state['viewport']!=m['viewport']:
            raise ValueError('viewport_mismatch_remap_required')
        stored=self.store.mapping_evidence(id)
        if stored:
            validate_presentation({**stored['metadata']['presentation'],'scroll':stored['metadata']['scroll']},state)
        status=(await self.probe())['status']
        if status!='ready': raise ValueError('page_not_ready_'+status)
        loc=self.page.locator(m['selector'])
        if await loc.count()!=1: raise ValueError('selector_not_unique_or_missing')
        if not await loc.is_visible(): raise ValueError('target_hidden')
        typ=(await loc.get_attribute('type') or '').lower()
        if typ in ('password','hidden','file'): raise ValueError('sensitive_field_blocked')
        dims=await loc.bounding_box()
        if not dims: raise ValueError('no_bounding_box')
        bounds={'x1':dims['x'],'y1':dims['y'],'x2':dims['x']+dims['width'],'y2':dims['y']+dims['height']}
        r=m['rect']
        if overlap(r,bounds)<0.30*((r['x2']-r['x1'])*(r['y2']-r['y1'])):
            raise ValueError('mapped_region_shifted')
        x,y=center(r)
        hit=await self.page.evaluate('''({x,y,s})=>{let t=document.elementFromPoint(x,y),e=document.querySelector(s);
           return !!t&&!!e&&(t===e||e.contains(t))}''',{'x':x,'y':y,'s':m['selector']})
        if not hit: raise ValueError('target_obscured_or_shifted')
        if stored:
            identity=await loc.evaluate('''e=>({tag:e.tagName.toLowerCase(),input_type:(e.getAttribute('type')||'').toLowerCase(),
                testid:e.getAttribute('data-testid')||'',aria_label:e.getAttribute('aria-label')||'',
                placeholder:e.getAttribute('placeholder')||'',dom_role:e.getAttribute('role')||'',
                button_text:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,80)})''')
            check_anchor(stored['metadata']['anchor'],identity)
        if click: await loc.click(trial=True,timeout=3000)
        return {'ok':True,'map':m,'bounds':bounds,'geometry_delta_px':movement_px(r,bounds),
                'revision':stored['revision'] if stored else 0,
                'mapping_quality':'v2_verified' if stored else 'legacy_geometry_only'}

    async def preview_map(self,id):
        chk=await self.verify_map(id,click=False)
        return {'ok':True,'map_id':id,'selector':chk['map']['selector'],
                'role':chk['map']['role'],'mapped_rect':chk['map']['rect'],
                'detected_rect':chk['bounds'],'geometry_delta_px':chk['geometry_delta_px'],
                'mapping_quality':chk['mapping_quality'],'revision':chk['revision'],
                'note':'Read-only verification; site may change after preview. Actions always revalidate.'}

    async def click_map(self, id):
        async with self.lock:
            chk=await self.verify_map(id,click=True)
            m=chk['map']
            if m['role'] not in ('send','action'):raise ValueError('map_not_clickable_role')
            await self.page.locator(m['selector']).click(timeout=4000)
            return {'ok':True,'map_id':id,'url':self.page.url}

    async def fill_map(self, id, value):
        if not isinstance(value,str) or len(value)>10000:raise ValueError('invalid_input_length')
        async with self.lock:
            chk=await self.verify_map(id)
            m=chk['map']
            if m['role']!='input':raise ValueError('map_not_input_role')
            await self.page.locator(m['selector']).fill(value,timeout=4000)
            return {'ok':True,'map_id':id,'length':len(value)}

    async def read_map(self, id):
        chk=await self.verify_map(id)
        m=chk['map']
        if m['role']=='input':
            text = await self.page.locator(m['selector']).input_value()
        else:
            text = await self.page.locator(m['selector']).inner_text(timeout=3000)
        return {'map_id':id,'text':text}

    async def _check_chat_selector(self, selector, role):
        """Default selectors are not implicitly trusted when map IDs are omitted."""
        if not selector:
            raise ValueError('chat_selector_missing_' + role)
        loc = self.page.locator(selector)
        if await loc.count() != 1:
            raise ValueError('chat_selector_missing_or_ambiguous_' + role)
        if not await loc.is_visible() or not await loc.is_enabled():
            raise ValueError('chat_target_not_actionable_' + role)
        typ = (await loc.get_attribute('type') or '').lower()
        if typ in ('hidden', 'password', 'file'):
            raise ValueError('chat_sensitive_field_blocked')
        if role == 'input':
            if not await loc.is_editable():
                raise ValueError('chat_input_not_editable')
        elif role == 'send':
            semantic = await loc.evaluate("e=>e.matches('button,[role=button],input[type=submit],input[type=button]')")
            if not semantic:
                raise ValueError('chat_send_requires_button')
            await loc.click(trial=True, timeout=3000)
        return loc

    async def chat(self, prompt, input_id=None, send_id=None, response_id=None):
        if not isinstance(prompt,str) or not (1<=len(prompt)<=10000):
            raise ValueError('invalid_prompt_length')
        async with self.lock:
            self.ensure_page()
            page_state = await self.probe()
            if page_state['status'] != 'ready':
                raise ValueError('page_not_ready_' + page_state['status'])
            site=self.site_selectors()
            selectors = {'input':site.get('input',self.cfg['input_selector']),
                         'send':site.get('send',self.cfg['send_selector']),
                         'response':site.get('response',self.cfg['response_selector'])}
            for name, ident in (('input',input_id),('send',send_id),('response',response_id)):
                if ident:
                    chk = await self.verify_map(ident, click=name=='send')
                    if chk['map']['role'] != name:
                        raise ValueError('incorrect_map_role_' + name)
                    selectors[name] = chk['map']['selector']
            inp = await self._check_chat_selector(selectors['input'], 'input')
            send = await self._check_chat_selector(selectors['send'], 'send')
            resp = selectors['response']
            if not resp:
                raise ValueError('chat_response_selector_missing')
            response_locator = self.page.locator(resp).last
            baseline = await response_locator.inner_text() if await self.page.locator(resp).count() else ''
            await inp.fill(prompt, timeout=4000)
            # Re-probe immediately before submit; login/challenge transitions fail closed.
            state = (await self.probe())['status']
            if state != 'ready':
                raise ValueError('page_not_ready_' + state)
            await send.click(timeout=5000)
            start = time.monotonic()
            last_change = start
            last_text = baseline
            session = str(uuid.uuid4())
            saw_change = False
            while (time.monotonic()-start)*1000 < self.cfg['max_wait_ms']:
                p = await self.probe()
                if p['status'] != 'ready':
                    return {'status':p['status'],'text':last_text,'session_id':session}
                count = await self.page.locator(resp).count()
                if count:
                    curr = await self.page.locator(resp).last.inner_text(timeout=2500)
                    if curr != last_text:
                        last_text = curr
                        last_change = time.monotonic()
                        saw_change = True
                        self.store.change(resp,curr,session)
                if saw_change and (time.monotonic()-last_change)*1000 >= self.cfg['idle_ms']:
                    return {'status':'idle_unconfirmed','text':last_text,'session_id':session,
                            'note':'Text stability is NOT proof that the chatbot has completed.'}
                await asyncio.sleep(0.3)
            return {'status':'timeout','text':last_text,'session_id':session,'changed':saw_change}

    async def audit(self):
        """Read-only headed baseline / headless spot check; not a guarantee."""
        probe=await self.probe()
        checks=[]
        for m in self.store.maps():
            try:
                await self.verify_map(m['id'])
                checks.append({'name':m['name'],'id':m['id'],'ok':True})
            except Exception as ex:
                checks.append({'name':m['name'],'id':m['id'],'ok':False,'error':str(ex)[:180]})
        now={'status':probe.get('status'),'origin':origin(self.page.url),
             'viewport':await self.dimensions(), 'maps':checks}
        result={'current':now,'note':'Read-only audit, not proof of identical remote behavior'}
        baseline_file=self.data_root/'headed-baseline.json'
        if not self.cfg['headless'] and not self.cfg['cdp_endpoint']:
            if now['status']=='ready' and checks and all(m['ok'] for m in checks):
                baseline_file.write_text(json.dumps(now,indent=2),encoding='utf-8')
                result['baseline_saved']=True
            else:
                result['baseline_saved']=False
        elif self.cfg['headless']:
            if baseline_file.exists():
                baseline=json.loads(baseline_file.read_text(encoding='utf-8'))
                result['headless_parity_passed']=(
                    now['status']=='ready' and baseline['status']=='ready' and
                    now['origin']==baseline['origin'] and
                    now['viewport']['width']==baseline['viewport']['width'] and
                    now['viewport']['height']==baseline['viewport']['height'] and
                    bool(now['maps']) and all(m['ok'] for m in now['maps']) and
                    {m['id'] for m in now['maps']}=={m['id'] for m in baseline['maps']})
            else:
                result['headless_parity_passed']=False
                result['reason']='Missing headed baseline'
        return result

    async def watch(self, id):
        if self.watch_task and not self.watch_task.done():
            raise ValueError('watch_already_running')
        await self.verify_map(id)
        self.watch_session = str(uuid.uuid4())
        self.watch_error = None
        async def job():
            while True:
                try:
                    async with self.lock:
                        data = await self.read_map(id)
                        self.store.change(id, data['text'], self.watch_session)
                except asyncio.CancelledError:
                    raise
                except (ValueError, PlaywrightTimeout) as exc:
                    # Do not silently spin forever when mapping or page is invalid.
                    self.watch_error = str(exc) if isinstance(exc, ValueError) else 'watch_timeout'
                    return
                except Exception:
                    self.watch_error = 'watch_browser_or_storage_error'
                    return
                await asyncio.sleep(0.5)
        self.watch_task = asyncio.create_task(job())
        return {'running': True, 'session_id': self.watch_session, 'map_id': id}

    async def stop_watch(self):
        if self.watch_task:
            self.watch_task.cancel()
            try: await self.watch_task
            except asyncio.CancelledError: pass
            self.watch_task = None
        return {'running': False, 'last_error': self.watch_error}
