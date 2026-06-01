# MineMods Updater v1.0.0

Date de sortie: 2026-06-01

## Resume

Premiere version publique de MineMods Updater, une application desktop Windows qui automatise la verification et la mise a jour des mods Minecraft selon la version du jeu et le loader.

## Points forts

- Scan local des fichiers .jar du dossier mods
- Detection du contexte Minecraft (version + loader)
- Verification des mises a jour via Modrinth et CurseForge
- Matching strict anti faux-positifs
- Vue de confiance de matching avant validation
- Mode dry-run pour simuler les operations sans ecrire sur disque
- Mise a jour selective ou globale
- Backup automatique des anciens mods en .old
- Filtres de changelog (breaking, fix, performance, other)
- Export de rapport JSON et CSV (avant/apres)
- Gestion de profils multi-instances

## Distribution

Binaire Windows inclus:

- dist/MineModsUpdater/MineModsUpdater.exe

## Compatibilite

- OS: Windows 10/11
- Runtime: Python 3.10+ (pour execution source)
- Minecraft loaders supportes: Fabric, Forge, Quilt, NeoForge

## Notes d utilisation

1. Selectionner le dossier mods
2. Verifier la version Minecraft et le loader
3. Scanner puis verifier les updates
4. Consulter la confiance de matching
5. Lancer un dry-run si besoin
6. Appliquer les mises a jour
7. Exporter le rapport

## Limitations connues

- Depend de la disponibilite des APIs providers
- Certaines pages CurseForge peuvent necessiter une API key
- Le mapping de certains mods atypiques peut demander verification manuelle

## Feedback

Les retours utilisateurs sont bienvenus pour ameliorer:

- la precision du matching
- la lisibilite des rapports
- les outils de rollback
