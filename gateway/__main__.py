"""Runtime entrypoints; `doctor` doesn't open a browser or touch user sessions."""
import argparse
import json
import os
import platform
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import ROOT, Settings
from . import __version__


def demo():
    contents = (ROOT/'web'/'demo.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?',1)[0] != '/':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(contents)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(contents)
    server = ThreadingHTTPServer(('127.0.0.1', 8787), Handler)
    print('Demo local on http://127.0.0.1:8787')
    try: server.serve_forever()
    finally: server.server_close()


def network_check():
    """Isolate a genuine browser networking issue from local server/allowlist issues."""
    import re
    import threading
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    from urllib.request import urlopen
    result={'diagnostic':'loopback_network_check','python_http':False,
            'chromium_http':False,'browser_error':None}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'BAG_LOOPBACK_OK'
            self.send_response(200)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    url='http://127.0.0.1:'+str(server.server_port)+'/health'
    try:
        try:
            with urlopen(url,timeout=5) as reply:
                result['python_http']=reply.status==200 and reply.read()==b'BAG_LOOPBACK_OK'
        except Exception as exc:
            result['python_error']=type(exc).__name__
        try:
            from playwright.sync_api import sync_playwright
            cfg=Settings.load()
            with sync_playwright() as pw:
                kwargs={'headless':True}
                if cfg['chromium_executable']:
                    kwargs['executable_path']=cfg['chromium_executable']
                browser=pw.chromium.launch(**kwargs)
                try:
                    page=browser.new_page()
                    response=page.goto(url,wait_until='domcontentloaded',timeout=12000)
                    result['chromium_http']=bool(response and response.status==200 and
                                                 page.locator('body').inner_text()=='BAG_LOOPBACK_OK')
                finally:
                    browser.close()
        except Exception as exc:
            reason=re.search(r'net::(ERR_[A-Z_]+)',str(exc))
            result['browser_error']=reason.group(1) if reason else type(exc).__name__
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    result['status']='ok' if result['python_http'] and result['chromium_http'] else 'needs_attention'
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='ok' else 2

def doctor():
    status = {'version': __version__, 'python': sys.version.split()[0],
              'system': platform.system(), 'status': 'ok', 'checks': {}}
    try:
        cfg = Settings.load().raw
        status['checks']['config'] = 'valid'
        status['checks']['loopback'] = cfg['host']
        status['checks']['allowed_origins'] = len(cfg['allowed_origins'])
        if cfg['cdp_endpoint']:
            status['checks']['browser'] = 'remote_CDP_configured_not_connected'
        else:
            executable = cfg['chromium_executable']
            if not executable:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    executable = pw.chromium.executable_path
            status['checks']['browser_path_exists'] = Path(executable).is_file()
            if not Path(executable).is_file():
                status['status'] = 'needs_browser_install'
    except (Exception) as e:
        status['status'] = 'needs_attention'
        status['checks']['error_code'] = type(e).__name__
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 0 if status['status']=='ok' else 2


def main():
    parser=argparse.ArgumentParser(description='Browser Automation Gateway Python')
    parser.add_argument('mode',choices=('serve','demo','headless','doctor','test','network-check'),nargs='?',default='serve')
    args=parser.parse_args()
    if args.mode=='demo':return demo()
    if args.mode=='doctor':return doctor()
    if args.mode=='network-check':return network_check()
    if args.mode=='test':
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args.mode=='headless':
        os.environ['BAG_HEADLESS'] = '1'
    import uvicorn
    cfg=Settings.load().raw
    uvicorn.run('gateway.api:app',host=cfg['host'],port=cfg['port'],log_level='info')
    return 0

if __name__=='__main__':sys.exit(main())
