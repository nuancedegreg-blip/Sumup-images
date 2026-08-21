import csv
import html
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin

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

PRODUCT_RE = re.compile(r'https://www\.leroymerlin\.fr/produits/[^\"\'<>\s]+?\.html', re.I)
MEDIA_RE = re.compile(r'https://media\.adeo\.com/(?:media/\d+/media\.(?:jpg|jpeg|png)|[^\"\'<>\s]+\.(?:jpg|jpeg|png|webp|avif))(?:\?[^\"\'<>\s]*)?', re.I)


def ref_any(row):
    sku = (row.get('SKU') or '').strip()
    nums = re.findall(r'\d{5,8}', sku)
    if nums:
        return nums[-1]
    name = row.get('Item name') or ''
    nums = re.findall(r'\d{5,8}', name)
    return nums[-1] if nums else ''


def clean_name(row):
    name = str(row.get('Item name') or '')
    name = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{5,8}.*$', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:100]


def get(url, referer=None, accept_image=False):
    headers = {
        'User-Agent': UA,
        'Accept-Language': 'fr-FR,fr;q=0.9',
        'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8' if accept_image else 'text/html,application/xhtml+xml,*/*;q=0.8',
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
    # Prefer the direct ADEO transform already validated by SumUp.
    m = re.search(r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png)', url, re.I)
    if m:
        return m.group(0) + '?fit=bounds&format=jpg&height=700&quality=82&width=700'
    return url


def extract_product_urls(text, ref):
    text = unquote(html.unescape(text).replace('\\/', '/'))
    urls = []
    for m in PRODUCT_RE.finditer(text):
        u = m.group(0)
        if u not in urls:
            urls.append(u)
    # Relative Leroy links.
    for m in re.finditer(r'/produits/[^\"\'<>\s]+?\.html', text, re.I):
        u = urljoin('https://www.leroymerlin.fr', m.group(0))
        if u not in urls:
            urls.append(u)
    # Strongly prefer URLs containing the reference.
    return sorted(urls, key=lambda u: (ref not in u, len(u)))[:8]


def extract_image(page_text):
    text = html.unescape(page_text).replace('\\/', '/')
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"image"\s*:\s*"(https://media\.adeo\.com/[^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            u = normalize_image(m.group(1))
            if u:
                return u
    m = MEDIA_RE.search(text)
    return normalize_image(m.group(0)) if m else ''


def search_product_and_image(ref, name):
    terms = [
        f'"{ref}" "{name}" site:leroymerlin.fr/produits/',
        f'{ref} {name} Leroy Merlin',
        f'"{ref}" site:leroymerlin.fr/produits/',
    ]
    search_urls = []
    # Leroy first, then two public search engines.
    search_urls.append('https://www.leroymerlin.fr/recherche?q=' + quote_plus(f'{ref} {name}'))
    for term in terms:
        search_urls.append('https://www.bing.com/search?q=' + quote_plus(term))
        search_urls.append('https://html.duckduckgo.com/html/?q=' + quote_plus(term))

    seen_pages = set()
    for search_url in search_urls:
        r = get(search_url)
        if not r:
            continue

        # Leroy may redirect directly to the product page.
        candidates = []
        if 'leroymerlin.fr/produits/' in r.url and '.html' in r.url:
            candidates.append(r.url)
        candidates.extend(extract_product_urls(r.text, ref))

        # Sometimes search HTML exposes a usable ADEO image directly; only use it
        # when the reference is present in the surrounding page text.
        decoded = unquote(html.unescape(r.text).replace('\\/', '/'))
        if ref in decoded:
            direct = extract_image(decoded)
            if direct and 'media.adeo.com' in direct:
                return r.url, direct, 'search_direct'

        for product_url in candidates:
            if product_url in seen_pages:
                continue
            seen_pages.add(product_url)
            page = get(product_url, 'https://www.leroymerlin.fr/')
            if not page:
                continue
            text = html.unescape(page.text).replace('\\/', '/')
            # Guard against a wrong search result: require reference in URL or page.
            if ref not in product_url and ref not in text:
                continue
            image = extract_image(text)
            if image:
                return page.url, image, 'product_page'
    return '', '', 'not_found'


def save_jpg(ref, image_url):
    out = IMG_DIR / f'LM-{ref}.jpg'
    public = f'{RAW_BASE}/{out.name}'
    if out.exists() and out.stat().st_size > 700:
        return public, 'already_hosted', ''
    r = get(image_url, 'https://www.leroymerlin.fr/', accept_image=True)
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
    ref = ref_any(row)
    sku = row.get('SKU', '')
    name = clean_name(row)
    if not ref:
        return index, '', 'invalid_ref', '', '', 'no usable reference'
    product_url, image_url, source = search_product_and_image(ref, name)
    if not image_url:
        return index, '', 'image_not_found', product_url, '', f'no image found for {ref} + product name'
    hosted, status, error = save_jpg(ref, image_url)
    if hosted:
        status = f'{status}_{source}'
    return index, hosted, status, product_url, image_url, error


if not MASTER.exists():
    raise SystemExit('Catalogue_Maitre_SumUp.csv not found')

with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

missing_indexes = [i for i, row in enumerate(rows) if not (row.get('Image 1') or '').strip()]
print(f'MISSING IMAGE REPAIR V2 START | catalogue={len(rows)} | manquantes={len(missing_indexes)} | workers={WORKERS}', flush=True)

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
        report_rows.append([rows[index].get('SKU', ''), status, product_url, source_image, hosted, error])
        if completed % 20 == 0 or completed == len(missing_indexes):
            print(f'MISSING V2 {completed}/{len(missing_indexes)} | récupérées={recovered} | restantes={len(missing_indexes)-recovered}', flush=True)

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
print(f'MISSING IMAGE REPAIR V2 FINAL | récupérées={recovered} | encore sans image={remaining} | catalogue={len(rows)}', flush=True)
