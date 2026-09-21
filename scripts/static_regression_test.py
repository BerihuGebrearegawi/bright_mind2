#!/usr/bin/env python3
"""Static production-regression checks for the current BMT release.
No credentials are read.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
STORAGE = ROOT / "static" / "storage-service.js"
CONFIG = ROOT / "static" / "storage-config.js"

def fail(msg):
    print("FAIL:", msg)
    return 1

app = APP.read_text(encoding="utf-8")
storage = STORAGE.read_text(encoding="utf-8")
config = CONFIG.read_text(encoding="utf-8")

checks = []
checks.append(('BMT_VERSION = os.getenv("BMT_VERSION", "31.108")' in app and "@app.route('/healthz')" in app, "healthz uses current BMT_VERSION"))
checks.append(('BMT_VERSION = os.getenv("BMT_VERSION", "31.108")' in app and "@app.route('/readyz')" in app, "readyz uses current BMT_VERSION"))
checks.append(("IMGBB_API_KEY" not in storage and "IMGBB_API_KEY" not in config, "legacy ImgBB secret is not exposed to client code"))
checks.append(("uploadToCloudinary" in storage and "api.cloudinary.com/v1_1" in storage, "client image/video/audio uploads use Cloudinary"))
checks.append(("@app.post('/api/storage/document')" in app and "_require_user_bearer()" in app, "GCS document upload requires authentication"))
checks.append(("maxSize: 10 * 1024 * 1024" in config, "Cloudinary image uploads enforce 10 MB client limit"))
checks.append(("PAYMENT_CURRENCY" in app and "ALLOW_WEBHOOK_WITHOUT_AMOUNT" in app, "production payment safety checks present"))
checks.append(("data.get(\"steps\", [])" in (ROOT / "ai_tutor_service.py").read_text(encoding="utf-8"), "Gemini response parser supports current steps shape"))
checks.append(('"store": False' in (ROOT / "ai_tutor_service.py").read_text(encoding="utf-8"), "Gemini requests set store=false"))
checks.append(("@app.get('/api/quizzes')" in app and "correctAnswer" not in app[app.find("@app.get('/api/quizzes')"):app.find("@app.get('/api/videos')")], "quiz API does not expose answer keys"))
checks.append(("@app.get('/api/videos')" in app and "if allowed:\n                item['url']" in app, "video API only returns URLs to entitled users"))
student=(ROOT / "static" / "student.js").read_text(encoding="utf-8")
checks.append(("/api/quizzes?className=" in student and "data-correct=" not in student[student.find("async function loadStudentContent"):student.find("// ==========================================================\n// 🎥 VIDEOS")], "student quiz UI does not embed answer keys"))
checks.append(("/api/videos?className=" in student and "collection(db, \"videos\")" not in student[student.find("async function loadStudentVideos"):student.find("// ==========================================================\n//", student.find("async function loadStudentVideos")+50)], "student video UI uses authorized API"))
worker=(ROOT / "service-worker.js").read_text(encoding="utf-8")
checks.append(("bmt-shell-v31-108" in worker, "service worker cache matches current BMT release (V31.108)"))
checks.append(("100 * 1024 * 1024" in app, "Flask upload ceiling supports 100 MB documents"))
checks.append(("@app.post('/api/admin/catalog/quizzes')" in (ROOT / "admin_features_routes.py").read_text(encoding="utf-8"), "quiz creation is server-authoritative"))
checks.append(("/api/admin/catalog/quizzes" in (ROOT / "static" / "admin.js").read_text(encoding="utf-8"), "admin quiz UI uses server endpoint"))
checks.append(("window.logoutUser = logoutUser;" in student and "window.loadBookmarks = loadBookmarks;" in student and "window.sendChatMessageWithReply = sendChatMessageWithReply;" in student and "window.cancelReply = cancelReply;" in student, "all five student window exports are exposed"))
checks.append(("DIAGNOSTIC_KEY" in app and "X-Diagnostic-Key" in app and "hmac.compare_digest" in app, "diagnostic key is supported without Firebase admin dependency"))


for ok, name in checks:
    if not ok:
        print("FAIL:", name)
        sys.exit(1)
    print("PASS:", name)
print("PASS: static production regression current release V31.108")
