import csv
from pathlib import Path

TEMPLATE = Path('Cat1.csv')
OUT = Path('SumUp_Prestations_Artisan.csv')
DESC = 'Description (Online Store and Invoices only)'

# category|name|sku|unit
SERVICES_DATA = '''
Prestations - Portail / Clôture|Dépose ancien portail|PREST-PORTAIL-DEPOSE|unité
Prestations - Portail / Clôture|Pose portail battant|PREST-PORTAIL-POSE|unité
Prestations - Portail / Clôture|Pose portail coulissant|PREST-PORTAIL-COULISSANT|unité
Prestations - Portail / Clôture|Pose motorisation portail|PREST-MOTOR-PORTAIL|unité
Prestations - Portail / Clôture|Pose clôture aluminium|PREST-CLOTURE-ALU|ml
Prestations - Portail / Clôture|Pose clôture composite|PREST-CLOTURE-COMPOSITE|ml
Prestations - Portail / Clôture|Création ou reprise seuil portail|PREST-SEUIL-PORTAIL|forfait
Prestations - Portail / Clôture|Rehausse ou reprise piliers portail|PREST-PILIERS-PORTAIL|forfait
Prestations - Carrelage|Dépose carrelage|PREST-CARRELAGE-DEPOSE|m²
Prestations - Carrelage|Pose carrelage sol|PREST-CARRELAGE-SOL|m²
Prestations - Carrelage|Pose carrelage mural / faïence|PREST-CARRELAGE-MUR|m²
Prestations - Carrelage|Pose nez de marche|PREST-NEZ-MARCHE|ml
Prestations - Carrelage|Réalisation joints carrelage|PREST-JOINT-CARRELAGE|m²
Prestations - Carrelage|Découpe et calepinage complexe|PREST-CALEPINAGE|forfait
Prestations - Préparation sols|Ragréage fibré|PREST-RAGREAGE-FIBRE|m²
Prestations - Préparation sols|Ragréage autolissant|PREST-RAGREAGE|m²
Prestations - Préparation sols|Ponçage ou surfaçage support|PREST-SURFACAGE|m²
Prestations - Préparation sols|Application primaire d’accrochage|PREST-PRIMAIRE|m²
Prestations - Salle de bain|Dépose baignoire|PREST-SDB-DEPOSE-BAIGNOIRE|unité
Prestations - Salle de bain|Pose receveur de douche|PREST-SDB-RECEVEUR|unité
Prestations - Salle de bain|Pose paroi de douche|PREST-SDB-PAROI|unité
Prestations - Salle de bain|Pose meuble vasque|PREST-SDB-MEUBLE|unité
Prestations - Salle de bain|Pose mitigeur|PREST-SDB-MITIGEUR|unité
Prestations - Salle de bain|Étanchéité SPEC douche|PREST-SDB-SPEC|forfait
Prestations - Salle de bain|Modification douche complète|PREST-SDB-DOUCHE|forfait
Prestations - Plomberie|Modification alimentation eau|PREST-PLOMB-ALIM|forfait
Prestations - Plomberie|Modification évacuation|PREST-PLOMB-EVAC|forfait
Prestations - Plomberie|Pose réseau multicouche|PREST-MULTICOUCHE|ml
Prestations - Plomberie|Pose raccord plomberie|PREST-RACCORD-PLOMB|unité
Prestations - Plomberie|Recherche et réparation fuite|PREST-FUITE|forfait
Prestations - Plomberie|Pose siphon ou bonde|PREST-SIPHON-BONDE|unité
Prestations - Électricité|Création prise électrique|PREST-ELEC-PRISE|unité
Prestations - Électricité|Création point lumineux|PREST-ELEC-LUMIERE|unité
Prestations - Électricité|Pose interrupteur|PREST-ELEC-INTERRUPTEUR|unité
Prestations - Électricité|Pose disjoncteur|PREST-ELEC-DISJONCTEUR|unité
Prestations - Électricité|Tirage câble électrique|PREST-ELEC-CABLE|ml
Prestations - Électricité|Recherche panne électrique|PREST-ELEC-PANNE|forfait
Prestations - VMC / Ventilation|Pose VMC|PREST-VMC|unité
Prestations - VMC / Ventilation|Pose bouche VMC|PREST-VMC-BOUCHE|unité
Prestations - VMC / Ventilation|Pose extracteur d’air|PREST-EXTRACTEUR|unité
Prestations - Peinture / Enduit|Préparation murs avant peinture|PREST-PREPA-MURS|m²
Prestations - Peinture / Enduit|Enduit de rebouchage / lissage|PREST-ENDUIT-LISSAGE|m²
Prestations - Peinture / Enduit|Peinture murs|PREST-PEINTURE-MURS|m²
Prestations - Peinture / Enduit|Peinture plafonds|PREST-PEINTURE-PLAFONDS|m²
Prestations - Peinture / Enduit|Peinture extérieure|PREST-PEINTURE-EXT|m²
Prestations - Peinture / Enduit|Enduit extérieur|PREST-ENDUIT-EXT|m²
Prestations - Maçonnerie|Démolition maçonnerie|PREST-MACON-DEMO|forfait
Prestations - Maçonnerie|Création dalle béton|PREST-DALLE-BETON|m²
Prestations - Maçonnerie|Création fondation|PREST-FONDATION|ml
Prestations - Maçonnerie|Montage muret|PREST-MURET|m²
Prestations - Maçonnerie|Montage pilier béton|PREST-PILIER|unité
Prestations - Maçonnerie|Reprise béton ou mortier|PREST-REPRISE-BETON|forfait
Prestations - Maçonnerie|Création saignée et rebouchage|PREST-SAIGNEE|ml
Prestations - Terrasse|Pose terrasse composite|PREST-TERRASSE-COMPOSITE|m²
Prestations - Terrasse|Pose terrasse bois|PREST-TERRASSE-BOIS|m²
Prestations - Terrasse|Pose lambourdes terrasse|PREST-LAMBOURDES|m²
Prestations - Bois / Placo|Pose ossature bois|PREST-OSSATURE-BOIS|m²
Prestations - Bois / Placo|Pose panneaux OSB|PREST-OSB|m²
Prestations - Bois / Placo|Pose plaque de plâtre BA13|PREST-BA13|m²
Prestations - Bois / Placo|Pose isolation|PREST-ISOLATION|m²
Prestations - Toiture / Étanchéité|Pose membrane EPDM|PREST-EPDM|m²
Prestations - Toiture / Étanchéité|Étanchéité toiture ou terrasse|PREST-ETANCHEITE|m²
Prestations - Toiture / Étanchéité|Pose gouttière|PREST-GOUTTIERE|ml
Prestations - Chauffage|Dépose radiateur|PREST-RADIATEUR-DEPOSE|unité
Prestations - Chauffage|Pose radiateur|PREST-RADIATEUR-POSE|unité
Prestations - Chauffage|Modification raccordement radiateur|PREST-RADIATEUR-RACC|unité
Prestations - Chauffage|Pose robinet thermostatique|PREST-THERMOSTATIQUE|unité
Prestations - Menuiserie / Fermeture|Pose porte|PREST-PORTE|unité
Prestations - Menuiserie / Fermeture|Réglage ou réparation volet roulant|PREST-VOLET|unité
Prestations - Menuiserie / Fermeture|Pose store banne|PREST-STORE-BANNE|unité
Prestations - Menuiserie / Fermeture|Pose garde-corps|PREST-GARDE-CORPS|ml
Compléments chantier|Déplacement chantier|PREST-DEPLACEMENT|unité
Compléments chantier|Protection de chantier|PREST-PROTECTION|forfait
Compléments chantier|Évacuation gravats et déchèterie|PREST-EVAC-GRAVATS|forfait
Compléments chantier|Main-d’œuvre complémentaire|PREST-MO-COMP|heure
'''.strip()

