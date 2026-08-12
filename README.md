# Betting sandbox – Playwright, férové kurzy a +EV (vzdelávací projekt)

Lokálny tréningový projekt na tri veci, ktoré tvoria jadro „stávkového bota“:

1. **scraping cez Playwright** (page objects, robustné selektory, čítanie live dát),
2. **modelovanie férového kurzu** (Poisson model na over/under 2.5, marža, implied probability),
3. **detekcia hodnoty (+EV)** a **staking/backtest**.

Všetko beží proti **dvom lokálnym mock stránkam**, ktoré si projekt sám spúšťa
(`mocksite/`): simulovaný livescore portál a simulovaná stávková kancelária.

## Čo tento projekt zámerne NEROBÍ

- **Neobsahuje anti-detekčné techniky.** Žiadne maskovanie automatizácie, spoofing
  fingerprintu ani „ľudské“ pohyby myši s cieľom obísť anti-bot ochranu. Obchádzanie
  týchto ochrán je porušenie ToS Flashscore/Sofascore aj Tipsportu/Fortuny.
- **Nepripája sa na žiadnu reálnu stávkovú kanceláriu**, nemá prihlasovacie údaje,
  nemanipuluje s reálnym účtom ani peniazmi. Automatizované ovládanie účtu v
  licencovanej stávkovej kancelárii je v rozpore s jej podmienkami a v regulovanom
  prostredí to môže mať právne dôsledky.
- Modul `sandbox_bot/pacing.py` robí **rate limiting**, nie evasion: rozostupy medzi
  requestmi existujú preto, aby scraper cieľ nebombardoval v tesnej slučke.

Modelovacia a EV časť je plne prenositeľná na legálne dáta (napr. verejné dátové sady,
oficiálne API s licenciou, alebo dáta, na ktorých scraping máš povolenie).

## Inštalácia

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Spustenie

```bash
# 1) mock stránky (nechaj bežať v samostatnom termináli)
python -m sandbox_bot serve          # http://127.0.0.1:8000

# 2) predzápasová analýza – vyscrapuje H2H a vypíše férové kurzy (headed prehliadač)
python -m sandbox_bot pregame

# 3) live monitor – hľadá +EV, pripraví tiket a čaká na tvoje potvrdenie
python -m sandbox_bot monitor --rounds 10

# 4) offline backtest bez prehliadača
python -m sandbox_bot backtest --staking kelly --threshold 0.12

# testy
pytest -q
```

## Rozpis zápasov: reálny alebo syntetický

Rozpis (kto s kým a o koľkej) sa dá načítať z otvorených dát; **live štatistiky
a všetky kurzy sú vždy simulované lokálne** – nescrapujú sa zo žiadnej kancelárie.

```bash
# reálny rozpis z footballdata.io (vyžaduje API kľúč, viď nižšie)
python -m sandbox_bot serve --fixtures footballdata

# reálny rozpis z OpenLigaDB (otvorené dáta, bez kľúča a bez registrácie)
python -m sandbox_bot serve --fixtures openliga --league bl1

# vlastný JSON so zápasmi
python -m sandbox_bot serve --fixtures /cesta/k/zapasy.json

# offline syntetický rozpis (default)
python -m sandbox_bot serve --fixtures synthetic

# čo je načítané a v akom stave to práve je
python -m sandbox_bot fixtures --fixtures openliga --date 2026-08-28
```

Prepínače majú aj ekvivalent v premenných prostredia: `MOCK_FIXTURES`,
`MOCK_LEAGUE`, `MOCK_DATE`, `MOCK_CLOCK`, `MOCK_MINUTES_PER_SECOND`,
`MOCK_REFRESH_S`. Stiahnuté odpovede sa cachujú do `.cache/`.

### footballdata.io

Kľúč sa **nikdy nepíše do kódu**. Najpohodlnejšie je skopírovať `.env.example`
na `.env` v koreni projektu (je v `.gitignore`) a doplniť riadok:

```
FOOTBALLDATA_API_KEY=fd_...
```

Alternatívne premenná prostredia, ktorá má vždy prednosť pred `.env`:

```powershell
# Windows PowerShell
$env:FOOTBALLDATA_API_KEY = "fd_..."
python -m sandbox_bot fixtures --fixtures footballdata
```

```bash
# Linux / macOS
export FOOTBALLDATA_API_KEY=fd_...
python -m sandbox_bot serve --fixtures footballdata --league "Premier League"
python -m sandbox_bot serve --fixtures footballdata --league 9   # alebo league_id
python -m sandbox_bot fixtures --fixtures footballdata --date 2026-08-13
```

