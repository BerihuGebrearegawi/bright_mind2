from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
rules=(ROOT/'firestore.rules').read_text()
admin=(ROOT/'admin_features_routes.py').read_text()
student=(ROOT/'static/student.js').read_text()
teacher=(ROOT/'static/teacher.js').read_text()
config=(ROOT/'static/storage-config.js').read_text()
app=(ROOT/'app.py').read_text()
checks=[
 ('/api/chat/community/send' in admin, 'community server send endpoint'),
 ('isApprovedTeacher()' in rules and "request.resource.data.role == 'student'" in rules, 'community role rule hardening'),
 ("'/api/chat/community/send'" in student and 'addDoc(collection(db, "chats")' not in student[student.find('async function sendChatMessageWithReply'):student.find('function replyToMessage')], 'student community send is server-authoritative'),
 ("'/api/chat/community/send'" in teacher, 'teacher community send is server-authoritative'),
 ("CLOUDINARY" in admin and 'api.cloudinary.com/v1_1' in admin, 'admin logo uses Cloudinary'),
 ("documents: { provider: 'gcs', resourceType: 'raw', maxSize: 100 * 1024 * 1024" in config, 'Cloudinary raw/document free-plan limit'),
 ('"version": "30.86"' in app, 'version 30.86'),
 ('bookStorageProvider' in (ROOT/'templates/admin.html').read_text() and 'Google Drive Link' in (ROOT/'templates/admin.html').read_text(), 'book UI supports Google Drive link source'),
 ('parseGoogleDriveFileUrl' in (ROOT/'static/admin.js').read_text() and 'previewUrl' in (ROOT/'static/admin.js').read_text() and 'downloadUrl' in (ROOT/'static/admin.js').read_text(), 'Google Drive book link is normalized'),
 ("storageProvider: provider" in (ROOT/'static/admin.js').read_text() and "fileId: drive.fileId" in (ROOT/'static/admin.js').read_text(), 'book provider metadata stores Google Drive file ID'),
 ("data.storageProvider === 'google_drive'" in student and 'Preview' in student, 'student book UI supports Google Drive preview'),
]
failed=False
for ok,msg in checks:
 print(('PASS: ' if ok else 'FAIL: ')+msg); failed |= not ok
raise SystemExit(1 if failed else 0)
