import csv
import gzip
import html
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

SRC = Path('Cat1.csv')
MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
REPORT = Path('rebuild_report.csv')
IMG_DIR = Path('images_v3')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '20'))
TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '12'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v3'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'

CATEGORY_RULES = [
    ('Électricité', ['cable', 'câble', 'gaine', 'disjoncteur', 'prise', 'interrupteur', 'wago', 'électr', 'spot', 'luminaire']),
    ('VMC / Ventilation', ['vmc', 'extracteur', 'ventilation', 'bouche']),
    ('Plomberie', ['multicouche', 'cuivre', 'raccord', 'siphon', 'bonde', 'mitigeur', 'robinet', 'plomberie']),
    ('Carrelage', ['carrelage', 'joint', 'colle carrelage', 'nez de marche', 'profil']),
    ('Peinture / Enduit', ['peinture', 'enduit', 'sous couche', 'sous-couche', 'rouleau', 'masquage', 'primaire']),
    ('Maçonnerie', ['ciment', 'béton', 'beton', 'mortier', 'treillis', 'fer à béton', 'coffrage', 'ragréage']),
    ('Fixation / Visserie', ['vis ', 'cheville', 'scellement chimique', 'tige filet']),
    ('Étanchéité', ['epdm', 'étanch', 'etanche', 'spec ', 'pare pluie', 'pare vapeur']),
    ('Bois / Panneaux', ['osb', 'tasseau', 'lambourde', 'madrier', 'bois', 'panneau']),
    ('Portail / Clôture', ['portail', 'portillon', 'cloture', 'clôture', 'motorisation portail', 'cellule portail']),
    ('Terrasse', ['terrasse composite', 'lame terrasse', 'lambourde composite']),
    ('Chauffage', ['radiateur', 'thermostatique']),
]

session = requests.Session()
session.headers.update({'User-Agent': UA, 'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8'})


def ref_any(row):
    sku = str(row.get('SKU') or '')
    nums = re.findall(r'\d{5,8}', sku)
    if nums:
        return nums[-1]
    text = str(row.get('Description (Online Store and Invoices only)') or '') + ' ' + str(row.get('Item name') or '')
    nums = re.findall(r'\d{5,8}', text)
    return nums[-1] if nums else ''


def clean_name(name):
    name = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{5,8}.*$', '', str(name or ''), flags=re.I)
    return re.sub(r'\s+', ' ', name).strip(' -–')[:120]


def category_for(name):
    text = str(name or '').lower()
    for cat, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return f'Fournitures client - {cat}'
    return 'Fournitures client - Autres'


def canonical_row(row):
    r = dict(row)
    ref = ref_any(r)
    name = clean_name(r.get('Item name', ''))
    if ref:
        r['SKU'] = f'LM-{ref}'
        r['Item name'] = f'{name} – Réf. {ref}'
        r['Description (Online Store and Invoices only)'] = f'À fournir par le client - Leroy Merlin - Réf. {ref}'
    r['Category'] = category_for(name)
    r['Price'] = '0.00'
    r['Cost price'] = ''
    r['Variable price? (Yes/No)'] = 'No'
    r['Tax rate (%)'] = ''
    r['On sale in Online Store?'] = 'No'
    r['Regular price (before sale)'] = ''
    r['Track inventory? (Yes/No)'] = 'No'
    r['Quantity'] = ''
    r['Low stock threshold'] = ''
    r['Display item at Checkout? (Yes/No)'] = 'Yes'
    r['Display item in Online Store? (Yes/No)'] = 'No'
    return r


def http_get(url, image=False):
    headers = {'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8' if image else '*/*'}
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, headers=headers)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    return None


def xml_bytes(url):
    r = http_get(url)
    if r is None:
        return b''
    data = r.content
    if data[:2] == b'\x1f\x8b':
        try:
            data = gzip.decompress(data)
        except Exception:
            return b''
    return data


def sitemap_locations(data):
    if not data:
        return []
    text = data.decode('utf-8', errors='ignore')
    return [html.unescape(x.strip()) for x in re.findall(r'<loc>(.*?)</loc>', text, flags=re.I | re.S)]


def build_sitemap_index(target_refs):
    print('SITEMAP: téléchargement index officiel', flush=True)
    index_data = xml_bytes('https://www.leroymerlin.fr/sitemap-index.xml')
    urls = sitemap_locations(index_data)
    if not urls:
        print('SITEMAP: index inaccessible', flush=True)
        return {}

    # Product sitemaps first; if naming is generic, scan all sitemap children.
    product_maps = [u for u in urls if re.search(r'produit|product', u, re.I)]
    maps = product_maps or urls
    print(f'SITEMAP: {len(maps)} fichiers à scanner', flush=True)

    found = {}
    targets = set(target_refs)
    for n, sm_url in enumerate(maps, 1):
        data = xml_bytes(sm_url)
        if not data:
            continue
        text = data.decode('utf-8', errors='ignore')

        # Parse each <url> block so a sitemap image can be associated with its product.
        blocks = re.findall(r'<url>(.*?)</url>', text, flags=re.I | re.S)
        if blocks:
            for block in blocks:
                locs = [html.unescape(x.strip()) for x in re.findall(r'<loc>(.*?)</loc>', block, flags=re.I | re.S)]
                if not locs:
                    continue
                product_url = locs[0]
                m = re.search(r'-(\d{5,8})\.html(?:\?|$)', product_url)
                if not m:
                    continue
                ref = m.group(1)
                if ref not in targets or ref in found:
                    continue
                image_url = ''
                for candidate in locs[1:]:
                    if re.search(r'\.(?:jpg|jpeg|png|webp|avif)(?:\?|$)', candidate, re.I) or 'media.adeo.com' in candidate:
                        image_url = candidate
                        break
                found[ref] = (product_url, image_url)
        else:
            # Fallback for plain sitemap entries.
            for product_url in sitemap_locations(data):
                m = re.search(r'-(\d{5,8})\.html(?:\?|$)', product_url)
                if m and m.group(1) in targets and m.group(1) not in found:
                    found[m.group(1)] = (product_url, '')

        if n % 10 == 0 or n == len(maps):
            print(f'SITEMAP {n}/{len(maps)} | références trouvées={len(found)}/{len(targets)}', flush=True)
        if len(found) >= len(targets):
            break
    return found


