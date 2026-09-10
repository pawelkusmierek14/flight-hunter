"""Watch list and tunables for Flight Hunter.

Origin is Warsaw only (per user's choice). DESTINATIONS is the pool the scan
rotates through — deliberately mixed: short-haul weekend cities (where cheap
fares actually happen) plus a few long-haul wishlist targets.
"""

# Warsaw Chopin. Kiwi resolves city names too, but being explicit keeps results
# comparable across runs — a route whose airport changes is not the same route.
ORIGIN = "WAW"

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
