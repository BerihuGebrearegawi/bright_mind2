#!/usr/bin/env python3
"""Static regression checks for the V31 parallel scanner/challenge boundary."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
challenge = (ROOT / 'learning_challenge_routes.py').read_text(encoding='utf-8')
rules = (ROOT / 'firestore.rules').read_text(encoding='utf-8')
checks = [
    ('challenge round is bounded to 1..7', 'round_number = max(1, min(7' in challenge),
    ('challenge rejects duplicate questions', 'Duplicate questionIds are not allowed.' in challenge and 'len(set(ids)) != len(ids)' in challenge),
    ('qualification count is server-derived', 'qualification_count = max(0, int(challenge.get("qualificationCount", 0) or 0))' in challenge),
    ('Cloud-B scanner routes are not duplicated', not (ROOT / 'parallel_scanner_routes.py').exists() and '/api/teacher/library/scans' not in challenge),
    ('Cloud-B scanner manifest rules are not duplicated', 'match /libraryScanManifests/{scanId}' not in rules),
    ('challenge attempts are browser denied', 'match /challengeAttempts/{attemptId}' in rules and 'allow read, write: if false;' in rules),
]
for name, ok in checks:
    print(('PASS: ' if ok else 'FAIL: ') + name)
    if not ok: sys.exit(1)
print('PASS: V31.34 parallel regression')
