"""Cloudinary adapter for BMT books and PDF/documents.

Drop-in replacement for gcs_storage.py that does NOT require a Google Cloud
billing account (no credit card needed) - it uses Cloudinary's free tier,
which is already used elsewhere in this app for images/logos.

NOTE ON THE 'gcs' LABEL: the rest of the codebase (see app.py's
_can_access_gcs_book) authorizes document access by checking
book.get('storageProvider') == 'gcs'. Rather than touching every one of
those checks across the codebase, this module intentionally keeps
provider="gcs" in its return value - it just means "goes through BMT's
controlled document-storage flow", not literally Google Cloud Storage.
The real storage backend here is Cloudinary.

ACCESS MODES:
  access="authenticated" (default) - the resource is uploaded with
    Cloudinary's `type="private"`. It is never reachable through a plain
    CDN URL (guessable or not) - the ONLY way to fetch it is a freshly
    minted, genuinely time-limited link from signed_url() below. This
    closes two separate problems the original version of this module had:
      1. The old code returned a permanent, unsigned, public
         `res.cloudinary.com/.../raw/upload/...` URL. Anyone who ever saw
         that URL (browser devtools, history, a forwarded link, a cached
         request) could fetch the file forever - there was no expiry and
         no way to revoke it. Protection relied entirely on the public_id
         being an unguessable UUID (security through obscurity, not real
         access control).
      2. A later revision switched to Cloudinary's `type="authenticated"` +
         `sign_url=True`, which does stop guessing/enumeration and does
         require a fresh authorization check to mint a URL - but that URL
         *still never expires* (its own docstring flagged this as a
         CAVEAT). A leaked authenticated-but-unexpiring URL is still a
         standing bearer credential forever.
    This version fixes both: documents are uploaded with `type="private"`,
    and signed_url() calls Cloudinary's Upload API `/download` endpoint
    (`cloudinary.utils.private_download_url`) with an `expires_at`
    timestamp. That endpoint is authenticated with the account's API
    secret (api.cloudinary.com, not the CDN) and is available on every
    Cloudinary plan. It is NOT the same as Cloudinary's CDN-level
    "token-based authentication", which Cloudinary restricts to
    Advanced-plan accounts with a custom CNAME - so this works on the free
    tier the rest of this app is built for.
  access="public" - for content that is intentionally open to any
    logged-in user once published (e.g. Development Center admin media),
    where per-request authorization isn't the model to begin with. This
    keeps the previous simple "one permanent URL" behavior.

Required environment variables on Render (no credit card required, free
Cloudinary account is enough):
  CLOUDINARY_CLOUD_NAME
  CLOUDINARY_API_KEY
  CLOUDINARY_API_SECRET

Optional:
  CLOUDINARY_SIGNED_URL_TTL_SECONDS - how long a minted document link stays
  valid (default 300 seconds / 5 minutes; capped at 1 hour). Every access
  point that calls signed_url() (see app.py's document_access_url and
  upload_document_proxy) re-checks Firestore authorization before minting
  a new link, so a short TTL costs nothing in normal use - the browser
  loads/downloads the file within seconds of receiving the link.
"""
import io
import os
import re
import time
import uuid
from pathlib import Path

import cloudinary
import cloudinary.uploader
import cloudinary.utils

_VALID_ACCESS_MODES = {"authenticated", "public"}
DEFAULT_SIGNED_URL_TTL_SECONDS = 300
MAX_SIGNED_URL_TTL_SECONDS = 3600


