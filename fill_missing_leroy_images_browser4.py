import csv
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from playwright.sync_api import sync_playwright

MASTER = Path('Catalogue_Maitre_SumUp.csv')
FINAL = Path('Cat1_SumUp_FINAL.csv')
MAP = Path('verified_image_map.csv')
VERIFIED = Path('verified_leroy_images.csv')
AUDIT = Path('verified_leroy_images_audit.csv')
STATE = Path('verified_leroy_images_state_browser4.json')
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '100'))
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.10'))

HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127 Safari/537.36'})


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def load_map():
    out = {}
    if MAP.exists():
        _, rows = read_csv(MAP)
        for r in rows:
            sku = (r.get('SKU') or '').strip(); img = (r.get('Image 1') or '').strip()
            if sku and img: out[sku] = img
    return out


def load_state():
    if not STATE.exists(): return {'processed': [], 'completed': False}
    try:
        obj = json.loads(STATE.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {'processed': [], 'completed': False}
    except Exception:
        return {'processed': [], 'completed': False}


def save_state(processed, completed):
    STATE.write_text(json.dumps({'processed': sorted(processed), 'completed': completed}, ensure_ascii=False, indent=2), encoding='utf-8')


def is_adeo(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host == 'media.adeo.com' or host.endswith('.media.adeo.com')
    except Exception:
        return False


def image_opens(url):
    if not is_adeo(url): return False
    r = None
    try:
        r = HTTP.get(url, timeout=20, allow_redirects=True, stream=True)
        return r.status_code == 200 and (r.headers.get('content-type') or '').lower().startswith('image/') and len(next(r.iter_content(2048), b'')) >= 64
    except Exception:
        return False
    finally:
        if r is not None:
            try: r.close()
            except Exception: pass


def accept_cookies(page):
    for label in ['Tout accepter', 'Accepter tout', 'Accepter', 'J’accepte', "J'accepte"]:
        try:
            b = page.get_by_role('button', name=re.compile(label, re.I))
            if b.count():
                b.first.click(timeout=1000); page.wait_for_timeout(300); return
        except Exception:
            pass


def exact_product_from_rendered_page(page, pid):
    if '/produits/' in page.url and pid in page.url and '.html' in page.url:
        return page.url
    selector = f'a[href*="/produits/"][href*="{pid}"]'
    try: page.wait_for_selector(selector, timeout=15000)
    except Exception: pass
    links = page.locator(selector)
    for i in range(min(links.count(), 40)):
        href = links.nth(i).get_attribute('href') or ''
        if pid in href and '.html' in href:
            if href.startswith('/'): href = 'https://www.leroymerlin.fr' + href
            return href
    try:
        raw = html.unescape(page.content().replace('\\u002F','/').replace('\\/','/').replace('\\u0026','&'))
        m = re.search(r'https://www\.leroymerlin\.fr/produits/[^\s"\'<>]*?' + re.escape(pid) + r'\.html', raw, re.I)
        if m: return m.group(0)
    except Exception:
        pass
    return ''


def find_exact_product(page, pid):
    for base, source in [
        ('https://www.leroymerlin.fr/search?q=', 'direct_search'),
        ('https://www.leroymerlin.fr/recherche?q=', 'direct_recherche'),
    ]:
        try:
            page.goto(base + quote(pid), wait_until='domcontentloaded', timeout=45000)
            accept_cookies(page)
            page.wait_for_timeout(2500)
            href = exact_product_from_rendered_page(page, pid)
            if href:
                if href != page.url:
                    page.goto(href, wait_until='domcontentloaded', timeout=45000)
                    accept_cookies(page); page.wait_for_timeout(1800)
                if '/produits/' in page.url and pid in page.url:
                    return page.url, source
        except Exception as e:
            last = type(e).__name__
            continue
    try:
        title = page.title()
        body = page.locator('body').inner_text(timeout=3000)[:180].replace('\n',' ')
    except Exception:
        title, body = '', ''
    return '', 'not_found:url=' + page.url[:80] + ':title=' + title[:50] + ':body=' + body[:80]


def srcset_urls(value):
    if not value: return []
    return [html.unescape(p.strip().split()[0]) for p in value.split(',') if p.strip()]


def extract_main_image(page, pid):
    try:
        body = page.locator('body').inner_text(timeout=8000).replace(' ', '')
    except Exception:
        body = ''
    if pid not in page.url and pid not in body: return '', 'ref_not_on_page'
    try:
        page.wait_for_selector('img.mc-carousel-main__image, .mc-carousel-main source, source[srcset*="media.adeo.com"], img[src*="media.adeo.com"]', timeout=15000)
    except Exception:
        pass
    candidates = []
    for sel in ['img.mc-carousel-main__image','.mc-carousel-main source','.mc-carousel-main img','picture source[srcset*="media.adeo.com"]','source[srcset*="media.adeo.com"]','img[src*="media.adeo.com"]','img[data-src*="media.adeo.com"]']:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 80)):
            el = loc.nth(i)
            for attr in ('src','srcset','data-src','data-srcset'):
                try: val = el.get_attribute(attr)
                except Exception: val = None
                if val: candidates.extend(srcset_urls(val) if 'srcset' in attr else [html.unescape(val)])
    try:
        raw = html.unescape(page.content().replace('\\u002F','/').replace('\\/','/').replace('\\u0026','&'))
        candidates += re.findall(r'https://media\.adeo\.com/media/[^\s"\'<>]+', raw, re.I)
    except Exception:
        pass
    seen = set()
    for url in candidates:
        url = str(url).strip().rstrip('),]};')
        if not url or url in seen or not is_adeo(url): continue
        seen.add(url)
        if image_opens(url): return url, 'verified_browser4'
    return '', 'no_working_adeo'


