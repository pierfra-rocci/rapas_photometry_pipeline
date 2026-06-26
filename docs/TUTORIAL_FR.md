# Tutoriel pas à pas – Pipeline de Photométrie RAPAS

Ce tutoriel vous guide à travers une session d'analyse typique.

> **Important** : Le pipeline nécessite des images FITS encodées sur **16 bits ou 32 bits**. Les images en 8 bits ne sont pas prises en charge et peuvent produire des résultats incorrects.

## 1. Charger un Fichier FITS

Dans la zone principale, utilisez l'outil de chargement pour sélectionner votre image FITS. Extensions supportées : `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.

Le chargement du fichier le met en attente dans l'interface et affiche immédiatement un aperçu brut de l'en-tête pour vérifier les métadonnées. Le chargement complet du FITS et l'analyse ne démarrent qu'après un clic sur le bouton **Start Analysis Pipeline**.

## 2. Configurer l'Observatoire

Dans la barre latérale (section **🔭 Observatory**), renseignez les informations de l'observatoire :

- **Name** : ex. « Backyard Observatory »
- **Latitude**, **Longitude**, **Elevation** : En degrés décimaux / mètres. Ces valeurs peuvent être remplies automatiquement depuis l'en-tête FITS — vérifiez-les.

## 3. Définir les Paramètres d'Analyse

Dans la barre latérale (section **⚙️ Parameters**), ajustez :

- **Estimated FWHM** : Estimation initiale en secondes d'arc
- **Detection Threshold** : Seuil sigma pour la détection des sources
- **FWHM Radius Factor** : Multiplicateur pour l'ouverture définie par l'utilisateur (0,5 – 2,0). Les valeurs 1,1 et 1,3 sont réservées aux deux ouvertures fixes.
- **Border Mask** : Pixels à exclure en bordure d'image
- **Filter Band** : Bande photométrique pour la calibration (ex. g, r, i)
- **Astrometry Check** : Activer pour forcer la solution astrométrique et l'ajustement de la WCS

*La suppression des rayons cosmiques est toujours effectuée automatiquement via l'algorithme L.A.Cosmic (astroscrappy).*

## 4. (Optionnel) Clé API Astro-Colibri

Entrez votre clé API dans la section **🔑 Astro-Colibri** de la barre latérale (**UID Key**) pour activer le croisement avec les sources transitoires.

## 5. (Optionnel) Candidats Transitoires

Développez la section **Transient Candidates** dans la barre latérale :

- **Enable Transient Finder** : Lance la détection par soustraction d'image avec un relevé de référence (PanSTARRS1 au nord, SkyMapper au sud).
- **Reference Filter** : Sélectionnez la bande pour la comparaison avec le gabarit.

## 6. Lancer l'Analyse

Cliquez sur **▶️ Start Analysis** pour démarrer. À ce moment, l'application charge le fichier FITS, vérifie l'en-tête et le WCS, puis exécute le pipeline.

Les étapes réalisées sont :

