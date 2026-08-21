import csv
import json
import os
import re
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')
REPORT = Path('repair_v7_report.csv')
STATE = Path('repair_v7_state.json')
IMG_DIR = Path('images_v7')
IMG_DIR.mkdir(exist_ok=True)

BATCH = int(os.environ.get('V7_BATCH', '120'))
MAX_ATTEMPTS = int(os.environ.get('V7_MAX_ATTEMPTS', '2'))
REPO = os.environ.get('GITHUB_REPOSITORY', 'nuancedegreg-blip/Sumup-images')
BRANCH = os.environ.get('GITHUB_REF_NAME', 'main') or 'main'
RAW_BASE = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/images_v7'


def ref_from_row(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{5,8})', sku)
    return m.group(1) if m else ''


def clean_name(row):
    s = (row.get('Item name') or '').strip()
    s = re.sub(r'\s+[–-]\s+Réf\.?\s*\d{5,8}.*$', '', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip()


def load_state():
    if not STATE.exists():
        return {'attempts': {}}
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {'attempts': {}}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def product_link_from_search(page, ref):
    # Direct redirect to a product page.
    if '/produits/' in page.url and ref in page.url:
        return page.url

    # Prefer an href that literally contains the reference.
    links = page.locator('a[href*="/produits/"]')
    count = min(links.count(), 120)
    for i in range(count):
        try:
            href = links.nth(i).get_attribute('href') or ''
            txt = links.nth(i).inner_text(timeout=500) or ''
        except Exception:
            continue
        if ref in href or ref in txt:
            if href.startswith('/'):
                href = 'https://www.leroymerlin.fr' + href
            if href.startswith('https://www.leroymerlin.fr/produits/'):
                return href.split('#', 1)[0]
    return ''


def image_from_product_page(page, ref):
    # Hard validation: exact reference must exist in URL or rendered page text.
    body = ''
    try:
        body = page.locator('body').inner_text(timeout=5000)
    except Exception:
        pass
    if ref not in page.url and ref not in body:
        return ''

    selectors = [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count():
            try:
                u = loc.first.get_attribute('content') or ''
                if u.startswith('http'):
                    return u.replace('&amp;', '&')
            except Exception:
                pass

    # Fallback: inspect rendered product images only.
    imgs = page.locator('img')
    count = min(imgs.count(), 120)
    candidates = []
    for i in range(count):
        try:
            u = imgs.nth(i).get_attribute('src') or imgs.nth(i).get_attribute('data-src') or ''
            alt = imgs.nth(i).get_attribute('alt') or ''
        except Exception:
            continue
        if not u.startswith('http'):
            continue
        if 'media.adeo.com' not in u:
            continue
        score = 0
        if '/media/' in u and '/marketplace/' not in u:
            score += 4
        if ref in u:
            score += 3
        if alt and len(alt) > 8:
            score += 1
        candidates.append((score, u))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return ''


def save_jpg(context, ref, image_url):
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
            if len(data) < 800:
                continue
            ct = (r.headers.get('content-type') or '').lower()
            if 'text/html' in ct:
                continue
        except Exception:
            continue

        out = IMG_DIR / f'LM-{ref}.jpg'
        tmp = Path(tempfile.gettempdir()) / f'lm-v7-{ref}.src'
        tmp.write_bytes(data)
        try:
            subprocess.run([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp),
                '-vf', "scale='min(900,iw)':'min(900,ih)':force_original_aspect_ratio=decrease",
                '-frames:v', '1', '-q:v', '4', str(out)
            ], check=True, timeout=25)
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

state = load_state()
attempts = state.setdefault('attempts', {})

jobs = []
for idx, row in enumerate(rows):
    if (row.get('Image 1') or '').strip():
        continue
    sku = (row.get('SKU') or '').strip()
    ref = ref_from_row(row)
    if not ref:
        continue
    if int(attempts.get(sku, 0)) >= MAX_ATTEMPTS:
        continue
    jobs.append((idx, sku, ref, clean_name(row)))
    if len(jobs) >= BATCH:
        break

print(f'V7 START | batch={len(jobs)} | total={len(rows)}', flush=True)
report = []
recovered = 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(
        locale='fr-FR',
        timezone_id='Europe/Paris',
        viewport={'width': 1440, 'height': 1000},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    )
    page = context.new_page()
    page.set_default_timeout(10000)

    for n, (idx, sku, ref, name) in enumerate(jobs, 1):
        attempts[sku] = int(attempts.get(sku, 0)) + 1
        status = 'not_found'
        product_url = ''
        image_url = ''
        hosted = ''
        try:
            search_url = 'https://www.leroymerlin.fr/recherche?q=' + quote_plus(ref)
            page.goto(search_url, wait_until='domcontentloaded', timeout=25000)
            page.wait_for_timeout(1800)
            product_url = product_link_from_search(page, ref)
            if product_url:
                page.goto(product_url, wait_until='domcontentloaded', timeout=25000)
                page.wait_for_timeout(1800)
                image_url = image_from_product_page(page, ref)
                if image_url:
                    hosted = save_jpg(context, ref, image_url)
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

        report.append([sku, status, ref, name, product_url, image_url, hosted, attempts[sku]])
        if n % 10 == 0 or n == len(jobs):
            print(f'V7 {n}/{len(jobs)} | recovered={recovered}', flush=True)
        save_state(state)

    context.close()
    browser.close()

for target in (MASTER, OUT):
    with target.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

with REPORT.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['SKU','status','reference','name','product_url','source_image_url','hosted_image_url','attempt'])
    w.writerows(report)

remaining = sum(1 for r in rows if not (r.get('Image 1') or '').strip())
print(f'V7 FINAL | recovered={recovered} | with_image={len(rows)-remaining} | remaining={remaining}', flush=True)
