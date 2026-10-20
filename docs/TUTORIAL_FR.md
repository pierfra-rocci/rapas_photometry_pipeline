# Tutoriel pas à pas – Pipeline de Photométrie RAPAS

Ce tutoriel vous guide à travers une session d'analyse typique.

Utilisez une image scientifique FITS contenant des données numériques. Le
chargeur accepte les types numériques FITS pris en charge et extrait le premier
plan 2D d'un cube ; vérifiez le plan sélectionné avant l'analyse.

## 1. Charger un Fichier FITS

Dans la zone principale, utilisez l'outil de chargement pour sélectionner votre image FITS. Extensions supportées : `.fits`, `.fit`, `.fts`, `.fits.gz`, `.fts.gz`.

Le chargement du fichier le met en attente dans l'interface et affiche immédiatement un aperçu brut de l'en-tête pour vérifier les métadonnées. Le chargement complet du FITS et l'analyse ne démarrent qu'après un clic sur le bouton **Start Analysis**.

> **Note** : Chaque session de navigateur est isolée des autres. Si un fichier ou un en-tête incorrect s'affiche, appuyez une fois sur **Reset** : cela ne réinitialise que votre propre session et supprime immédiatement le fichier chargé.

## 2. Configurer l'Observatoire

Dans la barre latérale (section **🔭 Observatory**), renseignez les informations de l'observatoire :

- **Name** : ex. « Backyard Observatory »
- **Latitude**, **Longitude**, **Elevation** : En degrés décimaux / mètres. Ces valeurs peuvent être remplies automatiquement depuis l'en-tête FITS — vérifiez-les.

## 3. Définir les Paramètres d'Analyse

Dans la barre latérale (section **⚙️ Parameters**), ajustez :

- **Estimated FWHM** : Estimation initiale en secondes d'arc
- **Detection Threshold** : Seuil sigma pour la détection des sources
- **FWHM Radius Factor** : Multiplicateur pour l'ouverture définie par l'utilisateur (0,5 – 2,0). Les valeurs 1,1 et 1,3 sont réservées aux deux ouvertures fixes.
- **Pixel scale override (arcsec/pixel)** : Laissez à `0` (automatique) lorsque l'en-tête FITS ou le WCS fournit l'échelle. Si l'échelle est introuvable, l'analyse s'arrête avec un message ; saisissez alors la valeur ici (ex. `0,75`) et relancez l'analyse. Aucune échelle par défaut n'est supposée.
- **Border Mask** : Pixels à exclure en bordure d'image
- **Max Magnitude Error** : Coupe de dernier recours sur l'erreur de magnitude calibrée. Une source n'est écartée que si *toutes* les méthodes de photométrie rapportent une erreur supérieure ou égale à cette valeur ; la régler à 0 désactive complètement la coupe
- **Detector Gain** : Gain effectif en e-/ADU utilisé pour le terme de Poisson. Une valeur FITS valide `EGAIN`/`CCDGAIN`, ou le facteur de conversion `CVF` (`gain = 1/CVF`), est prioritaire ; un simple `GAIN` n'est utilisé que s'il ressemble à un vrai facteur de conversion (0,02–100 e-/ADU et pas une caméra à gain « driver » QHY/ZWO, où `GAIN` est un réglage abstrait). Si aucune valeur n'est disponible, le gain configuré est utilisé lorsqu'il est non nul. À 0, le pipeline estime le gain à partir de l'image et utilise 1 e-/ADU si l'estimation n'est pas fiable. Le terme de Poisson n'est ajouté que si le gain retenu correspond aux unités des données : s'il prédit un bruit de ciel très supérieur au RMS de fond mesuré (par exemple sur une pose empilée normalisée dont le ciel vaut ~0,01), le gain est recalculé à partir du niveau de ciel et de ce RMS (`gain = niveau de ciel / RMS²`) et un message d'information le signale. Une image déjà soustraite du ciel ne peut pas être contrôlée ainsi : fournissez alors le gain dans les unités des données (ou une image en comptes).
- **Filter Band** : Bande photométrique pour la calibration (ex. g, r, i)
- **Astrometry Check** : Activer pour forcer la solution astrométrique et l'ajustement de la WCS

