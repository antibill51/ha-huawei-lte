# Huawei LTE (fork antibill51)

Fork personnel de l'intégration Home Assistant native `huawei_lte`,
ajoutant la mémorisation persistante des endpoints détectés comme non
supportés par le routeur (évite le warning répété à chaque démarrage) et
un service `reset_unsupported_endpoints` pour forcer une nouvelle
détection.

## Installation via HACS (dépôt personnalisé)

1. HACS -> Intégrations -> menu (⋮) -> Dépôts personnalisés
2. Ajoutez `https://github.com/antibill51/ha-huawei-lte`
3. Catégorie : Intégration
4. Installez, puis redémarrez Home Assistant

## Important

Ce dépôt remplace le domaine natif `huawei_lte`. Si l'intégration native
est déjà configurée, HACS/HA peuvent entrer en conflit de domaine.
Désactivez ou désinstallez l'intégration native avant d'installer ce fork,
ou renommez le domaine dans `manifest.json` si vous voulez les faire
coexister.

Voir `PATCH_NOTES.txt` pour le détail des fichiers modifiés vs. la version
native, et les fichiers à copier depuis votre installation existante
(sensor.py, select.py, switch.py, device_tracker.py, notify.py,
diagnostics.py, strings.json, icons.json, translations/en.json).
