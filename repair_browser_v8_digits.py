import csv
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
REPORT = Path('repair_v8_report.csv')
IMG_DIR = Path('images_v8')
IMG_DIR.mkdir(exist_ok=True)
BATCH = int(os.environ.get('V8_BATCH', '10'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v8'


def digits_only(value):
    return ''.join(re.findall(r'\d', str(value or '')))


def ref_from_row(row):
    # IMPORTANT: the Leroy query is digits only. SKU may be LM-12345678,
    # but query_ref becomes exactly 12345678.
    sku = (row.get('SKU') or '').strip()
    d = digits_only(sku)
    return d if 5 <= len(d) <= 8 else ''


def product_link_from_search(page, query_ref):
    if '/produits/' in page.url and query_ref in page.url:
        return page.url.split('#', 1)[0]
    links = page.locator('a[href*="/produits/"]')
    for i in range(min(links.count(), 200)):
        try:
            href = links.nth(i).get_attribute('href') or ''
            txt = links.nth(i).inner_text(timeout=400) or ''
        except Exception:
            continue
        if query_ref not in href and query_ref not in txt:
            continue
        if href.startswith('/'):
            href = 'https://www.leroymerlin.fr' + href
        if href.startswith('https://www.leroymerlin.fr/produits/'):
            return href.split('#', 1)[0]
    return ''


def image_from_product_page(page, query_ref):
    try:
        body = page.locator('body').inner_text(timeout=4000)
    except Exception:
        body = ''
    if query_ref not in page.url and query_ref not in body:
        return ''
    for sel in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        loc = page.locator(sel)
        if loc.count():
            u = loc.first.get_attribute('content') or ''
            if u.startswith('http'):
                return u.replace('&amp;', '&')
    candidates = []
    imgs = page.locator('img')
    for i in range(min(imgs.count(), 200)):
        try:
            u = imgs.nth(i).get_attribute('src') or imgs.nth(i).get_attribute('data-src') or ''
        except Exception:
            continue
        if u.startswith('http') and 'media.adeo.com' in u:
            score = 2 if '/marketplace/' not in u else 0
            candidates.append((score, u))
    if not candidates:
        return ''
    candidates.sort(reverse=True)
    return candidates[0][1]


def save_jpg(context, query_ref, image_url):
    tries = [image_url]
    if 'media.adeo.com/media/' in image_url:
        base = image_url.split('?', 1)[0]
        tries.insert(0, base + '?fit=bounds&format=jpg&height=1000&quality=85&width=1000')
    for u in tries:
        try:
            r = context.request.get(u, timeout=20000, headers={'Referer': 'https://www.leroymerlin.fr/'})
            if not r.ok:
                continue
            data = r.body()
            if len(data) < 800 or 'text/html' in (r.headers.get('content-type') or '').lower():
                continue
        except Exception:
            continue
        out = IMG_DIR / f'LM-{query_ref}.jpg'
        tmp = Path(tempfile.gettempdir()) / f'lm-v8-{query_ref}.src'
        tmp.write_bytes(data)
        try:
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(tmp),'-vf',"scale='min(900,iw)':'min(900,ih)':force_original_aspect_ratio=decrease",'-frames:v','1','-q:v','4',str(out)], check=True, timeout=25)
            if out.exists() and out.stat().st_size > 800:
                return f'{RAW_BASE}/{out.name}'
        except Exception:
            out.unlink(missing_ok=True)
        finally:
            tmp.unlink(missing_ok=True)
    return ''


with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

jobs = []
for idx, row in enumerate(rows):
    if (row.get('Image 1') or '').strip():
        continue
    query_ref = ref_from_row(row)
    if not query_ref:
        continue
    jobs.append((idx, (row.get('SKU') or '').strip(), query_ref, (row.get('Item name') or '').strip()))
    if len(jobs) >= BATCH:
        break

print(f'V8 START | test={len(jobs)}', flush=True)
report = []
recovered = 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(locale='fr-FR', timezone_id='Europe/Paris', viewport={'width':1440,'height':1000}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36')
    page = context.new_page()
    page.set_default_timeout(10000)

    for n, (idx, sku, query_ref, name) in enumerate(jobs, 1):
        status = 'not_found'
        product_url = image_url = hosted = ''
        print(f'V8 {n}/{len(jobs)} | SEARCH={query_ref}', flush=True)
        try:
            # ONLY DIGITS are inserted after q=
            search_url = 'https://www.leroymerlin.fr/recherche/?q=' + quote_plus(query_ref)
            page.goto(search_url, wait_until='domcontentloaded', timeout=25000)
            page.wait_for_timeout(2500)
            product_url = product_link_from_search(page, query_ref)
            if product_url:
                page.goto(product_url, wait_until='domcontentloaded', timeout=25000)
                page.wait_for_timeout(2000)
                image_url = image_from_product_page(page, query_ref)
                if image_url:
                    hosted = save_jpg(context, query_ref, image_url)
                    if hosted:
                        rows[idx]['Image 1'] = hosted
                        recovered += 1
                        status = 'recovered'
                    else:
                        status = 'image_download_failed'
                else:
                    status = 'product_found_no_image'
        except PlaywrightTimeoutError:
            status = 'timeout'
        except Exception as e:
            status = 'error_' + type(e).__name__
        report.append([sku, query_ref, status, product_url, image_url, hosted])
        print(f'V8 RESULT | SEARCH={query_ref} | {status}', flush=True)

    context.close(); browser.close()

for target in (MASTER, OUT):
    with target.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f); w.writerow(['SKU','query_ref_digits_only','status','product_url','source_image_url','hosted_image_url']); w.writerows(report)

print(f'V8 FINAL | recovered={recovered}/{len(jobs)}', flush=True)
