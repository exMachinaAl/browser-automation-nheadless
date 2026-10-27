"""Simple CLI for local gateway. Example: python client.py chat 'Halo'"""
import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
url=f"http://{cfg['host']}:{cfg['port']}"
token=(ROOT/'data'/'gateway-token.txt').read_text(encoding='utf-8').strip()

p=argparse.ArgumentParser()
p.add_argument('action',choices=['probe','status','audit','maps','records','chat','navigate','read','verify','click','watch','stopwatch'])
p.add_argument('value',nargs='?',default='')
a=p.parse_args()
routes={
 'probe':('GET','/api/probe',None),'status':('GET','/api/status',None),'audit':('GET','/api/audit',None),
 'maps':('GET','/api/maps',None),'records':('GET','/api/changes',None),
 'chat':('POST','/api/chat',{'prompt':a.value}),
 'navigate':('POST','/api/navigate',{'url':a.value}),
 'stopwatch':('POST','/api/watch/stop',None)
}
if a.action in ('read','verify','click','watch'):
 method='POST' if a.action in ('click','watch') else 'GET'
 routes[a.action]=(method,f'/api/maps/{a.value}/{a.action if a.action != "watch" else "watch"}',None)
method,path,data=routes[a.action]
req=urllib.request.Request(url+path,data=json.dumps(data).encode() if data is not None else (b'' if method=='POST' else None),method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
try:
 with urllib.request.urlopen(req,timeout=80) as resp:
  print(json.dumps(json.loads(resp.read()),indent=2,ensure_ascii=False))
except urllib.error.HTTPError as e:
 print(f'HTTP {e.code}: {e.read().decode()}')
 raise SystemExit(1)
