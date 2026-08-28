#!/usr/bin/env python3
"""BMT public-surface E2E smoke test. No credentials required."""
import os,sys,requests
BASE=os.getenv("BMT_BASE_URL","http://127.0.0.1:5000").rstrip("/")
EXPECTED=os.getenv("BMT_EXPECT_VERSION","31.17")
TIMEOUT=float(os.getenv("BMT_TIMEOUT","10"))
PATHS=("/","/teacher","/student","/parent","/healthz","/readyz")
def main():
    failures=[]
    for path in PATHS:
        try: r=requests.get(BASE+path,timeout=TIMEOUT,allow_redirects=False)
        except requests.RequestException as e: failures.append(f"{path}: {type(e).__name__}"); continue
        if path in ("/healthz","/readyz"):
            if r.headers.get("Cache-Control")!="no-store": failures.append(f"{path}: missing no-store")
            if r.headers.get("Content-Type","").split(";")[0].strip()!="application/json": failures.append(f"{path}: non-JSON")
            try: data=r.json()
            except ValueError: failures.append(f"{path}: invalid JSON"); continue
            if data.get("version")!=EXPECTED: failures.append(f"{path}: version {data.get('version')!r}")
        elif r.status_code>=500: failures.append(f"{path}: HTTP {r.status_code}")
    if failures:
        print("FAIL"); [print(x) for x in failures]; return 1
    print(f"PASS public E2E surface version={EXPECTED}"); return 0
if __name__=="__main__": sys.exit(main())
