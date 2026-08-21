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
    ('Bois / Panneaux', ['osb','tasseau','lambourde','madrier','contreplaqué']),
    ('Portail / Clôture', ['portail','portillon','cloture','clôture','motorisation']),
    ('Terrasse', ['terrasse','lame composite']),
    ('Chauffage', ['radiateur','thermostatique']),
]

CLEAR_FIELDS = [
    'Cost price', 'Tax rate (%)', 'Regular price (before sale)',
    'Takeaway price', 'Takeaway tax rate'
]

def ref_from(row):
    sku = (row.get('SKU') or '').strip()
    m = re.search(r'(\d{8})$', sku)
    return m.group(1) if m else ''

def trade_category(name):
    t = name.lower()
    for cat, words in RULES:
        if any(w in t for w in words):
            return cat
    return 'Autres'

def clean_name(name, ref):
    name = re.sub(r'\s+', ' ', name or '').strip()
    name = re.sub(r'\s+[–-]\s+Réf\.\s*\d{8}\s*$', '', name, flags=re.I)
    name = name[:135].rstrip(' -–|')
    return f'{name} – Réf. {ref}' if ref else name

def process(path):
    if not path.exists():
        return
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)

    seen = set()
    clean_rows = []
    for row in rows:
        ref = ref_from(row)
        if not ref or ref in seen:
            continue
        seen.add(ref)

        row['Item name'] = clean_name(row.get('Item name', ''), ref)
        if 'SKU' in row:
            row['SKU'] = f'LM-{ref}'
        if 'Category' in row:
            row['Category'] = f'Fournitures client - {trade_category(row["Item name"])}'
        if DESC in row:
            row[DESC] = f'À fournir par le client - Leroy Merlin - Réf. {ref}'

        # SumUp exige un prix pour créer un article. 0,00 € permet de l'utiliser
        # uniquement comme ligne de fourniture client, sans valeur commerciale ni TVA.
        if 'Price' in row:
            row['Price'] = '0.00'
        for field in CLEAR_FIELDS:
            if field in row:
                row[field] = ''
        if 'Variable price? (Yes/No)' in row:
            row['Variable price? (Yes/No)'] = 'No'
        if 'Track inventory? (Yes/No)' in row:
            row['Track inventory? (Yes/No)'] = 'No'
        if 'Display item at Checkout? (Yes/No)' in row:
            row['Display item at Checkout? (Yes/No)'] = 'Yes'
        if 'Display item in Online Store? (Yes/No)' in row:
            row['Display item in Online Store? (Yes/No)'] = 'No'
        clean_rows.append(row)

    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(clean_rows)
    print(path, len(clean_rows), 'articles Leroy à 0,00 €, sans TVA, optimisés pour les devis')

for p in FILES:
    process(p)
