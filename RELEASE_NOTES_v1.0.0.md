# MineMods Updater v1.0.0

Date de sortie: 2026-06-01

## Résumé

Première version publique de MineMods Updater, une application desktop Windows qui automatise la vérification et la mise à jour des mods Minecraft selon la version du jeu et le loader.

## Améliorations récentes (post v1.0.0)

- Vérification des mises à jour accélérée via traitement parallèle.
- Cache local court pour les checks répétitifs (moins de requêtes inutiles à la suite).
- Fallback automatique quand un mapping Modrinth est obsolète.
- Scan auto après sélection du dossier mods.
- Interface recentrée mods-first: tri de colonnes, redimensionnement manuel, filtres clairs, recherche, logs repliables.
- Interface plus compacte: espacements réduits, lignes plus denses, champ recherche élargi.
- Actions rapides: tout cocher / tout décocher sur les mods affichés.
- Bouton pour ouvrir la page provider du mod sélectionné (Modrinth/CurseForge).
- Matching acronymes amélioré pour les mods nommés de façon abrégée.
- Comparaison de versions améliorée (ex: `0.8.2` = `fabric-0.8.2`).
- Changelog: quand un mod est déjà à jour, affichage de la version actuelle sans section "versions intermédiaires" trompeuse.
- Détection modpack/private build: si la version locale est plus récente que le latest provider, le mod est classé introuvable (pas de faux upgrade proposé).
- Scan renforcé: ignore aussi les variantes de backup désactivées comme `*.old.jar`.
- "Tout mettre à jour" ne traite que les mods réellement éligibles (`Mise à jour disponible`) et ignore les statuts non applicables.

## Points forts

- Scan local des fichiers .jar du dossier mods
- Détection du contexte Minecraft (version + loader)
- Vérification des mises à jour via Modrinth et CurseForge
- Matching strict anti faux-positifs
- Mode dry-run pour simuler les opérations sans écrire sur disque
- Mise à jour sélective ou globale
- Backup automatique des anciens mods en .old
- Filtres de changelog (breaking, fix, performance, other)
- Export de rapport JSON et CSV (avant/après)

## Distribution

Binaire Windows inclus:

- dist/MineModsUpdater/MineModsUpdater.exe

## Compatibilité

- OS: Windows 10/11
- Runtime: Python 3.10+ (pour exécution source)
- Minecraft loaders supportés: Fabric, Forge, Quilt, NeoForge

## Notes d'utilisation

1. Sélectionner le dossier mods
2. Vérifier la version Minecraft et le loader
3. Scanner puis vérifier les mises à jour
4. Filtrer par état/source et sélectionner les mods affichés
5. Ouvrir la page provider du mod si nécessaire
6. Lancer un dry-run si besoin
7. Appliquer les mises à jour
8. Exporter le rapport

## Limitations connues

- Dépend de la disponibilité des APIs providers
- Certaines pages CurseForge peuvent nécessiter une API key
- Le mapping de certains mods atypiques peut demander une vérification manuelle

## Feedback

Les retours utilisateurs sont bienvenus pour améliorer:

- la précision du matching
- la lisibilité des rapports
- les outils de rollback
