"""Collect selected brand SVG data from the published Simple Icons package; execute no package code."""
from pathlib import Path
import urllib.request,json,tarfile,io,hashlib,re
app=Path(__file__).resolve().parents[1];out=app/'public/brands/catalog';out.mkdir(parents=True,exist_ok=True)
slugs=['sqlite','langchain','langgraph','mermaid','graphviz','llamaindex','typescript','pytest','ruff','mypy','pandas','apachearrow','pypi','onnx','opencv','modelcontextprotocol','pydantic','powershell','ffmpeg','duckdb','sqlalchemy','tableau','huggingface','fastapi','requests','polars','nextdotjs','react','threedotjs','framer','openai','pinecone','weaviate','milvus','opensearch','langsmith','langfuse','opentelemetry','grafana','libreoffice','json','python','git','nodedotjs']
def fetch(url):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'EvidenceLane asset intake'}),timeout=35) as r:return r.read()
meta=json.loads(fetch('https://registry.npmjs.org/simple-icons/latest'));archive=tarfile.open(fileobj=io.BytesIO(fetch(meta['dist']['tarball'])),mode='r:gz');members={m.name:m for m in archive.getmembers() if m.isfile()};data={}
for name in ['package/_data/simple-icons.json','package/data/simple-icons.json']:
 if name in members:
  raw=json.load(archive.extractfile(members[name]));icons=raw.get('icons',raw) if isinstance(raw,dict) else raw
  for icon in icons:
   if isinstance(icon,dict):data[icon.get('slug',re.sub('[^a-z0-9]','',icon.get('title','').lower()))]=icon
  break
records=[];missing=[]
for slug in slugs:
 name='package/icons/'+slug+'.svg'
 if name not in members:missing.append(slug);continue
 raw=archive.extractfile(members[name]).read();info=data.get(slug,{})
 color=info.get('hex','B9D8EA');color='E2EFF6' if color in ['000000','111111','181717','191919'] else color
 svg=raw.decode().replace('<svg ','<svg fill="#'+color+'" ',1);(out/(slug+'.svg')).write_text(svg,encoding='utf-8')
 records.append({'slug':slug,'file':slug+'.svg','packageVersion':meta['version'],'source':info.get('source','https://github.com/simple-icons/simple-icons'),'hex':color,'sha256':hashlib.sha256(svg.encode()).hexdigest()})
for name in ['package/LICENSE.md','package/DISCLAIMER.md']:
 if name in members:(out/Path(name).name).write_bytes(archive.extractfile(members[name]).read())
(out/'attribution.json').write_text(json.dumps({'package':'simple-icons','version':meta['version'],'tarball':meta['dist']['tarball'],'icons':records,'unavailableSlugs':missing},indent=2),encoding='utf-8')
print(json.dumps({'collected':len(records),'missing':missing,'version':meta['version']}))
