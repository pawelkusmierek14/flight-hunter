"""Watch list re-exports.

The list itself lives in `config.py` now, so a fork can change the origin and
destinations without hunting through the scanner. Kept as a module because it
reads naturally at call sites and preserves backwards compatibility.
"""

from config import DEFAULT_BATCH, DESTINATIONS, ORIGIN

__all__ = ["DESTINATIONS", "ORIGIN", "DEFAULT_BATCH"]
