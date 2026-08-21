import csv
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

SRC = Path('Cat1.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
MASTER = Path('Catalogue_Maitre_SumUp.csv')
REPORT = Path('conversion_report.csv')
IMG_DIR = Path('images')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '24'))
HTTP_TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '10'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images'

UA = 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1'

CATEGORY_RULES = [
    ('Électricité', ['cable', 'câble', 'gaine', 'disjoncteur', 'prise', 'interrupteur', 'wago', 'électr', 'spot', 'luminaire']),
    ('VMC / Ventilation', ['vmc', 'extracteur', 'ventilation', 'bouche']),
    ('Plomberie', ['multicouche', 'per ', 'cuivre', 'raccord', 'siphon', 'bonde', 'mitigeur', 'robinet', 'plomberie']),
    ('Carrelage', ['carrelage', 'joint carrelage', 'colle carrelage', 'nez de marche', 'profil carrelage']),
    ('Peinture / Enduit', ['peinture', 'enduit', 'sous couche', 'sous-couche', 'rouleau', 'masquage', 'primaire']),
    ('Maçonnerie', ['ciment', 'béton', 'beton', 'mortier', 'treillis', 'fer à béton', 'coffrage', 'ragréage', 'ragréage']),
    ('Fixation / Visserie', ['vis ', 'cheville', 'scellement chimique', 'tige filet']),
    ('Étanchéité', ['epdm', 'étanch', 'etanche', 'spec ', 'pare pluie', 'pare vapeur']),
    ('Bois / Panneaux', ['osb', 'tasseau', 'lambourde', 'madrier', 'bois', 'panneau']),
    ('Portail / Clôture', ['portail', 'portillon', 'cloture', 'clôture', 'motorisation portail', 'cellule portail']),
    ('Terrasse', ['terrasse composite', 'lame terrasse', 'lambourde composite']),
    ('Chauffage', ['radiateur', 'thermostatique']),
]


def ref8(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{8})', sku)
    if m:
        return m.group(1)
    name = row.get('Item name') or ''
    m = re.search(r'(\d{8})', name)
    return m.group(1) if m else ''


def short_name(name, ref):
    name = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{8}.*$', '', str(name or ''), flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' -–')
    if len(name) > 120:
        name = name[:117].rstrip() + '…'
    return f'{name} – Réf. {ref}' if ref else name


def category_for(name):
    text = str(name or '').lower()
    for cat, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return f'Fournitures client - {cat}'
    return 'Fournitures client - Autres'


def make_sumup_row(row):
    r = dict(row)
    ref = ref8(r)
    if ref:
        r['SKU'] = f'LM-{ref}'
        r['Item name'] = short_name(r.get('Item name', ''), ref)
        r['Description (Online Store and Invoices only)'] = f'À fournir par le client - Leroy Merlin - Réf. {ref}'
    r['Category'] = category_for(r.get('Item name', ''))
    r['Price'] = '0.00'
    r['Cost price'] = ''
    r['Variable price? (Yes/No)'] = 'No'
    r['Tax rate (%)'] = ''
    r['On sale in Online Store?'] = 'No'
    r['Regular price (before sale)'] = ''
    r['Set up different prices and VAT for takeaway'] = ''
    r['Takeaway price'] = ''
    r['Takeaway tax rate'] = ''
    r['Track inventory? (Yes/No)'] = 'No'
    r['Quantity'] = ''
    r['Low stock threshold'] = ''
    r['Display item at Checkout? (Yes/No)'] = 'Yes'
    r['Display item in Online Store? (Yes/No)'] = 'No'
    return r


def convert_one(index, row):
    sku = (row.get('SKU') or f'ROW-{index}').strip()
    ref = ref8(row)
    safe = f'LM-{ref}' if ref else re.sub(r'[^A-Za-z0-9_-]+', '_', sku)
    src_url = (row.get('Image 1') or '').strip()
    out = IMG_DIR / f'{safe}.jpg'
    public = f'{RAW_BASE}/{out.name}'

    if out.exists() and out.stat().st_size > 700:
        return index, public, 'already_hosted', src_url, ''
    if not src_url:
        return index, '', 'no_source_image', '', 'no image URL in source CSV'

    tmp = Path(tempfile.gettempdir()) / f'{safe}-{os.getpid()}-{index}.src'
    try:
        r = requests.get(
            src_url,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            headers={
                'User-Agent': UA,
                'Referer': 'https://www.leroymerlin.fr/',
                'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8',
            },
        )
        if r.status_code != 200 or len(r.content) < 500:
            return index, '', 'download_failed', src_url, f'HTTP {r.status_code} / {len(r.content)} bytes'
        if 'text/html' in (r.headers.get('content-type') or '').lower():
            return index, '', 'download_failed', src_url, 'HTML returned instead of image'
        tmp.write_bytes(r.content)
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(700,iw)':'min(700,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '5', str(out)
        ], check=True, timeout=20)
        if out.exists() and out.stat().st_size > 700:
            return index, public, 'converted', src_url, ''
        out.unlink(missing_ok=True)
        return index, '', 'convert_failed', src_url, 'JPG output invalid'
    except Exception as e:
        out.unlink(missing_ok=True)
        return index, '', 'failed', src_url, type(e).__name__
    finally:
        tmp.unlink(missing_ok=True)


with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    source_rows = list(reader)

# Deduplicate by Leroy reference/SKU before conversion.
rows = []
seen = set()
for row in source_rows:
    ref = ref8(row)
    key = ref or (row.get('SKU') or '').strip() or f'row-{len(rows)}'
    if key in seen:
        continue
    seen.add(key)
    rows.append(make_sumup_row(row))

print(f'TURBO START | {len(rows)} articles | {WORKERS} conversions parallèles', flush=True)
results = {}
report_rows = []
completed = 0
ok = 0

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(convert_one, i, row) for i, row in enumerate(rows)]
    for fut in as_completed(futures):
        index, image_url, status, source_url, error = fut.result()
        results[index] = image_url
        completed += 1
        if image_url:
            ok += 1
        sku = rows[index].get('SKU', '')
        report_rows.append([sku, status, source_url, image_url, error])
        if completed % 25 == 0 or completed == len(rows):
            print(f'IMAGES {completed}/{len(rows)} | JPG OK {ok} | sans image {completed-ok}', flush=True)

for i, row in enumerate(rows):
    row['Image 1'] = results.get(i, '')

for output in (OUT, MASTER):
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU', 'status', 'source_image_url', 'hosted_image_url', 'error'])
    w.writerows(sorted(report_rows, key=lambda x: x[0]))

print(f'TURBO FINAL | {len(rows)} articles | {ok} JPG hébergés | {len(rows)-ok} sans image', flush=True)
