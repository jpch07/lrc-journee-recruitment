from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    status: int | None
    detail: str


def health_urls(app_url: str) -> tuple[str, str]:
    base = app_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("APP_URL must start with http:// or https://")
    return f"{base}/health/live", f"{base}/health/ready"


def probe(url: str, *, timeout_seconds: int = 30) -> ProbeResult:
    request = Request(url, headers={"User-Agent": "LRC-Event-Day-Guard/1.0"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return ProbeResult(200 <= response.status < 300, response.status, body)
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
) -> int:
    health_urls(app_url)
    if initial_delay_seconds:
        print(f"Backup monitor offset: waiting {initial_delay_seconds} seconds.", flush=True)
        time.sleep(initial_delay_seconds)

    deadline = time.monotonic() + duration_minutes * 60 if duration_minutes is not None else None
    completed = 0
    failed = 0
    while True:
        cycle_started = time.monotonic()
        completed += 1
        if not check_cycle(
            app_url,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
            timeout_seconds=timeout_seconds,
        ):
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
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
