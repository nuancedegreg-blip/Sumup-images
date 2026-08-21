import csv
from pathlib import Path

MAP = Path('verified_image_map.csv')
MASTER = Path('Catalogue_Maitre_SumUp.csv')
OUT = Path('Cat1_SumUp_FINAL.csv')

with MAP.open('r', encoding='utf-8-sig', newline='') as f:
    image_map = {r['SKU'].strip(): r['Image 1'].strip() for r in csv.DictReader(f) if r.get('SKU') and r.get('Image 1')}

with MASTER.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []
    rows = list(reader)

updated = 0
for row in rows:
    sku = (row.get('SKU') or '').strip()
    url = image_map.get(sku)
    if url and (row.get('Image 1') or '').strip() != url:
        row['Image 1'] = url
        updated += 1

for target in (MASTER, OUT):
    with target.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

print(f'Applied {updated} verified images')
