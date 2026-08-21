import csv
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MASTER = Path('Catalogue_Maitre_SumUp.csv')
FINAL = Path('Cat1_SumUp_FINAL.csv')
MAP = Path('verified_image_map.csv')
VERIFIED = Path('verified_leroy_images.csv')
AUDIT = Path('verified_leroy_images_audit.csv')
STATE = Path('verified_leroy_images_state.json')
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.05'))
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '100'))

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/*,*/*;q=0.8',
})


def get(url, timeout=30, stream=False):
    for attempt in range(4):
        try:
            r = S.get(url, timeout=timeout, allow_redirects=True, stream=stream)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(0.7 * (attempt + 1))
    return None


def iter_jsonld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            obj = json.loads(tag.get_text(strip=True))
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        for x in stack:
            if not isinstance(x, dict):
                continue
            graph = x.get('@graph')
            if isinstance(graph, list):
                for y in graph:
                    if isinstance(y, dict):
                        yield y
            yield x


def product_page(pid):
    r = get('https://www.leroymerlin.fr/recherche?q=' + quote(pid))
    if not r:
        return None, None
    if '/produits/' in r.url and pid in r.url and '.html' in r.url:
        return r.url, r
    soup = BeautifulSoup(r.text, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = html.unescape(a['href'])
        if pid in href and '/produits/' in href and '.html' in href:
            u = urljoin('https://www.leroymerlin.fr', href)
            rr = get(u)
            if rr and pid in rr.url:
                return rr.url, rr
    m = re.search(r'https://www\.leroymerlin\.fr/produits/[^"\']*?' + re.escape(pid) + r'\.html', r.text)
    if m:
        rr = get(html.unescape(m.group(0)))
        if rr and pid in rr.url:
            return rr.url, rr
    return None, None


def image_url_really_opens(image_url):
    host = (urlparse(image_url).hostname or '').lower()
    if host != 'media.adeo.com' and not host.endswith('.media.adeo.com'):
        return False
    r = get(image_url, timeout=25, stream=True)
    if not r:
        return False
    try:
        ctype = (r.headers.get('content-type') or '').lower()
        if not ctype.startswith('image/'):
            return False
        chunk = next(r.iter_content(chunk_size=512), b'')
        return len(chunk) >= 32
    except Exception:
        return False
    finally:
        r.close()


def exact_image(pid, url, response):
    if not url or pid not in url:
        return '', 'rejected_url_mismatch'
    soup = BeautifulSoup(response.text, 'html.parser')
    product = None
    for obj in iter_jsonld(soup):
        typ = obj.get('@type')
        if typ == 'Product' or (isinstance(typ, list) and 'Product' in typ):
            product = obj
            break

    image = ''
    seller = ''
    sku_page = ''
    if product:
        sku_page = str(product.get('sku') or product.get('mpn') or '').strip()
        imgs = product.get('image')
        if isinstance(imgs, list):
            imgs = imgs[0] if imgs else ''
        if isinstance(imgs, dict):
            imgs = imgs.get('url', '')
        image = str(imgs or '').strip()
        offers = product.get('offers') or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            s = offers.get('seller') or {}
            if isinstance(s, dict):
                seller = str(s.get('name') or '')
            else:
                seller = str(s or '')

    if sku_page and pid not in sku_page:
        return '', 'rejected_sku_mismatch'

    page_text = soup.get_text(' ', strip=True).lower()
    sold_by_leroy = 'leroy merlin' in seller.lower() or 'vendu par leroy merlin' in page_text
    if not sold_by_leroy:
        return '', 'rejected_marketplace_or_seller_unknown'

    if not image:
        og = soup.find('meta', property='og:image')
        if og:
            image = str(og.get('content') or '').strip()
    image = html.unescape(image)
    if not image:
        return '', 'no_image'

    host = (urlparse(image).hostname or '').lower()
    if host != 'media.adeo.com' and not host.endswith('.media.adeo.com'):
        return '', 'rejected_non_adeo_image'
    if not image_url_really_opens(image):
        return '', 'rejected_image_unreachable'
    return image, 'verified'


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
        return {'processed': []}
    try:
        obj = json.loads(STATE.read_text(encoding='utf-8'))
        if not isinstance(obj, dict):
            return {'processed': []}
        return obj
    except Exception:
        return {'processed': []}


def save_state(processed, completed):
    STATE.write_text(json.dumps({
        'processed': sorted(processed),
        'completed': completed,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


fields, master = read_csv(MASTER)
image_map = load_map()
state = load_state()
processed = set(state.get('processed') or [])

# Reuse only mappings already accepted as verified, and only on blank rows.
pre_applied = 0
for row in master:
    sku = (row.get('SKU') or '').strip()
    if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
        if image_url_really_opens(image_map[sku]):
            row['Image 1'] = image_map[sku]
            pre_applied += 1

missing_all = [
    r for r in master
    if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip())
    and not (r.get('Image 1') or '').strip()
]
pending = [r for r in missing_all if (r.get('SKU') or '').strip() not in processed]
batch = pending[:max(0, BATCH_SIZE)]

print(
    f'START: blank={len(missing_all)} pending={len(pending)} batch={len(batch)} '
    f'pre_applied={pre_applied}',
    flush=True,
)

audit_existing = []
if AUDIT.exists():
    try:
        _, audit_existing = read_csv(AUDIT)
    except Exception:
        audit_existing = []

audit_new = []
verified_now = 0
for i, row in enumerate(batch, 1):
    sku = row['SKU'].strip()
    pid = sku[3:]
    url, resp = product_page(pid)
    if not resp:
        status = 'not_found'
        img = ''
        audit_new.append({'SKU': sku, 'Status': status, 'Product URL': '', 'Image 1': ''})
    else:
        img, status = exact_image(pid, url, resp)
        if img:
            row['Image 1'] = img
            image_map[sku] = img
            verified_now += 1
        audit_new.append({'SKU': sku, 'Status': status, 'Product URL': url or '', 'Image 1': img})
    processed.add(sku)
    if i % 10 == 0 or i == len(batch):
        print(f'{i}/{len(batch)} checked | newly_verified={verified_now}', flush=True)
    time.sleep(REQUEST_DELAY)

write_csv(MASTER, fields, master)

# Mirror only exact verified mappings into final catalog, preserving every other field.
if FINAL.exists():
    f_fields, final_rows = read_csv(FINAL)
    for row in final_rows:
        sku = (row.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
            if image_url_really_opens(image_map[sku]):
                row['Image 1'] = image_map[sku]
    write_csv(FINAL, f_fields, final_rows)

map_rows = [{'SKU': k, 'Image 1': image_map[k]} for k in sorted(image_map)]
for target in (MAP, VERIFIED):
    write_csv(target, ['SKU', 'Image 1'], map_rows)

# Keep a cumulative audit trail. For a SKU rechecked manually later, newest row is last.
audit_fields = ['SKU', 'Status', 'Product URL', 'Image 1']
write_csv(AUDIT, audit_fields, audit_existing + audit_new)

remaining_blank = sum(
    1 for r in master
    if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip())
    and not (r.get('Image 1') or '').strip()
)
remaining_unprocessed = sum(
    1 for r in master
    if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip())
    and not (r.get('Image 1') or '').strip()
    and (r.get('SKU') or '').strip() not in processed
)
completed = remaining_unprocessed == 0
save_state(processed, completed)

print(json.dumps({
    'checked_this_run': len(batch),
    'pre_applied_verified': pre_applied,
    'newly_verified': verified_now,
    'remaining_blank': remaining_blank,
    'remaining_unprocessed': remaining_unprocessed,
    'catalogue_finished': completed,
}, ensure_ascii=False), flush=True)
