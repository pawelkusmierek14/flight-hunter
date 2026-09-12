"""Alert delivery for Flight Hunter.

Deliberately standalone and channel-agnostic: a scan usually runs from cron,
outside any agent session, so it cannot rely on an assistant gateway to fan
messages out. Two transports ship here — Telegram, and an HTTP webhook for
anything else (Slack, Discord, ntfy, a home automation endpoint).

Credentials are read from the process environment first (so cron can inject
them securely), then from a dotenv file as a convenience. Nothing is ever
written back to disk, and no token appears in logs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# Checked in order; the first file that exists contributes values. The
# environment always wins over any file.
ENV_CANDIDATES = [
    os.environ.get("FLIGHT_HUNTER_ENV_FILE", ""),
    os.path.expanduser("~/.config/flight-hunter/.env"),
    os.path.expanduser("~/.hermes/.env"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
]

_SECRET_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_HOME_CHANNEL",
    "FLIGHT_HUNTER_WEBHOOK_URL",
)


class NotifyError(RuntimeError):
    """Raised when a channel is asked to deliver without being configured."""


def _load_env() -> dict[str, str]:
    """Collect credentials from the first available dotenv file, then the env."""
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
    # The real environment wins over any file.
    for key in _SECRET_KEYS:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _post(url: str, data: bytes, headers: dict[str, str], timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"connection failed: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def telegram_available() -> bool:
    env = _load_env()
    return bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_HOME_CHANNEL"))


def send_telegram(text: str, *, parse_mode: str = "Markdown",
                  disable_preview: bool = True) -> dict:
    """Send a message to the configured chat. Raises on failure."""
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat_id:
        raise NotifyError("TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL not configured")

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true" if disable_preview else "false",
    }).encode()
    payload = _post(f"https://api.telegram.org/bot{token}/sendMessage", body,
                    {"Content-Type": "application/x-www-form-urlencoded"})
    if not payload.get("ok"):
        raise NotifyError("telegram rejected message")
    return payload


def webhook_available() -> bool:
    return bool(_load_env().get("FLIGHT_HUNTER_WEBHOOK_URL"))


def send_webhook(text: str) -> dict:
    """POST {"text": ...} to the configured webhook. Raises on failure."""
    url = _load_env().get("FLIGHT_HUNTER_WEBHOOK_URL")
    if not url:
        raise NotifyError("FLIGHT_HUNTER_WEBHOOK_URL not configured")
    return _post(url, json.dumps({"text": text}).encode(),
                 {"Content-Type": "application/json"})


def send(text: str, *, channel: str = "auto") -> dict:
    """Deliver one message over the best available channel.

    channel: "auto" (webhook if configured, else Telegram), "telegram", or
    "webhook". Raises NotifyError when nothing is configured.
    """
    if channel == "telegram" or (channel == "auto" and not webhook_available()):
        return send_telegram(text)
    if channel == "webhook" or channel == "auto":
        return send_webhook(text)
    raise NotifyError(f"unknown channel: {channel}")


def notify_anomalies(anomalies: list, *, dry_run: bool = False,
                     channel: str = "auto") -> int:
    """Send one message per anomaly. Returns how many were delivered.

    A failed alert must never kill the scan, so per-item errors are reported and
    counted rather than raised.
    """
    from anomaly import format_alert

    sent = 0
    for a in anomalies:
        text = format_alert(a)
        if dry_run:
            print(f"[dry-run] would send:\n{text}\n")
            sent += 1
            continue
        try:
            send(text, channel=channel)
            sent += 1
        except (NotifyError, OSError) as exc:  # noqa: BLE001 — one alert must not stop the rest
            print(f"! send failed for {a.origin}->{a.destination}: {exc}")
    return sent


def _self_check() -> None:
    """Offline checks: parsing and configuration detection, no network calls."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# comment\nTELEGRAM_BOT_TOKEN='abc123'\n")
            fh.write('TELEGRAM_HOME_CHANNEL="456"\n')
            fh.write("MALFORMED LINE\n")
            fh.write("\n")
        saved = dict(os.environ)
        os.environ["FLIGHT_HUNTER_ENV_FILE"] = path
        for key in _SECRET_KEYS:
            os.environ.pop(key, None)
        # Point the module's own dotenv candidates at the fixture only; a stray
        # real .env lying next to the source must not decide the outcome.
        real_candidates = ENV_CANDIDATES[:]
        ENV_CANDIDATES[:] = [path]
        try:
            # Quoted values and the comment/malformed lines must round-trip clean.
            assert telegram_available() is True
            assert _load_env()["TELEGRAM_BOT_TOKEN"] == "abc123"
            assert _load_env()["TELEGRAM_HOME_CHANNEL"] == "456"

            # The real environment must win over the file.
            os.environ["TELEGRAM_BOT_TOKEN"] = "from-env"
            assert _load_env()["TELEGRAM_BOT_TOKEN"] == "from-env"
            del os.environ["TELEGRAM_BOT_TOKEN"]

            # Webhook config flips the channel choice.
            assert webhook_available() is False
        finally:
            ENV_CANDIDATES[:] = real_candidates
            os.environ.clear()
            os.environ.update(saved)

    # With no credentials anywhere, delivery must fail loudly, not silently pass.
    saved = dict(os.environ)
    for key in _SECRET_KEYS + ("FLIGHT_HUNTER_ENV_FILE",):
        os.environ.pop(key, None)
    try:
        assert telegram_available() is False
        try:
            send_telegram("x")
        except NotifyError:
            pass
        else:
            raise AssertionError("send_telegram must raise when unconfigured")
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    _self_check()
    print("notify.py self-check ok")
