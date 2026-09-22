from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    status: int | None
    detail: str


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    sender: str
    recipient: str


def email_settings() -> EmailSettings | None:
    names = ("EVALDAY_SMTP_HOST", "EVALDAY_SMTP_USERNAME", "EVALDAY_SMTP_PASSWORD", "EVALDAY_ALERT_FROM", "EVALDAY_ALERT_TO")
    values = [os.environ.get(name, "").strip() for name in names]
    if not all(values):
        return None
    try:
        port = int(os.environ.get("EVALDAY_SMTP_PORT") or "587")
        if not 1 <= port <= 65535 or any("\n" in value or "\r" in value for value in values):
            return None
    except ValueError:
        return None
    return EmailSettings(values[0], port, values[1], values[2], values[3], values[4])


def send_email_alert(configuration: EmailSettings, app_url: str, state: str) -> bool:
    """Only explicit SMTP credentials enable sending; never downgrade TLS."""
    try:
        message = EmailMessage()
        label = "RECOVERED" if state == "healthy" else "OUTAGE"
        message["Subject"] = f"Evalday {label}: {urlsplit(app_url).hostname}"
        message["From"] = configuration.sender
        message["To"] = configuration.recipient
        message.set_content(
            f"{app_url}\n\n"
            + ("Both application health checks are working again." if state == "healthy" else
               "Application health checks failed after all configured retries. Check the host and database dashboards.")
            + f"\nChecked at {datetime.now(timezone.utc).isoformat()}\n"
            + "This is an automated health alert, not a guarantee of availability."
        )
        context = ssl.create_default_context()
        if configuration.port == 465:
            connection = smtplib.SMTP_SSL(configuration.host, configuration.port, timeout=10, context=context)
        else:
            connection = smtplib.SMTP(configuration.host, configuration.port, timeout=10)
        with connection as client:
            if configuration.port != 465:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(configuration.username, configuration.password)
            client.send_message(message)
        print(f"Email alert accepted by SMTP server: {label}.", flush=True)
        return True
    except Exception as exc:
        # SMTP exceptions may contain server responses or credentials. Log the
        # class only; mail delivery must never stop application monitoring.
        print(f"Email alert failed ({type(exc).__name__}); monitoring continues.", flush=True)
        return False


class EmailAlertMonitor:
    def __init__(self, app_url: str, *, state_file: str | None = None, disabled: bool = False):
        self.app_url = app_url.rstrip("/")
        self.app_key = hashlib.sha256(self.app_url.encode()).hexdigest()
        self.path = Path(state_file) if state_file else None
        self.configuration = None if disabled else email_settings()
        self.state = {"app": self.app_key, "status": None, "pending": None, "last_attempt": 0.0}
        if self.path:
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if (saved.get("app") == self.app_key
                        and saved.get("status") in ("healthy", "outage", None)
                        and saved.get("pending") in ("healthy", "outage", None)
                        and isinstance(saved.get("last_attempt"), (int, float))):
                    self.state = saved
            except (OSError, ValueError, AttributeError):
                pass
        print("Email alerts: configured (delivery still requires verification)." if self.configuration else
              "Email alerts: disabled or not configured; no email will be sent.", flush=True)

    def observe(self, healthy: bool) -> None:
        state = "healthy" if healthy else "outage"
        previous = self.state["status"]
        if state != previous:
            self.state["status"] = state
            # A first healthy reading is not a recovery and needs no email.
            self.state["pending"] = state if previous is not None or not healthy else None
            self.state["last_attempt"] = 0.0
        now = time.time()
        if (self.configuration and self.state["pending"] is not None
                and now - self.state["last_attempt"] >= 300):
            self.state["last_attempt"] = now
            if send_email_alert(self.configuration, self.app_url, self.state["pending"]):
                self.state["pending"] = None
        if self.path:
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                    temporary = stream.name
                    json.dump(self.state, stream)
                os.replace(temporary, self.path)
            except OSError as exc:
                print(f"Monitor state could not be saved ({type(exc).__name__}); monitoring continues.", flush=True)
            finally:
                if temporary and os.path.exists(temporary):
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass


