#!/usr/bin/env python3
"""Static security-rule invariants for BMT V30.76. No credentials required."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
firestore = (ROOT / "firestore.rules").read_text(encoding="utf-8")
storage = (ROOT / "storage.rules").read_text(encoding="utf-8")
checks = [
    ("chat reads are class-scoped", "resource.data.className == get(/databases/$(database)/documents/users/$(request.auth.uid)).data.class" in firestore),
    ("chat creates are class-scoped", "request.resource.data.className == get(/databases/$(database)/documents/users/$(request.auth.uid)).data.class" in firestore),
    ("typing reads are class-scoped", "className == get(/databases/$(database)/documents/users/$(request.auth.uid)).data.class" in firestore),
    ("typing create is own uid only", "request.resource.data.keys().hasOnly([request.auth.uid])" in firestore),
    ("exam attempts cannot be browser-created", "match /examAttempts/{attemptId}" in firestore and "allow create: if false;" in firestore),
    ("notifications cannot be browser-created", "match /notifications/{notificationId}" in firestore and "allow create: if false;" in firestore),
    ("exams are not directly student-client-readable (grade-gated only via /api/exams)",
        (lambda block: "allow read: if isAdmin() || (isApprovedTeacher() && resource.data.teacherUid == request.auth.uid);" in block
                       and "isAuthenticated() && resource.data.status == 'published'" not in block)
        (firestore[firestore.index("match /exams/{examId}"):firestore.index("match /examKeys/")])),
    ("storage has deny-all fallback", "match /{allPaths=**}" in storage and "allow read, write: if false;" in storage),
    ("storage no longer has broad authenticated write", "allow write: if request.auth != null;" not in storage),
    ("storage admin helper exists", "function isAdmin()" in storage),
    ("paid video metadata is not client-readable", "match /videos/{videoId}" in firestore and "allow read: if isAdmin();" in firestore),
    ("quiz answer keys are not client-readable", "match /quizzes/{quizId}" in firestore and "Quiz documents contain answer keys" in firestore and "allow read: if isAdmin();" in firestore),
    ("user creation fields are allowlisted", "request.resource.data.keys().hasOnly([" in firestore and "freeTrial.usedDays == 0" in firestore),
    # V31.108 security fix regression checks
    ("book collections are not directly student-client-readable (grade/targeting-gated only via /api/library)",
        all(f"match /{name}/{{bookId}}" in firestore for name in
            ["books", "bookLibrary", "teacherGuides", "referenceBooks", "psychologyBooks"])
        and "allow read: if true;" not in firestore[:firestore.index("match /videos/{videoId}")]
        and firestore[:firestore.index("match /videos/{videoId}")].count("allow read: if isAdmin();") >= 5),
]
failed = False
for name, ok in checks:
    print(("PASS: " if ok else "FAIL: ") + name)
    failed |= not ok
# V30.86 storage and signed-URL authorization hardening
storage = (ROOT / "storage.rules").read_text(encoding="utf-8")
app = (ROOT / "app.py").read_text(encoding="utf-8")
checks = [
    ("match /videos/{classId}/{fileName} {\n      allow read: if request.auth != null;" in storage, "legacy video storage requires authentication"),
    ("match /books/{classId}/{fileName} {\n      allow read: if request.auth != null;" in storage, "legacy book storage requires authentication"),
    ("match /quiz_images/{classId}/{fileName} {\n      allow read: if request.auth != null;" in storage, "quiz images require authentication"),
    ("def _can_access_gcs_book" in app and "You are not authorized to access this document." in app, "GCS signed URL is authorization-gated"),
    ("path.startswith('bmt/books/')" in app and "collection('books').where(field, '==', path)" in app, "GCS URL endpoint is limited to known book records"),
]
failed = [name for ok, name in checks if not ok]
for ok, name in checks:
    print(("PASS: " if ok else "FAIL: ") + name)
if failed:
    raise SystemExit(1)
