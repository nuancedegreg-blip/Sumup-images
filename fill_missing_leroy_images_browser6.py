import csv
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from playwright.sync_api import sync_playwright

MASTER=Path('Catalogue_Maitre_SumUp.csv'); FINAL=Path('Cat1_SumUp_FINAL.csv')
MAP=Path('verified_image_map.csv'); VERIFIED=Path('verified_leroy_images.csv')
AUDIT=Path('verified_leroy_images_audit.csv'); STATE=Path('verified_leroy_images_state_browser6.json')
BATCH_SIZE=int(os.environ.get('BATCH_SIZE','100')); REQUEST_DELAY=float(os.environ.get('REQUEST_DELAY','0.10'))

def read_csv(path):
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f); return r.fieldnames or [],list(r)

def write_csv(path,fields,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def load_map():
    out={}
    if MAP.exists():
        _,rows=read_csv(MAP)
        for r in rows:
            sku=(r.get('SKU') or '').strip(); img=(r.get('Image 1') or '').strip()
            if sku and img: out[sku]=img
    return out

def load_state():
    if not STATE.exists(): return {'processed':[],'completed':False}
    try:
        x=json.loads(STATE.read_text(encoding='utf-8')); return x if isinstance(x,dict) else {'processed':[],'completed':False}
    except Exception: return {'processed':[],'completed':False}

def save_state(processed,completed):
    STATE.write_text(json.dumps({'processed':sorted(processed),'completed':completed},ensure_ascii=False,indent=2),encoding='utf-8')

def is_adeo(url):
    try:
        h=(urlparse(url).hostname or '').lower(); return h=='media.adeo.com' or h.endswith('.media.adeo.com')
    except Exception: return False

def accept_cookies(page):
    for label in ['Tout accepter','Accepter tout','Accepter','J’accepte',"J'accepte"]:
        try:
            b=page.get_by_role('button',name=re.compile(label,re.I))
            if b.count(): b.first.click(timeout=1000); page.wait_for_timeout(300); return
        except Exception: pass

def exact_url(page,pid):
    product_re=re.compile(r'https://www\.leroymerlin\.fr/produits/[^\s"\'<>?&]*?'+re.escape(pid)+r'\.html',re.I)
    for search_url in ['https://www.bing.com/search?q='+quote(f'{pid} Leroy Merlin'),'https://www.google.com/search?q='+quote(f'{pid} Leroy Merlin')]:
        try:
            page.goto(search_url,wait_until='domcontentloaded',timeout=45000); accept_cookies(page); page.wait_for_timeout(1800)
        except Exception: continue
        links=page.locator('a[href]')
        for i in range(min(links.count(),250)):
            try: href=links.nth(i).get_attribute('href') or ''
            except Exception: continue
            for candidate in (href,unquote(href)):
                m=product_re.search(candidate)
                if m: return html.unescape(m.group(0)),'web_search_link'
        try:
            raw=html.unescape(page.content().replace('\\u002F','/').replace('\\/','/').replace('\\u0026','&'))
            m=product_re.search(unquote(raw))
            if m: return m.group(0),'web_search_html'
        except Exception: pass
    return '','web_search_not_found'

def open_product(page,pid,url):
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=45000); accept_cookies(page); page.wait_for_timeout(2200)
    except Exception: return False
    return 'leroymerlin.fr/produits/' in page.url and pid in page.url

def rendered_image(page,pid):
    try: body=page.locator('body').inner_text(timeout=8000).replace(' ','')
    except Exception: body=''
    if pid not in page.url and pid not in body: return '','ref_not_on_page'
    try: page.wait_for_selector('img.mc-carousel-main__image, .mc-carousel-main img, img[src*="media.adeo.com"]',timeout=15000)
    except Exception: pass
    page.wait_for_timeout(1000)
    for sel in ['img.mc-carousel-main__image','.mc-carousel-main img','img[src*="media.adeo.com"]']:
        loc=page.locator(sel)
        for i in range(min(loc.count(),80)):
            try: data=loc.nth(i).evaluate("el => ({src:el.currentSrc||el.src||'',w:el.naturalWidth||0,h:el.naturalHeight||0,complete:!!el.complete})")
            except Exception: continue
            src=html.unescape(str(data.get('src') or '')).strip()
            if is_adeo(src) and data.get('complete') and int(data.get('w') or 0)>0 and int(data.get('h') or 0)>0:
                return src,'verified_currentSrc'
    return '','no_rendered_adeo'

fields,master=read_csv(MASTER); image_map=load_map(); state=load_state(); processed=set(state.get('processed') or [])
missing=[r for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending=[r for r in missing if (r.get('SKU') or '').strip() not in processed]; batch=pending[:BATCH_SIZE]
print(f'BROWSER6 START blank={len(missing)} pending={len(pending)} batch={len(batch)}',flush=True)
if not batch:
    if not state.get('completed'): save_state(processed,True)
    print(json.dumps({'catalogue_finished':True,'newly_verified':0,'remaining_blank':len(missing)}),flush=True); raise SystemExit(0)
audit_existing=[]
if AUDIT.exists():
    try: _,audit_existing=read_csv(AUDIT)
    except Exception: pass
audit_new=[]; verified_now=0
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True); context=browser.new_context(locale='fr-FR',viewport={'width':1440,'height':1000}); page=context.new_page()
    for i,row in enumerate(batch,1):
        sku=(row.get('SKU') or '').strip(); pid=sku[3:]; product_url,source=exact_url(page,pid); img=''; status=source
        if product_url and open_product(page,pid,product_url): img,istatus=rendered_image(page,pid); status=istatus+':'+source
        elif product_url: status='product_open_failed:'+source
        if img: row['Image 1']=img; image_map[sku]=img; verified_now+=1
        audit_new.append({'SKU':sku,'Status':status,'Product URL':product_url,'Image 1':img}); processed.add(sku)
        print(f'{i}/{len(batch)} {sku} status={status} url={product_url} newly_verified={verified_now}',flush=True); time.sleep(REQUEST_DELAY)
    browser.close()
write_csv(MASTER,fields,master)
if FINAL.exists():
    ffields,frows=read_csv(FINAL)
    for r in frows:
        sku=(r.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}',sku) and not (r.get('Image 1') or '').strip() and sku in image_map: r['Image 1']=image_map[sku]
    write_csv(FINAL,ffields,frows)
map_rows=[{'SKU':k,'Image 1':image_map[k]} for k in sorted(image_map)]
write_csv(MAP,['SKU','Image 1'],map_rows); write_csv(VERIFIED,['SKU','Image 1'],map_rows); write_csv(AUDIT,['SKU','Status','Product URL','Image 1'],audit_existing+audit_new)
remaining_blank=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed=remaining_unprocessed==0; save_state(processed,completed)
print(json.dumps({'checked_this_run':len(batch),'newly_verified':verified_now,'remaining_blank':remaining_blank,'remaining_unprocessed':remaining_unprocessed,'catalogue_finished':completed},ensure_ascii=False),flush=True)
