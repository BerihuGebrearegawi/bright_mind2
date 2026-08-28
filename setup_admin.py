"""Bright Mind Tutor admin bootstrap.

Sets Firebase custom claims for the configured administrator account.
It never stores the service-account key in the project.

Usage:
  python setup_admin.py

The script reads FIREBASE_SERVICE_ACCOUNT_JSON when present. Otherwise it
asks for the path to a downloaded Firebase service-account JSON file.
"""
import getpass
import json
import os
from pathlib import Path

PROJECT_ID = "bright-mind-tutor-app"
DEFAULT_EMAIL = "berihu144@gmail.com"


def load_service_account():
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
        return data

    entered = input("Path to Firebase service-account JSON: ").strip().strip('"')
    if not entered:
        raise SystemExit("No service-account file was provided.")
    path = Path(entered).expanduser()
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit("The selected file is not valid JSON.") from exc


def main():
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
    except ImportError:
        raise SystemExit("firebase-admin is not installed. Run: py -m pip install firebase-admin==6.9.0")

    data = load_service_account()
    if data.get("project_id") != PROJECT_ID:
        raise SystemExit(
            f"Wrong Firebase project. Expected {PROJECT_ID!r}, got {data.get('project_id')!r}."
        )
    if not data.get("private_key") or not data.get("client_email"):
        raise SystemExit("The service-account JSON is missing required credentials.")

    email = input(f"Admin email [{DEFAULT_EMAIL}]: ").strip().lower() or DEFAULT_EMAIL
    if not email or "@" not in email:
        raise SystemExit("Please enter a valid admin email.")

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(data))

    try:
        user = auth.get_user_by_email(email)
    except Exception as exc:
        raise SystemExit(
            f"Firebase user {email!r} was not found. Create the account in Firebase Authentication first."
        ) from exc

    current = dict(user.custom_claims or {})
    current.update({"admin": True, "role": "admin"})
    auth.set_custom_user_claims(user.uid, current)

    print("\nSUCCESS")
    print(f"Admin: {email}")
    print(f"UID:   {user.uid}")
    print("Claims: admin=true, role=admin")
    print("\nNow sign out of Bright Mind Tutor, close the browser tab, reopen /admin, and sign in again.")


if __name__ == "__main__":
    main()
