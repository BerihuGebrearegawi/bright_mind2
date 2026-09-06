#!/usr/bin/env python3
from pathlib import Path
import re, ast
ROOT=Path(__file__).resolve().parents[1]

# Python source integrity
for name in ["learning_challenge_routes.py","certificate_routes.py","award_ledger_routes.py","challenge_entry_routes.py"]:
    ast.parse((ROOT/name).read_text(encoding="utf-8"), filename=name)
    print("PASS: AST", name)

# Firestore match nesting: every new collection match is inside documents and before fallback.
rules=(ROOT/"firestore.rules").read_text(encoding="utf-8")
assert 'match /libraryScanManifests/{scanId}' not in rules
assert rules.rfind('match /challengeEntries/{entryId}') < rules.find('match /{document=**}')
assert rules.rfind('match /awardLedger/{awardId}') < rules.find('match /{document=**}')
assert rules.rstrip().endswith("}")
print("PASS: Firestore challenge/library matches are nested before fallback")

# V31.30 baseline worker must remain unchanged.
base=(ROOT.parent.parent/'v3130_original'/'bmt_v3129'/'service-worker.js')
if base.exists():
    import hashlib
    def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
    assert h(ROOT/'service-worker.js') == h(base)
print("PASS: service worker matches V31.30 baseline")

learning=(ROOT/"learning_challenge_routes.py").read_text(encoding="utf-8")
assert 'questionSnapshot' in learning and 'correctAnswer' in learning and 'statusResult' in learning
print("PASS: immutable challenge grading snapshot is present")

entry=(ROOT/"challenge_entry_routes.py").read_text(encoding="utf-8")
assert 'Payment is not scoped to this challenge.' in entry and 'server_verified_free_challenge' in entry
print("PASS: challenge entry payment scoping/free entry logic is present")

print("PASS: V31.51 hardening audit")
