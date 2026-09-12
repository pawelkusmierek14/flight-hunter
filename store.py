"""SQLite storage for flight price observations.

Schema is deliberately small: one row per (route, date, trip-shape) per
sampling run, so the anomaly detector can compare against a real history
instead of guessing.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_NAME

DB_PATH = os.environ.get(
    "FLIGHT_HUNTER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_NAME),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT    NOT NULL,
    origin        TEXT    NOT NULL,
    destination   TEXT    NOT NULL,
    depart_date   TEXT    NOT NULL,
    return_date   TEXT,
    trip_type     TEXT    NOT NULL,
    price         REAL    NOT NULL,
    currency      TEXT    NOT NULL,
    carrier       TEXT,
    stops         INTEGER,
    duration_s    INTEGER,
    booking_url   TEXT,
    routing       TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_route
    ON observations (origin, destination, trip_type, depart_date, observed_at);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at       TEXT    NOT NULL,
    origin        TEXT    NOT NULL,
    destination   TEXT    NOT NULL,
    depart_date   TEXT    NOT NULL,
    return_date   TEXT,
    price         REAL    NOT NULL,
    baseline      REAL    NOT NULL,
    z_score       REAL    NOT NULL,
    pct_below     REAL    NOT NULL,
    booking_url   TEXT,
    trip_type     TEXT,
    notified      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    routes_tried  INTEGER NOT NULL DEFAULT 0,
    routes_ok     INTEGER NOT NULL DEFAULT 0,
    errors        TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: str = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add columns that older databases predate. Idempotent.

    An existing install keeps its price history; the schema only ever grows.
    Returns the list of columns actually added, for reporting.
    """
    added: list[str] = []
    wanted = {
        "observations": {"return_date": "TEXT", "trip_type": "TEXT",
                         "carrier": "TEXT", "stops": "INTEGER",
                         "duration_s": "INTEGER", "booking_url": "TEXT",
                         "routing": "TEXT"},
        "alerts": {"trip_type": "TEXT"},
    }
    for table, columns in wanted.items():
        existing = {row["name"] for row in _columns(conn, table)}
        if not existing:
            continue
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
    return added


def record(path: str, rows: list[dict]) -> int:
    """Insert observation rows. Each row needs the columns named in SCHEMA."""
    if not rows:
        return 0
    cols = (
        "observed_at origin destination depart_date return_date trip_type price "
        "currency carrier stops duration_s booking_url routing"
    ).split()
    sql = f"INSERT INTO observations ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    with connect(path) as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def history(path: str, origin: str, destination: str, trip_type: str, depart_date: str,
            limit: int = 200) -> list[sqlite3.Row]:
    """Chronological price history for one exact route shape."""
    with connect(path) as conn:
        return conn.execute(
            """
            SELECT observed_at, price, currency FROM observations
             WHERE origin=? AND destination=? AND trip_type=? AND depart_date=?
             ORDER BY observed_at DESC LIMIT ?
            """,
            (origin, destination, trip_type, depart_date, limit),
        ).fetchall()


def cheapest_ever(path: str, origin: str, destination: str, trip_type: str,
                  depart_date: str) -> sqlite3.Row | None:
    with connect(path) as conn:
        return conn.execute(
            """
            SELECT MIN(price) AS price, currency FROM observations
             WHERE origin=? AND destination=? AND trip_type=? AND depart_date=?
            """,
            (origin, destination, trip_type, depart_date),
        ).fetchone()


def start_run(path: str = DB_PATH) -> int:
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (utcnow(),))
        return int(cur.lastrowid)


def finish_run(path: str, run_id: int, tried: int, ok: int, errors: str | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, routes_tried=?, routes_ok=?, errors=? WHERE id=?",
            (utcnow(), tried, ok, errors, run_id),
        )


def record_alert(path: str, alert: dict) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (sent_at, origin, destination, depart_date, return_date,
                                price, baseline, z_score, pct_below, booking_url,
                                trip_type, notified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                utcnow(), alert["origin"], alert["destination"], alert["depart_date"],
                alert.get("return_date"), alert["price"], alert["baseline"],
                alert["z_score"], alert["pct_below"], alert.get("booking_url"),
                alert.get("trip_type"), 1 if alert.get("notified") else 0,
            ),
        )
        return int(cur.lastrowid)


def recent_alert_exists(path: str, origin: str, destination: str, depart_date: str,
                        within_hours: int = 48, trip_type: str | None = None) -> bool:
    """Dedupe: did we already alert on this exact route shape recently?

    One-way and round-trip fares for the same date are different products, so
    trip_type can narrow the check. The alerts table predates that column, so
    filtering on it is opt-in.
    """
    sql = """
        SELECT 1 FROM alerts
         WHERE origin=? AND destination=? AND depart_date=?
           AND sent_at >= datetime('now', ?)
    """
    params: list = [origin, destination, depart_date, f"-{int(within_hours)} hours"]
    if trip_type:
        sql += " AND trip_type = ?"
        params.append(trip_type)
    sql += " LIMIT 1"

    with connect(path) as conn:
        return conn.execute(sql, params).fetchone() is not None


