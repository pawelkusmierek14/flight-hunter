"""Telegram delivery for Flight Hunter alerts.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_HOME_CHANNEL from the Hermes .env.
Deliberately standalone: the cron job runs outside a Hermes session, so it
cannot rely on the gateway to fan messages out.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

ENV_CANDIDATES = [
    os.environ.get("HERMES_ENV_FILE", ""),
    "/opt/data/.env",
    os.path.expanduser("~/.hermes/.env"),
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    env.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        except OSError:
            continue
    # Real environment wins over the file.
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def telegram_available() -> bool:
    env = _load_env()
    return bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_HOME_CHANNEL"))


def send_telegram(text: str, *, parse_mode: str = "Markdown",
                  disable_preview: bool = True) -> dict:
    """Send a message to the configured home channel. Raises on failure."""
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL not configured")

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true" if disable_preview else "false",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"telegram rejected message: {payload}")
    return payload


def notify_anomalies(anomalies: list, *, dry_run: bool = False) -> int:
    """Send one Telegram message per anomaly. Returns how many were delivered."""
    from anomaly import format_alert

    sent = 0
    for a in anomalies:
        text = format_alert(a)
        if dry_run:
            print(f"[dry-run] would send:\n{text}\n")
            sent += 1
            continue
        try:
            send_telegram(text)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — a failed alert must not kill the scan
            print(f"! telegram send failed for {a.origin}->{a.destination}: {exc}")
    return sent
