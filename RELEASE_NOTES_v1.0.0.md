# MineMods Updater v1.0.0

Date de sortie: 2026-06-01

## Résumé

Première version publique de MineMods Updater, une application desktop Windows qui automatise la vérification et la mise à jour des mods Minecraft selon la version du jeu et le loader.

## Points forts

- Scan local des fichiers .jar du dossier mods
- Détection du contexte Minecraft (version + loader)
- Vérification des mises à jour via Modrinth et CurseForge
- Matching strict anti faux-positifs
- Vue de confiance de matching avant validation
- Mode dry-run pour simuler les opérations sans écrire sur disque
- Mise à jour sélective ou globale
- Backup automatique des anciens mods en .old
- Filtres de changelog (breaking, fix, performance, other)
- Export de rapport JSON et CSV (avant/après)
- Gestion de profils multi-instances

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
4. Consulter la confiance de matching
5. Lancer un dry-run si besoin
6. Appliquer les mises à jour
7. Exporter le rapport

## Limitations connues

- Dépend de la disponibilité des APIs providers
- Certaines pages CurseForge peuvent nécessiter une API key
- Le mapping de certains mods atypiques peut demander une vérification manuelle

## Feedback

Les retours utilisateurs sont bienvenus pour améliorer:

- la précision du matching
- la lisibilité des rapports
- les outils de rollback
