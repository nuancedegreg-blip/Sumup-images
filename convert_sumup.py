import csv
import html
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote

import requests

SRC = Path('Cat1.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
IMG_DIR = Path('images')
IMG_DIR.mkdir(exist_ok=True)

repo = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
branch = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
raw_base = f'https://raw.githubusercontent.com/{repo}/{branch}/images'

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'
session = requests.Session()
session.headers.update({
    'User-Agent': UA,
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
    'Accept': '*/*',
})

PRODUCT_RE = re.compile(r'https://www\.leroymerlin\.fr/produits/[^\"\'<>\s]+?-(\d{8})\.html', re.I)
MEDIA_RE = re.compile(r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)(?:\?[^\"\'<>\s]*)?', re.I)


def get(url, timeout=35, tries=4, referer=None):
    last = None
    headers = {'User-Agent': UA}
    if referer:
        headers['Referer'] = referer
    for attempt in range(tries):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True, headers=headers)
            if r.status_code == 200:
                return r
            last = f'HTTP {r.status_code}'
        except Exception as e:
            last = repr(e)
        time.sleep(1.2 * (attempt + 1))
    return None


def download(url, dest):
    r = get(url, timeout=45, tries=5, referer='https://www.leroymerlin.fr/')
    if r is not None and len(r.content) > 500:
        dest.write_bytes(r.content)
        return True, r.headers.get('content-type', '')
    return False, 'download failed'


def to_jpg(src, dst):
    # 800 px is ample for SumUp and keeps the public GitHub repository compact.
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(src),
        '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
        '-q:v', '4', str(dst)
    ]
    subprocess.run(cmd, check=True, timeout=90)
    return dst.exists() and dst.stat().st_size > 500


def normalize_media_url(url):
    if not url:
        return ''
    url = html.unescape(url).replace('\\u0026', '&').replace('\\/', '/')
    m = re.search(r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)', url, re.I)
    if not m:
        return ''
    # Same direct ADEO family already validated in SumUp.
    return m.group(0) + '?fit=bounds&format=jpg&height=800&quality=85&width=800'


def find_product_url(sku8):
    queries = [
        'https://www.bing.com/search?q=' + quote_plus(f'"{sku8}" site:leroymerlin.fr/produits/'),
        'https://html.duckduckgo.com/html/?q=' + quote_plus(f'"{sku8}" site:leroymerlin.fr/produits/'),
        'https://www.leroymerlin.fr/recherche?q=' + quote_plus(sku8),
    ]
    for search_url in queries:
        r = get(search_url, timeout=30, tries=3)
        if r is None:
            continue
        text = html.unescape(r.text).replace('\\/', '/')
        # Direct product URLs visible in page / search HTML.
        for m in PRODUCT_RE.finditer(text):
            if m.group(1) == sku8:
                return m.group(0)
        # Also inspect encoded redirect URLs.
        dec = unquote(text)
        for m in PRODUCT_RE.finditer(dec):
            if m.group(1) == sku8:
                return m.group(0)
    return ''


def image_from_product_page(product_url, sku8):
    r = get(product_url, timeout=40, tries=4, referer='https://www.leroymerlin.fr/')
    if r is None:
        return ''
    text = html.unescape(r.text).replace('\\/', '/')
    # Sanity check: the expected reference should be somewhere in the page.
    if sku8 not in text:
        return ''

    # Prefer og:image / twitter:image where available.
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            u = normalize_media_url(m.group(1))
            if u:
                return u

    # Fallback to first direct ADEO jpg/png found on the product page.
    for m in MEDIA_RE.finditer(text):
        u = normalize_media_url(m.group(0))
        if u:
            return u
    return ''


def recover_image_by_sku(sku_full):
    sku8 = re.sub(r'\D', '', sku_full)[-8:]
    if len(sku8) != 8:
        return '', '', 'invalid SKU'
    product_url = find_product_url(sku8)
    if not product_url:
        return '', '', 'product page not found'
    image_url = image_from_product_page(product_url, sku8)
    if not image_url:
        return '', product_url, 'direct image not found on product page'
    return image_url, product_url, ''


with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

ok = 0
recovered = 0
failed = []
report = []

for idx, row in enumerate(rows, 1):
    sku = (row.get('SKU') or f'row-{idx}').strip()
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in sku)
    original_url = (row.get('Image 1') or '').strip()
    out_img = IMG_DIR / f'{safe}.jpg'

    if out_img.exists() and out_img.stat().st_size > 500:
        row['Image 1'] = f'{raw_base}/{out_img.name}'
        ok += 1
        report.append((sku, 'already_hosted', '', original_url, row['Image 1'], ''))
        continue

    candidates = []
    if original_url:
        candidates.append(('original', original_url, ''))

    # If original URL fails, recover the actual Leroy Merlin product page/image by SKU.
    recovered_url = ''
    product_url = ''
    recovery_error = ''

    success = False
    last_error = ''
    for source, url, purl in candidates:
        tmp = Path(tempfile.gettempdir()) / f'{safe}.source'
        try:
            got, info = download(url, tmp)
            if got and to_jpg(tmp, out_img):
                row['Image 1'] = f'{raw_base}/{out_img.name}'
                ok += 1
                success = True
                report.append((sku, source, purl, url, row['Image 1'], ''))
                break
            last_error = str(info)
        except Exception as e:
            last_error = repr(e)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    if not success:
        recovered_url, product_url, recovery_error = recover_image_by_sku(sku)
        if recovered_url:
            tmp = Path(tempfile.gettempdir()) / f'{safe}.recovered'
            try:
                got, info = download(recovered_url, tmp)
                if got and to_jpg(tmp, out_img):
                    row['Image 1'] = f'{raw_base}/{out_img.name}'
                    ok += 1
                    recovered += 1
                    success = True
                    report.append((sku, 'sku_recovered', product_url, recovered_url, row['Image 1'], ''))
                else:
                    last_error = str(info)
            except Exception as e:
                last_error = repr(e)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    if not success:
        # Do not leave a known-bad AVIF/proxy URL in the SumUp file.
        row['Image 1'] = ''
        err = recovery_error or last_error or 'no usable image'
        failed.append((sku, err))
        report.append((sku, 'failed', product_url, recovered_url or original_url, '', err))

    if idx % 20 == 0 or idx == len(rows):
        print(f'[{idx}/{len(rows)}] JPEG OK: {ok} | SKU recovered: {recovered} | failures: {len(failed)}', flush=True)

with OUT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

with Path('conversion_report.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU', 'status', 'product_url', 'source_image_url', 'hosted_image_url', 'error'])
    w.writerows(report)

print(f'FINAL: {len(rows)} rows, {ok} hosted JPEGs, {recovered} recovered by SKU, {len(failed)} failures')
