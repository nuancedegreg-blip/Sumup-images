import csv
import html
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

import requests

MASTER = Path('Catalogue_Maitre_SumUp.csv')
REPORT_IN = Path('rebuild_report.csv')
REPORT_OUT = Path('repair_v4_report.csv')
IMG_DIR = Path('images_v3')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '16'))
TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '12'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v3'

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'


def decode_html(text):
    text = str(text or '')
    replacements = {
        r'\u002F': '/', r'\u002f': '/', r'\u003A': ':', r'\u003a': ':',
        r'\u0026': '&', r'\u003F': '?', r'\u003f': '?', r'\u003D': '=',
        r'\u003d': '=', r'\/': '/', '&amp;': '&'
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return unquote(html.unescape(text))


def fetch(url, image=False):
    headers = {
        'User-Agent': UA,
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
        'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8' if image else 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Referer': 'https://www.leroymerlin.fr/'
    }
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    return None


def image_candidates(text):
    text = decode_html(text)
    candidates = []

    # Metadata and JSON-LD first.
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"image"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
        r'"url"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
        r'"src"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            candidates.append(m.group(1))

    # Catch every ADEO product media URL anywhere in the hydrated page JSON.
    for m in re.finditer(r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\'<>\s]*)?', text, re.I):
        candidates.append(m.group(0))

    # Marketplace can still work when copied from a live product page, so keep as fallback.
    for m in re.finditer(r'https://media\.adeo\.com/marketplace/[^"\'<>\s]+?\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\'<>\s]*)?', text, re.I):
        candidates.append(m.group(0))

    cleaned = []
    seen = set()
    for u in candidates:
        u = decode_html(u).rstrip('\\,]})')
        # Prefer the original asset path; ADEO conversion query can be added later.
        m = re.match(r'(https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png|webp|avif))', u, re.I)
        if m:
            u = m.group(1)
        if u.startswith('https://media.adeo.com/') and u not in seen:
            seen.add(u)
            cleaned.append(u)

    # Direct /media/ assets are much more reliable than marketplace URLs.
    cleaned.sort(key=lambda u: (0 if '/media/' in u and '/marketplace/' not in u else 1, len(u)))
    return cleaned[:30]


def convert_to_jpg(ref, content):
    out = IMG_DIR / f'LM-{ref}.jpg'
    tmp = Path(tempfile.gettempdir()) / f'lm-v4-{ref}-{os.getpid()}.src'
    tmp.write_bytes(content)
    try:
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '4', str(out)
        ], check=True, timeout=25)
        if out.exists() and out.stat().st_size > 700:
            return f'{RAW_BASE}/{out.name}'
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    return ''


def repair_one(sku, ref, product_url):
    page = fetch(product_url)
    if not page:
        return sku, ref, product_url, '', '', 'page_fetch_failed'

    candidates = image_candidates(page.text)
    if not candidates:
        return sku, ref, product_url, '', '', 'no_media_url_in_html'

    errors = []
    for source in candidates:
        # Try exact source first, then ADEO's JPG transform for direct media assets.
        tries = [source]
        if '/media/' in source and '/marketplace/' not in source:
            base = source.split('?', 1)[0]
            tries.append(base + '?fit=bounds&format=jpg&height=1000&quality=85&width=1000')
        for img_url in tries:
            img = fetch(img_url, image=True)
            if not img or len(img.content) < 500:
                errors.append('download')
                continue
            ct = (img.headers.get('content-type') or '').lower()
            if 'text/html' in ct:
                errors.append('html')
                continue
            hosted = convert_to_jpg(ref, img.content)
            if hosted:
                return sku, ref, product_url, img_url, hosted, 'recovered'
            errors.append('convert')

    return sku, ref, product_url, candidates[0], '', 'all_candidates_failed:' + ','.join(errors[:8])


with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

row_by_sku = {(r.get('SKU') or '').strip(): r for r in rows}

jobs = []
with REPORT_IN.open('r', encoding='utf-8-sig', newline='') as f:
    for rec in csv.DictReader(f):
        sku = (rec.get('SKU') or '').strip()
        product_url = (rec.get('product_url') or '').strip()
        row = row_by_sku.get(sku)
        if not row or (row.get('Image 1') or '').strip():
            continue
        if not product_url.startswith('https://www.leroymerlin.fr/produits/'):
            continue
        m = re.search(r'(\d{5,8})', sku)
        if not m:
            continue
        jobs.append((sku, m.group(1), product_url))

print(f'V4 START | known product pages={len(jobs)} | workers={WORKERS}', flush=True)
recovered = 0
report = []

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(repair_one, *j) for j in jobs]
    for i, fut in enumerate(as_completed(futures), 1):
        sku, ref, product_url, source_image, hosted, status = fut.result()
        if hosted:
            row_by_sku[sku]['Image 1'] = hosted
            recovered += 1
        report.append([sku, status, product_url, source_image, hosted])
        if i % 25 == 0 or i == len(jobs):
            print(f'V4 {i}/{len(jobs)} | recovered={recovered}', flush=True)

with MASTER.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(rows)

with Path('Cat1_SumUp_FINAL.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(rows)

with REPORT_OUT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU','status','product_url','source_image_url','hosted_image_url'])
    w.writerows(sorted(report))

remaining = sum(1 for r in rows if not (r.get('Image 1') or '').strip())
print(f'V4 FINAL | recovered={recovered} | total_with_image={len(rows)-remaining} | remaining={remaining}', flush=True)
