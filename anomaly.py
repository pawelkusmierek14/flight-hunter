"""Anomaly detection over stored price history.

The whole point of keeping history: decide whether today's price is actually
cheap *for this route* or just cheap in absolute terms. A 99 EUR fare to Rome
is boring in January and remarkable in August.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

from config import BASELINE_METHOD, MIN_PCT_BELOW, MIN_SAMPLES, MIN_Z


@dataclass
class Anomaly:
    """A price that is materially and statistically below its own history."""

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


def baseline(prices: list[float], method: str = BASELINE_METHOD) -> tuple[float, float]:
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
    method: str = BASELINE_METHOD,
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


def _self_check() -> None:
    """Boundary checks on the detector itself — the core thesis of this project."""
    # Not enough history: silence beats a guess.
    assert evaluate([100.0, 100.0], 50.0, origin="WAW", destination="BCN",
                    depart_date="01/01/2027") is None

    # A material drop against a stable history.
    hit = evaluate([100.0] * 6, 70.0, origin="WAW", destination="BCN",
                   depart_date="01/01/2027")
    assert hit is not None and hit.pct_below == 0.3 and hit.samples == 6

    # Below the percentage threshold: 5% off is not news.
    assert evaluate([100.0] * 6, 95.0, origin="WAW", destination="BCN",
                    depart_date="01/01/2027") is None

    # A price above the baseline is never an anomaly, however noisy the history.
    assert evaluate([100.0, 110.0, 90.0, 105.0, 95.0], 130.0,
                    origin="WAW", destination="BCN", depart_date="01/01/2027") is None

    # Flat history, real drop → spread 0 must not block the alert.
    assert evaluate([200.0] * 5, 150.0, origin="WAW", destination="BCN",
                    depart_date="01/01/2027") is not None

    # The just-recorded sample must not dilute its own comparison.
    noisy = [100.0] * 5 + [80.0]
    assert evaluate(noisy, 80.0, origin="WAW", destination="BCN",
                    depart_date="01/01/2027") is not None

    # Median resists a past flash sale that would drag a mean down.
    centre, _ = baseline([100.0, 100.0, 100.0, 100.0, 10.0])
    assert centre == 100.0
    assert round(baseline([100.0, 100.0, 100.0, 100.0, 10.0], "mean")[0], 1) == 82.0

    # Zero/negative prices are junk, not data.
    assert evaluate([0.0, -5.0, 100.0], 50.0, origin="WAW", destination="BCN",
                    depart_date="01/01/2027") is None


if __name__ == "__main__":
    _self_check()
    print("anomaly.py self-check ok")
