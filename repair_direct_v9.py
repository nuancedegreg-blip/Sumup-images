import csv, os, re, subprocess, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MASTER=Path('Catalogue_Maitre_SumUp.csv')
OUT=Path('Cat1_SumUp_FINAL.csv')
MAP=Path('rebuild_report.csv')
REPORT=Path('repair_v9_report.csv')
IMG_DIR=Path('images_v9'); IMG_DIR.mkdir(exist_ok=True)
BATCH=int(os.environ.get('V9_BATCH','10'))
REPO=os.environ.get('GITHUB_REPOSITORY','nuancedegreg-blip/Sumup-images')
BRANCH=os.environ.get('GITHUB_REF_NAME','main') or 'main'
RAW_BASE=f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v9'

def ref_from_sku(s):
    m=re.search(r'(\d{5,8})',s or '')
    return m.group(1) if m else ''

def save_jpg(context, ref, urls):
    seen=set()
    for u in urls:
        if not u or u in seen: continue
        seen.add(u)
        tries=[u]
        if 'media.adeo.com/media/' in u:
            base=u.split('?',1)[0]
            tries.insert(0,base+'?fit=bounds&format=jpg&height=1000&quality=85&width=1000')
        for x in tries:
            try:
                r=context.request.get(x,timeout=20000,headers={'Referer':'https://www.leroymerlin.fr/'})
                if not r.ok: continue
                data=r.body()
                if len(data)<1000: continue
                if 'text/html' in (r.headers.get('content-type') or '').lower(): continue
            except Exception:
                continue
            tmp=Path(tempfile.gettempdir())/f'v9-{ref}.src'; tmp.write_bytes(data)
            out=IMG_DIR/f'LM-{ref}.jpg'
            try:
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(tmp),'-vf',"scale='min(900,iw)':'min(900,ih)':force_original_aspect_ratio=decrease",'-frames:v','1','-q:v','4',str(out)],check=True,timeout=25)
                if out.exists() and out.stat().st_size>1000:
                    return f'{RAW_BASE}/{out.name}',x
            except Exception:
                out.unlink(missing_ok=True)
            finally:
                tmp.unlink(missing_ok=True)
    return '',''

with MAP.open('r',encoding='utf-8-sig',newline='') as f:
    known={r['SKU'].strip():r.get('product_url','').strip() for r in csv.DictReader(f) if r.get('product_url','').startswith('https://www.leroymerlin.fr/produits/')}
with MASTER.open('r',encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); fields=rd.fieldnames or []; rows=list(rd)

jobs=[]
for idx,row in enumerate(rows):
    if (row.get('Image 1') or '').strip(): continue
    sku=(row.get('SKU') or '').strip(); url=known.get(sku,'')
    if not url: continue
    ref=ref_from_sku(sku)
    if not ref: continue
    jobs.append((idx,sku,ref,url,row.get('Item name','')))
    if len(jobs)>=BATCH: break

print(f'V9 START | direct_known_urls={len(jobs)}',flush=True)
report=[]; recovered=0
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
    context=browser.new_context(locale='fr-FR',timezone_id='Europe/Paris',viewport={'width':1440,'height':1000},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    page=context.new_page(); page.set_default_timeout(10000)
    for n,(idx,sku,ref,url,name) in enumerate(jobs,1):
        media=[]; status='page_no_image'; title=''; final_url=''
        def on_response(resp):
            u=resp.url
            if 'media.adeo.com' in u and re.search(r'\.(jpg|jpeg|png|webp|avif)(\?|$)',u,re.I):
                media.append(u)
        page.on('response',on_response)
        try:
            page.goto(url,wait_until='domcontentloaded',timeout=30000)
            page.wait_for_timeout(3500)
            final_url=page.url; title=page.title()
            if ref not in final_url:
                try:
                    body=page.locator('body').inner_text(timeout=3000)
                except Exception: body=''
                if ref not in body:
                    status='reference_missing_on_page'
                else:
                    status='page_loaded'
            else:
                status='page_loaded'
            for sel in ('meta[property="og:image"]','meta[name="twitter:image"]'):
                loc=page.locator(sel)
                if loc.count():
                    u=loc.first.get_attribute('content') or ''
                    if u.startswith('http'): media.insert(0,u.replace('&amp;','&'))
            for u in page.eval_on_selector_all('img','els => els.map(e => e.currentSrc || e.src || e.getAttribute("data-src") || "")'):
                if isinstance(u,str) and 'media.adeo.com' in u: media.append(u)
            hosted,chosen=save_jpg(context,ref,media)
            if hosted:
                rows[idx]['Image 1']=hosted; recovered+=1; status='recovered'
            elif status=='page_loaded':
                status='page_loaded_no_downloadable_image'
        except PlaywrightTimeoutError:
            status='timeout'
        except Exception as e:
            status='error_'+type(e).__name__
        page.remove_listener('response',on_response)
        report.append([sku,status,ref,name,url,final_url,title,len(media), chosen if 'chosen' in locals() else '', hosted if 'hosted' in locals() else ''])
        print(f'V9 {n}/{len(jobs)} | {sku} | {status} | media={len(media)} | title={title[:60]}',flush=True)
    context.close(); browser.close()

for target in (MASTER,OUT):
    with target.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
with REPORT.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f); w.writerow(['SKU','status','reference','name','known_product_url','final_url','page_title','media_candidates','chosen_source','hosted_image_url']); w.writerows(report)
print(f'V9 FINAL | recovered={recovered}/{len(jobs)}',flush=True)
