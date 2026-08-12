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

Rýchlosť simulácie sa dá meniť: `MOCK_MINUTES_PER_SECOND=6 python -m sandbox_bot serve`
(default 3 minúty zápasu za sekundu, takže zápas trvá ~30 s).

## Štruktúra

```
mocksite/           lokálne simulované stránky
  data.py           tímy, zápasy, H2H história (fixný seed)
  simulator.py      celá 90-minútová časová os zápasu + kurzy s maržou
  app.py            Flask: /livescore a /book
sandbox_bot/
  config.py         VŠETKY konfiguračné premenné (URL, prah, vklad, headless…)
  models.py         dátové triedy (H2HResult, LiveStats, MarketQuote, ValueSignal)
  browser.py        Playwright session – dva nezávislé kontexty (livescore + kancelária)
  pages/            page objects; selektory sú v jednom slovníku na vrchu triedy
  odds.py           Poisson model, férový kurz, marža, edge, Kelly
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
