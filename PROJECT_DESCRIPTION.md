# Description Projet - MineMods Updater

## Description courte (GitHub About)

MineMods Updater met à jour tes mods Minecraft automatiquement en respectant la version du jeu, le loader (Fabric/Forge/Quilt/NeoForge) et la compatibilité provider.

## Description courte alternative (plus concise)

Updater desktop Windows pour mods Minecraft avec vérification de compatibilité loader/version, dry-run et rapports JSON/CSV.

## Description complète

MineMods Updater est une application desktop Windows qui simplifie la maintenance des mods Minecraft.

Le logiciel scanne un dossier mods local (scan automatique dès sélection du dossier), détecte les versions actuelles, interroge Modrinth et CurseForge, puis propose des mises à jour compatibles avec ton environnement (version Minecraft + loader).

Avant toute action, tu peux filtrer clairement la liste (État + Source), sélectionner/désélectionner rapidement les mods visibles, puis ouvrir la page provider du mod sélectionné (Modrinth ou CurseForge). L'interface a été compactée pour afficher plus de mods à l'écran (marges réduites, densité de lignes augmentée, actions fréquentes plus directes). Ensuite, tu choisis entre simulation dry-run ou application réelle. Lors d'une mise à jour, l'ancien mod est sauvegardé automatiquement en .old, et un rapport complet JSON/CSV est généré.

La vérification des mises à jour est optimisée pour les modpacks volumineux: traitement parallèle des mods, cache local court sur les checks récents, fallback automatique si un mapping provider est devenu obsolète, et gestion plus stricte des faux positifs (notamment sur les mods spécifiques à certains modpacks).

## Valeur utilisateur

- Évite les updates incompatibles
- Réduit le risque de casser un modpack
- Accélère les checks répétés grâce au cache local
- Simplifie la sélection des mods à mettre à jour avec les actions sur lignes affichées
- Évite de proposer des upgrades incohérents quand la version locale est un build modpack/private plus récent
- Garde une trace des opérations
- Permet une validation avant écriture

## Public cible

- Joueurs Minecraft moddés
- Créateurs de modpacks
- Administrateurs de packs privés

## Mots-clés GitHub recommandés

minecraft, mods, updater, modrinth, curseforge, fabric, forge, quilt, neoforge, desktop, pyside6
