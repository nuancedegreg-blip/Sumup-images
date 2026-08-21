# Catalogue SumUp – NuanceDeGreg

Ce dépôt automatise la préparation d’un catalogue SumUp simple et rapide à utiliser pour les devis et interventions.

## Objectif

Le but est de retrouver rapidement :

- les **articles Leroy Merlin** à faire fournir au client ;
- les **prestations artisan** ;
- les **tarifs Leroy Merlin HT** issus des barèmes utilisés dans le projet.

Le catalogue est pensé pour une utilisation rapide dans SumUp : recherche par quelques mots, catégorie claire, référence visible et image quand elle est disponible.

## Articles Leroy Merlin

Les articles Leroy servent uniquement à identifier précisément ce que Leroy Merlin doit ajouter au client.

Règles appliquées :

- nom court et facilement recherchable ;
- référence Leroy Merlin visible dans le nom ;
- SKU au format `LM-XXXXXXXX` ;
- catégorie au format `Fournitures client - ...` ;
- description : `À fournir par le client - Leroy Merlin - Réf. XXXXXXXX` ;
- prix technique `0,00 €` ;
- aucune TVA renseignée ;
- pas de suivi de stock ;
- image produit ajoutée quand une image exploitable est disponible ;
- suppression des doublons par référence Leroy Merlin.

Exemple :

`Bonde de douche extra-plate Valentin 90 mm – Réf. 82240552`

## Prestations

Les prestations sont séparées en trois familles :

- `Prestations - ...` : prestations courantes à prix variable ;
- `Compléments chantier` : déplacement, protection, évacuation, main-d’œuvre complémentaire, etc. ;
- `Barème LM - ...` : prestations Leroy Merlin au tarif HT de référence.

### Prestations à prix libre

Les prestations génériques utilisent :

- prix variable activé ;
- prix technique de base `1,00 €` pour permettre l’import SumUp ;
- aucune TVA pré-renseignée ;
- unité adaptée : unité, forfait, ml, m² ou heure.

Le vrai montant est saisi au moment de l’utilisation dans SumUp.

### Barèmes Leroy Merlin

Les lignes `Barème LM - ...` utilisent :

- une seule ligne par prestation ;
- prix achat **HT** ;
- aucune TVA pré-renseignée ;
- code OAP conservé dans le SKU et la description ;
- unité du barème conservée.

Les tarifs ne doivent être ajoutés que lorsqu’ils proviennent d’une source de barème vérifiée.

## Fichiers principaux

### `Catalogue_Maitre_SumUp.csv`

Catalogue maître des articles Leroy Merlin.

### `SumUp_Nouveaux_Articles.csv`

Articles nouvellement trouvés lors d’une exécution.

### `SumUp_MAJ_Articles.csv`

Articles existants ayant été corrigés ou enrichis.

### `SumUp_Prestations_Artisan.csv`

Catalogue des prestations artisan et des barèmes Leroy Merlin HT.

### `catalogue_audit_report.csv`

Rapport de contrôle du catalogue.

### `catalogue_state.json`

État utilisé par l’automatisation pour reprendre les recherches et audits progressivement.

### `catalog_images/`

Images hébergées utilisées pour les articles lorsque nécessaire.

## Scripts

### `auto_import.py`

Recherche et enrichit les références Leroy Merlin, récupère les informations produit, contrôle les vendeurs et prépare les images.

### `sumup_devis_mode.py`

Nettoie le catalogue articles pour l’utilisation en devis : noms courts, références visibles, catégories, dédoublonnage, prix 0 €, TVA vide.

### `build_services_catalog.py`

Génère le catalogue des prestations artisan et les lignes de barèmes Leroy Merlin HT.

### `convert_sumup.py`

Convertit et héberge les images nécessaires au format compatible avec l’import SumUp.

## GitHub Actions

Le workflow principal est :

`.github/workflows/auto_import.yml`

Il exécute automatiquement :

1. recherche et enrichissement des produits Leroy Merlin ;
2. optimisation des produits pour SumUp ;
3. génération du catalogue prestations ;
4. sauvegarde des fichiers générés dans le dépôt.

Il est également programmé pour se relancer régulièrement afin d’enrichir progressivement le catalogue.

## Avant import dans SumUp

Ne pas importer un fichier tant que les contrôles suivants ne sont pas validés :

- références Leroy au bon format ;
- absence de doublons massifs ;
- prix article à `0,00 €` ;
- TVA article vide ;
- catégories correctes ;
- images exploitables ;
- prestations Leroy au prix HT uniquement ;
- fichier CSV bien compatible avec le modèle SumUp.

## Principe général

Le catalogue doit rester **simple, rapide et pratique**. Il ne faut pas ajouter de champs, catégories ou informations qui ralentissent la recherche quotidienne dans SumUp.
