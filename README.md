# Sumup-images

Catalogue SumUp et images produits.

## Vérification stricte des images Leroy Merlin

Le script `fill_missing_leroy_images.py` traite uniquement les références `LM-########` dont `Image 1` est vide. Il recherche la référence exacte sur Leroy Merlin, exige une fiche produit correspondant au SKU, rejette les vendeurs marketplace ou inconnus, et n'accepte qu'une image issue de `media.adeo.com`. Les références sans image certaine restent vides.

Le workflow `.github/workflows/fill_missing_leroy_images.yml` exécute cette vérification en une passe sur toutes les références vides et met à jour :

- `Catalogue_Maitre_SumUp.csv`
- `Cat1_SumUp_FINAL.csv`
- `verified_image_map.csv`
- `verified_leroy_images.csv`
- `verified_leroy_images_audit.csv`

Les images déjà présentes ne sont jamais remplacées par ce traitement.
