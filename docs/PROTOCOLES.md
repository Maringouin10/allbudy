# Protocoles pilotés par AllBudy

Référence des deux protocoles locaux implémentés, de ce qu'ils exposent et de la
façon dont AllBudy les traduit en état normalisé.

Aucun des deux ne passe par un service en ligne : tout se joue entre le serveur
AllBudy et l'imprimante, sur le réseau local.

---

## 1. Moonraker / Klipper (port 7125)

Protocole documenté et stable, à privilégier. Implémenté dans
`allbudy/printers/moonraker.py`.

### Transport

AllBudy ouvre un **WebSocket JSON-RPC** sur `ws://<ip>:7125/websocket` et souscrit
aux objets Klipper (`printer.objects.subscribe`) : l'imprimante pousse alors ses
changements d'état, sans interrogation répétée. Si le WebSocket est indisponible,
le transport bascule automatiquement sur une **interrogation HTTP** de
`/printer/objects/query`.

Les commandes ponctuelles et les téléversements passent par l'API HTTP.

### Objets lus

Au moment de la connexion, AllBudy demande `printer.objects.list` puis ne souscrit
qu'aux objets réellement présents. Les objets périphériques sont détectés par nom :

| Rôle | Noms recherchés |
|---|---|
| Chambre | `heater_generic chamber_heater`, `temperature_sensor chamber_temp`, puis tout objet contenant `chamber` |
| Ventilateur auxiliaire | `fan_generic auxiliary_cooling_fan`, `fan_generic aux_fan` |
| Ventilateur de chambre | `fan_generic chamber_circulation_fan`, `fan_generic chamber_fan` |
| Éclairage | `output_pin caselight`, `output_pin LED`, `led chamber_light` |
| CFS | `box`, `filament_hub`, `cfs` |

Un objet absent n'est pas une erreur : le champ correspondant reste vide dans
l'interface.

### Correspondance des états

| `print_stats.state` | État AllBudy |
|---|---|
| `standby`, `cancelled` | `idle` |
| `printing` | `printing` |
| `paused` | `paused` |
| `complete` | `complete` |
| `error` | `error` |

Si `webhooks.state` vaut `shutdown` ou `error` (Klipper arrêté), l'état passe à
`error` quel qu'ait été `print_stats`, avec le message de Klipper.

Le **temps restant** n'est pas fourni par Klipper : AllBudy l'extrapole depuis la
durée écoulée et l'avancement (`print_duration × (100 − progress) / progress`).

### Points d'API utilisés

```
GET    /printer/info
GET    /printer/objects/list
GET    /printer/objects/query?<objets>
POST   /printer/print/start?filename=<nom>
POST   /printer/print/pause | /resume | /cancel
POST   /printer/gcode/script?script=<gcode>
POST   /printer/emergency_stop
GET    /server/files/list?root=gcodes
POST   /server/files/upload            (multipart: file, root, print)
DELETE /server/files/gcodes/<nom>
```

Une clé API Moonraker, si elle est configurée, est envoyée en en-tête `X-Api-Key`
(et en paramètre `token` pour le WebSocket).

---

## 2. LAN Creality (port 9999)

Protocole propriétaire parlé par Creality Print quand l'imprimante est en mode
LAN / développeur. **Il n'est pas documenté publiquement** : ce qui suit a été
reconstruit par observation, et les noms de champs changent selon les versions de
firmware. Implémenté dans `allbudy/printers/creality_lan.py`.

### Transport

WebSocket brut sur `ws://<ip>:9999`. L'imprimante émet des trames JSON d'état ;
certaines versions encapsulent la charge utile dans une clé `params`, AllBudy
accepte les deux formes.

Le firmware **ferme la session au bout d'une dizaine de secondes sans trafic** :
AllBudy envoie un battement `{"ModeCode":"heart_beat","msg":<timestamp>}` toutes
les 4 secondes.

### Lecture tolérante

Chaque grandeur est cherchée sous plusieurs noms, dans l'ordre, et vaut « absente »
si aucun ne répond :

