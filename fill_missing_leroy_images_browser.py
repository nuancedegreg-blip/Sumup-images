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
STATE = Path('verified_leroy_images_state_browser.json')
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '100'))
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.10'))

HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'Mozilla/5.0 Chrome/127 Safari/537.36'})


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def load_map():
    out = {}
    if MAP.exists():
        _, rows = read_csv(MAP)
        for r in rows:
            sku = (r.get('SKU') or '').strip()
            img = (r.get('Image 1') or '').strip()
            if sku and img:
                out[sku] = img
    return out


def load_state():
    if not STATE.exists():
        return {'processed': [], 'completed': False}
    try:
        obj = json.loads(STATE.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {'processed': [], 'completed': False}
    except Exception:
        return {'processed': [], 'completed': False}


def save_state(processed, completed):
    STATE.write_text(json.dumps({'processed': sorted(processed), 'completed': completed}, ensure_ascii=False, indent=2), encoding='utf-8')


def adeo(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host == 'media.adeo.com' or host.endswith('.media.adeo.com')
    except Exception:
        return False


def image_opens(url):
    if not adeo(url):
        return False
    try:
        r = HTTP.get(url, timeout=25, allow_redirects=True, stream=True)
        if r.status_code != 200:
            return False
        if not (r.headers.get('content-type') or '').lower().startswith('image/'):
            return False
        chunk = next(r.iter_content(2048), b'')
        return len(chunk) >= 64
    except Exception:
        return False
    finally:
        try:
            r.close()
        except Exception:
            pass


def srcset_urls(value):
    if not value:
        return []
    out = []
    for part in value.split(','):
        u = part.strip().split()[0] if part.strip() else ''
        if u:
            out.append(html.unescape(u))
    return out


def find_exact_product(page, pid):
    search = 'https://www.leroymerlin.fr/recherche?q=' + quote(pid)
    try:
        page.goto(search, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(2200)
    except Exception:
        pass

    if '/produits/' in page.url and pid in page.url and '.html' in page.url:
        return page.url

    # Same thing a human sees in the rendered search results.
    links = page.locator(f'a[href*="/produits/"][href*="{pid}"]')
    count = min(links.count(), 20)
    for i in range(count):
        href = links.nth(i).get_attribute('href') or ''
        if pid in href and '.html' in href:
            if href.startswith('/'):
                href = 'https://www.leroymerlin.fr' + href
            try:
                page.goto(href, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(1600)
            except Exception:
                continue
            if '/produits/' in page.url and pid in page.url:
                return page.url
    return ''


def extract_main_adeo_image(page, pid):
    # Confirm exact reference on the product page, either URL or visible text.
    try:
        body = page.locator('body').inner_text(timeout=10000)
    except Exception:
        body = ''
    if pid not in page.url and pid not in body.replace(' ', ''):
        return ''

    candidates = []

    # Screenshot-confirmed Leroy DOM: img.mc-carousel-main__image + source/srcset.
    selectors = [
        'img.mc-carousel-main__image',
        '.mc-carousel-main img',
        '.mc-carousel-main source',
        'img[class*="carousel-main"]',
        'source[srcset*="media.adeo.com"]',
        'img[src*="media.adeo.com"]',
    ]
    attrs = ['src', 'srcset', 'data-src', 'data-srcset']
    for sel in selectors:
        loc = page.locator(sel)
        n = min(loc.count(), 40)
        for i in range(n):
            el = loc.nth(i)
            for attr in attrs:
                try:
                    val = el.get_attribute(attr)
                except Exception:
                    val = None
                if not val:
                    continue
                if 'srcset' in attr:
                    candidates.extend(srcset_urls(val))
                else:
                    candidates.append(html.unescape(val))

    # Last fallback: every rendered ADEO URL in the final DOM.
    try:
        content = html.unescape(page.content().replace('\\u002F', '/').replace('\\/', '/').replace('\\u0026', '&'))
        candidates.extend(re.findall(r'https://media\.adeo\.com/media/[^\"\'<>\s]+', content, flags=re.I))
    except Exception:
        pass

    seen = set()
    for url in candidates:
        url = str(url).strip().rstrip('),]};')
        if not url or url in seen or not adeo(url):
            continue
        seen.add(url)
        if image_opens(url):
            return url
    return ''


fields, master = read_csv(MASTER)
image_map = load_map()
state = load_state()
processed = set(state.get('processed') or [])
missing = [r for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending = [r for r in missing if (r.get('SKU') or '').strip() not in processed]
batch = pending[:BATCH_SIZE]
print(f'BROWSER START blank={len(missing)} pending={len(pending)} batch={len(batch)}', flush=True)

if not batch:
    if not state.get('completed'):
        save_state(processed, True)
    print(json.dumps({'catalogue_finished': True, 'newly_verified': 0, 'remaining_blank': len(missing)}), flush=True)
    raise SystemExit(0)

audit_existing = []
if AUDIT.exists():
    try:
        _, audit_existing = read_csv(AUDIT)
    except Exception:
        pass

audit_new = []
verified_now = 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        locale='fr-FR',
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36',
        viewport={'width': 1440, 'height': 1000},
    )
    page = context.new_page()

    for i, row in enumerate(batch, 1):
        sku = (row.get('SKU') or '').strip()
        pid = sku[3:]
        product_url = find_exact_product(page, pid)
        img = ''
        status = 'browser_not_found'
        if product_url:
            img = extract_main_adeo_image(page, pid)
            status = 'verified_browser_dom' if img else 'browser_product_found_no_adeo'
        if img:
            row['Image 1'] = img
            image_map[sku] = img
            verified_now += 1
        audit_new.append({'SKU': sku, 'Status': status, 'Product URL': product_url, 'Image 1': img})
        processed.add(sku)
        if i % 5 == 0 or i == len(batch):
            print(f'{i}/{len(batch)} checked | newly_verified={verified_now}', flush=True)
        time.sleep(REQUEST_DELAY)

    browser.close()

write_csv(MASTER, fields, master)
if FINAL.exists():
    ffields, frows = read_csv(FINAL)
    for row in frows:
        sku = (row.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
            row['Image 1'] = image_map[sku]
    write_csv(FINAL, ffields, frows)

map_rows = [{'SKU': k, 'Image 1': image_map[k]} for k in sorted(image_map)]
write_csv(MAP, ['SKU', 'Image 1'], map_rows)
write_csv(VERIFIED, ['SKU', 'Image 1'], map_rows)
write_csv(AUDIT, ['SKU', 'Status', 'Product URL', 'Image 1'], audit_existing + audit_new)

remaining_blank = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed = remaining_unprocessed == 0
save_state(processed, completed)
print(json.dumps({'checked_this_run': len(batch), 'newly_verified': verified_now, 'remaining_blank': remaining_blank, 'remaining_unprocessed': remaining_unprocessed, 'catalogue_finished': completed}, ensure_ascii=False), flush=True)
