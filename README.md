# Flight Hunter ✈️

Agent do łowienia tanich lotów z Warszawy. Wykrywa **anomalie cenowe** —
realne obniżki względem historii trasy, nie „tanio mi się wydaje".

## Skąd dane

Publiczny MCP server Kiwi.com: `https://mcp.kiwi.com`

- **Bez klucza API, bez rejestracji, bez kosztów.**
- Jedno narzędzie: `search-flight` — przelot w jedną stronę lub powrotny,
  ±3 dni elastyczności, pasażerowie, klasa.
- Zwraca prawdziwe ceny, czasy, przewoźników, przesiadki i **link do rezerwacji**.

### Dlaczego nie inne źródła (stan: wrzesień 2026)

| Źródło | Status |
|---|---|
| Amadeus Self-Service | Zamknięte 17.07.2026 — tylko Enterprise |
| Kiwi Tequila API | Od 2024 invite-only |
| Travelpayouts / Aviasales Data API | Darmowe, ale dane cache'owane 48h; Search API wymaga 50k MAU |
| Skyscanner / Duffel | Partnerstwo / KYC, progi MAU |
| Scraping Google Flights | Szara strefa, łamie ToS — świadomie odrzucone |

## Struktura

```
kiwi.py       klient MCP (JSON-RPC 2.0 po SSE) — jedyne miejsce z siecią
store.py      SQLite: observations, alerts, runs
anomaly.py    detekcja anomalii (mediana + odchylenie standardowe)
notify.py     wysyłka na Telegram (czyta TELEGRAM_* z .env Hermesa)
routes.py     lista kierunków i lotnisko startowe
hunt.py       CLI
flights.db    historia obserwacji (tworzy się sama)
```

## Użycie

```bash
cd /opt/data/flight-hunter

# Tryb na żądanie — jedna trasa, wyniki na żywo
python3 hunt.py find --to BKK --depart 09/12/2026 --flex 3
python3 hunt.py find --to LIS --depart 15/10/2026 --return 22/10/2026   # z powrotem
python3 hunt.py find --to Tokio --depart 01/03/2027 --json   # nazwy miast działają

# Zbieranie historii + detekcja anomalii (to robi cron)
python3 hunt.py scan --batch 10                              # w jedną stronę
python3 hunt.py scan --batch 10 --round-trip --trip-length 7 # tam i z powrotem
python3 hunt.py scan --batch 10 --dry-run                    # bez zapisu do bazy

# Co wiemy
python3 hunt.py report --top 30
python3 hunt.py stats
```

## Jak działa detekcja

Dla każdej kombinacji **trasy + data wylotu + typu podróży** trzymamy historię cen.
**Loty w jedną stronę i powrotne mają rozdzielne historie** — to różne produkty
o różnych cenach, więc mieszanie ich zafałszowałoby baseline.

Alert leci, gdy spełnione są **wszystkie** warunki:

- cena jest **poniżej mediany** historii, oraz
- spadek ≥ **12%** (materialna oszczędność, nie drgnięcie o kilka euro), oraz
- odchylenie ≥ **2σ** (statystycznie wyjątkowe), oraz
- mamy ≥ **5 próbek** historii (inaczej nie zgadujemy).

Mediana, nie średnia — pojedyncza flash-sale z przeszłości nie zaniży baseline'u.

Progi siedzą w `anomaly.py`: `MIN_SAMPLES`, `MIN_Z`, `MIN_PCT_BELOW`.

Dedupe: ta sama trasa + data nie zaalarmuje dwa razy w ciągu 48h.

## Automatyzacja

Cron `flight-hunter-scan` (job `6c1f2f02e829`) — **co 4 godziny**, dwa przebiegi:
6 tras w jedną stronę + 6 tras powrotnych (wylot + 7 dni). Razem ~12 tras na cykl.
Rotuje po liście z `routes.py`, więc w ciągu doby sprawdza ~70 tras.
Alerty idą na Telegram; raport z każdego runa też.

**Uwaga:** dopóki baza nie zbierze ~5 obserwacji na trasę (realnie ~2 tygodnie),
detektor milczy z założenia. To nie błąd — to ochrona przed fałszywymi alarmami.

## Kierunki

`routes.py` → `DESTINATIONS`. Mieszanka krótkich weekendowych (BCN, LIS, ROM,
ATH, TIA…) i długodystansowych (BKK, DPS, HND, SIN, NYC, GRU…).
Dodaj własne — skaner sam je włączy do rotacji.

## Rozwiązywanie problemów

```bash
python3 hunt.py stats          # czy baza rośnie
python3 hunt.py scan --batch 3 --dry-run   # czy sieć działa
```

Alert nie przyszedł na Telegram? Sprawdź `python3 -c "import notify; print(notify.telegram_available())"`.
Brak historii? Sprawdź, czy cron ma status `scheduled` w `cronjob_manage action='list'`.
