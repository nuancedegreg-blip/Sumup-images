import csv
import html
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

# -----------------------------
# CONFIG
# -----------------------------
BASE_CATALOG = Path('Cat1_SumUp_FINAL.csv') if Path('Cat1_SumUp_FINAL.csv').exists() else Path('Cat1.csv')
MASTER = Path('Catalogue_Maitre_SumUp.csv')
NEW_OUT = Path('SumUp_Nouveaux_Articles.csv')
UPDATE_OUT = Path('SumUp_MAJ_Articles.csv')
REPORT = Path('catalogue_audit_report.csv')
STATE = Path('catalogue_state.json')
IMG_DIR = Path('catalog_images')
IMG_DIR.mkdir(exist_ok=True)

MAX_NEW = int(os.environ.get('MAX_NEW', '500'))
AUDIT_EXISTING = int(os.environ.get('AUDIT_EXISTING', '150'))
REQUEST_DELAY = float(os.environ.get('REQUEST_DELAY', '0.15'))

REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/catalog_images'
NOW = datetime.now(timezone.utc).isoformat(timespec='seconds')

QUERIES = [
    'vis bois', 'vis placo', 'vis terrasse', 'cheville', 'scellement chimique', 'tige filetee',
    'silicone sanitaire', 'mastic acrylique', 'mousse expansive', 'colle carrelage', 'joint carrelage',
    'primaire accroche', 'ragréage fibré', 'enduit extérieur', 'enduit rebouchage', 'mortier rapide',
    'ciment', 'béton prêt emploi', 'treillis soudé', 'fer à béton', 'coffrage',
    'multicouche 16', 'raccord multicouche', 'robinet arrêt', 'siphon', 'bonde douche',
    'PER plomberie', 'cuivre plomberie', 'raccord laiton', 'joint plomberie', 'mitigeur',
    'cable electrique', 'gaine ICTA', 'disjoncteur', 'prise electrique', 'interrupteur',
    'boite derivation', 'Wago', 'VMC', 'extracteur air', 'spot led',
    'peinture mur', 'sous couche', 'rouleau peinture', 'bac peinture', 'ruban masquage',
    'OSB 18', 'OSB 22', 'tasseau bois', 'lambourde', 'madrier',
    'pare pluie', 'pare vapeur', 'laine de bois', 'plaque de platre', 'fermacell',
    'EPDM toiture', 'gouttiere', 'etancheite terrasse', 'SPEC douche', 'bande etancheite',
    'portail battant', 'motorisation portail', 'cellule portail', 'cloture aluminium', 'cloture composite',
    'lame terrasse composite', 'lambourde composite', 'nez de marche', 'profil carrelage',
    'radiateur eau chaude', 'vanne thermostatique', 'fixation radiateur', 'plinthe', 'parquet stratifié'
]

CATEGORY_RULES = [
    ('Électricité', ['cable', 'gaine', 'disjoncteur', 'prise', 'interrupteur', 'wago', 'électr', 'spot', 'luminaire']),
    ('VMC / Ventilation', ['vmc', 'extracteur', 'ventilation', 'bouche']),
    ('Plomberie', ['multicouche', 'per ', 'cuivre', 'raccord', 'siphon', 'bonde', 'mitigeur', 'robinet', 'plomberie']),
    ('Carrelage', ['carrelage', 'joint carrelage', 'colle carrelage', 'nez de marche', 'profil carrelage']),
    ('Peinture / Enduit', ['peinture', 'enduit', 'sous couche', 'rouleau', 'ruban masquage']),
    ('Maçonnerie', ['ciment', 'béton', 'mortier', 'treillis', 'fer à béton', 'coffrage', 'ragréage']),
    ('Fixation / Visserie', ['vis ', 'cheville', 'scellement chimique', 'tige filetee', 'tige filetée']),
    ('Étanchéité', ['epdm', 'étanch', 'etanche', 'spec ', 'pare pluie', 'pare vapeur']),
    ('Bois / Panneaux', ['osb', 'tasseau', 'lambourde', 'madrier', 'bois']),
    ('Portail / Clôture', ['portail', 'cloture', 'clôture', 'motorisation portail', 'cellule portail']),
    ('Terrasse', ['terrasse composite', 'lame terrasse', 'lambourde composite']),
    ('Chauffage', ['radiateur', 'thermostatique']),
]

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.5',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/*,*/*;q=0.8',
})


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'query_cursor': 0, 'audit_cursor': 0, 'seen_new': [], 'not_found': {}}


