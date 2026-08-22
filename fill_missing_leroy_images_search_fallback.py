import csv, html, json, os, re, time
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

MASTER=Path('Catalogue_Maitre_SumUp.csv')
FINAL=Path('Cat1_SumUp_FINAL.csv')
MAP=Path('verified_image_map.csv')
VERIFIED=Path('verified_leroy_images.csv')
AUDIT=Path('verified_leroy_images_audit.csv')
STATE=Path('verified_leroy_images_state_pass3.json')
BATCH_SIZE=int(os.environ.get('BATCH_SIZE','100'))
DELAY=float(os.environ.get('REQUEST_DELAY','0.15'))
S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36','Accept-Language':'fr-FR,fr;q=0.9,en;q=0.6'})

def get(url, timeout=25, stream=False):
    for n in range(3):
        try:
            r=S.get(url,timeout=timeout,allow_redirects=True,stream=stream)
            if r.status_code==200:return r
        except Exception: pass
        time.sleep(0.7*(n+1))
    return None

def read_csv(p):
    with p.open('r',encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f); return r.fieldnames or [],list(r)

def write_csv(p,fields,rows):
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def product_links_from_search(pid,name):
    q=quote_plus(f'site:leroymerlin.fr/produits {pid} {name[:80]}')
    urls=[
      'https://www.google.com/search?q='+q,
      'https://www.bing.com/search?q='+q,
      'https://html.duckduckgo.com/html/?q='+q,
    ]
    found=[]
    pat=re.compile(r'https?://(?:www\.)?leroymerlin\.fr/produits/[^\s"<>]+?'+re.escape(pid)+r'\.html',re.I)
    for su in urls:
        r=get(su)
        if not r: continue
        txt=html.unescape(r.text)
        for m in pat.findall(txt): found.append(m.split('&')[0])
        soup=BeautifulSoup(txt,'html.parser')
        for a in soup.find_all('a',href=True):
            h=html.unescape(a['href'])
            if 'uddg=' in h:
                try:h=unquote(parse_qs(urlparse(h).query).get('uddg',[''])[0])
                except Exception: pass
            m=pat.search(h)
            if m: found.append(m.group(0))
        if found: break
    out=[];seen=set()
    for u in found:
        if u not in seen:seen.add(u);out.append(u)
    return out

def image_ok(u):
    host=(urlparse(u).hostname or '').lower()
    if host!='media.adeo.com' and not host.endswith('.media.adeo.com'): return False
    r=get(u,stream=True)
    if not r:return False
    try:
        ct=(r.headers.get('content-type') or '').lower()
        chunk=next(r.iter_content(1024),b'')
        return ct.startswith('image/') and len(chunk)>32
    except Exception:return False
    finally:r.close()

def extract_image(pid,url):
    r=get(url)
    if not r or pid not in r.url:return '','page_unreachable'
    soup=BeautifulSoup(r.text,'html.parser')
    candidates=[]
    for tag in soup.find_all('script',type='application/ld+json'):
        try:obj=json.loads(tag.get_text(strip=True))
        except Exception:continue
        stack=obj if isinstance(obj,list) else [obj]
        for x in stack:
            if not isinstance(x,dict):continue
            for y in ([x]+(x.get('@graph') if isinstance(x.get('@graph'),list) else [])):
                if not isinstance(y,dict):continue
                t=y.get('@type')
                if t=='Product' or (isinstance(t,list) and 'Product' in t):
                    sku=str(y.get('sku') or y.get('mpn') or '')
                    if sku and pid not in sku:continue
                    im=y.get('image')
                    if isinstance(im,list):candidates.extend([str(v.get('url','') if isinstance(v,dict) else v) for v in im])
                    elif isinstance(im,dict):candidates.append(str(im.get('url','')))
                    elif im:candidates.append(str(im))
    for prop in [('property','og:image'),('name','twitter:image')]:
        m=soup.find('meta',attrs={prop[0]:prop[1]})
        if m and m.get('content'):candidates.append(str(m['content']))
    candidates += re.findall(r'https://media\.adeo\.com/media/[^"\'<>\\ ]+',html.unescape(r.text))
    for im in candidates:
        im=html.unescape(im).replace('\\u0026','&').strip().rstrip('),]')
        if image_ok(im): return im,'verified_pass3'
    return '','no_valid_adeo_image'

def load_map():
    d={}
    if MAP.exists():
        _,rows=read_csv(MAP)
        for r in rows:
            if (r.get('SKU') or '').strip() and (r.get('Image 1') or '').strip():d[r['SKU'].strip()]=r['Image 1'].strip()
    return d

def load_state():
    if STATE.exists():
        try:return json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:pass
    return {'processed':[],'completed':False}

fields,master=read_csv(MASTER); image_map=load_map(); state=load_state(); processed=set(state.get('processed') or [])
missing=[r for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending=[r for r in missing if (r.get('SKU') or '').strip() not in processed]
batch=pending[:BATCH_SIZE]
print(f'PASS3 START blank={len(missing)} pending={len(pending)} batch={len(batch)}',flush=True)
if not batch:
    STATE.write_text(json.dumps({'processed':sorted(processed),'completed':True},indent=2),encoding='utf-8')
    print(json.dumps({'catalogue_finished':True,'newly_verified':0,'remaining_blank':len(missing)}),flush=True);raise SystemExit

audit=[]
if AUDIT.exists():
    try:_,audit=read_csv(AUDIT)
    except Exception:pass
new=[];added=0
for i,row in enumerate(batch,1):
    sku=row['SKU'].strip();pid=sku[3:];name=(row.get('Item name') or '').replace('– Réf. '+pid,'')
    links=product_links_from_search(pid,name)
    img='';status='pass3_not_found';purl=''
    for u in links:
        if pid not in u:continue
        purl=u;img,status=extract_image(pid,u)
        if img:break
    if img:
        row['Image 1']=img;image_map[sku]=img;added+=1
    new.append({'SKU':sku,'Status':status,'Product URL':purl,'Image 1':img});processed.add(sku)
    if i%10==0 or i==len(batch):print(f'{i}/{len(batch)} checked | newly_verified={added}',flush=True)
    time.sleep(DELAY)
write_csv(MASTER,fields,master)
if FINAL.exists():
    ff,fr=read_csv(FINAL)
    for r in fr:
        sku=(r.get('SKU') or '').strip()
        if not (r.get('Image 1') or '').strip() and sku in image_map:r['Image 1']=image_map[sku]
    write_csv(FINAL,ff,fr)
maprows=[{'SKU':k,'Image 1':image_map[k]} for k in sorted(image_map)]
for p in (MAP,VERIFIED):write_csv(p,['SKU','Image 1'],maprows)
write_csv(AUDIT,['SKU','Status','Product URL','Image 1'],audit+new)
remaining=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
unprocessed=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
STATE.write_text(json.dumps({'processed':sorted(processed),'completed':unprocessed==0},indent=2),encoding='utf-8')
print(json.dumps({'checked_this_run':len(batch),'newly_verified':added,'remaining_blank':remaining,'remaining_unprocessed':unprocessed,'catalogue_finished':unprocessed==0}),flush=True)
