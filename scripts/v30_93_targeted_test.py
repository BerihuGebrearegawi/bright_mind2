"""Static targeted checks for V30.93 hardening."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "app.py").read_text(encoding="utf-8")
archive = (root / "archive_routes.py").read_text(encoding="utf-8")

checks = {
    "FCM error-code diagnostics": '"errorCodes": error_codes' in app,
    "FCM invalid-token cleanup": 'collection("fcmTokens").document(token_id)' in app,
    "FCM safe configuration category": 'FCM_CONFIGURATION_OR_PERMISSION_ERROR' in app,
    "Archive HTTPS validation": 'parsed.scheme != "https"' in archive,
    "Google Drive host validation": 'drive.google.com' in archive,
    "Cloudinary host validation": 'cloudinary.com' in archive,
    "MEGA host validation": 'mega.nz' in archive,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}: {name}")
if failed:
    raise SystemExit(f"Targeted checks failed: {failed}")
print("All V30.93 targeted checks passed.")
