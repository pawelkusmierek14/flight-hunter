"""Flight Hunter CLI.

Usage:
  python3 hunt.py find --from WAW --to Lisbon --depart 15/10/2026 [--return 22/10/2026]
                       [--flex 3] [--cabin economy] [--json]
  python3 hunt.py scan [--batch 6] [--dry-run]     # sample routes, store history, alert
  python3 hunt.py report [--top 20]                # what we know so far
  python3 hunt.py stats
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

import anomaly
import kiwi
import store
from notify import notify_anomalies
from routes import DESTINATIONS, ORIGIN


# ── helpers ───────────────────────────────────────────────────────────────

def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "?"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %H:%M")
    except ValueError:
        return iso


def next_departure_dates(count: int, horizon_days: int = 120) -> list[str]:
    """Spread departure dates across the booking horizon (dd/mm/yyyy)."""
    today = date.today()
    step = max(1, horizon_days // max(1, count))
    out = []
    for i in range(count):
        d = today + timedelta(days=14 + i * step)
        out.append(d.strftime("%d/%m/%Y"))
    return out


def render_table(payload: dict, limit: int = 10) -> str:
    items = [i for i in payload.get("itineraries", []) if i.get("price") is not None]
    if not items:
        return "Brak wyników."
    items.sort(key=lambda i: i["price"])
    head = f"{payload.get('query', '')}\n"
    rows = []
    for it in items[:limit]:
        seg = (it.get("outbound", {}).get("segments") or [{}])[0]
        rows.append(
            f"  {it['price']:>7.0f} {payload.get('currency', 'EUR')}  "
            f"{kiwi.route_str(it):<28} "
            f"{fmt_dt(it.get('outbound', {}).get('departureTime')):<12} "
            f"{fmt_duration(it.get('outbound', {}).get('durationSeconds')):<7} "
            f"{seg.get('carrierName', '?')}"
        )
    return head + "\n".join(rows) + "\n" + "\n".join(
        f"    → {it.get('bookingUrl')}" for it in items[:limit]
    )


# ── commands ──────────────────────────────────────────────────────────────

def cmd_find(args) -> int:
    payload = kiwi.search_flight(
        args.frm, args.to, args.depart,
        return_date=getattr(args, "return", None),
        flex_days=args.flex, adults=args.adults,
        children=args.children, infants=args.infants,
        cabin_class=args.cabin,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_table(payload, limit=args.limit))
    return 0


def cmd_scan(args) -> int:
    """Sample a rotating batch of routes, store prices, raise anomalies."""
    store.init()
    run_id = store.start_run()
    dates = next_departure_dates(args.batch, args.horizon)

    # Rotate through destinations so each run samples different routes.
    seed = date.today().toordinal()
    picks = []
    for i, depart in enumerate(dates):
        dest = DESTINATIONS[(seed + i) % len(DESTINATIONS)]
        picks.append((dest, depart))

    tried = ok = 0
    errors: list[str] = []
    alerts: list[anomaly.Anomaly] = []
    rows: list[dict] = []

    for dest, depart in picks:
        tried += 1
        try:
            payload = kiwi.search_flight(ORIGIN, dest, depart, flex_days=3)
        except kiwi.KiwiError as exc:
            errors.append(f"{dest}@{depart}: {exc}")
            continue

        best = kiwi.cheapest(payload)
        if not best:
            errors.append(f"{dest}@{depart}: no itineraries")
            continue
        ok += 1

        trip_type = "oneway"
        seg = (best.get("outbound", {}).get("segments") or [{}])[0]
        routing = kiwi.route_str(best)

        rows.append({
            "observed_at": store.utcnow(), "origin": ORIGIN, "destination": dest,
            "depart_date": depart, "return_date": None, "trip_type": trip_type,
            "price": float(best["price"]), "currency": payload.get("currency", "EUR"),
            "carrier": seg.get("carrierName"), "stops": best.get("outbound", {}).get("stops"),
            "duration_s": best.get("outbound", {}).get("durationSeconds"),
            "booking_url": best.get("bookingUrl"), "routing": routing,
        })

        hist = store.history(store.DB_PATH, ORIGIN, dest, trip_type, depart)
        prices = [r["price"] for r in hist]
        hit = anomaly.evaluate(
            prices, float(best["price"]), origin=ORIGIN, destination=dest,
            depart_date=depart, currency=payload.get("currency", "EUR"),
            booking_url=best.get("bookingUrl"), routing=routing,
        )
        if hit and not store.recent_alert_exists(store.DB_PATH, ORIGIN, dest, depart):
            alerts.append(hit)

    if not args.dry_run:
        store.record(store.DB_PATH, rows)
    store.finish_run(store.DB_PATH, run_id, tried, ok, "; ".join(errors) or None)

    print(f"scan run #{run_id}: {ok}/{tried} ok, {len(rows)} zapisanych, "
          f"{len(alerts)} anomalii{'(dry-run)' if args.dry_run else ''}")
    for e in errors:
        print(f"  ! {e}", file=sys.stderr)

    if alerts and not args.dry_run:
        delivered = notify_anomalies(alerts)
        for a in alerts:
            store.record_alert(store.DB_PATH, {**a.as_dict(), "notified": bool(delivered)})
        print(f"  → wysłano {delivered}/{len(alerts)} alertów na Telegram")
    for a in alerts:
        print("\n" + anomaly.format_alert(a))
    return 0


def cmd_report(args) -> int:
    store.init()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT origin, destination, depart_date, MIN(price) AS best, currency,
                   COUNT(*) AS samples, MAX(observed_at) AS last_seen
              FROM observations
             GROUP BY origin, destination, depart_date
             ORDER BY best ASC LIMIT ?
            """,
            (args.top,),
        ).fetchall()
    if not rows:
        print("Brak danych — uruchom najpierw: python3 hunt.py scan")
        return 0
    print(f"{'trasa':<16}{'data':<12}{'najtaniej':>10}{'n':>4}  ostatnio")
    for r in rows:
        print(f"{r['origin']+'→'+r['destination']:<16}{r['depart_date']:<12}"
              f"{r['best']:>8.0f} {r['currency']:<3}{r['samples']:>4}  {r['last_seen'][:16]}")
    return 0


def cmd_stats(args) -> int:
    store.init()
    s = store.stats()
    print(json.dumps(s, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hunt", description="Flight Hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="live search for one route")
    f.add_argument("--from", dest="frm", default=ORIGIN)
    f.add_argument("--to", dest="to", required=True)
    f.add_argument("--depart", required=True, help="dd/mm/yyyy")
    f.add_argument("--return", dest="return", default=None, help="dd/mm/yyyy")
    f.add_argument("--flex", type=int, default=0, help="+/- days around depart")
    f.add_argument("--cabin", default="economy")
    f.add_argument("--adults", type=int, default=1)
    f.add_argument("--children", type=int, default=0)
    f.add_argument("--infants", type=int, default=0)
    f.add_argument("--limit", type=int, default=10)
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_find)

    s = sub.add_parser("scan", help="sample routes, store history, detect anomalies")
    s.add_argument("--batch", type=int, default=6, help="how many routes this run")
    s.add_argument("--horizon", type=int, default=120, help="booking horizon in days")
    s.add_argument("--dry-run", action="store_true", help="do not write to the DB")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("report", help="cheapest known prices per route")
    r.add_argument("--top", type=int, default=20)
    r.set_defaults(func=cmd_report)

    st = sub.add_parser("stats", help="database stats")
    st.set_defaults(func=cmd_stats)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except kiwi.KiwiError as exc:
        print(f"błąd Kiwi: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