# family|name|code|prix achat HT|unit|detail
# Source : catalogue maître V17 du projet / barèmes Leroy Merlin validés.
LM_RATES_DATA = '''
Portail|Arrivée électrique portail prévue|OAP025-001|241.67|forfait|Passage de gaines et câbles entre piliers pour moteurs et accessoires
Portail|Création installation électrique portail|OAP025-002|864.17|forfait|Installation électrique depuis le tableau principal jusqu’au portail
Portail|Supplément excavation sol dur|OAP025-003|806.67|forfait|Excavation mécanique dans un sol dur
Portail|Portillon avec gâche électrique|OAP025-004|316.67|unité|Pose et raccordement d’une gâche électrique sur arrivée disponible
Portail|Pose portail battant|OAP025-005|280.00|unité|Installation, mise en service et réglage
Portail|Pose portail coulissant|OAP025-006|365.00|unité|Installation, mise en service et réglage
Portail|Pose portillon simple|OAP025-007|230.00|unité|Installation, mise en service et réglage
Portail|Pose interphone / visiophone|OAP025-008|100.00|unité|Installation et raccordement sur arrivée disponible
Portail|Pose digicode|OAP025-009|40.00|unité|Installation et raccordement sur arrivée disponible
Portail|Dépose portail + mise en déchetterie|OAP025-010|100.00|unité|Démontage du portail existant, gonds et mise en déchetterie
Portail|Transformation battant vers coulissant|OAP025-011|806.67|forfait|Création de la longrine nécessaire au refoulement
Portail|Démolition + remontage 2 piliers|OAP025-012|1210.00|forfait|Création de 2 piliers maçonnés après démolition
Portail|Piliers remplacés par poteaux aluminium|OAP025-013|967.50|forfait|Démolition, évacuation et remplacement par poteaux aluminium
Portail|Portail battant neuf avec maçonnerie|OAP025-014|806.67|forfait|Création seuil béton et 2 piliers maçonnés
Portail|Portail coulissant neuf avec maçonnerie|OAP025-015|1008.33|forfait|Création seuil, longrine et 2 piliers maçonnés
Portail|Mise en service motorisation portail|OAP025-016|225.00|unité|Mise en service et réglage de la motorisation
Portail|Portillon - démolition + remontage piliers|OAP025-017|403.33|forfait|Création de 2 piliers maçonnés pour portillon
Portail|Portillon neuf avec maçonnerie|OAP025-018|403.33|forfait|Création seuil béton et 2 piliers maçonnés
Portail|Déplacement artisan portail|OAP025-019|50.00|unité|Déplacement de l’artisan
Carrelage sol|Pose droite carrelage standard - 1 pièce|OAP036-003|52.43|m²|Pose droite, coupes, primaire si nécessaire et joints
Carrelage sol|Pose droite carrelage standard - 2 à 3 pièces|OAP036-004|57.20|m²|Pose droite, coupes, primaire si nécessaire et joints
Carrelage sol|Pose très grands carreaux|OAP036-014|119.17|m²|Pose collée de carreaux de très grand format
Carrelage sol|Dépose carrelage|OAP036-015|38.13|m²|Dépose de carrelage
Carrelage sol|Ragréage autolissant / fibré|OAP036-020|16.68|m²|Préparation, primaire et application du ragréage
Carrelage sol|Ragréage haute épaisseur|OAP036-021|20.26|m²|Préparation, primaire et ragréage haute épaisseur
Carrelage sol|Dépose / pose plinthes carrelage|OAP036-025|15.49|ml|Dépose et pose avec mastic périphérique
Carrelage mural|Pose carrelage mural format standard|OAP039-002|54.95|m²|Pose sur murs sains, primaire, joints, baguettes et silicone
Carrelage mural|Pose carrelage mural très grand format|OAP039-009|97.68|m²|Pose de carreaux très grand format sur murs sains
Carrelage mural|Dépose carrelage mural|OAP039-010|38.66|m²|Dépose du carrelage mural existant
Carrelage mural|Étanchéité espace douche|OAP039-012|125.43|unité|Application d’un kit d’étanchéité avec produit hydrofuge et bandes
Peinture|Rafraîchissement murs|OAP034-002|15.75|m²|Protection, lessivage et 2 couches de finition
Peinture|Rafraîchissement plafonds|OAP034-003|18.00|m²|Protection, lessivage et 2 couches de finition
Peinture|Peinture murs prêts à peindre|OAP034-004|20.25|m²|Protection, lessivage, sous-couche et 2 couches
Peinture|Peinture plafonds prêts à peindre|OAP034-005|26.25|m²|Protection, lessivage, sous-couche et 2 couches
Peinture|Enduisage partiel + peinture murs|OAP034-006|29.25|m²|Lessivage, enduisage partiel, sous-couche et 2 couches
Peinture|Redressage complet + peinture murs|OAP034-008|37.50|m²|Redressage total, sous-couche et 2 couches
'''.strip()