def save_state(state):
    state['seen_new'] = list(dict.fromkeys(state.get('seen_new', [])))[-20000:]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def get(url, timeout=35, accept_image=False):
    headers = {'Referer': 'https://www.leroymerlin.fr/'} if accept_image else None
    for attempt in range(4):
        try:
            r = S.get(url, timeout=timeout, allow_redirects=True, headers=headers)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(0.8 * (attempt + 1))
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
                if isinstance(x.get('@graph'), list):
                    for y in x['@graph']:
                        if isinstance(y, dict):
                            yield y
                yield x


def product_ids_from_search(query):
    r = get('https://www.leroymerlin.fr/recherche?q=' + quote(query))
    if not r:
        return []
    ids = []
    patterns = [
        r'/produits/[^"\']*?-(\d{8})\.html',
        r'"productId"\s*:\s*"?(\d{8})"?',
        r'"product_id"\s*:\s*"?(\d{8})"?'
    ]
    for pat in patterns:
        for m in re.finditer(pat, r.text, re.I):
            pid = m.group(1)
            if pid not in ids:
                ids.append(pid)
    return ids


def product_page(pid):
    r = get('https://www.leroymerlin.fr/recherche?q=' + quote(pid))
    if not r:
        return None, None
    if '/produits/' in r.url and '.html' in r.url:
        return r.url, r
    soup = BeautifulSoup(r.text, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if pid in href and '/produits/' in href and '.html' in href:
            u = urljoin('https://www.leroymerlin.fr', href)
            rr = get(u)
            if rr:
                return rr.url, rr
    m = re.search(r'https://www\.leroymerlin\.fr/produits/[^"\']*?' + re.escape(pid) + r'\.html', r.text)
    if m:
        rr = get(html.unescape(m.group(0)))
        if rr:
            return rr.url, rr
    return None, None


def clean_text(value, max_len):
    s = BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:max_len]


def clean_title(name):
    name = clean_text(name, 250)
    name = re.sub(r'\s*\|\s*Leroy Merlin.*$', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' -|')
    return name[:180]


def normalize_price(value):
    if value is None:
        return ''
    s = str(value).strip().replace('€', '').replace('\u00a0', '').replace(' ', '').replace(',', '.')
    m = re.search(r'\d+(?:\.\d+)?', s)
    if not m:
        return ''
    try:
        p = float(m.group(0))
        if not (0.01 <= p <= 100000):
            return ''
        return f'{p:.2f}'
    except Exception:
        return ''


def classify(name, description=''):
    text = f'{name} {description}'.lower()
    for cat, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return cat
    return 'Leroy Merlin'


def parse_product(pid, url, response):
    soup = BeautifulSoup(response.text, 'html.parser')
    data = {'pid': pid, 'url': url}
    product_obj = None
    for obj in iter_jsonld(soup):
        typ = obj.get('@type')
        if typ == 'Product' or (isinstance(typ, list) and 'Product' in typ):
            product_obj = obj
            break

    if product_obj:
        data['name'] = product_obj.get('name') or ''
        imgs = product_obj.get('image')
        if isinstance(imgs, list):
            imgs = imgs[0] if imgs else ''
        if isinstance(imgs, dict):
            imgs = imgs.get('url', '')
        data['image'] = str(imgs or '')
        offers = product_obj.get('offers') or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        seller = offers.get('seller') or {}
        if isinstance(seller, dict):
            seller = seller.get('name', '')
        data['seller'] = str(seller or '')
        data['price'] = offers.get('price') or offers.get('lowPrice') or ''
        data['description'] = product_obj.get('description') or ''
        data['sku_page'] = str(product_obj.get('sku') or product_obj.get('mpn') or '')

    ogt = soup.find('meta', property='og:title')
    ogi = soup.find('meta', property='og:image')
    if not data.get('name') and ogt:
        data['name'] = ogt.get('content', '')
    if not data.get('image') and ogi:
        data['image'] = ogi.get('content', '')

    page_text = soup.get_text(' ', strip=True)
    page_lower = page_text.lower()
    seller = data.get('seller', '').lower()
    sold_by_leroy = ('leroy merlin' in seller) or ('vendu par leroy merlin' in page_lower)
    marketplace = ('marketplace' in page_lower and not sold_by_leroy)
    if marketplace or not sold_by_leroy:
        return None, 'marketplace_or_seller_unknown'

    data['name'] = clean_title(data.get('name'))
    data['description'] = clean_text(data.get('description'), 1500)
    data['price'] = normalize_price(data.get('price'))
    data['category'] = classify(data['name'], data['description'])
    data['image'] = html.unescape(str(data.get('image') or '')).strip()

    if not data['name']:
        return None, 'missing_name'
    return data, 'ok' if data['image'] else 'ok_no_image'


