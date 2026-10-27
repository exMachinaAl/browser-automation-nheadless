"""Local demo fixture started automatically only with the default demo start URL."""
from __future__ import annotations
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit

class DemoInstance:
    def __init__(self,server):
        self.server=server
        self.thread=threading.Thread(target=server.serve_forever,daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

def start_demo_if_needed(config,root):
    u=urlsplit(config['start_url'])
    if not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost') and u.port==8787 and u.path in ('','/')):
        return None
    data=(root/'web'/'demo.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?',1)[0]!='/':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(data)
        def log_message(self,*args): pass
    try:
        server=ThreadingHTTPServer(('127.0.0.1',8787),Handler)
    except OSError:
        print('[BAG] Port 8787 sudah dipakai. Tidak menjalankan demo bawaan; pastikan server lokal yang dimaksud aktif.',flush=True)
        return None
    print('[BAG] Demo lokal aktif otomatis pada http://127.0.0.1:8787',flush=True)
    return DemoInstance(server)