1. **Estimation du fond et du bruit**
2. **Détection des sources et suppression des rayons cosmiques**
3. **Photométrie** : Multi-ouvertures (jusqu'à 3) et photométrie PSF, calcul du S/N, de l'erreur et des indicateurs de qualité
4. **Raffinement astrométrique** (si activé)
5. **Calibration photométrique** : Croisement avec des catalogues pour le point zéro et, lorsque les couleurs de catalogue sont disponibles, correction simple du terme de couleur
6. **Croisement multi-catalogues** : GAIA DR3, SIMBAD, SkyBoT, AAVSO VSX, Milliquas, Catalogue 10 pc, Astro-Colibri
7. **Détection de transitoires** (si activée)

Si **Astrometry Check** est activé, l'application force une nouvelle solution même si un WCS valide est déjà présent. En cas d'échec, elle tente de rétablir et de continuer avec le WCS original si disponible.

## 7. Télécharger et Interpréter les Résultats

Après le traitement, téléchargez l'archive ZIP contenant :

- `*_catalog.csv` / `.vot` : Catalogue des sources avec photométrie, erreurs, indicateurs et croisements. Chaque ouverture produit ses propres colonnes : `aperture_mag_X_X`, `aperture_mag_err_X_X`, `snr_X_X`, `quality_flag_X_X` (ouvertures fixes : `_1_1`, `_1_3` ; ouverture utilisateur : ex. `_1_5` pour un facteur 1,5). Le catalogue conserve aussi les noms historiques et ajoute des alias préfixés par le filtre de calibration sélectionné, par exemple `rapasg_psf_mag` ou `rapasg_aperture_mag_1_5`.

	Lorsque les étoiles de calibration possèdent une couleur de catalogue exploitable, l'export peut aussi contenir des colonnes de magnitude corrigée du terme de couleur en parallèle, par exemple `psf_mag_colorcorr` ou `aperture_mag_1_3_colorcorr`, ainsi que des colonnes de suivi comme `source_color`, `color_term_label`, `color_term_value` et `color_term_offset`. Ces colonnes supplémentaires ne remplacent pas les magnitudes calibrées standard par point zéro ; elles s'ajoutent au catalogue. Comme pour les magnitudes calibrées de base, ces colonnes corrigées peuvent aussi recevoir des alias préfixés par le filtre sélectionné, par exemple `rapasg_psf_mag_colorcorr` ou `r_aperture_mag_1_3_colorcorr`.
- `*_background.fits` : Cartes de fond 2D et de bruit
- `*_psf.fits` : Modèle PSF empirique
- `*_wcs_header.txt` : En-tête de la solution astrométrique
- `*_wcs.fits` : Image originale avec l'en-tête WCS mis à jour (si astrométrie effectuée)
- `*.log` : Journal de traitement détaillé
- `*.png` : Graphiques de diagnostic

**Note sur les fichiers FITS avec WCS** : Lorsqu'une astrométrie est effectuée, le pipeline sauvegarde l'image originale avec la solution WCS mise à jour à deux endroits :

1. **Dans l'archive ZIP** (`*_wcs.fits`) — inclus dans votre téléchargement
2. **Dans `rpp_data/fits/`** — copie permanente écrasée si le fichier est retraité

**Suivi des analyses** : Tous les résultats sont automatiquement enregistrés dans la base de données :

- Chaque fichier FITS avec WCS est associé à votre nom d'utilisateur
- Chaque archive ZIP est liée à son (ses) fichier(s) FITS source
- Plusieurs analyses du même fichier (paramètres différents) sont possibles
- L'historique est consultable via les fonctions de `src/db_tracking.py`

### Indicateurs de Qualité Photométrique

| Indicateur | Plage S/N   | Fiabilité | Usage recommandé        |
| :--------- | :---------- | :-------- | :---------------------- |
| `good`     | S/N > 10    | Élevée    | Données scientifiques   |
| `marginal` | 5 < S/N ≤ 10 | Modérée  | À utiliser avec prudence |
| `poor`     | S/N ≤ 5     | Faible    | À exclure               |

Les erreurs de magnitude sont propagées comme suit :

- **Erreur instrumentale** : σ_mag = 1,0857 × (σ_flux / flux)
- **Erreur calibrée** : σ_mag_calib = √(σ_mag_inst² + σ_zp²)

**Analyse interactive** : Utilisez le visualiseur Aladin Lite intégré pour l'exploration en temps réel, ou exportez les coordonnées vers ESA SkyView.

### Services Externes et Résultats Partiels

Certaines étapes de croisement dépendent de services externes (GAIA, SIMBAD, SkyBoT, Astro-Colibri). Des erreurs réseau, des délais d'attente ou des catalogues vides n'arrêtent pas nécessairement toute l'analyse. Le pipeline continue dans la plupart des cas avec des résultats partiels et enregistre les avertissements dans le journal.

## Coordonnées RA/DEC

Les coordonnées du centre du champ sont affichées dans la section **Statistiques** en degrés décimaux et en format sexagésimal (HH:MM:SS / ±DD:MM:SS). Ces valeurs sont également enregistrées dans le journal.

## Support

En cas de problème, consultez le fichier journal. Pour les bugs ou les retours, contactez `rpp_support@saf-astronomie.fr`.
