#!/usr/bin/env python3
"""BMT provider smoke tests.

Reads secrets only from the process environment. Never prints secret values.
Use against the same runtime environment where Render/Firebase credentials are set.
"""
import os
import sys
import requests


def present(name):
    return bool(os.getenv(name, "").strip())


def gemini_test():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return "SKIP: GEMINI_API_KEY not set"
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    payload = {"model": model, "input": "Reply with exactly: BMT_PROVIDER_OK", "store": False}
    try:
        r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                          json=payload, timeout=float(os.getenv("GEMINI_SMOKE_TIMEOUT", "30")))
    except requests.RequestException as exc:
        return f"FAIL: Gemini network error ({type(exc).__name__})"
    if not r.ok:
        return f"FAIL: Gemini HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return "FAIL: Gemini invalid JSON"
    text = str(data.get("output_text") or "").strip()
    if not text:
        for item in data.get("output", []) or []:
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("text"):
                    text += str(part["text"])
    if not text:
        for step in data.get("steps", []) or []:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            content = step.get("content", []) or []
            if isinstance(content, str):
                text += content
            else:
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        text += str(part["text"])
    return "PASS: Gemini provider" if "BMT_PROVIDER_OK" in text else "FAIL: Gemini unexpected response"


def payment_config_test():
    required = ("TELEBIRR_API_KEY", "TELEBIRR_API_SECRET", "TELEBIRR_WEBHOOK_SECRET")
    missing = [k for k in required if not present(k)]
    currency = os.getenv("PAYMENT_CURRENCY", "").strip().upper()
    if missing:
        return "SKIP: Telebirr configuration missing " + ", ".join(missing)
    if not currency:
        return "FAIL: PAYMENT_CURRENCY is required for production payment verification"
    return "PASS: Telebirr server configuration"


def remote_readiness_test():
    base = os.getenv("BMT_BASE_URL", "").strip().rstrip("/")
    if not base:
        return "SKIP: BMT_BASE_URL not set"
    try:
        r = requests.get(base + "/readyz", timeout=float(os.getenv("BMT_SMOKE_TIMEOUT", "15")))
    except requests.RequestException as exc:
        return f"FAIL: BMT readiness network error ({type(exc).__name__})"
    if r.status_code not in (200, 503):
        return f"FAIL: BMT /readyz HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return "FAIL: BMT /readyz invalid JSON"
    if not isinstance(data, dict) or "checks" not in data:
        return "FAIL: BMT /readyz malformed response"
    checks = data.get("checks") or {}
    if r.status_code == 200 and not (checks.get("firebase") and checks.get("ai_configured") and checks.get("payment_webhook_configured") and checks.get("production_safety")):
        return "FAIL: BMT readiness claims ready without required production checks"
    return "PASS: BMT readiness endpoint"


def main():
    results = [gemini_test(), payment_config_test(), remote_readiness_test()]
    for result in results:
        print(result)
    return 1 if any(x.startswith("FAIL:") for x in results) else 0


if __name__ == "__main__":
    sys.exit(main())
