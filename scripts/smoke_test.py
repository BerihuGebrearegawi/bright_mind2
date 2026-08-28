#!/usr/bin/env python3
"""Safe BMT deployment smoke test.

Usage:
  BMT_BASE_URL=http://127.0.0.1:5000 python scripts/smoke_test.py

Optional:
  BMT_EXPECT_VERSION=31.17
  BMT_REQUIRE_READY=1   # fail unless /readyz is HTTP 200
  BMT_TIMEOUT=10

This test never sends credentials and never calls payment or AI provider APIs.
It validates the public deployment surface first; authenticated provider tests
must be run separately with real staging credentials.
"""
import os
import sys
import requests

BASE = os.getenv("BMT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
EXPECTED = os.getenv("BMT_EXPECT_VERSION", "31.17")
TIMEOUT = float(os.getenv("BMT_TIMEOUT", "10"))
REQUIRE_READY = os.getenv("BMT_REQUIRE_READY", "0").lower() in {"1", "true", "yes"}


def check(path, expected_statuses):
    url = BASE + path
    r = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
    if r.status_code not in expected_statuses:
        raise AssertionError(f"{path}: HTTP {r.status_code}, expected {sorted(expected_statuses)}")
    data = r.json()
    if data.get("version") != EXPECTED:
        raise AssertionError(f"{path}: version={data.get('version')!r}, expected {EXPECTED!r}")
    if "requestId" not in data:
        raise AssertionError(f"{path}: missing requestId")
    if r.headers.get("Cache-Control") != "no-store":
        raise AssertionError(f"{path}: Cache-Control must be no-store")
    if r.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
        raise AssertionError(f"{path}: Content-Type must be application/json")
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "X-Request-ID"):
        if not r.headers.get(header):
            raise AssertionError(f"{path}: missing {header}")
    return r, data


def main():
    health, _ = check("/healthz", {200})
    ready, ready_data = check("/readyz", {200, 503})
    if REQUIRE_READY and ready.status_code != 200:
        raise AssertionError(f"/readyz not ready: {ready_data}")
    if not isinstance(ready_data.get("checks"), dict):
        raise AssertionError("/readyz: checks must be an object")
    print(f"PASS healthz={health.status_code} readyz={ready.status_code} version={EXPECTED}")
    print(f"READY_CHECKS={ready_data['checks']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
