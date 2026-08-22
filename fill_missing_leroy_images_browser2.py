import csv
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse, unquote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MASTER = Path('Catalogue_Maitre_SumUp.csv')
FINAL = Path('Cat1_SumUp_FINAL.csv')
MAP = Path('verified_image_map.csv')
VERIFIED = Path('verified_leroy_images.csv')
AUDIT = Path('verified_leroy_images_audit.csv')
STATE = Path('verified_leroy_images_state_browser2.json')
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


def adeo(url):
    try:
        h = (urlparse(url).hostname or '').lower()
        return h == 'media.adeo.com' or h.endswith('.media.adeo.com')
    except Exception:
        return False


def image_opens(url):
    if not adeo(url): return False
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
    labels = ['Tout accepter', 'Accepter', 'Accepter tout', 'J’accepte', "J'accepte"]
    for label in labels:
        try:
            b = page.get_by_role('button', name=re.compile(label, re.I))
            if b.count():
                b.first.click(timeout=1200)
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def wait_exact_link(page, pid, ms=12000):
    selector = f'a[href*="/produits/"][href*="{pid}"]'
    try:
        page.wait_for_selector(selector, timeout=ms)
    except Exception:
        pass
    loc = page.locator(selector)
    for i in range(min(loc.count(), 30)):
        href = loc.nth(i).get_attribute('href') or ''
        if pid in href and '.html' in href:
            if href.startswith('/'):
                href = 'https://www.leroymerlin.fr' + href
            return href
    return ''


def extract_leroy_link_from_search(page, pid):
    # Search engines sometimes wrap the target in a redirect URL, so inspect href and decoded href.
    links = page.locator('a[href]')
    for i in range(min(links.count(), 200)):
        href = links.nth(i).get_attribute('href') or ''
        decoded = unquote(href)
        for candidate in (href, decoded):
            m = re.search(r'https://www\.leroymerlin\.fr/produits/[^\s"&<>]*?' + re.escape(pid) + r'\.html', candidate, re.I)
            if m:
                return html.unescape(m.group(0))
    content = html.unescape(page.content().replace('\\u002F', '/').replace('\\/', '/').replace('\\u0026', '&'))
    m = re.search(r'https://www\.leroymerlin\.fr/produits/[^\s"<>]*?' + re.escape(pid) + r'\.html', content, re.I)
    return m.group(0) if m else ''


def find_exact_product(page, pid, item_name=''):
    # 1. Native Leroy search, but wait for client-side rendering instead of checking after 2 seconds.
    try:
        page.goto('https://www.leroymerlin.fr/recherche?q=' + quote(pid), wait_until='domcontentloaded', timeout=45000)
        accept_cookies(page)
        if '/produits/' in page.url and pid in page.url and '.html' in page.url:
            page.wait_for_timeout(1200)
            return page.url, 'leroy_redirect'
        href = wait_exact_link(page, pid, 12000)
        if href:
            page.goto(href, wait_until='domcontentloaded', timeout=45000)
            accept_cookies(page); page.wait_for_timeout(1800)
            if pid in page.url: return page.url, 'leroy_rendered_search'
    except Exception:
        pass

    # 2. Rendered Bing fallback. The final accepted page must still be Leroy and contain the exact ref.
    query = f'site:leroymerlin.fr/produits/ "{pid}"'
    if item_name:
        query += ' ' + item_name[:70]
    try:
        page.goto('https://www.bing.com/search?q=' + quote(query), wait_until='domcontentloaded', timeout=45000)
        accept_cookies(page); page.wait_for_timeout(1800)
        href = extract_leroy_link_from_search(page, pid)
        if href:
            page.goto(href, wait_until='domcontentloaded', timeout=45000)
            accept_cookies(page); page.wait_for_timeout(1800)
            if 'leroymerlin.fr/produits/' in page.url and pid in page.url:
                return page.url, 'bing_exact_ref'
    except Exception:
        pass

    return '', 'not_found'


def srcset_urls(v):
    if not v: return []
    return [html.unescape(p.strip().split()[0]) for p in v.split(',') if p.strip()]


