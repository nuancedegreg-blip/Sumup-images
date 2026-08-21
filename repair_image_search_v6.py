import csv
import html
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus, unquote

import requests

MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
REPORT = Path('repair_v6_report.csv')
IMG_DIR = Path('images_v6')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '10'))
TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '10'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v6'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'

session = requests.Session()
session.headers.update({'User-Agent': UA, 'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7'})


def ref_of(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{5,8})', sku)
    if m:
        return m.group(1)
    name = row.get('Item name') or ''
    m = re.search(r'(\d{5,8})', name)
    return m.group(1) if m else ''


def clean_name(row):
    name = str(row.get('Item name') or '')
    name = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{5,8}.*$', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:120]


def fetch_text(url):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, headers={'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
        if r.status_code == 200 and len(r.text) > 300:
            return r.text
    except Exception:
        pass
    return ''


def bing_candidates(ref, name):
    q = f'"{ref}" {name}'
    url = 'https://www.bing.com/images/search?q=' + quote_plus(q) + '&form=HDRSC3&first=1'
    text = fetch_text(url)
    out = []
    if not text:
        return out

    # Bing image result metadata is stored in m="{...}" attributes.
    for raw in re.findall(r'\bm="([^"]+)"', text, flags=re.I):
        try:
            obj = json.loads(html.unescape(raw))
        except Exception:
            continue
        image = obj.get('murl') or obj.get('turl') or ''
        page = obj.get('purl') or ''
        title = obj.get('t') or obj.get('desc') or ''
        blob = f'{image} {page} {title}'
        if image.startswith('http'):
            score = 0
            if ref in blob:
                score += 20
            words = [w.lower() for w in re.findall(r'[A-Za-zÀ-ÿ0-9]+', name) if len(w) >= 4][:8]
            score += sum(1 for w in words if w in blob.lower())
            out.append((score, image, page, 'bing'))

    # Fallback for escaped murl values present directly in HTML.
    for m in re.finditer(r'"murl"\s*:\s*"(https?:\\?/\\?/[^"<>]+)"', text, re.I):
        u = html.unescape(m.group(1)).replace('\\/', '/')
        if u.startswith('http'):
            out.append((0, u, '', 'bing_html'))

    dedup = {}
    for item in out:
        key = item[1]
        if key not in dedup or item[0] > dedup[key][0]:
            dedup[key] = item
    return sorted(dedup.values(), key=lambda x: -x[0])[:15]


def google_candidates(ref, name):
    q = f'"{ref}" {name}'
    url = 'https://www.google.com/search?tbm=isch&q=' + quote_plus(q)
    text = fetch_text(url)
    if not text:
        return []
    urls = []
    for pat in [
        r'\["(https?://[^"\\]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\\]*)?)"',
        r'"(https?://[^"<> ]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"<> ]*)?)"',
    ]:
        for m in re.finditer(pat, text, re.I):
            u = html.unescape(m.group(1)).replace('\\u003d', '=').replace('\\u0026', '&').replace('\\/', '/')
            if any(bad in u for bad in ('google.com', 'gstatic.com', 'googleusercontent.com')):
                continue
            urls.append((0, u, '', 'google'))
    seen = set()
    result = []
    for x in urls:
        if x[1] not in seen:
            seen.add(x[1]); result.append(x)
    return result[:10]


def download_image(url, referer=''):
    headers = {'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8'}
    if referer:
        headers['Referer'] = referer
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, headers=headers)
        ct = (r.headers.get('content-type') or '').lower()
        if r.status_code == 200 and len(r.content) >= 1500 and 'text/html' not in ct:
            return r.content, ct
    except Exception:
        pass
    return b'', ''


def to_jpg(ref, content):
    out = IMG_DIR / f'LM-{ref}.jpg'
    tmp = Path(tempfile.gettempdir()) / f'lm-v6-{ref}-{os.getpid()}-{time.time_ns()}.src'
    tmp.write_bytes(content)
    try:
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
            '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
            '-frames:v', '1', '-q:v', '4', str(out)
        ], check=True, timeout=20)
        if out.exists() and out.stat().st_size > 1200:
            return f'{RAW_BASE}/{out.name}'
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    return ''


def repair_one(index, row):
    ref = ref_of(row)
    name = clean_name(row)
    sku = (row.get('SKU') or '').strip()
    if not ref or not name:
        return index, sku, 'invalid', '', '', '', ''

    candidates = bing_candidates(ref, name)
    if not candidates:
        candidates = google_candidates(ref, name)
    elif len(candidates) < 4:
        candidates += google_candidates(ref, name)

    if not candidates:
        return index, sku, 'no_search_results', '', '', '', ''

    # High-ranked exact-reference results first, then the rest.
    for score, image_url, page_url, engine in candidates[:20]:
        content, ct = download_image(image_url, page_url)
        if not content:
            continue
        hosted = to_jpg(ref, content)
        if hosted:
            return index, sku, 'recovered', engine, image_url, page_url, hosted

    return index, sku, 'no_downloadable_image', candidates[0][3], candidates[0][1], candidates[0][2], ''


if not MASTER.exists():
    raise SystemExit('Catalogue_Maitre_SumUp.csv not found')

with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

missing = [i for i, r in enumerate(rows) if not (r.get('Image 1') or '').strip()]
print(f'V6 IMAGE SEARCH START | total={len(rows)} | missing={len(missing)} | workers={WORKERS}', flush=True)

report = []
recovered = 0
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(repair_one, i, rows[i]) for i in missing]
    for n, fut in enumerate(as_completed(futures), 1):
        index, sku, status, engine, source_image, source_page, hosted = fut.result()
        if hosted:
            rows[index]['Image 1'] = hosted
            recovered += 1
        report.append([sku, status, engine, source_image, source_page, hosted])
        if n % 25 == 0 or n == len(missing):
            print(f'V6 {n}/{len(missing)} | recovered={recovered}', flush=True)

for output in (MASTER, OUT):
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU','status','engine','source_image_url','source_page_url','hosted_image_url'])
    w.writerows(sorted(report))

remaining = sum(1 for r in rows if not (r.get('Image 1') or '').strip())
print(f'V6 FINAL | recovered={recovered} | total_with_image={len(rows)-remaining} | remaining={remaining}', flush=True)
