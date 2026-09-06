from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
config = (root/'static/storage-config.js').read_text()
service = (root/'static/storage-service.js').read_text()
admin = (root/'static/admin.js').read_text()
student = (root/'static/student.js').read_text()
html = (root/'templates/admin.html').read_text()
app = (root/'app.py').read_text()
archive = (root/'archive_routes.py').read_text()
requirements = (root/'requirements.txt').read_text()
ai = (root/'ai_material_routes.py').read_text()
checks = [
    ('_require_user_bearer()' in app and 'approved teacher access required for document uploads' in app, 'document upload is role-gated'),
    ("images: { provider: 'cloudinary'" in config and "videos: { provider: 'cloudinary'" in config, 'images/videos remain on Cloudinary'),
    ("documents: { provider: 'gcs'" in config, 'PDF/document uploads route to GCS'),
    ("async function uploadToGCS" in service and "'/api/storage/document'" in service, 'browser documents use authenticated GCS backend'),
    ("@app.post('/api/storage/document')" in app and "from gcs_storage import upload_document, signed_url" in app, 'server exposes GCS document upload'),
    ("@app.post('/api/storage/document-url')" in app and "signed_url(path)" in app, 'server can refresh short-lived signed URLs'),
    ('google-cloud-storage==2.19.0' in requirements, 'GCS SDK pinned'),
    ('value="gcs" selected' in html and 'Google Cloud Storage (recommended)' in html, 'admin book/archive UI defaults to GCS'),
    ("storagePath: result.storagePath || result.path || result.fileId" in admin, 'book metadata stores GCS object path'),
    ("provider not in {\"gcs\", \"cloudinary\", \"google_drive\", \"mega\", \"external\"}" in archive, 'archive API accepts GCS'),
    ("'storagePath': gcs_result.get('path')" in ai, 'teacher AI PDFs retain original GCS object path'),
    ("data.storageProvider === 'gcs'" in student and "/api/storage/document-url" in student, 'student books refresh GCS URLs securely'),
]
for ok, label in checks:
    print(('PASS' if ok else 'FAIL') + ': ' + label)
if not all(ok for ok,_ in checks): sys.exit(1)
print('PASS: GCS storage regression')
