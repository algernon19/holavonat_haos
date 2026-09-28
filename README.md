# Holavonat – Home Assistant integráció

Home Assistant egyedi integráció (HACS) vonattal ingázóknak. Két megadott vasútállomás között mindkét irányban mutatja a következő 3 közvetlen vonatot, és ahol elérhető, a valós idejű késést is.

A valós idejű késésadatok a [holavonat.is](https://holavonat.is) projektből származnak (forráskód: [github.com/holavonat/holavonatis](https://github.com/holavonat/holavonatis)).

> [!WARNING]
> **Felelősségkizárás**
>
> - Ez **nem hivatalos** integráció. Nem áll kapcsolatban a MÁV-val, a GYSEV-vel vagy a [holavonat.is](https://holavonat.is)-szel.
> - A menetrend a MÁV hivatalos GTFS adataiból jön, a késés a holavonat.is nem hivatalos adatfolyamából. Ez utóbbi egy nem dokumentált közlekedési API-ra épül.
> - **Az adatok pontatlanok, hiányosak vagy késve frissülők lehetnek, és bármelyik szolgáltatás bármikor megszűnhet.**
> - **Az integráció készítői nem vállalnak felelősséget az adatokért, azok pontosságáért, elérhetőségéért, sem az integráció használatából eredő semmilyen következményért.** A használat kizárólag a felhasználó saját felelősségére történik.
> - Utazás előtt mindig ellenőrizd a hivatalos szolgáltató (pl. [MÁV](https://www.mavcsoport.hu)) adatait.
>
> A beállítás első lépésében ezt a nyilatkozatot el kell fogadni.

## Hogyan működik

| Adat | Forrás | Frissítés |
|---|---|---|
| Menetrend (közvetlen vonatok, átszállás nélkül) | A MÁV hivatalos GTFS menetrendje (`gtfsMavMenetrend.zip`), a saját MÁV-hozzáféréseddel | Minden nap 01:30-kor. Sikertelen letöltésnél a régi menetrend marad, és óránként újrapróbálja. |
| Valós idejű késés és vágány | [holavonat.is](https://github.com/holavonat/holavonatis) adatfolyam (`train_data_v3.json`) | 60 másodpercenként |

A letöltött menetrend a Home Assistant `.storage/holavonat_gtfs.zip` fájljába kerül, így újraindításkor nem töltődik le újra. Memóriába csak a beállított állomásokat érintő járatok kerülnek.

A késés az indulási állomás neve és a menetrend szerinti indulási idő alapján kerül a vonathoz.

## MÁV GTFS hozzáférés

A menetrend letöltéséhez saját hozzáférés kell. A MÁV-tól a [GTFS igénybejelentő](https://www.mavcsoport.hu/gtfs-igenybejelento) oldalon lehet kérni. Egy letöltési linket és basic auth felhasználónevet/jelszót kapsz. Ezeket a Home Assistant beállításában kell megadni, csak a saját Home Assistantodban tárolódnak. **Ne tedd őket a tárolóba, és ne oszd meg őket.**

## Korlátok

- A késés csak akkor ismert, ha a vonat már közlekedik, vagyis elindult a kiinduló állomásáról. Ha a te állomásod a vonat kezdőállomása, a késés indulásig ismeretlen (`delay_min: null`), és a szenzor a menetrend szerinti időt mutatja.
- Csak átszállás nélküli vonatok jelennek meg. A MÁV által busszal pótolt járatok is megjelennek, ezeknél `replacement_bus: true`.
- A vágányszám csak valós idejű adattal ismert.

## Telepítés

### HACS (egyedi tároló)

[![Megnyitás HACS-ben](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=algernon19&repository=holavonat_haos&category=integration)

Vagy kézzel:

1. HACS → jobb felső menü → **Custom repositories**.
2. Repository: `https://github.com/algernon19/holavonat_haos`, Type: **Integration**.
3. Telepítsd a **Holavonat** integrációt, majd indítsd újra a Home Assistantot.

Minimum Home Assistant verzió: 2025.6.0.

### Kézi

Másold a `custom_components/holavonat` mappát a Home Assistant `config/custom_components/` mappájába, majd indítsd újra.

## Beállítás

**Beállítások → Eszközök és szolgáltatások → Integráció hozzáadása → Holavonat**

1. Fogadd el a felelősségkizárást.
2. Add meg a MÁV GTFS letöltési linkjét, a felhasználónevet és a jelszót. A menetrend ekkor letöltődik, így rögtön kiderül, jók-e az adatok.
3. Az integráció oldalán az **Útvonal hozzáadása** gombbal vegyél fel állomáspárokat: írd be a két állomás nevét (ékezet nélkül is megy), majd válaszd ki a pontos állomást.

Minden állomáspár mindkét irányt tartalmazza. Az A→B és B→A pár ugyanannak számít.

A link vagy a jelszó később az integráció menüjében az **Újrakonfigurálás** ponttal módosítható. Ha a MÁV elutasítja a hozzáférést, a Home Assistant értesítést küld, és ott megadhatod az újat.

## Entitások

Állomáspáronként 6 timestamp szenzor jön létre, a nevükben az iránnyal. Példa `Szeged ⇄ Budapest-Nyugati` párra:

| Név | Entitás | Jelentés |
|---|---|---|
| Szeged → Budapest-Nyugati 1. … 3. | `sensor.szeged_budapest_nyugati_1` … `_3` | A következő 3 vonat várható indulása Szegedről |
| Budapest-Nyugati → Szeged 1. … 3. | `sensor.budapest_nyugati_szeged_1` … `_3` | A következő 3 vonat várható indulása a Nyugatiból |

Az állapot a **várható** indulás: menetrend + késés, ha ismert. Attribútumok:

```yaml
train: IC 726 NAPFÉNY
headsign: Szeged
origin: Budapest-Nyugati
destination: Szeged
scheduled_departure: "2026-09-24T16:44:00+02:00"
expected_departure: "2026-09-24T16:44:00+02:00"
delay_min: 0            # null, ha nincs valós idejű adat
scheduled_arrival: "2026-09-24T19:16:00+02:00"
expected_arrival: "2026-09-24T19:16:00+02:00"
platform: "6"           # null, ha nincs valós idejű adat
current_stop: Vác       # hol jár most a vonat
realtime: true
replacement_bus: false  # true, ha a MÁV busszal pótolja a járatot
```

Ezen felül a **MÁV menetrend** eszközön van egy diagnosztikai szenzor (`Menetrend frissítve`), amely a legutóbbi sikeres letöltés idejét mutatja. Attribútumai: `feed_version`, `valid_until`.

## Példa kártyák

Egyszerű lista:

```yaml
type: entities
title: Szeged ⇄ Budapest-Nyugati
entities:
  - entity: sensor.szeged_budapest_nyugati_1
    format: time
  - entity: sensor.szeged_budapest_nyugati_2
    format: time
  - entity: sensor.szeged_budapest_nyugati_3
    format: time
  - entity: sensor.budapest_nyugati_szeged_1
    format: time
  - entity: sensor.budapest_nyugati_szeged_2
    format: time
  - entity: sensor.budapest_nyugati_szeged_3
    format: time
```

Késéssel és vágánnyal:

```yaml
type: markdown
content: >
  {% for dir in ['szeged_budapest_nyugati', 'budapest_nyugati_szeged'] %}
  **{{ state_attr('sensor.' ~ dir ~ '_1', 'origin') }} → {{ state_attr('sensor.' ~ dir ~ '_1', 'destination') }}**

  {% for i in range(1, 4) %}
  {% set e = 'sensor.' ~ dir ~ '_' ~ i %}
  {% if states(e) not in ['unknown', 'unavailable'] %}
  {{ as_timestamp(state_attr(e, 'scheduled_departure')) | timestamp_custom('%H:%M') }}
  {{ state_attr(e, 'train') }}
  {% if state_attr(e, 'delay_min') %}**+{{ state_attr(e, 'delay_min') }} perc**{% endif %}
  {% if state_attr(e, 'platform') %}· {{ state_attr(e, 'platform') }}. vágány{% endif %}

  {% endif %}
  {% endfor %}
  {% endfor %}

  Menetrend: MÁV GTFS · Késés: holavonat.is
```

## Hibakeresés

Részletes napló a `configuration.yaml` fájlban:

```yaml
logger:
  default: warning
  logs:
    custom_components.holavonat: debug
```

Mit érdemes ellenőrizni:

| Tünet | Hol nézd |
|---|---|
| Nem frissül a menetrend | **MÁV menetrend → Menetrend frissítve** szenzor: a legutóbbi sikeres letöltés ideje, `feed_version`, `valid_until`. Sikertelen letöltésnél a naplóban `MÁV GTFS update failed` figyelmeztetés van. |
| Hozzáférési hiba | A Home Assistant értesítést küld „újrahitelesítés szükséges” szöveggel. |
| Nincs késés | A `realtime` attribútum `false`. Ez normális, ha a vonat még nem indult el a kezdőállomásáról. Ha minden vonatnál `false`, a naplóban nézd a `holavonat.is` hibákat. |
| `unknown` állapot | Nincs több közvetlen vonat a következő héten, vagy az állomás kikerült a menetrendből. |

Hibabejelentés: [GitHub issues](https://github.com/algernon19/holavonat_haos/issues). **Naplórészletet csak úgy csatolj, hogy abból a letöltési link és a jelszó ki legyen törölve.**

## Adatforrások és felhasználási feltételek

- **MÁV GTFS:** a menetrendet minden felhasználó a saját MÁV-hozzáférésével tölti le. Az integráció nem tartalmaz és nem továbbít menetrendi adatot. A felhasználási feltételeket a MÁV adja meg a hozzáféréssel együtt.
- **holavonat.is:** a [README](https://github.com/holavonat/holavonatis) szerint az adatfolyam nyilvánosan használható, de semmilyen garancia nincs rá.

## Köszönetnyilvánítás

- [holavonat.is](https://holavonat.is), forráskód: [github.com/holavonat/holavonatis](https://github.com/holavonat/holavonatis). Az ő nyilvános adatfolyamuk adja a valós idejű késést. Az integráció nem tartalmazza a kódjukat, és nem áll kapcsolatban a projekttel.
- [holavonat.hu](https://holavonat.hu), az első nyílt forráskódú magyar vonatkövető, amelyet a holavonat.is is elődjének tekint.

---

## English summary

Unofficial Home Assistant custom integration (HACS) for train commuters. For a pair of stations it shows the next 3 direct trains in both directions, with realtime delay when available.

- Timetable: the official MÁV GTFS timetable, downloaded daily at 01:30 with your own MÁV access (download link and basic auth, entered in the Home Assistant UI).
- Realtime delay: the unofficial feed of [holavonat.is](https://holavonat.is) (source code: [github.com/holavonat/holavonatis](https://github.com/holavonat/holavonatis)). Thanks to the holavonat.is project for making it public.

**Disclaimer:** This project is not affiliated with MÁV, GYSEV or holavonat.is. The data may be inaccurate, incomplete, delayed or unavailable at any time. **The authors accept no responsibility for the data or for any consequence of using this integration. Use it at your own risk** and always verify with the official operator.

## License

[MIT](LICENSE) © 2026 algernon19. The holavonat.is project is licensed separately under AGPL-3.0. This integration does not contain its code, it only reads its public data feed.