Z API sa berú len **názvy tímov, čas výkopu, súťaž, stav zápasu, prematch xG
a H2H**; minútové live štatistiky aj kurzy si sandbox naďalej generuje sám.
Odpovede sú cachované (dnešný rozpis 2 min, H2H 24 h) a H2H sa ťahá najviac pre
20 zápasov dňa, aby sa zbytočne nemíňal limit. Bežiaci server si rozpis sám
obnoví každých `MOCK_REFRESH_S` sekúnd (default 300, `0` vypne).
Už odohrané / zrušené / odložené zápasy sa do rozpisu neberú.

Ak Windows hlási `CERTIFICATE_VERIFY_FAILED`, aktualizuj závislosť:

```powershell
python -m pip install --upgrade certifi
```

Pri firemnom proxy treba namiesto vypínania SSL overenia nastaviť PEM certifikát:
`$env:FOOTBALLDATA_CA_BUNDLE = "C:\cesta\k\proxy.pem"`.

### Režim hodín

- `--clock real` (default) – zápas je `live` iba v reálnom okne od výkopu do
  výkop + 105 min. Pred výkopom je `scheduled`, potom `finished`. `/book/live`
  ponúka **len práve hrané** zápasy a monitor sleduje len tie; keď sa nehrá nič,
  stránka aj monitor korektne zobrazia prázdny stav.
- `--clock demo` – všetky zápasy dňa začnú pri štarte servera a bežia zrýchlene
  (`MOCK_MINUTES_PER_SECOND`, default 3 → zápas trvá ~30 s). Vhodné na nácvik,
  keď sa práve nič nehrá.

Formát vlastného JSON súboru:

```json
[
  {
    "match_id": "m1",
    "home": "Tím A",
    "away": "Tím B",
    "kickoff_utc": "2026-08-12T17:00:00Z",
    "competition": "Liga",
    "h2h": [
      {"played_on": "2025-04-01", "home": "Tím A", "away": "Tím B",
       "home_goals": 2, "away_goals": 1}
    ]
  }
]
```

Keď na dnešný deň v danej lige nie sú zápasy, program to povie a nič potichu
nenahradí – `fixtures` vypíše najbližšie hracie dni.

## Lokálna databáza a ratingy

Každý stiahnutý zápas z `footballdata.io` alebo OpenLigaDB sa priebežne ukladá
do SQLite databázy. Predvolená cesta je `data/sandbox.sqlite3`; inú cestu možno
nastaviť premennou prostredia `MOCK_DB_PATH`. Zápisy sú zámerne nepovinné:
problém so súborom alebo zámkom databázy nezhodí lokálny server.

Na spätné stiahnutie posledných dní použite napríklad:

```bash
python -m sandbox_bot backfill --fixtures footballdata --days 30
```

Príkaz rešpektuje existujúcu diskovú cache poskytovateľa. Uložené dokončené
výsledky sa dajú zobraziť podľa ligy:

```bash
python -m sandbox_bot ratings
python -m sandbox_bot ratings --league 9
```

`ratings` počíta ligový priemer gólov a silu útoku/obrany doma aj vonku.
Tieto hodnoty tvoria Poissonov odhad oddelene pre každú súťaž. Pri malej vzorke
(menej ako tri dokončené zápasy ligy alebo tímu) sa použije konzervatívny
ligový alebo providerový xG odhad, nie nespoľahlivá sila tímu.

## Štruktúra

```
mocksite/           lokálne simulované stránky
  fixtures_source.py zdroje rozpisu: footballdata.io / OpenLigaDB / vlastný JSON / syntetický
  store.py          SQLite úložisko stiahnutých líg, tímov a zápasov
  data.py           aktuálne načítaný rozpis (FIXTURES) + reload
  simulator.py      stav zápasu (scheduled/live/finished), časová os + kurzy s maržou
  app.py            Flask: /livescore a /book
sandbox_bot/
  config.py         VŠETKY konfiguračné premenné (URL, prah, vklad, headless…)
  models.py         dátové triedy (H2HResult, LiveStats, MarketQuote, ValueSignal)
  browser.py        Playwright session – dva nezávislé kontexty (livescore + kancelária)
  pages/            page objects; selektory sú v jednom slovníku na vrchu triedy
  odds.py           Poisson model, férový kurz, marža, edge, Kelly
  ratings.py        Poisson ratingy výsledkov podľa súťaže
  analysis.py       predzápasový model z H2H
  monitor.py        live slučka, porovnávač, príprava tiketu + potvrdenie človekom
  backtest.py       offline vyhodnotenie pravidla nad simulovanými zápasmi
tests/              unit testy modelu + e2e Playwright testy proti mock stránke
```

## Ako to počíta férový kurz

1. **λ (očakávané góly)** – priemer gólov z H2H sa „stiahne“ k ligovému prioru
   (`expected_total_goals`), aby 8 zápasov neurčovalo celý model:
   `λ = trust · H2H_priemer + (1 − trust) · prior`, kde `trust` rastie s počtom vzoriek.
