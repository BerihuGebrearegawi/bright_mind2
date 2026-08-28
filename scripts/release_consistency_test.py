#!/usr/bin/env python3
"""BMT V31.17 release consistency checks. No credentials are read or printed."""
from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
EXPECTED="31.17"
FILES=[ROOT/"app.py", ROOT/"scripts"/"smoke_test.py", ROOT/"scripts"/"e2e_public_smoke_test.py"]
def main():
    failures=[]
    for path in FILES:
        text=path.read_text(encoding="utf-8")
        if EXPECTED not in text: failures.append(f"{path.relative_to(ROOT)}: missing expected version {EXPECTED}")
    app=(ROOT/"app.py").read_text(encoding="utf-8")
    for stale in sorted(set(re.findall(r"\b30\.(?:[0-5]\d|6[01])\b", app))): failures.append(f"app.py: stale version reference {stale}")
    if failures:
        [print("FAIL:",x) for x in failures]; return 1
    print("PASS: release version consistency V31.17"); return 0
if __name__=="__main__": sys.exit(main())
