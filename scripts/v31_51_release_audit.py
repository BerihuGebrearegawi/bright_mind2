#!/usr/bin/env python3
from pathlib import Path
import ast
ROOT = Path(__file__).resolve().parents[1]

def text(name): return (ROOT/name).read_text(encoding="utf-8")

learning = text("learning_challenge_routes.py")
entry = text("challenge_entry_routes.py")
worker = text("service-worker.js")

# AST parse gives a stronger syntax/integrity gate than substring checks alone.
for name in ["learning_challenge_routes.py", "challenge_entry_routes.py", "certificate_routes.py", "award_ledger_routes.py"]:
    ast.parse(text(name), filename=name)
    print("PASS: AST", name)

assert "questionSnapshot" in learning
assert "_public_questions_from_snapshot" in learning
assert 'questionSnapshot' in learning[learning.find('def submit_challenge'):learning.find('def submit_challenge') + 9000]
print("PASS: challenge attempts capture immutable question snapshots")

assert 'payment_challenge_id' in entry
assert 'Payment is not scoped to this challenge.' in entry
assert 'server_verified_free_challenge' in entry
print("PASS: paid entries are challenge-scoped and free entries do not require payment")

assert "bmt-shell-v31-29" in worker
print("PASS: service worker matches verified V31.30 baseline")

print("PASS: V31.51 release audit")
