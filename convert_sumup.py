import csv
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests

SRC = Path('Cat1.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
IMG_DIR = Path('images')
IMG_DIR.mkdir(exist_ok=True)

repo = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
branch = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
raw_base = f'https://raw.githubusercontent.com/{repo}/{branch}/images'

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Referer': 'https://www.leroymerlin.fr/'
})

def download(url, dest):
    last = None
    for attempt in range(5):
        try:
            r = session.get(url, timeout=45, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 500:
                dest.write_bytes(r.content)
                return True, r.headers.get('content-type','')
            last = f'HTTP {r.status_code}'
        except Exception as e:
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))
    return False, last or 'download failed'

def to_jpg(src, dst):
    cmd = [
        'ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i', str(src),
        '-vf', "scale='min(1500,iw)':'min(1500,ih)':force_original_aspect_ratio=decrease",
        '-q:v','2', str(dst)
    ]
    subprocess.run(cmd, check=True, timeout=90)
    return dst.exists() and dst.stat().st_size > 500

with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

ok = 0
failed = []

for idx, row in enumerate(rows, 1):
    sku = (row.get('SKU') or f'row-{idx}').strip()
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in sku)
    url = (row.get('Image 1') or '').strip()
    out_img = IMG_DIR / f'{safe}.jpg'

    if out_img.exists() and out_img.stat().st_size > 500:
        row['Image 1'] = f'{raw_base}/{out_img.name}'
        ok += 1
        continue

    if not url:
        failed.append((sku, 'empty image URL'))
        continue

    tmp = Path(tempfile.gettempdir()) / f'{safe}.source'
    try:
        got, info = download(url, tmp)
        if not got:
            failed.append((sku, str(info)))
            continue
        if to_jpg(tmp, out_img):
            row['Image 1'] = f'{raw_base}/{out_img.name}'
            ok += 1
        else:
            failed.append((sku, 'conversion produced no JPEG'))
    except Exception as e:
        failed.append((sku, repr(e)))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    if idx % 25 == 0 or idx == len(rows):
        print(f'[{idx}/{len(rows)}] JPEG OK: {ok} | failures: {len(failed)}', flush=True)

with OUT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

with Path('conversion_report.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU','error'])
    w.writerows(failed)

print(f'FINAL: {len(rows)} rows, {ok} hosted JPEGs, {len(failed)} failures')
