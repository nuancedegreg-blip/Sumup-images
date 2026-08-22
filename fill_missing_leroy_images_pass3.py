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
STATE = Path('verified_leroy_images_state_pass3.json')
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '100'))
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.08'))

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/*,*/*;q=0.8',
})


def get(url, timeout=30, stream=False):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=timeout, allow_redirects=True, stream=stream)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(0.6 * (attempt + 1))
    return None


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalized_html(text):
    return html.unescape(text.replace('\\u002F', '/').replace('\\/', '/').replace('\\u0026', '&'))


def extract_product_links(text, pid):
    text = normalized_html(text)
    out = []
    patterns = [
        r'https://www\.leroymerlin\.fr/produits/[^\"\'<>\s]*?' + re.escape(pid) + r'\.html(?:\?[^\"\'<>\s]*)?',
        r'/produits/[^\"\'<>\s]*?' + re.escape(pid) + r'\.html(?:\?[^\"\'<>\s]*)?',
    ]
    for pattern in patterns:
        for found in re.findall(pattern, text, flags=re.I):
            url = urljoin('https://www.leroymerlin.fr', found)
            if pid in url and '/produits/' in url:
                out.append(url)
    return out


def product_page(pid):
    search_url = 'https://www.leroymerlin.fr/recherche?q=' + quote(pid)
    r = get(search_url)
    candidates = []
    if r:
        if '/produits/' in r.url and pid in r.url and '.html' in r.url:
            return r.url, r, 'leroy_search_redirect'
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = normalized_html(a.get('href', ''))
            if pid in href and '/produits/' in href and '.html' in href:
                candidates.append(urljoin('https://www.leroymerlin.fr', href))
        candidates.extend(extract_product_links(r.text, pid))

    # Fallback when Leroy's search results are injected client-side and requests cannot see them.
    # The result must still point to an exact Leroy Merlin product URL containing the reference.
    if not candidates:
        bing_url = 'https://www.bing.com/search?q=' + quote('site:leroymerlin.fr/produits/ "' + pid + '"')
        br = get(bing_url)
        if br:
            bs = BeautifulSoup(br.text, 'html.parser')
            for a in bs.find_all('a', href=True):
                href = normalized_html(a.get('href', ''))
                if 'leroymerlin.fr/produits/' in href and pid in href and '.html' in href:
                    candidates.append(href)
            candidates.extend(extract_product_links(br.text, pid))

    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if 'leroymerlin.fr/produits/' not in url or pid not in url or '.html' not in url:
            continue
        rr = get(url)
        if rr and 'leroymerlin.fr/produits/' in rr.url and pid in rr.url:
            return rr.url, rr, 'exact_product_result'
    return None, None, 'not_found'


def image_really_opens(url):
    host = (urlparse(url).hostname or '').lower()
    if host != 'media.adeo.com' and not host.endswith('.media.adeo.com'):
        return False
    r = get(url, timeout=25, stream=True)
    if not r:
        return False
    try:
        ctype = (r.headers.get('content-type') or '').lower()
        if not ctype.startswith('image/'):
            return False
        chunk = next(r.iter_content(chunk_size=2048), b'')
        return len(chunk) >= 64
    except Exception:
        return False
    finally:
        r.close()


def iter_jsonld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            obj = json.loads(tag.get_text(strip=True))
        except Exception:
            continue
        todo = obj if isinstance(obj, list) else [obj]
        while todo:
            item = todo.pop(0)
            if not isinstance(item, dict):
                continue
            yield item
            graph = item.get('@graph')
            if isinstance(graph, list):
                todo.extend(graph)


def image_candidates(pid, response):
    soup = BeautifulSoup(response.text, 'html.parser')
    out = []

    for obj in iter_jsonld(soup):
        typ = obj.get('@type')
        if typ == 'Product' or (isinstance(typ, list) and 'Product' in typ):
            sku = str(obj.get('sku') or obj.get('mpn') or '')
            if sku and pid not in sku.replace(' ', ''):
                continue
            imgs = obj.get('image')
            if isinstance(imgs, str):
                out.append(imgs)
            elif isinstance(imgs, list):
                for x in imgs:
                    if isinstance(x, str):
                        out.append(x)
                    elif isinstance(x, dict) and x.get('url'):
                        out.append(str(x['url']))
            elif isinstance(imgs, dict) and imgs.get('url'):
                out.append(str(imgs['url']))

    for selector in [
        ('meta', {'property': 'og:image'}),
        ('meta', {'name': 'twitter:image'}),
        ('meta', {'itemprop': 'image'}),
    ]:
        tag = soup.find(selector[0], attrs=selector[1])
        if tag and tag.get('content'):
            out.append(str(tag.get('content')))

    # Leroy pages often contain the ADEO URL inside hydration JSON rather than visible tags.
    raw = normalized_html(response.text)
    out.extend(re.findall(r'https://media\.adeo\.com/media/[^\"\'<>\s]+', raw, flags=re.I))

    cleaned = []
    seen = set()
    for url in out:
        url = normalized_html(str(url)).strip().rstrip('),]};')
        if url in seen:
            continue
        seen.add(url)
        host = (urlparse(url).hostname or '').lower()
        if host == 'media.adeo.com' or host.endswith('.media.adeo.com'):
            cleaned.append(url)
    return cleaned


def exact_image(pid, url, response):
    if not url or pid not in url:
        return '', 'rejected_url_mismatch'
    for img in image_candidates(pid, response):
        if image_really_opens(img):
            return img, 'verified_pass3'
    return '', 'no_working_adeo_image'


def load_map():
    out = {}
    if MAP.exists():
        _, rows = read_csv(MAP)
        for row in rows:
            sku = (row.get('SKU') or '').strip()
            img = (row.get('Image 1') or '').strip()
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


fields, master = read_csv(MASTER)
image_map = load_map()
state = load_state()
processed = set(state.get('processed') or [])

missing = [r for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip()]
pending = [r for r in missing if (r.get('SKU') or '').strip() not in processed]
batch = pending[:BATCH_SIZE]
print(f'PASS3 START blank={len(missing)} pending={len(pending)} batch={len(batch)}', flush=True)

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
for i, row in enumerate(batch, 1):
    sku = (row.get('SKU') or '').strip()
    pid = sku[3:]
    url, resp, source = product_page(pid)
    if not resp:
        img, status = '', 'pass3_not_found'
    else:
        img, status = exact_image(pid, url, resp)
        if img:
            row['Image 1'] = img
            image_map[sku] = img
            verified_now += 1
    audit_new.append({'SKU': sku, 'Status': status + ':' + source, 'Product URL': url or '', 'Image 1': img})
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
write_csv(MAP, ['SKU', 'Image 1'], map_rows)
write_csv(VERIFIED, ['SKU', 'Image 1'], map_rows)
write_csv(AUDIT, ['SKU', 'Status', 'Product URL', 'Image 1'], audit_existing + audit_new)

remaining_blank = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip())
remaining_unprocessed = sum(1 for r in master if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip()) and not (r.get('Image 1') or '').strip() and (r.get('SKU') or '').strip() not in processed)
completed = remaining_unprocessed == 0
save_state(processed, completed)
print(json.dumps({'checked_this_run': len(batch), 'newly_verified': verified_now, 'remaining_blank': remaining_blank, 'remaining_unprocessed': remaining_unprocessed, 'catalogue_finished': completed}, ensure_ascii=False), flush=True)
