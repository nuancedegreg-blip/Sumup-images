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
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.05'))

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/*,*/*;q=0.8',
})


def get(url, timeout=30):
    for attempt in range(4):
        try:
            r = S.get(url, timeout=timeout, allow_redirects=True)
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


fields, master = read_csv(MASTER)
image_map = load_map()

# Existing verified mappings are immediately reusable, but only blanks are filled.
pre_applied = 0
for row in master:
    sku = (row.get('SKU') or '').strip()
    if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
        row['Image 1'] = image_map[sku]
        pre_applied += 1

missing = [r for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
print(f'START: {len(missing)} Leroy references still missing images; {pre_applied} filled from verified map', flush=True)

audit = []
verified_now = 0
for i, row in enumerate(missing, 1):
    sku = row['SKU'].strip()
    pid = sku[3:]
    url, resp = product_page(pid)
    if not resp:
        audit.append([sku, 'not_found', '', ''])
    else:
        img, status = exact_image(pid, url, resp)
        if img:
            row['Image 1'] = img
            image_map[sku] = img
            verified_now += 1
        audit.append([sku, status, url or '', img])
    if i % 25 == 0 or i == len(missing):
        print(f'{i}/{len(missing)} checked | newly verified={verified_now}', flush=True)
    time.sleep(REQUEST_DELAY)

write_csv(MASTER, fields, master)

# Mirror only exact verified mappings into final catalog, preserving every other field.
if FINAL.exists():
    f_fields, final_rows = read_csv(FINAL)
    for row in final_rows:
        sku = (row.get('SKU') or '').strip()
        if re.fullmatch(r'LM-\d{8}', sku) and not (row.get('Image 1') or '').strip() and sku in image_map:
            row['Image 1'] = image_map[sku]
    write_csv(FINAL, f_fields, final_rows)

map_rows = [{'SKU': k, 'Image 1': image_map[k]} for k in sorted(image_map)]
for target in (MAP, VERIFIED):
    write_csv(target, ['SKU', 'Image 1'], map_rows)

with AUDIT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU', 'Status', 'Product URL', 'Image 1'])
    w.writerows(audit)

remaining = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
print(json.dumps({
    'checked': len(missing),
    'pre_applied_verified': pre_applied,
    'newly_verified': verified_now,
    'remaining_blank': remaining,
}, ensure_ascii=False), flush=True)
