import csv
import html
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests

MASTER = Path('Catalogue_Maitre_SumUp.csv')
REPORT_IN = Path('rebuild_report.csv')
REPORT_OUT = Path('repair_v5_report.csv')
IMG_DIR = Path('images_v3')
IMG_DIR.mkdir(exist_ok=True)

WORKERS = int(os.environ.get('IMAGE_WORKERS', '10'))
TIMEOUT = float(os.environ.get('IMAGE_TIMEOUT', '20'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v3'
UA = 'Mozilla/5.0 Chrome/126 Safari/537.36'


def get(url, image=False):
    headers = {
        'User-Agent': UA,
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
        'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8' if image else 'text/plain,text/markdown,text/html,*/*;q=0.8',
    }
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    return None


def clean_url(u):
    u = html.unescape(str(u or '')).replace('\\/', '/')
    u = u.replace('\\u002F','/').replace('\\u002f','/').replace('\\u003A',':').replace('\\u003a',':')
    u = u.replace('\\u0026','&').replace('\\u003F','?').replace('\\u003f','?').replace('\\u003D','=').replace('\\u003d','=')
    return u.strip(' \t\r\n<>\"\'(),]')


def extract_candidates(text):
    text = clean_url(text)
    out = []
    pats = [
        r'https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png|webp|avif)(?:\?[^\s\)\]\"\'<>]*)?',
        r'https://media\.adeo\.com/marketplace/[^\s\)\]\"\'<>]+?\.(?:jpg|jpeg|png|webp|avif)(?:\?[^\s\)\]\"\'<>]*)?',
        r'!\[[^\]]*\]\((https?://[^\)]+)\)',
    ]
    for pat in pats:
        for m in re.finditer(pat, text, re.I):
            u = m.group(1) if m.lastindex else m.group(0)
            u = clean_url(u)
            if not u.startswith('http'):
                continue
            if 'media.adeo.com' not in u:
                continue
            # collapse direct ADEO URLs to original asset path first
            mm = re.match(r'(https://media\.adeo\.com/media/\d+/media\.(?:jpg|jpeg|png|webp|avif))', u, re.I)
            if mm:
                u = mm.group(1)
            if u not in out:
                out.append(u)
    out.sort(key=lambda u: (0 if '/media/' in u and '/marketplace/' not in u else 1, len(u)))
    return out[:40]


def fetch_reader(product_url):
    # Jina Reader renders/fetches blocked pages on its side and returns markdown containing image URLs.
    reader_url = 'https://r.jina.ai/' + product_url
    r = get(reader_url)
    if r:
        return r.text, reader_url, 'reader'
    return '', reader_url, 'reader_failed'


def fetch_search(ref, name):
    q = quote(f'{ref} {name} Leroy Merlin')
    search_url = 'https://s.jina.ai/' + q
    r = get(search_url)
    if r:
        return r.text, search_url, 'search'
    return '', search_url, 'search_failed'


def convert_to_jpg(ref, content):
    out = IMG_DIR / f'LM-{ref}.jpg'
    tmp = Path(tempfile.gettempdir()) / f'lm-v5-{ref}-{os.getpid()}.src'
    tmp.write_bytes(content)
    try:
        subprocess.run([
            'ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(tmp),
            '-vf',"scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
            '-frames:v','1','-q:v','4',str(out)
        ], check=True, timeout=25)
        if out.exists() and out.stat().st_size > 700:
            return f'{RAW_BASE}/{out.name}'
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    return ''


def try_candidates(ref, candidates):
    for source in candidates:
        tries = [source]
        if '/media/' in source and '/marketplace/' not in source:
            base = source.split('?',1)[0]
            tries.append(base + '?fit=bounds&format=jpg&height=1000&quality=85&width=1000')
        for u in tries:
            r = get(u, image=True)
            if not r or len(r.content) < 500:
                continue
            if 'text/html' in (r.headers.get('content-type') or '').lower():
                continue
            hosted = convert_to_jpg(ref, r.content)
            if hosted:
                return u, hosted
    return '', ''


def repair_one(sku, ref, name, product_url):
    texts = []
    if product_url:
        t, src, status = fetch_reader(product_url)
        if t:
            texts.append((t, src, status))
    # fallback search also helps refs that disappeared from current sitemap/page
    t, src, status = fetch_search(ref, name)
    if t:
        texts.append((t, src, status))

    if not texts:
        return sku, ref, product_url, '', '', 'reader_and_search_failed'

    seen = []
    for text, source_page, method in texts:
        candidates = extract_candidates(text)
        for c in candidates:
            if c not in seen:
                seen.append(c)
        img_url, hosted = try_candidates(ref, candidates)
        if hosted:
            return sku, ref, product_url or source_page, img_url, hosted, f'recovered_{method}'

    return sku, ref, product_url, (seen[0] if seen else ''), '', 'no_downloadable_image'


with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

row_by_sku = {(r.get('SKU') or '').strip(): r for r in rows}
known_url = {}
if REPORT_IN.exists():
    with REPORT_IN.open('r', encoding='utf-8-sig', newline='') as f:
        for rec in csv.DictReader(f):
            sku = (rec.get('SKU') or '').strip()
            url = (rec.get('product_url') or '').strip()
            if url:
                known_url[sku] = url

jobs = []
for row in rows:
    if (row.get('Image 1') or '').strip():
        continue
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{5,8})', sku)
    if not m:
        continue
    ref = m.group(1)
    name = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{5,8}.*$', '', str(row.get('Item name') or ''), flags=re.I).strip()
    jobs.append((sku, ref, name[:120], known_url.get(sku,'')))

print(f'V5 START | missing={len(jobs)} | workers={WORKERS}', flush=True)
report = []
recovered = 0
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(repair_one, *j) for j in jobs]
    for i, fut in enumerate(as_completed(futures), 1):
        sku, ref, product_url, source_image, hosted, status = fut.result()
        if hosted:
            row_by_sku[sku]['Image 1'] = hosted
            recovered += 1
        report.append([sku,status,product_url,source_image,hosted])
        if i % 20 == 0 or i == len(jobs):
            print(f'V5 {i}/{len(jobs)} | recovered={recovered}', flush=True)

for output in (MASTER, Path('Cat1_SumUp_FINAL.csv')):
    with output.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

with REPORT_OUT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU','status','product_url','source_image_url','hosted_image_url'])
    w.writerows(sorted(report))

remaining = sum(1 for r in rows if not (r.get('Image 1') or '').strip())
print(f'V5 FINAL | recovered={recovered} | total_with_image={len(rows)-remaining} | remaining={remaining}', flush=True)
