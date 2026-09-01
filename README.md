# Huawei LTE (fork antibill51)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/default)

Composant personnalisé pour Home Assistant améliorant l'intégration native `huawei_lte` pour les clés et routeurs 4G/LTE Huawei (ex: E3372, B525, B535, B715, B818...).

---

## 🚀 Fonctionnalités apportées par ce fork

### 1. 📩 Gestion complète & robuste des SMS
* **Envoi 100% fiable :** Résolution des erreurs de jeton tournant (*rolling session token* `125003: Wrong Session Token`) via requête directe avec jeton frais et retry automatique.
* **Réception d'événements :** Déclenchement automatique de l'événement Home Assistant `huawei_lte_sms_received` à chaque nouveau SMS reçu avec ses attributs (`phone`, `message`, `date`, `index`).
* **Capteur d'état :** Création de l'entité `sensor.<device>_dernier_sms_recu` contenant le texte et les métadonnées du dernier message.
* **Décodeur universel d'accents :**
  * Correction automatique du Mojibake UTF-8 ↔ Latin-1 (`Ã©` → `é`, `Ã ` → `à`, `Ã¨` → `è`, `Ã§` → `ç`, `â€™` → `’`, etc.).
  * Décodage des trames brutes hexadécimales UCS-2 / UTF-16 BE transmises par certains modems.

### 2. 🧹 Gestion du stockage & Purge de la mémoire
* **Option de suppression automatique :** Option Config Flow permettant de supprimer automatiquement chaque SMS entrant de la clé/SIM dès sa réception et son traitement.
* **Suppression automatique des accusés :** Nettoyage automatique des accusés de réception et rapports de statut (type 7).
* **Nouveaux services Home Assistant :**
  * `huawei_lte.delete_sms` : Supprime un SMS par son numéro d'index.
  * `huawei_lte.clear_sms_inbox` : Supprime tous les SMS reçus dans la boîte de réception.
  * `huawei_lte.clear_sms_drafts` : Supprime tous les SMS brouillons (*drafts*).
  * `huawei_lte.clear_sms_sent` : Supprime l'historique des SMS envoyés.
  * `huawei_lte.clear_sms_reports` : Supprime les accusés de réception résiduels.
  * `huawei_lte.clear_all_sms` : Purge totale de la mémoire du modem (boîtes 1, 2, 3 et 4).
  * `huawei_lte.resend_sms_drafts` : Tente de renvoyer automatiquement tous les brouillons stockés vers leurs destinataires et les supprime en cas de succès.

### 3. 🛡️ Mémorisation des endpoints non supportés
* Mémorise automatiquement les fonctionnalités non supportées par votre modèle de clé (ex: `lan_host_info`, `wlan_host_list` sur clé USB E3372).
* Élimine les warnings répétitifs à chaque démarrage de Home Assistant.
* Service `huawei_lte.reset_unsupported_endpoints` pour forcer une nouvelle détection après mise à jour.

---

## 🛠️ Installation

### Via HACS (Dépôt personnalisé)
1. Dans Home Assistant, ouvrez **HACS** → **Intégrations** → menu (⋮) → **Dépôts personnalisés**.
2. Ajoutez l'URL de votre dépôt GitHub (`https://github.com/antibill51/ha-huawei-lte`).
3. Sélectionnez la catégorie **Intégration** puis cliquez sur **Ajouter**.
4. Téléchargez l'intégration et **redémarrez Home Assistant**.

---

## 💡 Exemples d'automatisations & Scripts

Voici une collection d'exemples prêts à l'emploi couvrant les cas d'usage les plus fréquents.

---

### 1. 📤 Envoi de SMS (Notifications & Alertes)

#### A. Notification simple par SMS
```yaml
alias: "Alerte intrusion par SMS"
trigger:
  - trigger: state
    entity_id: alarm_control_panel.maison
    to: "triggered"
action:
  - action: notify.huawei_lte
    data:
      message: "ALERTE : Intrusion détectée à la maison !"
      target:
        - "0601020304"
        - "0605060708"
```

