"""Thin client for the public Kiwi.com MCP flight-search server.

No API key required. Speaks JSON-RPC 2.0 over Streamable HTTP; the server
answers with Server-Sent Events framing, which we unwrap here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

MCP_URL = "https://mcp.kiwi.com"
TIMEOUT = 90

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Agent": "hermes-flight-hunter",
}


class KiwiError(RuntimeError):
    pass


def _rpc(method: str, params: dict, rpc_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), headers=_HEADERS, method="POST"
    )
    try:
        raw = urllib.request.urlopen(req, timeout=TIMEOUT).read().decode()
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
    """'WAW → BCN' for the outbound leg; ' → ' joined, plus inbound when present."""
    def leg(part: dict) -> str:
        return " → ".join(part.get("route") or [part.get("from", "?"), part.get("to", "?")])

    out = leg(itinerary.get("outbound", {}))
    if itinerary.get("inbound"):
        out += " | " + leg(itinerary["inbound"])
    return out
