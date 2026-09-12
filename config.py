"""Configuration for Flight Hunter.

Everything tunable lives here so the scanner can be pointed at another origin,
another watch list, or another alert channel without touching the logic.

No secrets in this file, ever — the Telegram token is read from the environment
(or from a Hermes `.env`) by `notify.py`.
"""

from __future__ import annotations

import os

# ── Watch list ────────────────────────────────────────────────────────────────

# Warsaw Chopin. Kiwi resolves city names too, but being explicit keeps results
# comparable across runs — a route whose airport changes is not the same route.
ORIGIN = os.environ.get("FLIGHT_HUNTER_ORIGIN", "WAW")

# The pool the scan rotates through. Deliberately mixed: short-haul weekend
# cities (where cheap fares actually happen) plus long-haul wishlist targets.
DESTINATIONS = [
    # ── short haul / weekend ──
    "BCN",  # Barcelona
    "LIS",  # Lisbon
    "ROM",  # Rome
    "MIL",  # Milan
    "ATH",  # Athens
    "PMI",  # Palma de Mallorca
    "VLC",  # Valencia
    "OPO",  # Porto
    "CTA",  # Catania
    "NAP",  # Naples
    "TIA",  # Tirana
    "SKG",  # Thessaloniki
    "IST",  # Istanbul
    "TLL",  # Tallinn
    "REK",  # Reykjavik
    "DBV",  # Dubrovnik
    "SPU",  # Split
    "LCA",  # Larnaca
    "AGP",  # Malaga
    "FAO",  # Faro
    # ── long haul ──
    "NYC",  # New York
    "BKK",  # Bangkok
    "DPS",  # Bali
    "HKT",  # Phuket
    "DXB",  # Dubai
    "TBS",  # Tbilisi
    "EVN",  # Yerevan
    "JFK",  # New York JFK (explicit airport)
    "MIA",  # Miami
    "DEL",  # Delhi
    "CMB",  # Colombo
    "MEX",  # Mexico City
    "GRU",  # Sao Paulo
    "EZE",  # Buenos Aires
    "HND",  # Tokyo Haneda
    "SIN",  # Singapore
    "KUL",  # Kuala Lumpur
    "HAN",  # Hanoi
    "SGN",  # Ho Chi Minh
    "CAI",  # Cairo
]

# ── Anomaly thresholds ────────────────────────────────────────────────────────

# Minimum samples before a route's history is trustworthy enough to judge.
MIN_SAMPLES = 5
# How far below the baseline a price must sit to count as an anomaly.
MIN_Z = 2.0
# And it must also be a material saving, not a rounding wobble.
MIN_PCT_BELOW = 0.12
# Median, not mean — a single past flash sale must not drag the baseline down.
BASELINE_METHOD = "median"

# ── Scanning ──────────────────────────────────────────────────────────────────

# How many routes one scan samples.
DEFAULT_BATCH = 6
# Booking horizon to spread departure dates across, in days.
DEFAULT_HORIZON = 120
# Days between departure and return when monitoring round trips.
DEFAULT_TRIP_LENGTH = 7
# Days of flexibility requested from the search API (departureDateFlexDays).
FLEX_DAYS = 3
# Don't alert on the same route shape twice inside this window.
ALERT_DEDUPE_HOURS = 48

# ── Storage ───────────────────────────────────────────────────────────────────

DB_NAME = "flights.db"

# ── Kiwi MCP endpoint ─────────────────────────────────────────────────────────

MCP_URL = os.environ.get("FLIGHT_HUNTER_MCP_URL", "https://mcp.kiwi.com")
MCP_TIMEOUT = 90