def host_jpg(pid, image_url):
    if not image_url:
        return ''
    out = IMG_DIR / f'LM-{pid}.jpg'
    public = f'{RAW_BASE}/{out.name}'
    if out.exists() and out.stat().st_size > 800:
        return public
    r = get(image_url, timeout=45, accept_image=True)
    if not r or len(r.content) < 500:
        return ''
    ctype = (r.headers.get('content-type') or '').lower()
    if 'text/html' in ctype:
        return ''
    tmp = Path(tempfile.gettempdir()) / f'lm-{pid}.src'
    tmp.write_bytes(r.content)
    try:
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(1200,iw)':'min(1200,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '3', str(out)
        ], check=True, timeout=90)
        if out.exists() and out.stat().st_size > 800:
            return public
    except Exception:
        out.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    return ''


def blank_row(fields):
    return {k: '' for k in fields}


def build_new_row(fields, info, image):
    row = blank_row(fields)
    row['Item name'] = info['name']
    row['Price'] = info['price']
    row['Tax rate (%)'] = '20'
    row['SKU'] = f"LM-{info['pid']}"
    row['Description (Online Store and Invoices only)'] = info['description']
    row['Category'] = info['category']
    row['Display item at Checkout? (Yes/No)'] = 'Yes'
    row['Image 1'] = image
    row['Display item in Online Store? (Yes/No)'] = 'No'
    return row


def update_existing_row(row, info, image):
    before = dict(row)
    row['Item name'] = info['name'] or row.get('Item name', '')
    if info['price']:
        row['Price'] = info['price']
    if info['description']:
        row['Description (Online Store and Invoices only)'] = info['description']
    row['Category'] = info['category'] or row.get('Category', '')
    if image:
        row['Image 1'] = image
    if not row.get('Tax rate (%)'):
        row['Tax rate (%)'] = '20'
    changes = []
    for k in ['Item name', 'Price', 'Description (Online Store and Invoices only)', 'Category', 'Image 1', 'Tax rate (%)']:
        if (before.get(k) or '') != (row.get(k) or ''):
            changes.append(k)
    return changes


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


state = load_state()
with BASE_CATALOG.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    source_rows = list(reader)

master_rows = []
by_sku = {}
duplicate_count = 0
for row in source_rows:
    sku = (row.get('SKU') or '').strip()
    if sku and sku in by_sku:
        duplicate_count += 1
        continue
    master_rows.append(row)
    if sku:
        by_sku[sku] = row

existing_skus = set(by_sku)
report = []
updated_rows = []
new_rows = []

lm_rows = [r for r in master_rows if re.fullmatch(r'LM-\d{8}', (r.get('SKU') or '').strip())]
if lm_rows:
    cursor = int(state.get('audit_cursor', 0)) % len(lm_rows)
    batch = [lm_rows[(cursor + i) % len(lm_rows)] for i in range(min(AUDIT_EXISTING, len(lm_rows)))]
    state['audit_cursor'] = (cursor + len(batch)) % len(lm_rows)
else:
    batch = []

