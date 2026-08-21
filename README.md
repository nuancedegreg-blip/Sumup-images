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

## Conversion automatique des images

Le dépôt ne se contente pas de conserver les URL d’images d’origine. Il essaie de produire des images **réellement exploitables par SumUp**.

Le traitement prévu est le suivant :

1. récupération de l’image produit Leroy Merlin ;
2. si l’URL d’origine est exploitable, téléchargement de l’image ;
3. si nécessaire, recherche d’une autre source image correspondant à la même référence Leroy Merlin ;
4. conversion de l’image avec **FFmpeg** vers un fichier **JPG standard** compatible avec SumUp ;
5. redimensionnement/compression pour éviter des fichiers inutilement lourds ;
6. enregistrement de l’image convertie dans le dépôt GitHub ;
7. création d’une URL GitHub directe vers le JPG ;
8. remplacement de l’ancienne URL image dans le CSV SumUp par cette nouvelle URL ;
9. contrôle des échecs afin de ne pas conserver volontairement une URL cassée.

Les images converties sont stockées principalement dans :

- `catalog_images/` pour l’automatisation du catalogue maître ;
- `images/` pour le workflow historique de conversion d’images.

### Formats problématiques

Certaines images Leroy Merlin peuvent être fournies sous des formats ou URL mal acceptés par SumUp, notamment des URL Marketplace ou des fichiers de type **AVIF/WebP**.

Le rôle de la conversion est donc de transformer autant que possible ces images en **JPG classique accessible publiquement**, beaucoup plus fiable pour l’import SumUp.

### En cas d’échec

Si aucune image exploitable ne peut être récupérée pour une référence, l’article peut rester dans le catalogue avec son **nom et sa référence**, mais il ne faut pas remplacer volontairement l’image par une URL cassée.

L’objectif reste d’avoir une photo sur **le maximum d’articles possible**, puis de contrôler le taux réel d’images valides avant l’import final dans SumUp.

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

Images JPG converties et hébergées utilisées pour les articles lorsque nécessaire.

### `images/`

Images JPG générées par le workflow historique de conversion.

## Scripts

### `auto_import.py`

Recherche et enrichit les références Leroy Merlin, récupère les informations produit, contrôle les vendeurs, télécharge les images et peut les convertir/héberger pour les rendre utilisables par SumUp.

### `sumup_devis_mode.py`

Nettoie le catalogue articles pour l’utilisation en devis : noms courts, références visibles, catégories, dédoublonnage, prix 0 €, TVA vide.

### `build_services_catalog.py`

Génère le catalogue des prestations artisan et les lignes de barèmes Leroy Merlin HT.

### `convert_sumup.py`

Script dédié à la récupération, conversion en JPG, redimensionnement et hébergement des images afin de produire des URL compatibles avec l’import SumUp.

## GitHub Actions

Le workflow principal est :

`.github/workflows/auto_import.yml`

Il exécute automatiquement :

1. recherche et enrichissement des produits Leroy Merlin ;
2. récupération et traitement des images produit ;
3. conversion/hébergement des images lorsque nécessaire ;
4. optimisation des produits pour SumUp ;
5. génération du catalogue prestations ;
6. sauvegarde des fichiers et images générés dans le dépôt.

Un second workflow historique, `Convert SumUp images`, est consacré à la conversion des images du catalogue existant.

Les workflows sont programmés pour se relancer régulièrement afin d’enrichir progressivement le catalogue et corriger les images manquantes ou incompatibles.

## Avant import dans SumUp

Ne pas importer un fichier tant que les contrôles suivants ne sont pas validés :

- références Leroy au bon format ;
- absence de doublons massifs ;
- prix article à `0,00 €` ;
- TVA article vide ;
- catégories correctes ;
- images réellement accessibles et compatibles ;
- absence d’URL image cassées ;
- prestations Leroy au prix HT uniquement ;
- fichier CSV bien compatible avec le modèle SumUp.

## Principe général

Le catalogue doit rester **simple, rapide et pratique**. Il ne faut pas ajouter de champs, catégories ou informations qui ralentissent la recherche quotidienne dans SumUp.
