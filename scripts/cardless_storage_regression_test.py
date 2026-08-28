"""Compatibility name retained for the former cardless-storage test.
The production architecture is now Render + Firestore + Cloudinary + GCS.
"""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
config=(root/'static/storage-config.js').read_text(); service=(root/'static/storage-service.js').read_text(); html=(root/'templates/admin.html').read_text(); archive=(root/'archive_routes.py').read_text()
checks=[
    ("images: { provider: 'cloudinary'" in config, 'images use Cloudinary'),
    ("videos: { provider: 'cloudinary'" in config, 'videos use Cloudinary'),
    ("documents: { provider: 'gcs'" in config, 'documents use GCS'),
    ("async function uploadToGCS" in service, 'GCS uploader exists'),
    ('value="gcs" selected' in html, 'admin defaults to GCS'),
    ('provider not in {"gcs", "cloudinary", "google_drive", "mega", "external"}' in archive, 'archive accepts GCS and legacy providers'),
]
for ok,label in checks: print(('PASS' if ok else 'FAIL')+': '+label)
if not all(x[0] for x in checks): sys.exit(1)
print('PASS: storage architecture regression')