def _config():
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()
    if not cloud_name or not api_key or not api_secret:
        raise RuntimeError(
            "Cloudinary is not configured on Render. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )
    # cloudinary's config is a process-wide singleton. Re-applying it on every
    # call is cheap (no network I/O) and keeps it correct even if this module
    # is imported before env vars are fully available (e.g. under a test
    # runner), without needing a separate app-startup init step.
    cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
    return cloud_name, api_key, api_secret


def _safe_name(name):
    name = Path(name or "document.pdf").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return (name or "document.pdf")[:180]


def _resolve_access(access):
    access = str(access or "authenticated").strip().lower()
    if access not in _VALID_ACCESS_MODES:
        raise ValueError(f"Unknown Cloudinary access mode: {access}")
    return access


def upload_document(raw, filename, content_type, uid, folder="books", access="authenticated"):
    """Upload a document to Cloudinary. Mirrors gcs_storage.upload_document's return shape.

    access="authenticated" (default): uploaded with type="private" - the
    file is only ever retrievable through a freshly-minted, time-limited
    signed_url() call. See the module docstring.
    access="public": uploaded with type="upload" - retrievable forever via
    the plain Cloudinary URL returned as `_cloudinaryUrl`. Only use this
    for content that is meant to be openly available to any logged-in user.
    """
    access = _resolve_access(access)
    cloud_name, _api_key, _api_secret = _config()
    safe = _safe_name(filename)
    # The extension is folded into the public_id itself (rather than passed
    # as a separate Cloudinary `format`) so the single stored path/fileId
    # string stays a complete, self-sufficient identifier for signed_url()
    # later - no extra field needs to be threaded through Firestore/app.py.
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    ext = re.sub(r"[^a-z0-9]", "", ext)[:10]
    public_id = f"bmt/{folder}/{uid}/{uuid.uuid4().hex}" + (f".{ext}" if ext else "")
    cloud_type = "private" if access == "authenticated" else "upload"
    try:
        payload = cloudinary.uploader.upload(
            io.BytesIO(raw),
            resource_type="raw",
            type=cloud_type,
            public_id=public_id,
            filename=safe,
            use_filename=False,
            unique_filename=False,
            overwrite=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Cloudinary upload failed: {exc}") from exc
    if not payload.get("public_id"):
        raise RuntimeError("Cloudinary upload failed.")
    return {
        "bucket": cloud_name,
        "path": payload["public_id"],
        "fileId": payload["public_id"],
        "provider": "gcs",  # see module docstring - kept for compatibility
        "contentType": content_type or "application/octet-stream",
        "fileName": safe,
        "access": access,
        # Only meaningful when access="public" - a private upload has no
        # URL that works without signed_url()'s time-limited signature.
        "_cloudinaryUrl": payload.get("secure_url", "") if access == "public" else "",
    }


def signed_url(path, expiration_seconds=None, access="authenticated"):
    """Return a Cloudinary delivery URL for a previously uploaded document.

    access="authenticated" (default): mints a genuinely time-limited,
    signed download link (Cloudinary's Upload API `/download` endpoint,
    signed with the account's API secret and an `expires_at` timestamp).
    Call this again on every authorized request rather than
    caching/reusing the result - each call also re-proves the caller was
    authorized at the moment the link was minted (see app.py's
    _can_access_gcs_book / document_access_url).
    access="public": returns the plain, permanent delivery URL for content
    uploaded with access="public".

    expiration_seconds: how long the minted link stays valid, in seconds
    (default CLOUDINARY_SIGNED_URL_TTL_SECONDS or 300; capped at 3600).
    Ignored when access="public".
    """
    access = _resolve_access(access)
    cloud_name, _api_key, _api_secret = _config()
    if access == "public":
        url, _options = cloudinary.utils.cloudinary_url(path, resource_type="raw", type="upload", secure=True)
        return url

    ttl = expiration_seconds if expiration_seconds is not None else os.getenv(
        "CLOUDINARY_SIGNED_URL_TTL_SECONDS", str(DEFAULT_SIGNED_URL_TTL_SECONDS)
    )
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        ttl = DEFAULT_SIGNED_URL_TTL_SECONDS
    ttl = max(30, min(ttl, MAX_SIGNED_URL_TTL_SECONDS))
    expires_at = int(time.time()) + ttl

    # private_download_url signs a call to Cloudinary's Upload API
    # `/download` endpoint (api.cloudinary.com, NOT the CDN) with the
    # account's API secret and this expires_at timestamp. The extension is
    # already folded into `path` (see upload_document), so no separate
    # `format` argument is needed here.
    return cloudinary.utils.private_download_url(
        path,
        None,
        resource_type="raw",
        type="private",
        attachment=False,
        expires_at=expires_at,
    )
