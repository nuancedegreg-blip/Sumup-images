import csv
import re
from pathlib import Path

FILES = [Path('Catalogue_Maitre_SumUp.csv'), Path('SumUp_Nouveaux_Articles.csv'), Path('SumUp_MAJ_Articles.csv')]
DESC = 'Description (Online Store and Invoices only)'

RULES = [
    ('Électricité', ['wago','cable','câble','gaine','disjoncteur','prise','interrupteur','spot']),
    ('VMC / Ventilation', ['vmc','ventilation','extracteur']),
    ('Plomberie', ['multicouche','cuivre','raccord','siphon','bonde','mitigeur','robinet']),
    ('Carrelage', ['carrelage','joint carrelage','colle carrelage','nez de marche']),
    ('Peinture / Enduit', ['peinture','enduit','sous couche','sous-couche']),
    ('Maçonnerie', ['ciment','beton','béton','mortier','treillis','ragréage']),
    ('Fixation / Visserie', ['vis ','cheville','scellement','tige filet']),
    ('Étanchéité', ['epdm','etanch','étanch','spec','pare pluie','pare vapeur']),
    ('Bois / Panneaux', ['osb','tasseau','lambourde','madrier']),
    ('Portail / Clôture', ['portail','cloture','clôture','motorisation']),
    ('Terrasse', ['terrasse','lame composite']),
    ('Chauffage', ['radiateur','thermostatique']),
]

def ref_from(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{8})$', sku)
    return m.group(1) if m else ''

def category(name):
    t = name.lower()
    for cat, words in RULES:
        if any(w in t for w in words):
            return cat
    return 'Leroy Merlin'

def clean_name(name, ref):
    name = re.sub(r'\s+', ' ', name or '').strip()
    name = re.sub(r'\s+[–-]\s+Réf\.\s*\d{8}\s*$', '', name, flags=re.I)
    name = name[:150].rstrip(' -–|')
    return f'{name} – Réf. {ref}' if ref else name

def process(path):
    if not path.exists():
        return
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        fields = r.fieldnames or []
        rows = list(r)
    for row in rows:
        ref = ref_from(row)
        if not ref:
            continue
        row['Item name'] = clean_name(row.get('Item name',''), ref)
        if 'SKU' in row:
            row['SKU'] = f'LM-{ref}'
        if 'Category' in row:
            row['Category'] = category(row['Item name'])
        if DESC in row:
            row[DESC] = f'À fournir par le client - Leroy Merlin - Réf. {ref}'
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    print(path, len(rows), 'articles optimisés')

for p in FILES:
    process(p)
