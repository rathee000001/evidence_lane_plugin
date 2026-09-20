"""Read-only local route/link checks against the built public route inventory."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import urlopen
from urllib.error import HTTPError
import json

APP=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:3100'
class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.ids=set();self.h1=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a' and 'href' in a:self.links.append(a['href'])
        if 'id' in a:self.ids.add(a['id'])
        if tag=='h1':self.h1+=1
def read(route):
    try:
        with urlopen(BASE+route,timeout=30) as response:
            html=response.read().decode();status=response.status
    except HTTPError as error:
        html=error.read().decode();status=error.code
    p=Page();p.feed(html)
    return route,p,status
manifest=json.loads((APP/'.next/prerender-manifest.json').read_text())
routes=sorted(r for r in manifest['routes'] if not r.startswith('/_'))
with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(read,routes))
pages={r:p for r,p,_ in results};failures=[]
for route,page,status in results:
    if status!=200 or page.h1!=1:failures.append({'route':route,'status':status,'h1':page.h1})
    for href in page.links:
        u=urlsplit(href)
        if u.scheme or u.netloc:continue
        target=unquote(u.path) or route
        if not target.startswith('/'):continue
        target=target.rstrip('/') or '/'
        if target not in pages:
            failures.append({'route':route,'missingRoute':href})
        elif u.fragment and unquote(u.fragment) not in pages[target].ids:
            failures.append({'route':route,'missingAnchor':href})
report={'base':BASE,'routesChecked':len(routes),'routes':routes,'failures':failures,'scope':'HTTP rendering, single page heading, server-rendered internal links and anchors; browser motion/interaction checks are separate.'}
out=APP/'artwork/selected-page-concepts/FRONTEND_ROUTE_QA.json';out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'routesChecked':len(routes),'failures':failures},indent=2))
raise SystemExit(bool(failures))
