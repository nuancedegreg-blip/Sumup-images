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
STATE = Path('verified_leroy_images_state_pass2.json')
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.05'))
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '100'))

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/127 Safari/537.36',
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
        time.sleep(0.8 * (attempt + 1))
    return None


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def iter_jsonld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            obj = json.loads(tag.get_text(strip=True))
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        for item in stack:
            if not isinstance(item, dict):
                continue
            graph = item.get('@graph')
            if isinstance(graph, list):
                for child in graph:
                    if isinstance(child, dict):
                        yield child
            yield item


def product_page(pid):
    search_url = 'https://www.leroymerlin.fr/recherche?q=' + quote(pid)
    r = get(search_url)
    if not r:
        return None, None

    # Leroy often redirects a reference search directly to the matching product.
    if '/produits/' in r.url and pid in r.url and '.html' in r.url:
        return r.url, r

    soup = BeautifulSoup(r.text, 'html.parser')
    candidates = []
    for a in soup.find_all('a', href=True):
        href = html.unescape(a['href'])
        if pid in href and '/produits/' in href and '.html' in href:
            candidates.append(urljoin('https://www.leroymerlin.fr', href))

    # Also inspect embedded URLs used by the client-side search page.
    pattern = r'https://www\.leroymerlin\.fr/produits/[^"\']*?' + re.escape(pid) + r'\.html'
    candidates.extend(html.unescape(x) for x in re.findall(pattern, r.text))

    seen = set()
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        rr = get(u)
        if rr and '/produits/' in rr.url and pid in rr.url and '.html' in rr.url:
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
        chunk = next(r.iter_content(chunk_size=1024), b'')
        return len(chunk) >= 32
    except Exception:
        return False
    finally:
        r.close()


def main_image_from_exact_page(pid, url, response):
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
    if product:
        sku_page = str(product.get('sku') or product.get('mpn') or '').strip()
        if sku_page and pid not in sku_page:
            return '', 'rejected_sku_mismatch'
        imgs = product.get('image')
        if isinstance(imgs, list):
            imgs = imgs[0] if imgs else ''
        if isinstance(imgs, dict):
            imgs = imgs.get('url', '')
        image = str(imgs or '').strip()

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
    return image, 'verified_pass2'


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
    STATE.write_text(json.dumps({
        'processed': sorted(processed),
        'completed': completed,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


fields, master = read_csv(MASTER)
image_map = load_map()
state = load_state()
processed = set(state.get('processed') or [])

missing_all = [
    r for r in master
    if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip())
    and not (r.get('Image 1') or '').strip()
]
pending = [r for r in missing_all if (r.get('SKU') or '').strip() not in processed]
batch = pending[:max(0, BATCH_SIZE)]

print(f'PASS2 START: blank={len(missing_all)} pending={len(pending)} batch={len(batch)}', flush=True)

# Once the second pass is complete, repeated runs make no changes.
if not batch:
    if state.get('completed'):
        print(json.dumps({'catalogue_finished': True, 'newly_verified': 0, 'remaining_blank': len(missing_all)}), flush=True)
        raise SystemExit(0)
    save_state(processed, True)
    print(json.dumps({'catalogue_finished': True, 'newly_verified': 0, 'remaining_blank': len(missing_all)}), flush=True)
    raise SystemExit(0)

audit_existing = []
if AUDIT.exists():
    try:
        _, audit_existing = read_csv(AUDIT)
    except Exception:
        pass

audit_new = []
verified_now = 0
for i, row in enumerate(batch, 1):
    sku = (row.get('SKU') or '').strip()
    pid = sku[3:]
    url, resp = product_page(pid)
    if not resp:
        img, status = '', 'pass2_not_found'
    else:
        img, status = main_image_from_exact_page(pid, url, resp)
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

if FINAL.exists():
    final_fields, final_rows = read_csv(FINAL)
    for row in final_rows:
        sku = (row.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
            row['Image 1'] = image_map[sku]
    write_csv(FINAL, final_fields, final_rows)

map_rows = [{'SKU': sku, 'Image 1': image_map[sku]} for sku in sorted(image_map)]
for target in (MAP, VERIFIED):
    write_csv(target, ['SKU', 'Image 1'], map_rows)

write_csv(AUDIT, ['SKU', 'Status', 'Product URL', 'Image 1'], audit_existing + audit_new)

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
    'newly_verified': verified_now,
    'remaining_blank': remaining_blank,
    'remaining_unprocessed': remaining_unprocessed,
    'catalogue_finished': completed,
}, ensure_ascii=False), flush=True)
