from __future__ import annotations

import argparse
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Check both Journee Recruitment health endpoints.")
    parser.add_argument("base_url", help="For example https://lrc203-journee-recruitment.hf.space")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    for path in ("/health/live", "/health/ready"):
        try:
            response = httpx.get(base + path, timeout=args.timeout, follow_redirects=True)
            response.raise_for_status()
            print(f"OK {path}: {response.json()}")
        except Exception as exc:
            print(f"FAILED {path}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