fields, master = read_csv(MASTER)
image_map = load_map(); state = load_state(); processed = set(state.get('processed') or [])
missing = [r for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending = [r for r in missing if (r.get('SKU') or '').strip() not in processed]
batch = pending[:BATCH_SIZE]
print(f'BROWSER4 START blank={len(missing)} pending={len(pending)} batch={len(batch)}', flush=True)
if not batch:
    if not state.get('completed'): save_state(processed, True)
    print(json.dumps({'catalogue_finished': True, 'newly_verified': 0, 'remaining_blank': len(missing)}), flush=True); raise SystemExit(0)

audit_existing = []
if AUDIT.exists():
    try: _, audit_existing = read_csv(AUDIT)
    except Exception: pass

audit_new = []; verified_now = 0
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(locale='fr-FR', user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36', viewport={'width':1440,'height':1000})
    page = context.new_page()
    for i,row in enumerate(batch,1):
        sku=(row.get('SKU') or '').strip(); pid=sku[3:]
        product_url, source = find_exact_product(page,pid)
        img=''; status=source
        if product_url:
            img, istatus = extract_main_image(page,pid); status=istatus+':'+source
        if img:
            row['Image 1']=img; image_map[sku]=img; verified_now+=1
        audit_new.append({'SKU':sku,'Status':status,'Product URL':product_url,'Image 1':img})
        processed.add(sku)
        print(f'{i}/{len(batch)} {sku} status={status} newly_verified={verified_now}', flush=True)
        time.sleep(REQUEST_DELAY)
    browser.close()

write_csv(MASTER,fields,master)
if FINAL.exists():
    ff,fr=read_csv(FINAL)
    for r in fr:
        sku=(r.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}',sku) and not (r.get('Image 1') or '').strip() and sku in image_map: r['Image 1']=image_map[sku]
    write_csv(FINAL,ff,fr)
map_rows=[{'SKU':k,'Image 1':image_map[k]} for k in sorted(image_map)]
write_csv(MAP,['SKU','Image 1'],map_rows); write_csv(VERIFIED,['SKU','Image 1'],map_rows)
write_csv(AUDIT,['SKU','Status','Product URL','Image 1'],audit_existing+audit_new)
remaining_blank=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed=sum(1 for r in master if re.fullmatch(r'LM-\d{8}',(r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed=remaining_unprocessed==0; save_state(processed,completed)
print(json.dumps({'checked_this_run':len(batch),'newly_verified':verified_now,'remaining_blank':remaining_blank,'remaining_unprocessed':remaining_unprocessed,'catalogue_finished':completed},ensure_ascii=False),flush=True)