#### B. Script d'envoi réutilisable
```yaml
sequence:
  - action: notify.huawei_lte
    data:
      message: "{{ message }}"
      target: "{{ [numero] if numero is defined else [] }}"
fields:
  message:
    name: Message
    description: Texte du SMS à envoyer
    required: true
    example: "Porte de garage restée ouverte"
    selector:
      text:
  numero:
    name: Numéro destinataire
    description: Numéro de mobile (optionnel si destinataire par défaut configuré)
    example: "0601020304"
    selector:
      text:
```

---

### 2. 📥 Réception & Commandes par SMS

#### A. Transférer les SMS entrants vers l'application Home Assistant (ou Telegram)
```yaml
alias: "Transfert des SMS entrants sur smartphone"
trigger:
  - trigger: event
    event_type: huawei_lte_sms_received
action:
  - action: notify.notify
    data:
      title: "SMS reçu de {{ trigger.event.data.phone }}"
      message: "{{ trigger.event.data.message }}"
      data:
        subtitle: "{{ trigger.event.data.date }}"
```

#### B. Pilotage sécurisé par SMS avec accusé de réception
Exécute une commande (ex: "STATUT", "ALARME ON") uniquement si le numéro de l'expéditeur est autorisé, puis supprime le message.

```yaml
alias: "Pilotage domotique par SMS"
trigger:
  - trigger: event
    event_type: huawei_lte_sms_received
condition:
  # Vérification du numéro autorisé
  - condition: template
    value_template: "{{ trigger.event.data.phone in ['+33601020304', '0601020304'] }}"
action:
  - choose:
      # Commande 1 : Demande de statut
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.message | trim | lower == 'statut' }}"
        sequence:
          - action: notify.huawei_lte
            data:
              target: "{{ trigger.event.data.phone }}"
              message: >
                Statut Maison :
                Alarme : {{ states('alarm_control_panel.maison') }}
                Température salon : {{ states('sensor.temperature_salon') }}°C
                Volets : {{ states('cover.volets_rdc') }}

      # Commande 2 : Activation de l'alarme
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.message | trim | lower in ['alarme on', 'armer'] }}"
        sequence:
          - action: alarm_control_panel.alarm_arm_away
            target:
              entity_id: alarm_control_panel.maison
          - action: notify.huawei_lte
            data:
              target: "{{ trigger.event.data.phone }}"
              message: "Alarme armée avec succès."

  # Suppression du SMS traité pour ne pas saturer la SIM
  - action: huawei_lte.delete_sms
    data:
      index: "{{ trigger.event.data.index }}"
```

---

### 3. 🧹 Maintenance & Gestion de la mémoire

#### A. Suppression immédiate de chaque SMS reçu
Si vous n'utilisez pas l'option automatique du Config Flow :
```yaml
alias: "Huawei LTE - Supprimer le SMS après traitement"
trigger:
  - trigger: event
    event_type: huawei_lte_sms_received
action:
  - action: huawei_lte.delete_sms
    data:
      index: "{{ trigger.event.data.index }}"
```

#### B. Purge de secours si la mémoire SIM / Modem est saturée
```yaml
alias: "Huawei LTE - Purge d'urgence si stockage plein"
trigger:
  - trigger: state
    entity_id: binary_sensor.e3372_sms_storage_full
    to: "on"
action:
  - action: notify.persistent_notification
    data:
      title: "Huawei LTE"
      message: "Mémoire SMS saturée. Purge totale automatique effectuée."
  - action: huawei_lte.clear_all_sms
```

#### C. Nettoyage planifié hebdomadaire (Accusés & Envoyés)
```yaml
alias: "Huawei LTE - Nettoyage hebdomadaire de la mémoire SMS"
trigger:
  - trigger: time
    at: "03:30:00"
condition:
  - condition: time
    weekday:
      - sun
action:
  - action: huawei_lte.clear_sms_reports
  - action: huawei_lte.clear_sms_sent
```

---

### 4. 🔄 Résilience & Renvoi des brouillons

#### A. Renvoi automatique des brouillons au retour de la connexion cellulaire
Permet de renvoyer automatiquement les SMS qui n'avaient pas pu partir lors d'une perte temporaire de réseau 4G.

```yaml
alias: "Huawei LTE - Renvoi automatique des brouillons au retour réseau"
trigger:
  - trigger: state
    entity_id: binary_sensor.e3372_mobile_connection
    from: "off"
    to: "on"
    for:
      seconds: 15
action:
  - action: huawei_lte.resend_sms_drafts
```
