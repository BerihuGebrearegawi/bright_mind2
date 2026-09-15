#!/usr/bin/env python3
"""BMT V30.76 static rate-limit safety regression checks."""
from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
app=(ROOT/"app.py").read_text(encoding="utf-8")
checks=[
    ("rate limiting enabled", "_rate_limited()" in app),
    ("429 response present", 'status_code = 429' in app),
    ("Retry-After header present", 'Retry-After' in app),
    ("API-only limiter", 'target.startswith("/api/")' in app),
]
failed=False
for name, ok in checks:
    print(("PASS: " if ok else "FAIL: ")+name)
    failed |= not ok
sys.exit(1 if failed else 0)