| Grandeur | Alias acceptés |
|---|---|
| Température buse | `nozzleTemp`, `nozzleTemp0`, `curNozzleTemp` |
| Consigne buse | `targetNozzleTemp`, `targetNozzleTemp0`, `nozzleTempControl` |
| Température plateau | `bedTemp0`, `bedTemp`, `curBedTemp` |
| Chambre | `boxTemp`, `chamberTemp`, `caseTemp` |
| Avancement | `printProgress`, `dProgress` |
| Couches | `layer` / `TotalLayer`, `curLayer` / `totalLayer` |
| Temps | `printJobTime`, `printLeftTime`, `leftTime` |
| Fichier | `printFileName`, `curFileName`, `fileName` |
| État | `state`, `printStatus` |
| Pause | `pause`, `printPause` |
| Erreur | `err`, `error`, `errcode` |

Le champ `state` est interprété ainsi : `0` prête, `1` impression, `2` terminée,
`3` erreur, `4` en pause. Un champ `pause` à 1 **prime** sur `state`, et un champ
d'erreur non nul force l'état `error`.

### Commandes envoyées

```jsonc
{"method":"set","params":{"pause": 1}}                  // pause (0 = reprise)
{"method":"set","params":{"stop": 1}}                   // arrêt
{"method":"set","params":{"nozzleTempControl": 220}}
{"method":"set","params":{"bedTempControl": {"num":0, "val":60}}}
{"method":"set","params":{"lightSw": 1}}
{"method":"set","params":{"fan": 1}}                    // interrupteur, pas un %
{"method":"set","params":{"gcodeCmd": "G28"}}
{"method":"set","params":{"opGcodeFile":
    "printprt:/usr/data/printer_data/gcodes/piece.gcode"}}   // lancer
{"method":"get","params":{"reqGcodeFile": 1}}           // liste des fichiers
```

Le téléversement ne passe pas par le WebSocket mais par le serveur web embarqué :
`POST http://<ip>/upload/<nom>` en multipart.

### Limites

- Les ventilateurs ne sont qu'un **interrupteur** (marche/arrêt), pas un pourcentage.
- Les réponses aux commandes G-code ne reviennent pas : seul l'envoi est confirmé.
- L'état du CFS, quand il est présent, arrive sous `boxsInfo` / `cfsInfo`, parfois
  sérialisé en chaîne JSON — AllBudy désérialise ce cas.

---

## 3. CFS (Creality Filament System)

Le CFS est lu de la même façon quel que soit le transport, par
`allbudy/printers/parsing.py`. Deux structures sont acceptées :

```jsonc
// Liste de boîtiers, chacun avec ses emplacements
{"boxs": [{"id": 0, "materials": [{"id": 0, "type": "PLA", "color": "FF0000", "percent": 80}]}],
 "cur_id": 0}

// Liste plate d'emplacements
{"materials": [{"type": "PLA", "color": "#FF0000", "percent": 80}]}
```

Les clés de conteneur reconnues sont `boxs`, `boxes`, `units`, `hubs`, `cfs` ; celles
d'emplacements `materials`, `slots`, `filaments`, `spools`. Les couleurs sont acceptées
en `#RRGGBB`, `RRGGBB`, `RRGGBBAA`, entier, ou triplet `[r, g, b]`.

Un emplacement est considéré **vide** s'il n'annonce aucune matière, si son état est
`empty`/`none`, ou si son pourcentage restant est nul. La file d'attente n'y enverra
rien.

L'état lu est synchronisé dans la table des bobines, mais uniquement **quand il
change** (comparaison d'empreinte) : la boucle tourne toutes les deux secondes et
n'a pas à écrire en base à chaque tour. Une bobine modifiée à la main dans
l'interface repasse en gestion manuelle et n'est plus écrasée.

---

## 4. Découverte réseau

`allbudy/printers/discovery.py` balaye le sous-réseau local (par défaut le /24 de
l'interface de sortie, limité à /20) en ouvrant une connexion TCP sur les ports 7125
et 9999. Chaque machine qui répond est ensuite **identifiée par son propre
protocole** : `GET /printer/info` pour Moonraker, une trame `reqPrinterPara` pour le
LAN Creality. Une machine répondant sur les deux ports est présentée comme
Moonraker.

Aucun paquet ne sort du réseau local, et aucun service de découverte externe n'est
interrogé.
