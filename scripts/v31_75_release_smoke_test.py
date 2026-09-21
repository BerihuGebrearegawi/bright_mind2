#!/usr/bin/env python3
"""Static smoke checks for the V31.75 integration release."""
from pathlib import Path
import re

root=Path(__file__).resolve().parents[1]
app=(root/"app.py").read_text()
sw=(root/"service-worker.js").read_text()
assert 'BMT_VERSION = os.getenv("BMT_VERSION", "31.75")' in app
assert '"version": BMT_VERSION' in app
assert "bmt-shell-v31-75" in sw
for page in ("student.html","teacher.html","admin.html"):
    text=(root/"templates"/page).read_text()
    assert '?v=31.75' in text, page
for js in root.glob("**/*.js"):
    if "node_modules" in js.parts: continue
    # Basic check: files are non-empty and balanced enough for Node --check to handle.
    assert js.stat().st_size > 0, js
print("PASS: V31.75 release smoke checks")
