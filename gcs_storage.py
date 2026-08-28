"""Google Cloud Storage adapter for BMT books and PDF/documents.

Uses the same Firebase service-account JSON already configured on Render.
The browser never receives service-account credentials.
"""
import base64
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from google.cloud import storage
from google.oauth2 import service_account


def _service_account_info():
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    b64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_B64", "").strip()
    path = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
    if not raw:
        fields = {
            "type": os.getenv("FIREBASE_TYPE", "service_account").strip(),
            "project_id": os.getenv("FIREBASE_PROJECT_ID", "").strip(),
            "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID", "").strip(),
            "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").strip(),
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL", "").strip(),
            "client_id": os.getenv("FIREBASE_CLIENT_ID", "").strip(),
            "auth_uri": os.getenv("FIREBASE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth").strip(),
            "token_uri": os.getenv("FIREBASE_TOKEN_URI", "https://oauth2.googleapis.com/token").strip(),
            "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs").strip(),
            "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL", "").strip(),
        }
        if fields["project_id"] and fields["private_key"] and fields["client_email"]:
            raw = json.dumps({k:v for k,v in fields.items() if v})
    if not raw and b64:
        raw = base64.b64decode(b64).decode("utf-8").strip()
    if not raw and path:
        raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("Firebase service credentials are not configured.")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    info = json.loads(raw)
    if isinstance(info, str):
        info = json.loads(info)
    if not info.get("project_id") or not info.get("private_key") or not info.get("client_email"):
        raise RuntimeError("Firebase service credentials are incomplete.")
    info["private_key"] = str(info["private_key"]).replace("\\n", "\n")
    return info


def _bucket_name():
    value = os.getenv("GCS_BUCKET_NAME", "").strip()
    if not value:
        raise RuntimeError("GCS_BUCKET_NAME is not configured on Render.")
    return value


def _client():
    info = _service_account_info()
    credentials = service_account.Credentials.from_service_account_info(info)
    return storage.Client(project=info["project_id"], credentials=credentials)


def _safe_name(name):
    name = Path(name or "document.pdf").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return (name or "document.pdf")[:180]


def upload_document(raw, filename, content_type, uid, folder="books"):
    bucket_name = _bucket_name()
    client = _client()
    bucket = client.bucket(bucket_name)
    safe = _safe_name(filename)
    object_name = f"bmt/{folder}/{uid}/{__import__('uuid').uuid4().hex}-{safe}"
    blob = bucket.blob(object_name)
    blob.upload_from_string(raw, content_type=content_type or "application/octet-stream")
    return {
        "bucket": bucket_name,
        "path": object_name,
        "fileId": object_name,
        "provider": "gcs",
        "contentType": content_type or "application/octet-stream",
        "fileName": safe,
    }


def signed_url(path, expiration_seconds=None):
    expiration_seconds = int(expiration_seconds or os.getenv("GCS_SIGNED_URL_SECONDS", "3600"))
    expiration_seconds = max(300, min(expiration_seconds, 604800))
    client = _client()
    bucket = client.bucket(_bucket_name())
    blob = bucket.blob(path)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiration_seconds),
        method="GET",
    )
