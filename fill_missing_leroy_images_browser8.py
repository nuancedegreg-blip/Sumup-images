import csv, html, json, os, re, time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs
from playwright.sync_api import sync_playwright

MASTER=Path('Catalogue_Maitre_SumUp.csv'); FINAL=Path('Cat1_SumUp_FINAL.csv')
MAP=Path('verified_image_map.csv'); VERIFIED=Path('verified_leroy_images.csv')
AUDIT=Path('verified_leroy_images_audit.csv'); STATE=Path('verified_leroy_images_state_browser8.json')
BATCH_SIZE=int(os.environ.get('BATCH_SIZE','100')); REQUEST_DELAY=float(os.environ.get('REQUEST_DELAY','0.10'))
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36'

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

def accept_cookies(page):
    for label in ['Tout accepter','Accepter tout','Accepter','J’accepte',"J'accepte"]:
        try:
            b=page.get_by_role('button',name=re.compile(label,re.I))
            if b.count(): b.first.click(timeout=1200); page.wait_for_timeout(300); return
        except Exception: pass

def exact_url(page,pid):
    product_re=re.compile(r'https://www\\.leroymerlin\\.fr/produits/[^\\s\"\\\'<>?&]*?'+re.escape(pid)+r'\\.html',re.I)
    query=f'{pid} Leroy Merlin'
    for search_url in ['https://www.bing.com/search?q='+quote(query),'https://www.google.com/search?q='+quote(query)]:
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

def score_image(url):
    try:
        u=urlparse(url)
        if (u.hostname or '').lower()!='media.adeo.com': return -1
        if '/media/' not in u.path: return -1
        q=parse_qs(u.query)
        w=int((q.get('width') or q.get('w') or ['0'])[0] or 0)
        h=int((q.get('height') or q.get('h') or ['0'])[0] or 0)
        if 0 < w < 250 or 0 < h < 250: return -1
        if any(x in url.lower() for x in ['home-index','logo','flag','picto','icon']): return -1
        return (w*h if w and h else 1000000)
    except Exception: return -1

def capture_product_image(page,pid,url):
    hits=[]
    def on_response(resp):
        try:
            u=resp.url
            if 'media.adeo.com/media/' in u and resp.status==200:
                c=(resp.headers.get('content-type') or '').lower()
                if c.startswith('image/') and score_image(u)>=0: hits.append(u)
        except Exception: pass
    page.on('response',on_response)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=45000); accept_cookies(page)
        page.wait_for_timeout(2500)
        try: page.locator('body').evaluate('el => window.scrollTo(0, 450)')
        except Exception: pass
        page.wait_for_timeout(2500)
        if pid not in page.url: return '','product_open_failed'
        # Also inspect rendered DOM after network capture.
        try:
            raw=html.unescape(page.content().replace('\\u002F','/').replace('\\/','/').replace('\\u0026','&'))
            hits.extend(re.findall(r'https://media\\.adeo\\.com/media/[^\\s\"\\\'<>]+',raw,re.I))
        except Exception: pass
    finally:
        try: page.remove_listener('response',on_response)
        except Exception: pass
    uniq=[]; seen=set()
    for u in hits:
        u=str(u).strip().rstrip('),]};')
        if u not in seen and score_image(u)>=0:
            seen.add(u); uniq.append(u)
    if not uniq: return '','no_adeo_network_image'
    uniq.sort(key=score_image,reverse=True)
    return uniq[0],'verified_headful_network'

fields,master=read_csv(MASTER); image_map=load_map(); state=load_state(); processed=set(state.get('processed') or [])
missing=[r for r in master if re.fullmatch(r'LM-\\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending=[r for r in missing if (r.get('SKU') or '').strip() not in processed]; batch=pending[:BATCH_SIZE]
print(f'BROWSER8 START blank={len(missing)} pending={len(pending)} batch={len(batch)}',flush=True)
if not batch:
    if not state.get('completed'): save_state(processed,True)
    print(json.dumps({'catalogue_finished':True,'newly_verified':0,'remaining_blank':len(missing)}),flush=True); raise SystemExit(0)

audit_existing=[]
if AUDIT.exists():
    try: _,audit_existing=read_csv(AUDIT)
    except Exception: pass
audit_new=[]; verified_now=0
with sync_playwright() as p:
    browser=p.chromium.launch(headless=False)
    context=browser.new_context(locale='fr-FR',user_agent=UA,viewport={'width':1440,'height':1000})
    page=context.new_page()
    for i,row in enumerate(batch,1):
        sku=(row.get('SKU') or '').strip(); pid=sku[3:]
        product_url,source=exact_url(page,pid); img=''; status=source
        if product_url:
            img,istatus=capture_product_image(page,pid,product_url); status=istatus+':'+source
        if img:
            row['Image 1']=img; image_map[sku]=img; verified_now+=1
        audit_new.append({'SKU':sku,'Status':status,'Product URL':product_url,'Image 1':img}); processed.add(sku)
        print(f'{i}/{len(batch)} {sku} status={status} newly_verified={verified_now} image={img}',flush=True)
        time.sleep(REQUEST_DELAY)
    browser.close()

write_csv(MASTER,fields,master)
if FINAL.exists():
    ffields,frows=read_csv(FINAL)
    for r in frows:
        sku=(r.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\\d{8}',sku) and not (r.get('Image 1') or '').strip() and sku in image_map: r['Image 1']=image_map[sku]
    write_csv(FINAL,ffields,frows)
map_rows=[{'SKU':k,'Image 1':image_map[k]} for k in sorted(image_map)]
write_csv(MAP,['SKU','Image 1'],map_rows); write_csv(VERIFIED,['SKU','Image 1'],map_rows); write_csv(AUDIT,['SKU','Status','Product URL','Image 1'],audit_existing+audit_new)
remaining_blank=sum(1 for r in master if re.fullmatch(r'LM-\\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed=sum(1 for r in master if re.fullmatch(r'LM-\\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed=remaining_unprocessed==0; save_state(processed,completed)
print(json.dumps({'checked_this_run':len(batch),'newly_verified':verified_now,'remaining_blank':remaining_blank,'remaining_unprocessed':remaining_unprocessed,'catalogue_finished':completed},ensure_ascii=False),flush=True)