def extract_image_from_page(product_url, ref):
    r = http_get(product_url)
    if r is None:
        return ''
    text = html.unescape(r.text).replace('\\/', '/')
    if ref not in product_url and ref not in text:
        return ''
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"image"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
        r'(https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)(?:\?[^"\'<>\s]*)?)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return html.unescape(m.group(1)).replace('\\/', '/')
    return ''


def source_direct_url(row):
    u = str(row.get('Image 1') or '').strip()
    # Old direct ADEO /media/ URLs are useful; marketplace AVIF URLs are known to be mostly dead.
    if re.search(r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)', u, re.I):
        return u
    return ''


def convert_to_jpg(ref, image_url):
    if not image_url:
        return '', 'no_image_url'
    out = IMG_DIR / f'LM-{ref}.jpg'
    public = f'{RAW_BASE}/{out.name}'
    if out.exists() and out.stat().st_size > 700:
        return public, 'already_hosted'

    r = http_get(image_url, image=True)
    if r is None or len(r.content) < 500:
        return '', 'download_failed'
    if 'text/html' in (r.headers.get('content-type') or '').lower():
        return '', 'html_instead_image'

    tmp = Path(tempfile.gettempdir()) / f'lm-v3-{ref}-{os.getpid()}.src'
    tmp.write_bytes(r.content)
    try:
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '4', str(out)
        ], check=True, timeout=25)
        if out.exists() and out.stat().st_size > 700:
            return public, 'converted'
        out.unlink(missing_ok=True)
        return '', 'convert_failed'
    except Exception:
        out.unlink(missing_ok=True)
        return '', 'convert_failed'
    finally:
        tmp.unlink(missing_ok=True)


def process_one(index, row, sitemap):
    ref = ref_any(row)
    if not ref:
        return index, '', '', '', 'invalid_ref'

    # 1. Direct original ADEO source first: fastest and most reliable when present.
    direct = source_direct_url(row)
    if direct:
        hosted, status = convert_to_jpg(ref, direct)
        if hosted:
            return index, hosted, '', direct, f'original_{status}'

    # 2. Official sitemap gives the exact product page and sometimes the image directly.
    product_url, image_url = sitemap.get(ref, ('', ''))
    if image_url:
        hosted, status = convert_to_jpg(ref, image_url)
        if hosted:
            return index, hosted, product_url, image_url, f'sitemap_image_{status}'

    # 3. Exact product page from sitemap, extract page image.
    if product_url:
        page_image = extract_image_from_page(product_url, ref)
        if page_image:
            hosted, status = convert_to_jpg(ref, page_image)
            if hosted:
                return index, hosted, product_url, page_image, f'product_page_{status}'
        return index, '', product_url, '', 'product_found_image_failed'

    return index, '', '', '', 'reference_not_in_sitemap'


if not SRC.exists():
    raise SystemExit('Cat1.csv not found')

with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    source_rows = list(reader)

# Rebuild cleanly from original catalog and deduplicate by Leroy reference.
rows = []
seen = set()
for row in source_rows:
    ref = ref_any(row)
    key = ref or (row.get('SKU') or '').strip() or f'row-{len(rows)}'
    if key in seen:
        continue
    seen.add(key)
    rows.append(canonical_row(row))

target_refs = [ref_any(r) for r in rows if ref_any(r)]
print(f'REBUILD V3 START | source={len(source_rows)} | catalogue={len(rows)} | refs={len(target_refs)}', flush=True)
sitemap = build_sitemap_index(target_refs)
print(f'REBUILD V3 | sitemap correspondances={len(sitemap)}', flush=True)

results = {}
report = []
ok = 0
completed = 0
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(process_one, i, row, sitemap) for i, row in enumerate(rows)]
    for fut in as_completed(futures):
        index, hosted, product_url, source_image, status = fut.result()
        results[index] = hosted
        if hosted:
            ok += 1
        completed += 1
        report.append([rows[index].get('SKU', ''), status, product_url, source_image, hosted])
        if completed % 25 == 0 or completed == len(rows):
            print(f'REBUILD IMAGES {completed}/{len(rows)} | JPG={ok} | sans image={completed-ok}', flush=True)

for i, row in enumerate(rows):
    row['Image 1'] = results.get(i, '')

for output in (MASTER, OUT):
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU', 'status', 'product_url', 'source_image_url', 'hosted_image_url'])
    w.writerows(sorted(report, key=lambda x: x[0]))

print(f'REBUILD V3 FINAL | articles={len(rows)} | images={ok} | sans image={len(rows)-ok}', flush=True)
