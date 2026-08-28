from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
admin=(ROOT/"admin_features_routes.py").read_text()
student=(ROOT/"static/student.js").read_text()
teacher=(ROOT/"static/teacher.js").read_text()
app=(ROOT/"app.py").read_text()
checks=[
 ("/api/chat/report" in admin, "community report endpoint"),
 ("/api/admin/chat/reports" in admin, "admin report moderation endpoints"),
 ("reactionVotes" in admin and "previous == reaction" in admin, "reaction toggle prevents repeat spam"),
 ("data-chat-report" in student, "student report control"),
 ("data-t-report" in teacher, "teacher report control"),
 ('"version": "30.86"' in app, "version 30.82"),
]
failed=False
for ok,msg in checks:
 print(("PASS: " if ok else "FAIL: ")+msg); failed |= not ok
raise SystemExit(1 if failed else 0)
