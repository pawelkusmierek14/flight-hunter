"""Kiwi.com MCP flight search — no API key, no signup, no cost.

This is the only module that touches the network. It speaks JSON-RPC 2.0 over
Streamable HTTP to a public Model Context Protocol server; the server answers
with Server-Sent Events framing, which is unwrapped here.

Kept to the standard library on purpose so the tool can run from a bare cron
job: no venv, no dependencies to rot.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from config import MCP_TIMEOUT, MCP_URL

# A stable client identifier so the upstream server can tell agent traffic from
# a browser. Not a credential.
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Agent": "flight-hunter",
}


class KiwiError(RuntimeError):
    """Any failure that stops a search from producing usable results."""


def _rpc(method: str, params: dict, rpc_id: int = 1) -> dict:
    """One JSON-RPC call, unwrapping SSE framing."""
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), headers=_HEADERS, method="POST"
    )
    try:
        raw = urllib.request.urlopen(req, timeout=MCP_TIMEOUT).read().decode()
    except urllib.error.HTTPError as exc:
        raise KiwiError(f"HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    except urllib.error.URLError as exc:
        raise KiwiError(f"connection failed: {exc.reason}") from exc

    events = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    if not events:
        raise KiwiError(f"no SSE payload in response: {raw[:300]!r}")

    payload = events[-1]
    if "error" in payload:
        raise KiwiError(f"rpc error: {payload['error']}")
    return payload.get("result", {})


def search_flight(
    fly_from: str,
    fly_to: str,
    departure_date: str,
    return_date: str | None = None,
    flex_days: int = 0,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "economy",
) -> dict:
    """Run one search. Dates are dd/mm/yyyy. Returns Kiwi's raw result dict."""
    args: dict = {
        "flyFrom": fly_from,
        "flyTo": fly_to,
        "departureDate": departure_date,
        "departureDateFlexDays": flex_days,
        "passengers": {"adults": adults, "children": children, "infants": infants},
        "cabinClass": cabin_class,
    }
    if return_date:
        args["returnDate"] = return_date

    result = _rpc("tools/call", {"name": "search-flight", "arguments": args})
    if result.get("isError"):
        raise KiwiError(f"tool error: {result}")

    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if not texts:
        raise KiwiError(f"empty tool result: {result}")

    blob = "\n".join(texts).strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        raise KiwiError(f"unparsable tool output: {blob[:400]!r}") from exc


def cheapest(payload: dict) -> dict | None:
    """Lowest-priced itinerary in a search payload, or None."""
    items = [i for i in payload.get("itineraries", []) if isinstance(i.get("price"), (int, float))]
    return min(items, key=lambda i: i["price"]) if items else None


def route_str(itinerary: dict) -> str:
    """'WAW → BCN' for the outbound leg, plus the inbound leg when present."""
    def leg(part: dict) -> str:
        return " → ".join(part.get("route") or [part.get("from", "?"), part.get("to", "?")])

    out = leg(itinerary.get("outbound", {}))
    if itinerary.get("inbound"):
        out += " | " + leg(itinerary["inbound"])
    return out


def ranking(payload: dict, limit: int = 10) -> list[dict]:
    """Cheapest-first itineraries that carry a numeric price."""
    items = [i for i in payload.get("itineraries", []) if isinstance(i.get("price"), (int, float))]
    return sorted(items, key=lambda i: i["price"])[:limit]


def booking_links(payload: dict, limit: int = 10) -> list[str]:
    """Booking URLs for the cheapest itineraries, skipping empty ones."""
    return [i["bookingUrl"] for i in ranking(payload, limit) if i.get("bookingUrl")]


def _self_check() -> None:
    """Offline sanity check used by the test suite and `python3 kiwi.py`."""
    assert _HEADERS["Accept"].startswith("application/json")
    assert urllib.parse.urlparse(MCP_URL).scheme in ("http", "https")

    payload = {"itineraries": [
        {"price": 300.0, "bookingUrl": "https://example.test/c"},
        {"price": 120.0, "bookingUrl": "https://example.test/a"},
        {"price": None, "bookingUrl": "https://example.test/skip"},
        {"price": 200.0},
    ]}
    best = cheapest(payload)
    assert best is not None
    assert best["price"] == 120.0
    assert [i["price"] for i in ranking(payload)] == [120.0, 200.0, 300.0]
    assert booking_links(payload) == ["https://example.test/a", "https://example.test/c"]
    assert cheapest({"itineraries": []}) is None
    assert route_str({"outbound": {"route": ["WAW", "BCN"]}}) == "WAW → BCN"
    assert route_str({"outbound": {"route": ["WAW", "BCN"]},
                      "inbound": {"route": ["BCN", "WAW"]}}) == "WAW → BCN | BCN → WAW"


if __name__ == "__main__":
    _self_check()
    print("kiwi.py self-check ok")
