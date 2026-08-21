import csv
from pathlib import Path

TEMPLATE = Path('Cat1.csv')
OUT = Path('SumUp_Prestations_Artisan.csv')
DESC = 'Description (Online Store and Invoices only)'

SERVICES = [
    ('Portail / Clôture', 'Dépose ancien portail', 'PREST-PORTAIL-DEPOSE'),
    ('Portail / Clôture', 'Pose portail battant', 'PREST-PORTAIL-POSE'),
    ('Portail / Clôture', 'Pose portail coulissant', 'PREST-PORTAIL-COULISSANT'),
    ('Portail / Clôture', 'Pose motorisation portail battant', 'PREST-MOTOR-BATTANT'),
    ('Portail / Clôture', 'Pose motorisation portail coulissant', 'PREST-MOTOR-COULISSANT'),
    ('Portail / Clôture', 'Pose cellules de sécurité portail', 'PREST-CELLULES-PORTAIL'),
    ('Portail / Clôture', 'Pose clôture aluminium', 'PREST-CLOTURE-ALU'),
    ('Portail / Clôture', 'Pose clôture composite', 'PREST-CLOTURE-COMPOSITE'),
    ('Portail / Clôture', 'Création ou reprise de seuil portail', 'PREST-SEUIL-PORTAIL'),
    ('Portail / Clôture', 'Rehausse ou reprise de piliers portail', 'PREST-PILIERS-PORTAIL'),

    ('Carrelage', 'Dépose carrelage', 'PREST-CARRELAGE-DEPOSE'),
    ('Carrelage', 'Pose carrelage sol', 'PREST-CARRELAGE-SOL'),
    ('Carrelage', 'Pose carrelage mural', 'PREST-CARRELAGE-MUR'),
    ('Carrelage', 'Pose faïence', 'PREST-FAIENCE'),
    ('Carrelage', 'Pose nez de marche carrelage', 'PREST-NEZ-MARCHE'),
    ('Carrelage', 'Réalisation joints carrelage', 'PREST-JOINT-CARRELAGE'),
    ('Carrelage', 'Découpe et calepinage carrelage', 'PREST-CALEPINAGE'),

    ('Préparation sols', 'Ragréage fibré', 'PREST-RAGREAGE-FIBRE'),
    ('Préparation sols', 'Ragréage autolissant', 'PREST-RAGREAGE'),
    ('Préparation sols', 'Ponçage ou surfaçage support', 'PREST-SURFACAGE'),
    ('Préparation sols', 'Application primaire d’accrochage', 'PREST-PRIMAIRE'),

    ('Salle de bain', 'Dépose baignoire', 'PREST-SDB-DEPOSE-BAIGNOIRE'),
    ('Salle de bain', 'Pose receveur de douche', 'PREST-SDB-RECEVEUR'),
    ('Salle de bain', 'Pose paroi de douche', 'PREST-SDB-PAROI'),
    ('Salle de bain', 'Pose meuble vasque', 'PREST-SDB-MEUBLE'),
    ('Salle de bain', 'Pose mitigeur', 'PREST-SDB-MITIGEUR'),
    ('Salle de bain', 'Étanchéité SPEC douche', 'PREST-SDB-SPEC'),
    ('Salle de bain', 'Création ou modification douche complète', 'PREST-SDB-DOUCHE'),

    ('Plomberie', 'Modification alimentation eau', 'PREST-PLOMB-ALIM'),
    ('Plomberie', 'Modification évacuation', 'PREST-PLOMB-EVAC'),
    ('Plomberie', 'Pose réseau multicouche', 'PREST-MULTICOUCHE'),
    ('Plomberie', 'Pose raccord plomberie', 'PREST-RACCORD-PLOMB'),
    ('Plomberie', 'Recherche et réparation fuite', 'PREST-FUITE'),
    ('Plomberie', 'Pose robinet d’arrêt', 'PREST-ROBINET-ARRET'),
    ('Plomberie', 'Pose siphon ou bonde', 'PREST-SIPHON-BONDE'),

    ('Électricité', 'Création prise électrique', 'PREST-ELEC-PRISE'),
    ('Électricité', 'Création point lumineux', 'PREST-ELEC-LUMIERE'),
    ('Électricité', 'Pose interrupteur', 'PREST-ELEC-INTERRUPTEUR'),
    ('Électricité', 'Pose disjoncteur', 'PREST-ELEC-DISJONCTEUR'),
    ('Électricité', 'Tirage câble électrique', 'PREST-ELEC-CABLE'),
    ('Électricité', 'Pose boîte de dérivation', 'PREST-ELEC-BOITE'),
    ('Électricité', 'Recherche panne électrique', 'PREST-ELEC-PANNE'),

    ('VMC / Ventilation', 'Pose VMC', 'PREST-VMC'),
    ('VMC / Ventilation', 'Pose bouche VMC', 'PREST-VMC-BOUCHE'),
    ('VMC / Ventilation', 'Pose extracteur d’air', 'PREST-EXTRACTEUR'),

    ('Peinture / Enduit', 'Préparation murs avant peinture', 'PREST-PREPA-MURS'),
    ('Peinture / Enduit', 'Enduit de rebouchage', 'PREST-ENDUIT-REBOUCHAGE'),
    ('Peinture / Enduit', 'Enduit de lissage', 'PREST-ENDUIT-LISSAGE'),
    ('Peinture / Enduit', 'Peinture murs', 'PREST-PEINTURE-MURS'),
    ('Peinture / Enduit', 'Peinture plafonds', 'PREST-PEINTURE-PLAFONDS'),
    ('Peinture / Enduit', 'Peinture extérieure', 'PREST-PEINTURE-EXT'),
    ('Peinture / Enduit', 'Enduit extérieur', 'PREST-ENDUIT-EXT'),

    ('Maçonnerie', 'Démolition maçonnerie', 'PREST-MACON-DEMO'),
    ('Maçonnerie', 'Création dalle béton', 'PREST-DALLE-BETON'),
    ('Maçonnerie', 'Création fondation', 'PREST-FONDATION'),
    ('Maçonnerie', 'Montage muret', 'PREST-MURET'),
    ('Maçonnerie', 'Montage pilier béton', 'PREST-PILIER'),
    ('Maçonnerie', 'Reprise béton ou mortier', 'PREST-REPRISE-BETON'),
    ('Maçonnerie', 'Création saignée et rebouchage', 'PREST-SAIGNEE'),

    ('Terrasse', 'Pose terrasse composite', 'PREST-TERRASSE-COMPOSITE'),
    ('Terrasse', 'Pose terrasse bois', 'PREST-TERRASSE-BOIS'),
    ('Terrasse', 'Pose lambourdes terrasse', 'PREST-LAMBOURDES'),
    ('Terrasse', 'Dépose terrasse existante', 'PREST-TERRASSE-DEPOSE'),

    ('Bois / Placo', 'Pose ossature bois', 'PREST-OSSATURE-BOIS'),
    ('Bois / Placo', 'Pose panneaux OSB', 'PREST-OSB'),
    ('Bois / Placo', 'Pose plaque de plâtre BA13', 'PREST-BA13'),
    ('Bois / Placo', 'Pose Fermacell', 'PREST-FERMACELL'),
    ('Bois / Placo', 'Pose isolation', 'PREST-ISOLATION'),

    ('Toiture / Étanchéité', 'Pose membrane EPDM', 'PREST-EPDM'),
    ('Toiture / Étanchéité', 'Étanchéité toiture ou terrasse', 'PREST-ETANCHEITE'),
    ('Toiture / Étanchéité', 'Pose gouttière', 'PREST-GOUTTIERE'),

    ('Chauffage', 'Dépose radiateur', 'PREST-RADIATEUR-DEPOSE'),
    ('Chauffage', 'Pose radiateur', 'PREST-RADIATEUR-POSE'),
    ('Chauffage', 'Modification raccordement radiateur', 'PREST-RADIATEUR-RACC'),
    ('Chauffage', 'Pose robinet thermostatique', 'PREST-THERMOSTATIQUE'),

    ('Menuiserie / Fermeture', 'Pose porte', 'PREST-PORTE'),
    ('Menuiserie / Fermeture', 'Réglage ou réparation volet roulant', 'PREST-VOLET'),
    ('Menuiserie / Fermeture', 'Pose store banne', 'PREST-STORE-BANNE'),
    ('Menuiserie / Fermeture', 'Pose garde-corps', 'PREST-GARDE-CORPS'),

    ('Divers chantier', 'Déplacement chantier', 'PREST-DEPLACEMENT'),
    ('Divers chantier', 'Protection de chantier', 'PREST-PROTECTION'),
    ('Divers chantier', 'Évacuation gravats et déchèterie', 'PREST-EVAC-GRAVATS'),
    ('Divers chantier', 'Main-d’œuvre complémentaire', 'PREST-MO-COMP'),
]

with TEMPLATE.open('r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames or []

rows = []
for category, name, sku in SERVICES:
    row = {k: '' for k in fields}
    row['Item name'] = name
    row['Variable price? (Yes/No)'] = 'Yes'
    row['SKU'] = sku
    row['Category'] = category
    row['Display item at Checkout? (Yes/No)'] = 'Yes'
    row['Display item in Online Store? (Yes/No)'] = 'No'
    row['Track inventory? (Yes/No)'] = 'No'
    row[DESC] = 'Prestation de main-d’œuvre – tarif à définir selon le chantier'
    rows.append(row)

with OUT.open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print(f'{len(rows)} prestations générées dans {OUT}')
