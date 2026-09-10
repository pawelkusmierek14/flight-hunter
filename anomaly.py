"""Anomaly detection over stored price history.

The whole point of keeping history: decide whether today's price is actually
cheap *for this route* or just cheap in absolute terms. A 99 EUR fare to Rome
is boring in January and remarkable in August.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict

# Minimum samples before a route's history is trustworthy enough to judge.
MIN_SAMPLES = 5
# How far below the baseline a price must sit to count as an anomaly.
MIN_Z = 2.0
# And it must also be a material saving, not a rounding wobble.
MIN_PCT_BELOW = 0.12


@dataclass
class Anomaly:
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    price: float
    currency: str
    baseline: float
    z_score: float
    pct_below: float
    samples: int
    booking_url: str | None = None
    routing: str | None = None
    notified: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def baseline(prices: list[float], method: str = "median") -> tuple[float, float]:
    """Return (central tendency, spread). Median is robust to past flash sales."""
    if method == "mean":
        centre = statistics.fmean(prices)
    else:
        centre = statistics.median(prices)
    spread = statistics.pstdev(prices) if len(prices) > 1 else 0.0
    return centre, spread


def evaluate(
    prices: list[float],
    current: float,
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    currency: str = "EUR",
    booking_url: str | None = None,
    routing: str | None = None,
    min_samples: int = MIN_SAMPLES,
    min_z: float = MIN_Z,
    min_pct_below: float = MIN_PCT_BELOW,
    method: str = "median",
) -> Anomaly | None:
    """Judge `current` against `prices`. Returns an Anomaly if it qualifies.

    Filters out the just-recorded observation: comparing a price against a set
    that already contains it dilutes the signal, and for the very first sample
    it would make everything look perfectly average.
    """
    history = [p for p in prices if p > 0 and p != current]
    if len(history) < min_samples:
        return None

    centre, spread = baseline(history, method)
    if centre <= 0:
        return None

    pct_below = (centre - current) / centre
    z = (centre - current) / spread if spread > 0 else (float("inf") if current < centre else 0.0)

    if current >= centre or pct_below < min_pct_below:
        return None
    # With a flat history, spread is 0 and any real drop is a genuine anomaly.
    if spread > 0 and z < min_z:
        return None

    return Anomaly(
        origin=origin, destination=destination, depart_date=depart_date,
        return_date=return_date, price=current, currency=currency,
        baseline=round(centre, 2), z_score=round(z, 2) if z != float("inf") else 999.0,
        pct_below=round(pct_below, 4), samples=len(history),
        booking_url=booking_url, routing=routing,
    )


def format_alert(a: Anomaly) -> str:
    """Human-readable alert body (Markdown, Telegram-friendly)."""
    trip = f"{a.origin} → {a.destination}"
    dates = a.depart_date if not a.return_date else f"{a.depart_date} – {a.return_date}"
    lines = [
        f"*Tani lot: {trip}*",
        f"*{a.price:.0f} {a.currency}* — {a.pct_below * 100:.0f}% poniżej typowej ceny",
        f"Data: {dates}",
        f"Typowo: {a.baseline:.0f} {a.currency} (z {a.samples} obserwacji)",
    ]
    if a.routing:
        lines.append(f"Trasa: {a.routing}")
    if a.booking_url:
        lines.append(f"[Rezerwuj]({a.booking_url})")
    return "\n".join(lines)