def extract_main_image(page, pid):
    try:
        body = page.locator('body').inner_text(timeout=8000).replace(' ', '')
    except Exception:
        body = ''
    if pid not in page.url and pid not in body:
        return '', 'ref_not_on_page'

    # Wait for exactly the carousel seen in the user's Chrome DevTools.
    try:
        page.wait_for_selector('img.mc-carousel-main__image, .mc-carousel-main source, source[srcset*="media.adeo.com"], img[src*="media.adeo.com"]', timeout=12000)
    except Exception:
        pass

    candidates = []
    selectors = [
        'img.mc-carousel-main__image', '.mc-carousel-main source', '.mc-carousel-main img',
        'picture source[srcset*="media.adeo.com"]', 'source[srcset*="media.adeo.com"]',
        'img[src*="media.adeo.com"]', 'img[data-src*="media.adeo.com"]'
    ]
    for sel in selectors:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 60)):
            el = loc.nth(i)
            for a in ('src', 'srcset', 'data-src', 'data-srcset'):
                try: val = el.get_attribute(a)
                except Exception: val = None
                if not val: continue
                candidates.extend(srcset_urls(val) if 'srcset' in a else [html.unescape(val)])

    try:
        raw = html.unescape(page.content().replace('\\u002F', '/').replace('\\/', '/').replace('\\u0026', '&'))
        candidates += re.findall(r'https://media\.adeo\.com/media/[^\s"\'<>]+', raw, re.I)
    except Exception:
        pass

    seen = set()
    for u in candidates:
        u = str(u).strip().rstrip('),]};')
        if u in seen or not adeo(u): continue
        seen.add(u)
        if image_opens(u): return u, 'verified_browser2'
    return '', 'no_working_adeo'


fields, master = read_csv(MASTER)
image_map = load_map(); state = load_state(); processed = set(state.get('processed') or [])
missing = [r for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending = [r for r in missing if (r.get('SKU') or '').strip() not in processed]
batch = pending[:BATCH_SIZE]
print(f'BROWSER2 START blank={len(missing)} pending={len(pending)} batch={len(batch)}', flush=True)
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
    context = browser.new_context(locale='fr-FR', user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36', viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    for i, row in enumerate(batch, 1):
        sku = (row.get('SKU') or '').strip(); pid = sku[3:]; name = (row.get('Item name') or '')
        product_url, source = find_exact_product(page, pid, name)
        img = ''; status = 'browser2_not_found:' + source
        if product_url:
            img, istatus = extract_main_image(page, pid); status = istatus + ':' + source
        if img:
            row['Image 1'] = img; image_map[sku] = img; verified_now += 1
        audit_new.append({'SKU': sku, 'Status': status, 'Product URL': product_url, 'Image 1': img})
        processed.add(sku)
        if i % 5 == 0 or img:
            print(f'{i}/{len(batch)} {sku} status={status} newly_verified={verified_now}', flush=True)
        time.sleep(REQUEST_DELAY)
    browser.close()

write_csv(MASTER, fields, master)
if FINAL.exists():
    ffields, frows = read_csv(FINAL)
    for r in frows:
        sku = (r.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}', sku) and not (r.get('Image 1') or '').strip() and sku in image_map: r['Image 1'] = image_map[sku]
    write_csv(FINAL, ffields, frows)
map_rows = [{'SKU': k, 'Image 1': image_map[k]} for k in sorted(image_map)]
write_csv(MAP, ['SKU','Image 1'], map_rows); write_csv(VERIFIED, ['SKU','Image 1'], map_rows)
write_csv(AUDIT, ['SKU','Status','Product URL','Image 1'], audit_existing + audit_new)
remaining_blank = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed = remaining_unprocessed == 0; save_state(processed, completed)
print(json.dumps({'checked_this_run': len(batch), 'newly_verified': verified_now, 'remaining_blank': remaining_blank, 'remaining_unprocessed': remaining_unprocessed, 'catalogue_finished': completed}, ensure_ascii=False), flush=True)