SERVICES = [tuple(line.split('|')) for line in SERVICES_DATA.splitlines() if line.strip()]
LM_RATES = []
for line in LM_RATES_DATA.splitlines():
    family, name, code, ht, unit, detail = line.split('|', 5)
    LM_RATES.append((family, name, code, float(ht), unit, detail))

with TEMPLATE.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []

rows = []
def blank_row():
    return {k: '' for k in fields}

def common(row, category, sku, unit):
    row['SKU'] = sku
    row['Category'] = category
    if 'Unit' in row:
        row['Unit'] = unit
    if 'Tax rate (%)' in row:
        row['Tax rate (%)'] = ''
    row['Display item at Checkout? (Yes/No)'] = 'Yes'
    row['Display item in Online Store? (Yes/No)'] = 'No'
    row['Track inventory? (Yes/No)'] = 'No'

# Prestations génériques : prix libre, sans TVA pré-renseignée.
for category, name, sku, unit in SERVICES:
    row = blank_row()
    row['Item name'] = name
    row['Price'] = ''
    row['Variable price? (Yes/No)'] = 'Yes'
    common(row, category, sku, unit)
    row[DESC] = f'Prestation de main-d’œuvre - unité : {unit} - tarif HT à adapter au chantier'
    rows.append(row)

# Barème Leroy : une seule ligne par prestation, prix achat HT, aucune TVA dans le catalogue.
for family, name, code, ht, unit, detail in LM_RATES:
    row = blank_row()
    row['Item name'] = f'LM - {name} - {ht:.2f} € HT'
    row['Price'] = f'{ht:.2f}'
    row['Variable price? (Yes/No)'] = 'No'
    common(row, f'Barème LM - {family}', f'LM-{code}-HT', unit)
    row[DESC] = f'Tarif Leroy Merlin HT : {ht:.2f} € | {detail} | Barème {code} | Unité : {unit}'
    rows.append(row)

with OUT.open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print(f'{len(rows)} prestations générées : {len(SERVICES)} prix libres + {len(LM_RATES)} tarifs Leroy HT, sans TVA')