def stats(path: str = DB_PATH) -> dict:
    with connect(path) as conn:
        obs = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]
        routes = conn.execute(
            "SELECT COUNT(DISTINCT origin || destination) AS n FROM observations"
        ).fetchone()["n"]
        alerts = conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"]
        first = conn.execute("SELECT MIN(observed_at) AS t FROM observations").fetchone()["t"]
        last = conn.execute("SELECT MAX(observed_at) AS t FROM observations").fetchone()["t"]
    return {"observations": obs, "routes": routes, "alerts": alerts,
            "first_seen": first, "last_seen": last}


def _self_check() -> None:
    """Exercise the real storage layer against a throwaway database."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        init(db)
        assert stats(db)["observations"] == 0

        rows = [
            {"observed_at": utcnow(), "origin": "WAW", "destination": "BCN",
             "depart_date": "01/03/2027", "return_date": None, "trip_type": "oneway",
             "price": 120.0, "currency": "EUR", "carrier": "Ryanair", "stops": 0,
             "duration_s": 10800, "booking_url": "https://example.test/a",
             "routing": "WAW → BCN"},
            {"observed_at": utcnow(), "origin": "WAW", "destination": "BCN",
             "depart_date": "01/03/2027", "return_date": None, "trip_type": "oneway",
             "price": 140.0, "currency": "EUR", "carrier": "Wizz", "stops": 0,
             "duration_s": 10900, "booking_url": "https://example.test/b",
             "routing": "WAW → BCN"},
        ]
        assert record(db, rows) == 2
        assert record(db, []) == 0

        hist = history(db, "WAW", "BCN", "oneway", "01/03/2027")
        assert len(hist) == 2

        # Round trips must not contaminate one-way history.
        assert history(db, "WAW", "BCN", "roundtrip", "01/03/2027") == []
        cheapest = cheapest_ever(db, "WAW", "BCN", "oneway", "01/03/2027")
        assert cheapest is not None
        assert cheapest["price"] == 120.0

        run_id = start_run(db)
        assert run_id == 1
        finish_run(db, run_id, tried=2, ok=2)
        assert stats(db)["routes"] == 1

        assert not recent_alert_exists(db, "WAW", "BCN", "01/03/2027")

    # Schema migration: a legacy alerts table (pre trip_type) must be upgraded.
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "legacy.db")
        with sqlite3.connect(db) as conn:
            conn.execute("""
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at TEXT NOT NULL, origin TEXT NOT NULL,
                    destination TEXT NOT NULL, depart_date TEXT NOT NULL,
                    return_date TEXT, price REAL NOT NULL, baseline REAL NOT NULL,
                    z_score REAL NOT NULL, pct_below REAL NOT NULL,
                    booking_url TEXT, notified INTEGER NOT NULL DEFAULT 0
                )""")
            conn.execute(
                "INSERT INTO alerts (sent_at, origin, destination, depart_date, price,"
                " baseline, z_score, pct_below, notified) VALUES"
                " (datetime('now'), 'WAW', 'BCN', '01/03/2027', 90.0, 130.0, 3.1, 0.3, 1)"
            )
        init(db)
        with connect(db) as conn:
            cols = {r["name"] for r in _columns(conn, "alerts")}
        assert "trip_type" in cols
        # The legacy row survives and stays visible to the untagged dedupe check.
        assert recent_alert_exists(db, "WAW", "BCN", "01/03/2027")
        assert stats(db)["alerts"] == 1

        # A round-trip alert is not deduplicated against a one-way alert.
        record_alert(db, {"origin": "WAW", "destination": "BCN",
                          "depart_date": "01/03/2027", "return_date": None,
                          "price": 500.0, "baseline": 700.0, "z_score": 2.5,
                          "pct_below": 0.2857, "booking_url": None, "notified": True,
                          "trip_type": "oneway"})
        # The legacy (untagged) row must still suppress an untagged check…
        assert recent_alert_exists(db, "WAW", "BCN", "01/03/2027")
        assert recent_alert_exists(db, "WAW", "BCN", "01/03/2027", trip_type="oneway")
        # …but a round-trip alert on the same shape is a separate decision.
        assert not recent_alert_exists(db, "WAW", "BCN", "01/03/2027",
                                       trip_type="roundtrip")
        record_alert(db, {"origin": "WAW", "destination": "BCN",
                          "depart_date": "01/03/2027", "return_date": None,
                          "price": 90.0, "baseline": 130.0, "z_score": 3.1,
                          "pct_below": 0.3077, "booking_url": None, "notified": True,
                          "trip_type": "roundtrip"})
        assert recent_alert_exists(db, "WAW", "BCN", "01/03/2027", trip_type="roundtrip")
        assert not recent_alert_exists(db, "WAW", "BCN", "02/03/2027")
        assert stats(db)["alerts"] == 3


if __name__ == "__main__":
    _self_check()
    print("store.py self-check ok")
