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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flights.db")

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
                                price, baseline, z_score, pct_below, booking_url, notified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                utcnow(), alert["origin"], alert["destination"], alert["depart_date"],
                alert.get("return_date"), alert["price"], alert["baseline"],
                alert["z_score"], alert["pct_below"], alert.get("booking_url"),
                1 if alert.get("notified") else 0,
            ),
        )
        return int(cur.lastrowid)


def recent_alert_exists(path: str, origin: str, destination: str, depart_date: str,
                        within_hours: int = 48) -> bool:
    """Dedupe: did we already alert on this exact route shape recently?"""
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM alerts
             WHERE origin=? AND destination=? AND depart_date=?
               AND sent_at >= datetime('now', ?)
             LIMIT 1
            """,
            (origin, destination, depart_date, f"-{int(within_hours)} hours"),
        ).fetchone()
        return row is not None


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
