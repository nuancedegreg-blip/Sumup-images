import csv
import html
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

CATALOG = Path('Cat1.csv')
OUT = Path('SumUp_Nouveaux_Articles.csv')
REPORT = Path('auto_import_report.csv')
IMG_DIR = Path('new_images')
IMG_DIR.mkdir(exist_ok=True)
MAX_NEW = int(os.environ.get('MAX_NEW', '500'))

REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/new_images'

QUERIES = [
    'vis bois', 'vis placo', 'cheville', 'scellement chimique', 'tige filetee',
    'silicone sanitaire', 'mastic', 'mousse expansive', 'colle carrelage', 'joint carrelage',
    'primaire accroche', 'ragréage fibré', 'enduit extérieur', 'enduit rebouchage', 'mortier rapide',
    'ciment', 'béton', 'treillis soudé', 'fer à béton', 'coffrage',
    'multicouche 16', 'raccord multicouche', 'robinet arrêt', 'siphon', 'bonde douche',
    'PER plomberie', 'cuivre plomberie', 'raccord laiton', 'joint plomberie', 'mitigeur',
    'cable electrique', 'gaine ICTA', 'disjoncteur', 'prise electrique', 'interrupteur',
    'boite derivation', 'Wago', 'VMC', 'extracteur air', 'spot led',
    'peinture mur', 'sous couche', 'rouleau peinture', 'bac peinture', 'ruban masquage',
    'OSB 18', 'OSB 22', 'tasseau bois', 'lambourde', 'madrier',
    'pare pluie', 'pare vapeur', 'laine de bois', 'plaque de platre', 'fermacell',
    'EPDM toiture', 'gouttiere', 'etancheite terrasse', 'SPEC douche', 'bande etancheite',
    'portail battant', 'motorisation portail', 'cellule portail', 'cloture aluminium', 'cloture composite',
    'lame terrasse composite', 'lambourde composite', 'vis terrasse', 'nez de marche', 'profil carrelage'
]

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.5',
})

def get(url, timeout=35):
    for attempt in range(4):
        try:
            r = S.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(1.2 * (attempt + 1))
    return None

def iter_jsonld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            obj = json.loads(tag.get_text(strip=True))
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        for x in stack:
            if isinstance(x, dict):
                if '@graph' in x and isinstance(x['@graph'], list):
                    for y in x['@graph']:
                        if isinstance(y, dict):
                            yield y
                yield x

