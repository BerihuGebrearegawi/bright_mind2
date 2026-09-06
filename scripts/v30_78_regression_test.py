#!/usr/bin/env python3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
app=(ROOT/"app.py").read_text(encoding="utf-8")
admin=(ROOT/"static/admin.js").read_text(encoding="utf-8")

checks=[
 ("/api/admin/announcements" in app, "admin announcement endpoint exists"),
 ("ENABLE_FCM_PUSH" in app and "notificationPreferences" in app, "announcement push respects notification preferences"),
 ("target == \"all\"" in app and "fcmTokens" in app, "push endpoint supports registered-device targeting"),
 ("/api/notifications" in (ROOT/"static/student.js").read_text(encoding="utf-8"), "student notification center uses server API"),
 ("/api/admin/announcements" in admin, "admin announcement UI uses server announcement API"),
]
for ok,name in checks:
 print(("PASS: " if ok else "FAIL: ")+name)
 if not ok: raise SystemExit(1)
print("PASS: notification regression V30.82")
