import csv
import html
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
REPORT = Path('conversion_report.csv')
IMG_DIR = Path('images')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '16'))
HTTP_TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '8'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images'
UA = 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1'


def ref8(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{8})', sku)
    if m:
        return m.group(1)
    name = row.get('Item name') or ''
    m = re.search(r'(\d{8})', name)
    return m.group(1) if m else ''


def get(url, referer=None):
    headers = {
        'User-Agent': UA,
        'Accept-Language': 'fr-FR,fr;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8',
    }
    if referer:
        headers['Referer'] = referer
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True, headers=headers)
        return r if r.status_code == 200 else None
    except Exception:
        return None


def normalize_image(url):
    url = html.unescape(str(url or '')).replace('\\/', '/')
    if not url.startswith('http'):
        return ''
    return url


def product_page_and_image(ref):
    search_url = 'https://www.leroymerlin.fr/recherche?q=' + quote(ref)
    r = get(search_url)
    if not r:
        return '', ''

    text = html.unescape(r.text).replace('\\/', '/')
    product_url = ''

    if '/produits/' in r.url and '.html' in r.url:
        product_url = r.url
    else:
        patterns = [
            rf'https://www\.leroymerlin\.fr/produits/[^"\'<>\s]*?{re.escape(ref)}\.html',
            rf'/produits/[^"\'<>\s]*?{re.escape(ref)}\.html',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                product_url = urljoin('https://www.leroymerlin.fr', m.group(0))
                break

    page = r
    if product_url and product_url != r.url:
        rr = get(product_url, 'https://www.leroymerlin.fr/')
        if rr:
            page = rr

    page_text = html.unescape(page.text).replace('\\/', '/')
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'"image"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
        r'(https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)(?:\?[^"\'<>\s]*)?)',
    ]
    for pat in patterns:
        m = re.search(pat, page_text, re.I)
        if m:
            return product_url or page.url, normalize_image(m.group(1))
    return product_url or page.url, ''


def save_jpg(ref, image_url):
    out = IMG_DIR / f'LM-{ref}.jpg'
    public = f'{RAW_BASE}/{out.name}'
    if out.exists() and out.stat().st_size > 700:
        return public, 'already_hosted', ''
    r = get(image_url, 'https://www.leroymerlin.fr/')
    if not r or len(r.content) < 500:
        return '', 'download_failed', 'image unavailable'
    if 'text/html' in (r.headers.get('content-type') or '').lower():
        return '', 'download_failed', 'HTML instead of image'

    tmp = Path(tempfile.gettempdir()) / f'lm-{ref}-{os.getpid()}.src'
    tmp.write_bytes(r.content)
    try:
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(700,iw)':'min(700,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '5', str(out)
        ], check=True, timeout=20)
        if out.exists() and out.stat().st_size > 700:
            return public, 'recovered', ''
        out.unlink(missing_ok=True)
        return '', 'convert_failed', 'invalid JPG'
    except Exception as e:
        out.unlink(missing_ok=True)
        return '', 'convert_failed', type(e).__name__
    finally:
        tmp.unlink(missing_ok=True)


def repair_one(index, row):
    ref = ref8(row)
    sku = row.get('SKU', '')
    if not ref:
        return index, '', 'invalid_ref', '', '', 'no 8-digit reference'
    product_url, image_url = product_page_and_image(ref)
    if not image_url:
        return index, '', 'image_not_found', product_url, '', 'no image found by reference'
    hosted, status, error = save_jpg(ref, image_url)
    return index, hosted, status, product_url, image_url, error


if not MASTER.exists():
    raise SystemExit('Catalogue_Maitre_SumUp.csv not found')

with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

missing_indexes = [i for i, row in enumerate(rows) if not (row.get('Image 1') or '').strip()]
print(f'MISSING IMAGE REPAIR START | catalogue={len(rows)} | manquantes={len(missing_indexes)} | workers={WORKERS}', flush=True)

report_rows = []
recovered = 0
completed = 0

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(repair_one, i, rows[i]) for i in missing_indexes]
    for fut in as_completed(futures):
        index, hosted, status, product_url, source_image, error = fut.result()
        if hosted:
            rows[index]['Image 1'] = hosted
            recovered += 1
        completed += 1
        report_rows.append([
            rows[index].get('SKU', ''), status, product_url, source_image, hosted, error
        ])
        if completed % 20 == 0 or completed == len(missing_indexes):
            print(f'MISSING {completed}/{len(missing_indexes)} | récupérées={recovered} | restantes={len(missing_indexes)-recovered}', flush=True)

for output in (MASTER, OUT):
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU', 'status', 'product_url', 'source_image_url', 'hosted_image_url', 'error'])
    w.writerows(sorted(report_rows, key=lambda x: x[0]))

remaining = sum(1 for row in rows if not (row.get('Image 1') or '').strip())
print(f'MISSING IMAGE REPAIR FINAL | récupérées={recovered} | encore sans image={remaining} | catalogue={len(rows)}', flush=True)