def product_ids_from_search(query):
    url = 'https://www.leroymerlin.fr/recherche?q=' + quote(query)
    r = get(url)
    if not r:
        return []
    text = r.text
    ids = []
    patterns = [
        r'/produits/[^"\']*?-(\d{8})\.html',
        r'"productId"\s*:\s*"?(\d{8})"?',
        r'"id"\s*:\s*"?(\d{8})"?'
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            pid = m.group(1)
            if pid not in ids:
                ids.append(pid)
    return ids

def product_page(pid):
    # Leroy search usually redirects this query to the matching product or exposes its URL.
    r = get('https://www.leroymerlin.fr/recherche?q=' + quote(pid))
    if not r:
        return None, None
    if '/produits/' in r.url and r.url.endswith('.html'):
        return r.url, r
    soup = BeautifulSoup(r.text, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if pid in href and '/produits/' in href and '.html' in href:
            u = urljoin('https://www.leroymerlin.fr', href)
            rr = get(u)
            if rr:
                return u, rr
    m = re.search(r'https://www\.leroymerlin\.fr/produits/[^"\']*?' + re.escape(pid) + r'\.html', r.text)
    if m:
        rr = get(html.unescape(m.group(0)))
        if rr:
            return rr.url, rr
    return None, None

def parse_product(pid, url, response):
    soup = BeautifulSoup(response.text, 'html.parser')
    data = {}
    product_obj = None
    for obj in iter_jsonld(soup):
        typ = obj.get('@type')
        if typ == 'Product' or (isinstance(typ, list) and 'Product' in typ):
            product_obj = obj
            break
    if product_obj:
        data['name'] = str(product_obj.get('name') or '').strip()
        imgs = product_obj.get('image')
        if isinstance(imgs, list): imgs = imgs[0] if imgs else ''
        if isinstance(imgs, dict): imgs = imgs.get('url','')
        data['image'] = str(imgs or '').strip()
        offers = product_obj.get('offers') or {}
        if isinstance(offers, list): offers = offers[0] if offers else {}
        seller = offers.get('seller') or {}
        if isinstance(seller, dict): seller = seller.get('name','')
        data['seller'] = str(seller or '').strip()
        data['price'] = str(offers.get('price') or '').strip()
        data['description'] = BeautifulSoup(str(product_obj.get('description') or ''), 'html.parser').get_text(' ', strip=True)
    ogt = soup.find('meta', property='og:title')
    ogi = soup.find('meta', property='og:image')
    if not data.get('name') and ogt: data['name'] = ogt.get('content','').strip()
    if not data.get('image') and ogi: data['image'] = ogi.get('content','').strip()
    # Conservative marketplace filter: only accept when seller is Leroy Merlin, or page explicitly says so.
    page_lower = soup.get_text(' ', strip=True).lower()
    seller = data.get('seller','').lower()
    sold_by_leroy = ('leroy merlin' in seller) or ('vendu par leroy merlin' in page_lower)
    if not sold_by_leroy:
        return None
    if not data.get('name') or not data.get('image'):
        return None
    data['pid'] = pid
    data['url'] = url
    return data

def host_jpg(pid, image_url):
    out = IMG_DIR / f'LM-{pid}.jpg'
    if out.exists() and out.stat().st_size > 500:
        return f'{RAW_BASE}/{out.name}'
    r = get(image_url, timeout=45)
    if not r or len(r.content) < 500:
        return ''
    tmp = Path(tempfile.gettempdir()) / f'lm-{pid}.src'
    tmp.write_bytes(r.content)
    try:
        subprocess.run([
            'ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(tmp),
            '-vf',"scale='min(1200,iw)':'min(1200,ih)':force_original_aspect_ratio=decrease",
            '-q:v','3',str(out)
        ], check=True, timeout=90)
        if out.exists() and out.stat().st_size > 500:
            return f'{RAW_BASE}/{out.name}'
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return ''

with CATALOG.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    existing_rows = list(reader)
existing = {(r.get('SKU') or '').strip() for r in existing_rows}

candidates = []
seen = set()
for q in QUERIES:
    print('SEARCH', q, flush=True)
    for pid in product_ids_from_search(q):
        sku = f'LM-{pid}'
        if sku in existing or pid in seen:
            continue
        seen.add(pid)
        candidates.append(pid)
    if len(candidates) >= MAX_NEW * 3:
        break

new_rows = []
report = []
for n, pid in enumerate(candidates, 1):
    if len(new_rows) >= MAX_NEW:
        break
    url, resp = product_page(pid)
    if not resp:
        report.append([pid, 'not_found', '', ''])
        continue
    info = parse_product(pid, url, resp)
    if not info:
        report.append([pid, 'rejected_marketplace_or_incomplete', url or '', ''])
        continue
    img = host_jpg(pid, info['image'])
    if not img:
        report.append([pid, 'image_failed', url or '', info['image']])
        continue
    row = {k: '' for k in fields}
    row['Item name'] = info['name'][:250]
    row['Price'] = info.get('price','')
    row['Tax rate (%)'] = '20'
    row['SKU'] = f'LM-{pid}'
    row['Description (Online Store and Invoices only)'] = (info.get('description') or '')[:1500]
    row['Category'] = 'Leroy Merlin'
    row['Display item at Checkout? (Yes/No)'] = 'Yes'
    row['Image 1'] = img
    row['Display item in Online Store? (Yes/No)'] = 'No'
    new_rows.append(row)
    report.append([pid, 'added', url, img])
    print(f'ADDED {len(new_rows)}/{MAX_NEW}: LM-{pid} - {info["name"][:70]}', flush=True)
    time.sleep(0.25)

with OUT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(new_rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f); w.writerow(['reference','status','product_url','image_url']); w.writerows(report)

print(f'FINAL: {len(new_rows)} new Leroy Merlin products generated, {len(existing)} existing SKUs excluded')
