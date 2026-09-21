from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from urllib.parse import urlparse


def register_archive_routes(app, require_user_bearer, require_admin_bearer, firebase_admin_from_env):
    """National exam archive API.

    Firestore collection: examArchives/{archiveId}
    The browser never needs direct read access to this collection; Flask
    applies entitlement checks before returning premium archive metadata.
    """
    bp = Blueprint("exam_archive", __name__)

    def _db():
        firebase_admin_from_env()
        from firebase_admin import firestore
        return firestore.client()

    def _clean(value, max_len=120):
        return str(value or "").strip()[:max_len]

    def _normalize_grade(value):
        grade = _clean(value, 20).lower().replace("grade", "").strip()
        if grade not in {str(x) for x in range(6, 13)}:
            raise ValueError("National archive grades are 6 through 12.")
        return int(grade)

    def _validate_url(value, field, provider):
        """Accept only HTTPS archive URLs and provider-appropriate hosts."""
        value = _clean(value, 1000)
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{field} must be a valid HTTPS URL.")
        host = (parsed.hostname or "").lower().rstrip(".")
        if provider == "google_drive" and host not in {"drive.google.com", "docs.google.com"}:
            raise ValueError(f"{field} must use a Google Drive host.")
        if provider == "cloudinary" and not (host == "cloudinary.com" or host.endswith(".cloudinary.com")):
            raise ValueError(f"{field} must use a Cloudinary host.")
        if provider == "mega" and host not in {"mega.nz", "mega.io"} and not host.endswith(".mega.nz"):
            raise ValueError(f"{field} must use a MEGA host.")
        return value

    def _normalize_stream(grade, value):
        stream = _clean(value, 40).lower()
        if grade == 12:
            if stream not in {"natural sciences", "social sciences"}:
                raise ValueError("Grade 12 requires Natural Sciences or Social Sciences stream.")
            return "Natural Sciences" if stream == "natural sciences" else "Social Sciences"
        return None

    def _archive_doc(data, archive_id):
        return {
            "id": archive_id,
            "title": data.get("title", ""),
            "grade": data.get("grade"),
            "stream": data.get("stream"),
            "subject": data.get("subject", ""),
            "year": data.get("year"),
            "isPremium": bool(data.get("isPremium", True)),
            "accessTier": "paid" if bool(data.get("isPremium", True)) else "free",
            "priceLabel": data.get("priceLabel") or (data.get("price") if data.get("isPremium", True) else "Free"),
            "currency": data.get("currency", "ETB"),
            "storageProvider": data.get("storageProvider", "cloudinary"),
            "fileId": data.get("fileId", ""),
            "previewUrl": data.get("previewUrl", ""),
            "downloadUrl": data.get("downloadUrl", ""),
            "storagePath": data.get("storagePath", ""),
            "createdAt": data.get("createdAt").isoformat() if isinstance(data.get("createdAt"), datetime) else None,
            "updatedAt": data.get("updatedAt").isoformat() if isinstance(data.get("updatedAt"), datetime) else None,
        }

    def _has_archive_access(uid):
        """Backward-compatible entitlement check for the national archive.

        Existing premium subscribers keep access. New entitlement documents can
        explicitly grant examArchive access without granting every premium feature.
        """
        snap = _db().collection("entitlements").document(uid).get()
        if not snap.exists:
            return False
        data = snap.to_dict() or {}
        expires = data.get("expiresAt")
        if isinstance(expires, datetime) and expires <= datetime.now(timezone.utc):
            return False
        features = data.get("features") or {}
        products = data.get("products") or {}
        return bool(
            data.get("premium")
            or data.get("examArchive")
            or features.get("examArchive")
            or products.get("national_exam_archive")
            or products.get("examArchive")
        )

    @bp.get("/api/exam-archive")
    def list_archive():
        ok, detail = require_user_bearer()
        if not ok:
            return detail

        grade_raw = request.args.get("grade", "")
        subject = _clean(request.args.get("subject", ""), 80)
        stream = _clean(request.args.get("stream", ""), 40)
        year_raw = _clean(request.args.get("year", ""), 10)

        try:
            grade = _normalize_grade(grade_raw) if grade_raw else None
            if grade == 12 and stream:
                stream = _normalize_stream(12, stream)
            elif grade != 12:
                stream = None
            year = int(year_raw) if year_raw else None
            if year is not None and not 1900 <= year <= 2100:
                raise ValueError("Invalid year.")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        uid = detail.get("uid")
        premium = _has_archive_access(uid)
        query = _db().collection("examArchives")
        if grade is not None:
            query = query.where("grade", "==", grade)
        if subject:
            query = query.where("subject", "==", subject)
        if stream:
            query = query.where("stream", "==", stream)
        if year is not None:
            query = query.where("year", "==", year)

        docs = []
        for snap in query.stream():
            data = snap.to_dict() or {}
            if bool(data.get("isPremium", True)) and not premium:
                docs.append({
                    "id": snap.id,
                    "title": data.get("title", ""),
                    "grade": data.get("grade"),
                    "stream": data.get("stream"),
                    "subject": data.get("subject", ""),
                    "year": data.get("year"),
                    "isPremium": True,
                    "locked": True,
                })
            else:
                docs.append(_archive_doc(data, snap.id))

        docs.sort(key=lambda x: (x.get("grade") or 0, x.get("stream") or "", x.get("subject") or "", -(x.get("year") or 0)))
        return jsonify({"premium": premium, "count": len(docs), "items": docs})

    @bp.get("/api/exam-archive/<archive_id>")
    def get_archive_item(archive_id):
        """Return one archive item only after entitlement filtering."""
        ok, detail = require_user_bearer()
        if not ok:
            return detail
        ref = _db().collection("examArchives").document(_clean(archive_id, 150))
        snap = ref.get()
        if not snap.exists:
            return jsonify({"error": "Archive item not found."}), 404
        data = snap.to_dict() or {}
        if bool(data.get("isPremium", True)) and not _has_archive_access(detail.get("uid")):
            return jsonify({"error": "Premium archive access is required."}), 403
        return jsonify({"item": _archive_doc(data, snap.id)})

    @bp.post("/api/admin/exam-archive")
    def create_archive():
        ok, detail = require_admin_bearer()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        try:
            grade = _normalize_grade(body.get("grade"))
            stream = _normalize_stream(grade, body.get("stream"))
            subject = _clean(body.get("subject"), 80)
            title = _clean(body.get("title"), 160)
            year = int(body.get("year"))
            provider = _clean(body.get("storageProvider", "gcs"), 30).lower()
            file_id = _clean(body.get("fileId"), 300)
            storage_path = _clean(body.get("storagePath"), 500)
            preview_url = _clean(body.get("previewUrl"), 1000)
            download_url = _clean(body.get("downloadUrl"), 1000)
            is_premium = bool(body.get("isPremium", True))
            price_label = _clean(body.get("priceLabel") or (body.get("price") if is_premium else "Free"), 80)
            currency = _clean(body.get("currency", "ETB"), 8).upper() or "ETB"
            if is_premium and not price_label:
                raise ValueError("Paid archive items require a visible price label.")
            if not is_premium:
                price_label = "Free"
            if not subject or not title or not 1900 <= year <= 2100:
                raise ValueError("title, subject and a valid year are required.")
            if provider not in {"gcs", "cloudinary", "google_drive", "mega", "external"}:
                raise ValueError("Unsupported storage provider.")
            if provider == "google_drive" and file_id:
                if not preview_url:
                    preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
                if not download_url:
                    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            if provider in {"cloudinary", "mega", "external"} and not (preview_url or download_url or file_id):
                raise ValueError("For Mega/External storage provide a preview or download URL.")
            if provider == "gcs" and not storage_path and file_id:
                storage_path = file_id
            if provider != "gcs" and not file_id and not preview_url and not download_url:
                raise ValueError("fileId or previewUrl is required.")
            preview_url = _validate_url(preview_url, "previewUrl", provider)
            download_url = _validate_url(download_url, "downloadUrl", provider)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

        from firebase_admin import firestore
        now = datetime.now(timezone.utc)
        ref = _db().collection("examArchives").document()
        data = {
            "title": title,
            "grade": grade,
            "stream": stream,
            "subject": subject,
            "year": year,
            "isPremium": is_premium,
            "accessTier": "paid" if is_premium else "free",
            "priceLabel": price_label,
            "currency": currency,
            "storageProvider": provider,
            "fileId": file_id,
            "storagePath": storage_path,
            "previewUrl": preview_url,
            "downloadUrl": download_url,
            "createdAt": now,
            "updatedAt": now,
            "createdBy": detail.get("uid"),
        }
        ref.set(data)
        return jsonify({"success": True, "item": _archive_doc(data, ref.id)}), 201

    @bp.patch("/api/admin/exam-archive/<archive_id>")
    def update_archive(archive_id):
        ok, detail = require_admin_bearer()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        ref = _db().collection("examArchives").document(_clean(archive_id, 150))
        snap = ref.get()
        if not snap.exists:
            return jsonify({"error": "Archive item not found."}), 404
        current = snap.to_dict() or {}
        merged = {**current, **body}
        try:
            grade = _normalize_grade(merged.get("grade"))
            stream = _normalize_stream(grade, merged.get("stream"))
            year = int(merged.get("year"))
            provider = _clean(merged.get("storageProvider", "gcs"), 30).lower()
            if provider not in {"gcs", "cloudinary", "google_drive", "mega", "external"}:
                raise ValueError("Unsupported storage provider.")
            update = {
                "title": _clean(merged.get("title"), 160),
                "grade": grade,
                "stream": stream,
                "subject": _clean(merged.get("subject"), 80),
                "year": year,
                "isPremium": bool(merged.get("isPremium", True)),
                "storageProvider": provider,
                "fileId": _clean(merged.get("fileId"), 300),
                "storagePath": _clean(merged.get("storagePath"), 500),
                "previewUrl": _clean(merged.get("previewUrl"), 1000),
                "downloadUrl": _clean(merged.get("downloadUrl"), 1000),
                "updatedAt": datetime.now(timezone.utc),
            }
            if provider == "google_drive" and update["fileId"]:
                if not update["previewUrl"]:
                    update["previewUrl"] = f"https://drive.google.com/file/d/{update['fileId']}/preview"
                if not update["downloadUrl"]:
                    update["downloadUrl"] = f"https://drive.google.com/uc?export=download&id={update['fileId']}"
            update["previewUrl"] = _validate_url(update["previewUrl"], "previewUrl", provider)
            update["downloadUrl"] = _validate_url(update["downloadUrl"], "downloadUrl", provider)
            if not update["title"] or not update["subject"] or not 1900 <= year <= 2100:
                raise ValueError("title, subject and valid year are required.")
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        ref.update(update)
        snap = ref.get()
        return jsonify({"success": True, "item": _archive_doc(snap.to_dict() or {}, ref.id)})

    @bp.delete("/api/admin/exam-archive/<archive_id>")
    def delete_archive(archive_id):
        ok, detail = require_admin_bearer()
        if not ok:
            return detail
        ref = _db().collection("examArchives").document(_clean(archive_id, 150))
        if not ref.get().exists:
            return jsonify({"error": "Archive item not found."}), 404
        ref.delete()
        return jsonify({"success": True})

    app.register_blueprint(bp)