Pour activer le repli en ligne, configurez `ASTROMETRY_API_KEY` dans
l'environnement avant de démarrer Streamlit, ou définissez `api_key` sous
`[astrometry_net]` dans `~/.astropy/config/astroquery.cfg`. Sous WSL, vous
pouvez saisir la clé sans l'ajouter à l'historique du shell :

```bash
read -s -p "Clé API Astrometry.net : " ASTROMETRY_API_KEY
echo
export ASTROMETRY_API_KEY
```

Le pipeline essaie d'abord le solveur local. Le repli en ligne ne s'active
qu'après son échec et transmet la liste des sources détectées, pas l'image FITS.
Sans clé configurée, cette tentative en ligne est ignorée.

*La suppression des rayons cosmiques est toujours effectuée automatiquement via l'algorithme L.A.Cosmic (astroscrappy).*

## 4. (Optionnel) Clé API Astro-Colibri

Entrez votre clé API dans la section **🔑 Astro-Colibri** de la barre latérale (**UID Key**) pour activer le croisement avec les sources transitoires. Après le croisement, l'application indique le nombre de sources du pipeline associées à un événement Astro-Colibri (rayon progressif de 5, 10, 15 puis 20 secondes d'arc ; un avertissement est affiché lorsqu'une association n'est trouvée qu'à 15 ou 20 secondes d'arc), l'absence d'association, ou l'indisponibilité du service. Les sources renvoyées par Astro-Colibri pour le champ sont listées dans un tableau (nom, RA/Dec, type, classification, observatoire, identifiant de déclenchement, erreur de position, date, redshift, associée ou non, séparation), et les coordonnées de l'événement des sources associées sont exportées dans `astrocolibri_ra` et `astrocolibri_dec`. Dans Aladin Lite, les sources associées sont dessinées en carrés rouges plus grands (couche « Astro-Colibri matches ») pour être repérées d'un coup d'œil. Pour chaque association, des vignettes (image scientifique et modèle HiPS, PanSTARRS au nord, SkyMapper au sud avec repli automatique sur l'autre relevé) montrent la source avec sa magnitude calibrée.

## 5. (Optionnel) Candidats Transitoires

Développez la section **Transient Candidates** dans la barre latérale :

- **Enable Transient Finder** : Lance la recherche de transitoires, qui compare les sources détectées aux relevés de référence (PanSTARRS1 au nord, SkyMapper au sud) et rejette les objets connus des catalogues et du Système solaire.
- **Reference Filter** : Sélectionnez la bande pour la comparaison avec le relevé de référence et les vignettes du gabarit HiPS.
- Les détections sont croisées avec les catalogues de référence dans un rayon d'une demi-largeur à mi-hauteur (FWHM), et une source confondue avec une entrée de catalogue n'est conservée que si elle est au moins 2 magnitudes plus brillante (excès de brillance réel). Les sources sont détectées avec SExtractor, et les détections peu fiables (tronquées en bord de champ, issues d'un dédoublement, ou avec une ouverture défectueuse) sont écartées. Si le tableau de photométrie ne contient pas de magnitude PSF exploitable, le détecteur se rabat sur la magnitude d'ouverture calibrée à 1,1 x FWHM au lieu de ne retourner aucun candidat.
- Le détecteur de transitoires n'applique aucun seuil minimal de S/N. Les
  détections à faible S/N restent admissibles si elles passent les autres
  filtres de catalogue, de sources connues et d'objets du Système solaire.

## 6. Lancer l'Analyse

Cliquez sur **▶️ Start Analysis** pour démarrer. À ce moment, l'application charge le fichier FITS, vérifie l'en-tête et le WCS, puis exécute le pipeline.

Les étapes réalisées sont :

1. **Estimation du fond et du bruit**
2. **Détection des sources et suppression des rayons cosmiques**
3. **Photométrie** : Multi-ouvertures (jusqu'à 3) et photométrie PSF avec fond local robuste, correction d'ouverture, calcul du S/N et de l'erreur, et attribution des indicateurs de qualité
4. **Raffinement astrométrique** (si activé)
5. **Calibration photométrique** : Croisement avec des catalogues pour le point zéro et, lorsque les couleurs de catalogue sont disponibles, correction simple du terme de couleur
6. **Croisement multi-catalogues** : GAIA DR3, SIMBAD, SkyBoT, AAVSO VSX, Milliquas, Catalogue 10 pc, Astro-Colibri. Les mouvements propres disponibles du catalogue 10 pc sont propagés à l'époque de l'observation.
7. **Détection de transitoires** (si activée)

Si **Astrometry Check** est activé, l'application force une nouvelle solution,
même si un WCS valide est déjà présent. Elle essaie d'abord le solveur local,
puis Astrometry.net en ligne si le solveur local échoue et qu'une clé API est
configurée. Si les deux tentatives échouent, elle essaie de continuer avec le
WCS d'origine lorsqu'il est disponible.

Le solveur vise au moins 20 étoiles détectées ; si le seuil de 3 sigma donne moins
de 100 sources, le seuil de 2 sigma est essayé d'abord. Si le champ est petit ou pauvre, le
seuil de détection est abaissé de 3 à 2 puis 1 sigma ; si la résolution échoue
encore, elle est relancée avec la détection plus profonde avant de passer au
solveur en ligne.

## 7. Télécharger et Interpréter les Résultats

Après le traitement, téléchargez l'archive ZIP contenant le catalogue, les
en-têtes texte, le journal et les graphiques PNG générés. Les fichiers FITS ne
sont pas inclus dans l'archive.

- `*_catalog.csv` / `.vot` : Catalogue des sources limité à un jeu de colonnes choisi, dans cet ordre : `id`, `ra`, `dec`, `astrocolibri_name`, `astrocolibri_type`, `astrocolibri_classification`, `astrocolibri_ra`, `astrocolibri_dec`, `simbad_main_id`, `simbad_otype`, `simbad_B`, `simbad_V`, `skybot_NAME`, `skybot_OBJECT_TYPE`, `skybot_MAGV`, `aavso_Name`, `aavso_Type`, `aavso_Period`, `catalog_matches`, `x_center`, `y_center`, puis `snr_X_X`, les magnitudes d'ouverture `aperture_mag_X_X` (chacune suivie de son `quality_flag_X_X`), la magnitude PSF `psf_mag`, les erreurs `aperture_mag_err_X_X` et `psf_mag_err`, et les écarts `psf_minus_aperture_mag_X_X`. Les ouvertures sont les ouvertures fixes `_1_1` et `_1_3` plus celle de l'utilisateur (par exemple `_1_5` pour un facteur 1,5) : le dernier suffixe dépend donc de votre choix. Les colonnes inexistantes pour une exécution (par exemple sans clé Astro-Colibri) sont omises.

  `id` est un numéro de source entier unique ; c'est le même identifiant que celui affiché dans les fenêtres d'Aladin Lite, ce qui permet de retrouver dans les fichiers n'importe quelle source du visualiseur. Si des sources Astro-Colibri sont détectées, leurs lignes sont triées par `astrocolibri_name` et apparaissent tout au début des fichiers CSV et VOT.

  Lorsqu'un préfixe de filtre s'applique, les magnitudes et erreurs utilisent des noms préfixés, par exemple `rapasg_psf_mag` et `rapasg_aperture_mag_err_1_5` (les noms historiques sans préfixe sinon). Les erreurs de magnitude combinent la part aléatoire propre à la source et la part corrélée commune au champ (incertitude du point zéro et plancher systématique). Les magnitudes corrigées de la couleur (`*_colorcorr`), les facteurs de correction d'ouverture, le bruit de fond et les autres diagnostics internes servent pendant l'exécution mais ne sont pas écrits dans ces fichiers.
- `*_transients.csv` : Candidats transitoires (si le détecteur de transitoires est activé), avec les mêmes colonnes que le catalogue, dont `id`, ce qui permet de relier chaque candidat à sa ligne du catalogue. Les candidats sans correspondance dans le catalogue conservent leurs propres coordonnées, leur position en pixels et leur magnitude PSF calibrée.
- `*_header.txt` : En-tête FITS d'origine
- `*_wcs_header.txt` : En-tête de la solution astrométrique, si un WCS est disponible
- `*.log` : Journal de traitement détaillé
- `*.png` : Graphiques de diagnostic

**Produits FITS (non inclus dans l'archive ZIP)** : Le modèle PSF empirique,
lorsqu'il est généré, est enregistré sous `*_psf.fits` dans le répertoire de
résultats de l'utilisateur. Si l'astrométrie réussit, l'image originale avec le
WCS mis à jour est enregistrée sous `*_wcs.fits` dans le répertoire de résultats
et également dans `rpp_data/fits/` :

1. **Répertoire de résultats** : Le dossier de résultats de l'utilisateur ; le fichier est exclu de l'archive téléchargeable.
2. **`rpp_data/fits/`** : Copie persistante, écrasée si le même fichier est traité à nouveau.

**Suivi des analyses** : La base de données suit certains artefacts et leurs liens ; elle ne stocke pas les lignes de photométrie elles-mêmes :

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

L'erreur de flux utilisée pour le S/N combine l'erreur d'ouverture, l'incertitude
du fond local mesuré dans l'anneau (``σ_aperture`` et ``σ_total_bkg``) et la
variation de flux induite par l'incertitude sur le centre de la source
(``σ_centroid``) :

- **Erreur de flux** : σ_flux² = σ_aperture² + σ_total_bkg² + σ_centroid², où le
  terme de fond local utilise l'erreur standard médiane grand-échantillon de
  l'anneau (``ApertureStats.median_err``) et le terme de centroïde propage
  l'incertitude de position de la source
- **Erreur instrumentale** : σ_mag = 1,0857 × (σ_flux / flux), calculée à partir
  du même flux corrigé du fond et de l'ouverture que la magnitude elle-même
- **Erreur calibrée** : σ_mag_calib = √(σ_mag_inst² + σ_zp² + σ_sys²), où σ_zp est
  l'incertitude formelle de la médiane du point zéro, calculée comme 1,2533 × la
  dispersion de calibration non tronquée / √N, et σ_sys le plancher d'erreur
  systématique.
  L'incertitude formelle du point zéro est écrite dans la colonne `zero_point_std` ;
  les dispersions de calibration non tronquée et retenue (`zero_point_scatter`,
  `zero_point_scatter_kept`) et le nombre d'étoiles de calibration (`zero_point_n`)
  sont enregistrés dans la table de calibration appariée, et non dans le catalogue
  source exporté

Les magnitudes d'ouverture sont corrigées de la fraction de la PSF située hors de
l'ouverture à l'aide de la courbe d'énergie encerclée de la PSF empirique (repli
sur un modèle gaussien analytique), afin que les magnitudes d'ouverture et de PSF
partagent la même échelle de flux total. La soustraction du fond et l'ajustement
PSF aux moindres carrés utilisent tous deux un fond local robuste estimé sur un
anneau autour de chaque source.

Après la photométrie, le pipeline indique aussi l'écart médian
`psf_mag - aperture_mag` par ouverture et avertit lorsqu'il dépasse 0,15 mag,
afin de rendre directement visible un désaccord entre les deux méthodes (par
exemple un problème de forme ou de normalisation de l'ePSF). L'écart par source
est exporté dans les colonnes `psf_minus_aperture_mag_X_X` (NaN pour les sources
mesurées par une seule méthode), et la vue des résultats le trace en fonction de
la magnitude PSF calibrée (avec les lignes de médiane et de zéro par ouverture)
ainsi que son histogramme.

**Analyse interactive** : Utilisez le visualiseur Aladin Lite intégré pour l'exploration en temps réel, ou exportez les coordonnées vers ESA SkyView.

### Services Externes et Résultats Partiels

Certaines étapes de croisement dépendent de services externes (GAIA, SIMBAD, SkyBoT, Astro-Colibri). Des erreurs réseau, des délais d'attente ou des catalogues vides n'arrêtent pas nécessairement toute l'analyse. Le pipeline continue dans la plupart des cas avec des résultats partiels et enregistre les avertissements dans le journal.

## Coordonnées RA/DEC

Les coordonnées du centre du champ sont affichées dans la section **Statistiques** en degrés décimaux et en format sexagésimal (HH:MM:SS / ±DD:MM:SS). Ces valeurs sont également enregistrées dans le journal.

## Support

En cas de problème, consultez le fichier journal. Pour les bugs ou les retours, contactez `rpp_support@saf-astronomie.fr`.
