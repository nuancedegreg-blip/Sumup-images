import csv, html, json, os, re, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import requests

MASTER=Path('Catalogue_Maitre_SumUp.csv')
OUT=Path('Cat1_SumUp_FINAL.csv')
REBUILD=Path('rebuild_report.csv')
REPORT=Path('repair_v10_report.csv')
IMG_DIR=Path('images_v10'); IMG_DIR.mkdir(exist_ok=True)
BATCH=int(os.environ.get('V10_BATCH','10'))
REPO=os.environ.get('GITHUB_REPOSITORY','nuancedegreg-blip/Sumup-images')
BRANCH=os.environ.get('GITHUB_REF_NAME','main') or 'main'
RAW=f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v10'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'
S=requests.Session(); S.headers.update({'User-Agent':UA,'Accept-Language':'fr-FR,fr;q=0.9,en;q=0.8'})

def ref_of(row):
    m=re.search(r'(\d{5,8})',(row.get('SKU') or ''))
    return m.group(1) if m else ''

def known_urls():
    out={}
    if not REBUILD.exists(): return out
    with REBUILD.open('r',encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            sku=(r.get('SKU') or '').strip(); u=(r.get('product_url') or '').strip()
            if sku and u.startswith('https://www.leroymerlin.fr/produits/'):
                out[sku]=u
    return out

def strict_leroy_page(purl,ref,known=''):
    try:
        p=html.unescape(purl).replace('\\/','/')
        host=urlparse(p).netloc.lower()
        if host not in ('www.leroymerlin.fr','leroymerlin.fr'): return False
        if '/produits/' not in p: return False
        if ref not in p: return False
        if known and ref in known:
            # Same reference is the hard identity key; exact slug may vary over time.
            return True
        return True
    except Exception: return False

def bing_candidates(ref,known):
    queries=[]
    if known: queries.append('"'+known+'"')
    queries.append(f'site:leroymerlin.fr/produits "{ref}"')
    seen=set(); out=[]
    for q in queries:
        url='https://www.bing.com/images/search?q='+quote_plus(q)+'&form=HDRSC3&first=1'
        try:
            text=S.get(url,timeout=18).text
        except Exception:
            continue
        # Bing stores image metadata in the m attribute of result anchors.
        for raw in re.findall(r'\bm="([^"]+)"',text,re.I):
            try:
                obj=json.loads(html.unescape(raw))
            except Exception:
                continue
            purl=obj.get('purl') or obj.get('surl') or ''
            if not strict_leroy_page(purl,ref,known):
                continue
            for key in ('murl','turl'):
                iu=html.unescape(obj.get(key) or '')
                if iu.startswith('http') and iu not in seen:
                    seen.add(iu); out.append((iu,purl,key))
        if out: break
    return out

def save_img(ref,url):
    try:
        r=S.get(url,timeout=20,headers={'Referer':'https://www.bing.com/'})
        if r.status_code!=200 or len(r.content)<1000: return ''
        ct=(r.headers.get('content-type') or '').lower()
        if 'html' in ct: return ''
    except Exception: return ''
    tmp=Path(tempfile.gettempdir())/f'v10-{ref}.src'; tmp.write_bytes(r.content)
    out=IMG_DIR/f'LM-{ref}.jpg'
    try:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(tmp),'-vf',"scale='min(900,iw)':'min(900,ih)':force_original_aspect_ratio=decrease",'-frames:v','1','-q:v','4',str(out)],check=True,timeout=25)
        if out.exists() and out.stat().st_size>1000: return f'{RAW}/{out.name}'
    except Exception:
        out.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    return ''

with MASTER.open('r',encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); fields=rd.fieldnames or []; rows=list(rd)
known=known_urls(); jobs=[]
for i,r in enumerate(rows):
    if (r.get('Image 1') or '').strip(): continue
    ref=ref_of(r); sku=(r.get('SKU') or '').strip()
    if not ref or not known.get(sku): continue
    jobs.append((i,sku,ref,known[sku],r.get('Item name') or ''))
    if len(jobs)>=BATCH: break

report=[]; recovered=0
print(f'V10 START batch={len(jobs)}',flush=True)
for n,(idx,sku,ref,kurl,name) in enumerate(jobs,1):
    candidates=bing_candidates(ref,kurl)
    hosted=''; source=''; purl=''; via=''; status='no_strict_indexed_image'
    for source,purl,via in candidates[:8]:
        hosted=save_img(ref,source)
        if hosted:
            rows[idx]['Image 1']=hosted; recovered+=1; status='recovered_strict'; break
    report.append([sku,status,ref,name,kurl,purl,source,via,hosted,len(candidates)])
    print(f'V10 {n}/{len(jobs)} {sku} {status} candidates={len(candidates)}',flush=True)

for target in (MASTER,OUT):
    with target.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
with REPORT.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f); w.writerow(['SKU','status','reference','name','known_product_url','indexed_page_url','source_image_url','source_type','hosted_image_url','strict_candidates']); w.writerows(report)
remaining=sum(1 for r in rows if not (r.get('Image 1') or '').strip())
print(f'V10 FINAL recovered={recovered} with_image={len(rows)-remaining} remaining={remaining}',flush=True)