2. **Live úprava** – λ sa škáluje na zostávajúce minúty a mierne koriguje podľa
   objemu striel (`live_intensity`).
3. **Pravdepodobnosť** – `P(celkovo > 2.5)` z Poissonovho rozdelenia, podmienené už
   strelenými gólmi (`over_probability`).
4. **Férový kurz** = `1 / P`. Kurz kancelárie obsahuje maržu, preto je systematicky nižší;
   `bookmaker_margin()` ti ukáže overround dvojcestného trhu.
5. **Edge** = `kurz_kancelárie / férový_kurz − 1`. Signál sa berie ako hodnotný pri
   `edge ≥ 10 %` (`SETTINGS.value_threshold`) **a zároveň** kladnom EV.

Pozor na interpretáciu: kladné EV podľa vlastného modelu neznamená kladné EV v realite.
Backtest existuje presne preto, aby si videl, že pri zle nastavenom modeli/thresholde
ide bankroll dole (skús `--staking kelly --threshold 0.15`).

## Bezpečnostný mechanizmus pri podaní tiketu

`BookmakerPage.stage_bet()` spraví len tri veci: klikne na kurz, vyplní vklad a
**prejde myšou nad tlačidlo podania + spraví screenshot**. Nič nepodá.
Podanie je samostatná metóda `confirm_submit()`, ktorú `monitor.py` zavolá len ak
v termináli potvrdíš `y`. Test `test_stage_bet_does_not_submit` toto správanie stráži.

## Ako sa robia selektory (a čo si budeš dopĺňať)

Selektory sú v každom page objecte na jednom mieste – slovník `SELECTORS`.
Keď sa markup zmení, upravuješ len ten slovník, nie logiku.

**Poradie preferencií (od najstabilnejšieho po najkrehkejšie):**

1. **Atribút určený na testovanie** – `[data-testid="odds-button"]`.
   Mock stránka ich má; reálne weby zriedka.
2. **Sémantické role a text** – Playwright `get_by_role("button", name="Podať tiket")`,
   `get_by_label("Vklad")`. Prežije zmenu CSS tried.
3. **Stabilný atribút s dátami** – `[data-market="over_2.5"]`, `[data-match-id="m1"]`.
4. **Štruktúra + kotva textom** – `page.locator(".event", has_text="Slovan")
   .locator("button", has_text="Viac ako 2.5")`.
5. **Až nakoniec CSS trieda / XPath podľa pozície** – `div:nth-child(3) > button`.
   Toto sa rozbije pri prvom redizajne; obfuskované triedy typu `.css-1x2y3z`
   sa generujú pri každom builde nanovo.

**Praktické pravidlá:**

- **Kotvi sa na kontajner, potom hľadaj vnútri.** Najprv nájdi kartu zápasu
  (`[data-testid="offer-card"][data-match-id="m1"]`), až v nej tlačidlo. Vyhneš sa tomu,
  že klikneš na kurz iného zápasu – čo je pri live kurzoch najdrahšia chyba.
- **Nikdy nespoliehaj na poradie prvkov** v live ponuke; zápasy sa preraďujú.
- **Hodnoty čítaj z atribútov, nie z textu**, ak sa dá: `data-odds="2.35"` je
  spoľahlivejšie ako parsovanie `„Viac ako 2.5 2.35“`.
- **Čakaj na stav, nie na čas.** `wait_for_selector('[data-empty="false"]')` namiesto
  `sleep(2)`. Pozri `select_market()`.
- **Kurz over-verifikuj pred akciou.** V `MarketQuote` sa nesie kurz aj minúta; pred
  potvrdením porovnaj, či sa medzitým nezmenil (live kurzy sa menia v sekundách).
- Ako selektor nájsť: v DevTools pravý klik na prvok → Copy → Copy selector ti dá
  krehký `nth-child` reťazec. Lepšie je pozrieť si atribúty prvku a napísať vlastný,
  a overiť si ho v konzole cez `document.querySelectorAll('...')`.

Príklad, ako by vyzeral vlastný page object pre inú štruktúru:

```python
class MyBookPage(BookmakerPage):
    SELECTORS = {
        **BookmakerPage.SELECTORS,
        "offer_card": "div.event-row",
        "odds_button_for": 'div.event-row[data-event="{match_id}"] button[data-bet="{market}"]',
        "stake_input": "input[name='stake']",
        "submit_button": "button.betslip-submit",
    }
```

## Kam sa dá projekt posunúť legálne

- vymeniť Poissona za bivariate Poisson / Dixon-Coles (korelácia gólov),
- kalibrácia modelu (Brier score, log-loss) namiesto sledovania ROI na 4 zápasoch,
- closing line value ako metrika kvality modelu,
- napojenie na dáta, na ktoré máš licenciu alebo povolenie (open datasets, vlastný zber).
