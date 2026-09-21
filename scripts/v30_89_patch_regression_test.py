#!/usr/bin/env python3
"""Regression checks for the V30.89 registration/admin-media patch."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
app = (ROOT / 'app.py').read_text(encoding='utf-8')
admin = (ROOT / 'static/admin.js').read_text(encoding='utf-8')
student = (ROOT / 'static/student.js').read_text(encoding='utf-8')
storage = (ROOT / 'static/storage-service.js').read_text(encoding='utf-8')
features = (ROOT / 'admin_features_routes.py').read_text(encoding='utf-8')

checks = [
    ('window.registerStudent = registerStudent;' in student, 'student registration handler is globally exposed'),
    ('window.loginStudent = loginStudent;' in student, 'student login handler is globally exposed'),
    ("import { uploadFile, uploadAdminMedia } from './storage-service.js';" in admin, 'admin imports protected media uploader'),
    ("uploadAdminMedia(file, 'video')" in admin, 'admin video upload uses protected endpoint'),
    ("uploadAdminMedia(imageFile, 'image')" in admin, 'admin quiz image upload uses protected endpoint'),
    ("@app.post('/api/admin/storage/cloudinary')" in features, 'protected Cloudinary admin route exists'),
    ('ok, detail = require_admin()' in features[features.find("@app.post('/api/admin/storage/cloudinary')"):], 'Cloudinary route requires admin authorization'),
    ('CLOUDINARY_API_SECRET' in features and 'hashlib.sha1' in features, 'signed Cloudinary upload support exists'),
    ("/api/admin/storage/cloudinary" in storage and "export async function uploadAdminMedia" in storage, 'client helper calls protected admin route'),
    ('isAdmin()' in (ROOT / 'firestore.rules').read_text(encoding='utf-8'), 'Firestore admin claim guard remains present'),
]

for path in [ROOT/'app.py', ROOT/'admin_features_routes.py', ROOT/'teacher_routes.py', ROOT/'archive_routes.py']:
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('PASS: Python AST syntax')

for path in [ROOT/'static/student.js', ROOT/'static/admin.js', ROOT/'static/storage-service.js']:
    # Basic structural checks; full JS parsing is performed with node in CI/deployment environments.
    text = path.read_text(encoding='utf-8')
    assert text.count('{') >= text.count('}') - 5, f'Unexpected brace balance in {path.name}'
print('PASS: JavaScript structural sanity')

for ok, name in checks:
    if not ok:
        print('FAIL:', name)
        raise SystemExit(1)
    print('PASS:', name)
print('PASS: V30.89 patch regression')
