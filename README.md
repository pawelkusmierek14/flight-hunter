# Flight Hunter ✈️

Track flight prices from a chosen origin, keep a real price history per route,
and get alerted only when a fare drops **materially** below what that specific
route normally costs — not when it merely looks cheap.

The whole point is the baseline. A 99 EUR fare to Rome is unremarkable in
January and newsworthy in August; without history you cannot tell the two apart.
Flight Hunter stores every observation, so the alert threshold is the route's own
distribution: **median − 2σ**, and it refuses to judge at all until it has seen
the route at least five times.

- **No API key, no signup, no cost.** Prices come from Kiwi.com's public MCP
  server (`https://mcp.kiwi.com`), a JSON-RPC/SSE endpoint anyone can call.
- **Standard library only.** No dependencies to install, pin, or rot — it runs
  from a bare cron job.
- **One-way and round-trip are tracked as separate products**, each with its own
  history, because they are priced differently and mixing them corrupts the
  baseline.
- **Alert fatigue is treated as a bug**: a minimum saving, a statistical floor,
  a minimum sample count, and a 48 h dedupe window all have to agree first.

## Quick start

```bash
git clone https://github.com/pawelkusmierek14/flight-hunter.git
cd flight-hunter

# one live search, no database involved
python3 hunt.py find --to BCN --depart 09/12/2026 --flex 3

# round trip
python3 hunt.py find --to LIS --depart 15/10/2026 --return 22/10/2026

# offline sanity checks (no network, no writes)
python3 hunt.py selftest
python3 tests.py
```

To start collecting history:

```bash
python3 hunt.py scan --batch 10                    # one-ways
python3 hunt.py scan --batch 10 --round-trip       # round trips, 7-day stays
python3 hunt.py scan --batch 10 --dry-run          # don't write anything

python3 hunt.py report --top 30                    # cheapest known per route
python3 hunt.py report --trip-type roundtrip
python3 hunt.py stats
```

Until a route has ~5 observations (about two weeks at the default cadence) the
detector stays silent **by design**. That silence is the feature.

## How it works

```
kiwi.py     Kiwi MCP client (JSON-RPC over SSE) — the only module that uses the network
store.py    SQLite: observations, alerts, runs, plus the schema migration
anomaly.py  the detector: median + standard deviation, and its thresholds
notify.py   delivery: Telegram or a generic webhook
config.py   watch list, origin, thresholds, cadence — everything tunable
hunt.py     CLI: find / scan / report / stats / selftest
tests.py    unit tests (stdlib unittest, no network)
```

### An alert requires all four conditions

1. the price is **below the median** of that route's history, and
2. it is at least **12% below** that median, and
3. it sits at least **2σ** below it, and
4. the history has at least **5 samples** to compare against.

Median rather than mean, so one past flash sale cannot drag the baseline down and
make every later fare look average. The sample-recording step excludes the
just-recorded observation from its own comparison, otherwise the first sample
would always look perfectly typical.

Thresholds live in `config.py` (`MIN_SAMPLES`, `MIN_Z`, `MIN_PCT_BELOW`) and can
also be overridden per call to `anomaly.evaluate()`.

## Configuration

`config.py` holds the origin, the destination pool the scan rotates through, the
thresholds, batch size, booking horizon, and the alert dedupe window. Environment
overrides exist for the things you change per deployment:

| Variable | Purpose |
|---|---|
| `FLIGHT_HUNTER_ORIGIN` | Origin airport (default `WAW`) |
| `FLIGHT_HUNTER_DB` | Path to the SQLite file |
| `FLIGHT_HUNTER_MCP_URL` | Point at a different MCP server |
| `FLIGHT_HUNTER_WEBHOOK_URL` | Deliver alerts to an HTTP endpoint |
| `FLIGHT_HUNTER_ENV_FILE` | Explicit dotenv file to read credentials from |

### Alert delivery

Telegram is used when `TELEGRAM_BOT_TOKEN` + `TELEGRAM_HOME_CHANNEL` are set; a
webhook (`{"text": ...}`) is used when `FLIGHT_HUNTER_WEBHOOK_URL` is set.
Credentials are read from the environment first and from a dotenv file
second — nothing is written back to disk and no token is ever logged. A failed
delivery prints a warning and never aborts the scan.

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_HOME_CHANNEL=...
python3 hunt.py scan --batch 10
```

### Scheduling

Any scheduler works; hourly-to-4-hourly is a reasonable cadence. From cron:

```cron
0 */4 * * * cd /path/to/flight-hunter && /usr/bin/python3 hunt.py scan --batch 10 >> scan.log 2>&1
```

The scan rotates through the destination pool using the ordinal date as a seed, so
consecutive runs sample different routes and the whole list is covered over a day
without hammering any single route.

## Why Kiwi's MCP server

| Source | Status (as of 2026) |
|---|---|
| Kiwi public MCP | Open, no key, real bookable prices — **used here** |
| Amadeus Self-Service | Closed to new users July 2026, Enterprise only |
| Kiwi Tequila | Invite-only since 2024 |
| Travelpayouts / Aviasales | Free tier caches data for 48 h; Search API needs 50k MAU |
| Skyscanner / Duffel | Partnership or KYC, MAU thresholds |
| Scraping Google Flights | Grey area, breaks ToS — rejected deliberately |

## Testing

```bash
python3 tests.py        # 33 tests: detector boundaries, parsing, storage, CLI helpers
python3 hunt.py selftest  # per-module offline self-checks
```

Both run without network access and without touching a real database. The test
suite deliberately focuses on the boundaries that decide whether an alert is
trustworthy: too little history, an immaterial dip, a noisy baseline, a flat
baseline, a contaminated sample, and round-trip/one-way separation.

## Contributing

Issues and pull requests are welcome. Two things keep the bar:

- **Behaviour changes come with tests.** The detector's edge cases are the
  product; a change to a threshold or a comparison should show up in `tests.py`.
- **No new runtime dependencies** unless there is a strong argument — the
  zero-dependency property is what makes the tool deployable from a cron job.

Adding destinations is just editing `DESTINATIONS` in `config.py`; the scanner
picks them up automatically.

## License

MIT — see [LICENSE](LICENSE).