def health_urls(app_url: str) -> tuple[str, str]:
    base = app_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("APP_URL must start with http:// or https://")
    parsed = urlsplit(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("APP_URL must be a public application URL without credentials, query or fragment")
    return f"{base}/health/live", f"{base}/health/ready"


def probe(url: str, *, timeout_seconds: int = 30) -> ProbeResult:
    request = Request(url, headers={"User-Agent": "Evalday-Event-Day-Guard/2.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            # Render's startup page can return HTTP 200. A successful HTTP
            # response alone must never be reported as a working application.
            if not 200 <= response.status < 300:
                return ProbeResult(False, response.status, "Unexpected HTTP status")
            raw = response.read(8193)
            if len(raw) > 8192:
                return ProbeResult(False, response.status, "Unexpected oversized health response")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return ProbeResult(False, response.status, "Expected health JSON; received a startup page or invalid response")
            endpoint = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            expected_status = {"live": "ok", "ready": "ready"}.get(endpoint)
            if not expected_status or not isinstance(payload, dict) or payload.get("status") != expected_status:
                return ProbeResult(False, response.status, "Health JSON did not confirm the expected application state")
            return ProbeResult(True, response.status, json.dumps({"status": expected_status}))
    except HTTPError as exc:
        return ProbeResult(False, exc.code, str(exc.reason))
    except (URLError, TimeoutError, OSError) as exc:
        return ProbeResult(False, None, str(exc))


def check_cycle(
    app_url: str,
    *,
    retry_count: int,
    retry_delay_seconds: int,
    timeout_seconds: int,
) -> bool:
    cycle_ok = True
    for url in health_urls(app_url):
        endpoint_ok = False
        for attempt in range(1, retry_count + 1):
            result = probe(url, timeout_seconds=timeout_seconds)
            print(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "endpoint": url.rsplit("/", 1)[-1],
                        "attempt": attempt,
                        "ok": result.ok,
                        "status": result.status,
                        "detail": result.detail[:200],
                    }
                ),
                flush=True,
            )
            if result.ok:
                endpoint_ok = True
                break
            if attempt < retry_count:
                time.sleep(retry_delay_seconds)
        cycle_ok = cycle_ok and endpoint_ok
    return cycle_ok


def run_guard(
    app_url: str,
    *,
    duration_minutes: int | None,
    cycles: int | None,
    interval_seconds: int,
    initial_delay_seconds: int,
    retry_count: int,
    retry_delay_seconds: int,
    timeout_seconds: int,
    state_file: str | None = None,
    no_email: bool = False,
) -> int:
    health_urls(app_url)
    alerts = EmailAlertMonitor(app_url, state_file=state_file, disabled=no_email)
    if initial_delay_seconds:
        print(f"Backup monitor offset: waiting {initial_delay_seconds} seconds.", flush=True)
        time.sleep(initial_delay_seconds)

    deadline = time.monotonic() + duration_minutes * 60 if duration_minutes is not None else None
    completed = 0
    failed = 0
    while True:
        cycle_started = time.monotonic()
        completed += 1
        cycle_healthy = check_cycle(
            app_url,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
            timeout_seconds=timeout_seconds,
        )
        alerts.observe(cycle_healthy)
        if not cycle_healthy:
            failed += 1
            print(f"Health cycle {completed} failed after all retries; monitoring continues.", flush=True)

        if cycles is not None and completed >= cycles:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        sleep_seconds = max(0, interval_seconds - (time.monotonic() - cycle_started))
        if deadline is not None:
            sleep_seconds = min(sleep_seconds, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)

    print(f"Event-day guard finished: {completed} cycles, {failed} failed cycles.", flush=True)
    return 1 if failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep the LRC Journee service warm and verify readiness.")
    parser.add_argument("app_url")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--duration-minutes", type=int)
    mode.add_argument("--cycles", type=int)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--initial-delay-seconds", type=int, default=0)
    parser.add_argument("--retry-count", type=int, default=5)
    parser.add_argument("--retry-delay-seconds", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--state-file", help="Optional non-secret health/notification state for deduplication across runs")
    parser.add_argument("--no-email", action="store_true", help="Disable email for a redundant monitor lane")
    args = parser.parse_args(argv)
    for name in ("duration_minutes", "cycles", "interval_seconds", "retry_count", "timeout_seconds"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.initial_delay_seconds < 0 or args.retry_delay_seconds < 0:
        parser.error("delay values cannot be negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_guard(
            args.app_url,
            duration_minutes=args.duration_minutes,
            cycles=args.cycles,
            interval_seconds=args.interval_seconds,
            initial_delay_seconds=args.initial_delay_seconds,
            retry_count=args.retry_count,
            retry_delay_seconds=args.retry_delay_seconds,
            timeout_seconds=args.timeout_seconds,
            state_file=args.state_file,
            no_email=args.no_email,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