print(f'AUDIT START | {len(batch)} articles existants à contrôler', flush=True)
for i, row in enumerate(batch, 1):
    sku = row['SKU'].strip()
    pid = sku[3:]
    print(f'ARTICLE {i}/{len(batch)} | {sku}', flush=True)
    url, resp = product_page(pid)
    if not resp:
        cnt = int(state.get('not_found', {}).get(sku, 0)) + 1
        state.setdefault('not_found', {})[sku] = cnt
        report.append([NOW, sku, 'existing', 'not_found', f'consecutive={cnt}', '', ''])
        continue
    info, status = parse_product(pid, url, resp)
    if not info:
        report.append([NOW, sku, 'existing', status, '', url or '', ''])
        continue
    state.setdefault('not_found', {}).pop(sku, None)
    image = host_jpg(pid, info.get('image', ''))
    changes = update_existing_row(row, info, image)
    if changes:
        updated_rows.append(dict(row))
        report.append([NOW, sku, 'existing', 'updated', ';'.join(changes), url, image])
    else:
        report.append([NOW, sku, 'existing', 'unchanged', status, url, image])
    if i % 25 == 0:
        print(f'AUDIT {i}/{len(batch)} | updates={len(updated_rows)}', flush=True)
    time.sleep(REQUEST_DELAY)

seen_new = set(state.get('seen_new', []))
query_cursor = int(state.get('query_cursor', 0)) % len(QUERIES)
ordered_queries = [QUERIES[(query_cursor + i) % len(QUERIES)] for i in range(len(QUERIES))]
state['query_cursor'] = (query_cursor + max(1, min(12, len(QUERIES)))) % len(QUERIES)

candidates = []
seen_candidates = set()
for q in ordered_queries:
    print('SEARCH', q, flush=True)
    for pid in product_ids_from_search(q):
        sku = f'LM-{pid}'
        if sku in existing_skus or pid in seen_candidates or sku in seen_new:
            continue
        seen_candidates.add(pid)
        candidates.append(pid)
    if len(candidates) >= MAX_NEW * 4:
        break
    time.sleep(REQUEST_DELAY)

for pid in candidates:
    if len(new_rows) >= MAX_NEW:
        break
    sku = f'LM-{pid}'
    url, resp = product_page(pid)
    if not resp:
        report.append([NOW, sku, 'new', 'not_found', '', '', ''])
        continue
    info, status = parse_product(pid, url, resp)
    if not info:
        report.append([NOW, sku, 'new', status, '', url or '', ''])
        seen_new.add(sku)
        continue
    image = host_jpg(pid, info.get('image', ''))
    row = build_new_row(fields, info, image)
    new_rows.append(row)
    master_rows.append(row)
    existing_skus.add(sku)
    seen_new.add(sku)
    final_status = 'added' if image else 'added_without_image'
    report.append([NOW, sku, 'new', final_status, info['category'], url, image or info.get('image', '')])
    print(f'ADDED {len(new_rows)}/{MAX_NEW}: {sku} - {info["name"][:70]} | image={"yes" if image else "no"}', flush=True)
    time.sleep(REQUEST_DELAY)

state['seen_new'] = sorted(seen_new)
save_state(state)

write_csv(MASTER, fields, master_rows)
write_csv(NEW_OUT, fields, new_rows)
write_csv(UPDATE_OUT, fields, updated_rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['timestamp_utc', 'sku', 'scope', 'status', 'details', 'product_url', 'image_url'])
    w.writerows(report)

summary = {
    'timestamp_utc': NOW,
    'source_catalog': str(BASE_CATALOG),
    'source_rows': len(source_rows),
    'duplicates_removed': duplicate_count,
    'master_rows': len(master_rows),
    'existing_audited_this_run': len(batch),
    'existing_updated_this_run': len(updated_rows),
    'new_products_this_run': len(new_rows),
    'new_products_without_image': sum(1 for r in new_rows if not (r.get('Image 1') or '').strip()),
    'marketplace_policy': 'reject unless seller/page explicitly identifies Leroy Merlin',
    'safety': 'preserves SumUp item/variant IDs, barcode, inventory and user settings on existing rows'
}
Path('catalogue_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False), flush=True)
