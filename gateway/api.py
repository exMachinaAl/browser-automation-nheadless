"""Loopback-only HTTP gateway. Authenticated browser controls, no third-party bypasses."""
import asyncio
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel, Field

from .core import DATA, ROOT, Settings, Store, Engine, origin
from .site_profiles import validate_profile
from .jobs import JobQueue
from .security import valid_browser_origin, valid_gateway_host_header
from . import __version__
from .demo_server import start_demo_if_needed

cfg = Settings.load()
DATA.mkdir(parents=True, exist_ok=True)
TOKEN_FILE=DATA/'gateway-token.txt'
if not TOKEN_FILE.exists():
    # atomic exclusive create, private permissions from the first instant
    try:
        descriptor = os.open(str(TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(secrets.token_urlsafe(36))
    except FileExistsError:
        pass
try: os.chmod(TOKEN_FILE, 0o600)
except OSError: pass
TOKEN=TOKEN_FILE.read_text(encoding='utf-8').strip()
if len(TOKEN) < 32:
    raise RuntimeError('gateway_token_invalid_or_empty')
store=Store()
engine=Engine(cfg,store)

async def execute_job(action, params):
    if action == 'navigate':
        return await engine.goto(params['url'])
    if action == 'chat':
        return await engine.chat(params['prompt'],params.get('input_id'),params.get('send_id'),params.get('response_id'))
    if action == 'fill':
        return await engine.fill_map(params['map_id'],params['text'])
    if action == 'click':
        return await engine.click_map(params['map_id'])
    if action == 'read':
        return await engine.read_map(params['map_id'])
    if action == 'probe':
        return await engine.probe()
    raise ValueError('unsupported_job_action')

jobs = JobQueue(store,execute_job)

@asynccontextmanager
async def lifespan(app):
    store.interrupt_jobs()  # previous process may have crashed mid-action
    demo = start_demo_if_needed(cfg.raw,ROOT)
    try:
        await engine.start()
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()
            await engine.close()
    finally:
        if demo:
            demo.close()
        store.close()

app=FastAPI(title='Browser Automation Gateway Python',version=__version__,lifespan=lifespan,
            docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware('http')
async def reject_cross_origin(request: Request, call_next):
    if not valid_gateway_host_header(request.headers.get('host',''), cfg['port']):
        return JSONResponse({'error':'untrusted_host'},status_code=403)
    if not valid_browser_origin(request.headers.get('origin'), cfg['port']):
        return JSONResponse({'error':'untrusted_origin'},status_code=403)
    return await call_next(request)

async def auth(request:Request):
    a=request.headers.get('Authorization','')
    if not a.startswith('Bearer ') or not secrets.compare_digest(a[7:],TOKEN):
        raise HTTPException(401,'Bearer token required')

@app.exception_handler(ValueError)
async def invalid(request,exc):
    reason = str(exc)
    # Never return user-provided URLs or browser-private data via error strings.
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,100}', reason):
        reason = 'invalid_operation'
    return JSONResponse({'error':reason},status_code=409)

@app.exception_handler(sqlite3.Error)
async def database_failure(request, exc):
    return JSONResponse({'error':'storage_failure'},status_code=503)

class Rect(BaseModel):
    x1:float
    y1:float
    x2:float
    y2:float

class RectInspect(Rect):
    snapshot_id:str|None=None

class MapCreate(BaseModel):
    name:str
    role:str
    rect:Rect
    snapshot_id:str|None=None

class Fill(BaseModel):
    text:str=Field(max_length=10000)

class Chat(BaseModel):
    prompt:str=Field(max_length=10000)
    input_id:str|None=None
    send_id:str|None=None
    response_id:str|None=None

class Navigation(BaseModel):
    url:str

class SiteProfile(BaseModel):
    origin: str
    name: str
    selectors: dict[str,str]

class TrustOrigin(BaseModel):
    expected_origin: str

class ProfileSwitch(BaseModel):
    profile: str

class JobRequest(BaseModel):
    action: str
    url: str|None = None
    prompt: str|None = Field(default=None, max_length=10000)
    text: str|None = Field(default=None, max_length=10000)
    map_id: str|None = None
    input_id: str|None = None
    send_id: str|None = None
    response_id: str|None = None

    def params(self):
        data=self.model_dump(exclude_none=True)
        action=data.pop('action')
        mandatory = {'navigate':'url','chat':'prompt','fill':'text','click':'map_id','read':'map_id'}
        if action not in ('navigate','chat','fill','click','read','probe'):
            raise ValueError('unsupported_job_action')
        if action in mandatory and mandatory[action] not in data:
            raise ValueError('job_missing_required_parameter')
        allowed_fields = {
            'navigate':{'url'}, 'chat':{'prompt','input_id','send_id','response_id'},
            'fill':{'map_id','text'}, 'click':{'map_id'},'read':{'map_id'}, 'probe':set()}
        if set(data) - allowed_fields[action]:
            raise ValueError('job_unexpected_parameter')
        return action,data

@app.get('/health')
async def health():return {'ok':True,'version':__version__}

@app.get('/',response_class=HTMLResponse)
async def dashboard():return (ROOT/'web'/'mapper.html').read_text(encoding='utf-8')

@app.get('/api/status',dependencies=[Depends(auth)])
async def status():
    return {'url':engine.page.url if engine.page and not engine.page.is_closed() else None,'headless':cfg['headless'],'cdp':bool(cfg['cdp_endpoint']),
            'watch_running':bool(engine.watch_task and not engine.watch_task.done()),
            'watch_error':engine.watch_error,
            'viewport':await engine.dimensions() if engine.page and not engine.page.is_closed() else None,
            'browser':await engine.browser_status()}

@app.get('/api/probe',dependencies=[Depends(auth)])
async def probe():return await engine.probe()

@app.post('/api/navigate',dependencies=[Depends(auth)])
async def navigate(p:Navigation):return await engine.goto(p.url)

@app.get('/api/screenshot',dependencies=[Depends(auth)])
async def screenshot():
    result=await engine.capture()
    return Response(content=result['data'],media_type='image/png',headers={
        'Cache-Control':'no-store', 'X-BAG-Snapshot-Id':result['id'],
        'X-BAG-Viewport-Width':str(result['viewport']['width']),
        'X-BAG-Viewport-Height':str(result['viewport']['height'])})

@app.post('/api/inspect',dependencies=[Depends(auth)])
async def inspect(r:RectInspect):
    await engine.require_snapshot(r.snapshot_id)
    return await engine.inspect(r.model_dump(exclude={'snapshot_id'}))

@app.get('/api/maps',dependencies=[Depends(auth)])
async def maps():return {'maps':store.maps()}

@app.post('/api/maps',dependencies=[Depends(auth)])
async def save_map(r:MapCreate):return await engine.save_map(r.name,r.role,r.rect.model_dump(),r.snapshot_id)

@app.get('/api/maps/{id}/verify',dependencies=[Depends(auth)])
async def verify(id:str):return await engine.verify_map(id)

@app.get('/api/maps/{id}/preview',dependencies=[Depends(auth)])
async def preview(id:str,snapshot_id:str|None=None):
    await engine.require_snapshot(snapshot_id)
    return await engine.preview_map(id)

@app.get('/api/maps/{id}/revisions',dependencies=[Depends(auth)])
async def revisions(id:str):
    if not store.get_map(id):raise HTTPException(404,'unknown_map')
    return {'map_id':id,'revisions':store.mapping_revisions(id)}

@app.post('/api/maps/{id}/remap',dependencies=[Depends(auth)])
async def remap(id:str,r:MapCreate):
    old=store.get_map(id)
    if not old: raise HTTPException(404,'unknown_map')
    if old['role']!=r.role:raise ValueError('remap_role_mismatch')
    return await engine.save_map(r.name,r.role,r.rect.model_dump(),r.snapshot_id,map_id=id)

@app.get('/api/maps/{id}/read',dependencies=[Depends(auth)])
async def read(id:str):return await engine.read_map(id)

@app.post('/api/maps/{id}/click',dependencies=[Depends(auth)])
async def click(id:str):return await engine.click_map(id)

@app.post('/api/maps/{id}/fill',dependencies=[Depends(auth)])
async def fill(id:str,body:Fill):return await engine.fill_map(id,body.text)

@app.post('/api/chat',dependencies=[Depends(auth)])
async def chat(p:Chat):return await engine.chat(p.prompt,p.input_id,p.send_id,p.response_id)

@app.post('/api/maps/{id}/watch',dependencies=[Depends(auth)])
async def watch(id:str):
    m=store.get_map(id)
    if not m or m['role']!='response':raise ValueError('watch_requires_response_map')
    await engine.verify_map(id)
    return await engine.watch(id)

@app.post('/api/watch/stop',dependencies=[Depends(auth)])
async def stop_watch():return await engine.stop_watch()

@app.get('/api/audit',dependencies=[Depends(auth)])
async def audit():return await engine.audit()

@app.get('/api/changes',dependencies=[Depends(auth)])
async def changes(limit:int=50):return {'items':store.changes(limit)}


@app.get('/api/browser/status',dependencies=[Depends(auth)])
async def browser_status():return await engine.browser_status()

@app.post('/api/browser/restart',dependencies=[Depends(auth)])
async def browser_restart():
    if store.has_pending_jobs():
        raise ValueError('browser_busy_job_running')
    return await engine.restart()

@app.get('/api/browser/profiles',dependencies=[Depends(auth)])
async def browser_profiles():return engine.browser_profiles()

@app.post('/api/browser/profile',dependencies=[Depends(auth)])
async def browser_profile(p:ProfileSwitch):
    if store.has_pending_jobs():
        raise ValueError('browser_busy_job_running')
    return await engine.restart(p.profile)

@app.get('/api/browser/tabs',dependencies=[Depends(auth)])
async def tabs():return {'tabs':engine.manager.tabs() if engine.manager and engine.manager.state=='running' else []}

@app.post('/api/browser/tabs',dependencies=[Depends(auth)])
async def new_tab(p:Navigation):return await engine.new_tab(p.url)
@app.post('/api/browser/browse-tab',dependencies=[Depends(auth)])
async def browse_tab(p:Navigation):return await engine.browse_tab(p.url)

@app.post('/api/browser/trust-current',dependencies=[Depends(auth)])
async def trust_current(p:TrustOrigin):return await engine.trust_current(p.expected_origin)

@app.get('/api/browser/allowed-origins',dependencies=[Depends(auth)])
async def allowed_origins():return {'authorized_origins':engine.authorized_origins()}


@app.post('/api/browser/tabs/{tab_id}/select',dependencies=[Depends(auth)])
async def select_tab(tab_id:str):return await engine.select_tab(tab_id)

@app.post('/api/browser/tabs/{tab_id}/close',dependencies=[Depends(auth)])
async def close_tab(tab_id:str):return await engine.close_tab(tab_id)

@app.get('/api/site-profiles',dependencies=[Depends(auth)])
async def site_profiles():return {'profiles':store.site_profiles()}

@app.put('/api/site-profiles',dependencies=[Depends(auth)])
async def put_site_profile(p:SiteProfile):
    profile=validate_profile(p.model_dump(),engine.authorized_origins())
    store.put_site_profile(profile)
    return profile

@app.post('/api/jobs',dependencies=[Depends(auth)],status_code=202)
async def create_job(p:JobRequest):
    action,params=p.params()
    return jobs.submit(action,params)

@app.get('/api/jobs',dependencies=[Depends(auth)])
async def list_jobs(limit:int=30):return {'jobs':store.jobs(limit)}

@app.get('/api/jobs/{job_id}',dependencies=[Depends(auth)])
async def get_job(job_id:str):
    job=store.get_job(job_id)
    if not job:raise HTTPException(404,'unknown_job')
    return {'job':job, 'output':jobs.results.get(job_id)}

@app.post('/api/jobs/{job_id}/cancel',dependencies=[Depends(auth)])
async def cancel_job(job_id:str):return jobs.cancel(job_id)
