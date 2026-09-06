"""Teacher AI-material ingestion for Bright Mind Tutor.

The original file (PDF/lesson) can remain in the existing hybrid storage layer
(Google Drive/Cloudinary/etc.). This service stores only searchable text chunks
and metadata in Firestore, keeping the AI layer independent from file storage.
"""
import io
import re
from datetime import datetime, timezone
from flask import request, jsonify

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
CHUNK_SIZE = 6500
CHUNK_OVERLAP = 500
ALLOWED_GRADES = {"KG", *[str(i) for i in range(1, 13)]}


def _clean(value, limit):
    return str(value or "").strip()[:limit]


def _chunks(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + 1000:
                end = boundary
        part = text[start:end].strip()
        if part:
            out.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return out


def _extract_pdf(raw):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF text extraction requires pypdf on the server.") from exc
    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for page in reader.pages[:200]:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def register_ai_material_routes(app, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _teacher(db, uid):
        snap = db.collection("teachers").document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        return data if data.get("approved") or data.get("status") == "approved" else None

    @app.post('/api/teacher/ai-copilot')
    def teacher_ai_copilot():
        """Teacher-side AI copilot using the existing Gemini configuration."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = str(detail.get('uid', '')).strip()
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            body = request.get_json(silent=True) or {}
            task = _clean(body.get('task'), 6000)
            grade = _clean(body.get('grade'), 20)
            subject = _clean(body.get('subject'), 80)
            if not task:
                return jsonify({'error': 'Teaching task is required.'}), 400
            from ai_tutor_service import ask_gemini, check_and_record_quota
            allowed, remaining = check_and_record_quota(lambda: db, uid, premium=True)
            if not allowed:
                return jsonify({'error': 'Daily AI Copilot limit reached.', 'remaining': 0}), 429
            prompt = (
                'You are the Bright Mind Tutor Teacher Copilot. Help an approved school teacher '
                'prepare useful, age-appropriate teaching material. Return a polished result with '
                'clear headings. When asked for a lesson, include objectives, prerequisite knowledge, '
                'explanation, worked examples, guided activity, independent practice, differentiation, '
                'exit ticket and homework. When asked for a quiz, include an answer key. Never invent '
                'official curriculum requirements; label suggestions as suggestions.\n\nTask:\n' + task
            )
            answer, model = ask_gemini(prompt, [], grade, subject, [])
            return jsonify({'answer': answer, 'model': model, 'remainingToday': remaining})
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Teacher AI copilot failure')
            return jsonify({'error': 'Teacher AI Copilot is temporarily unavailable.'}), 502

    @app.get('/api/teacher/ai-materials')
    def list_ai_materials():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            docs = db.collection('aiMaterials').where('teacherUid', '==', uid).limit(100).stream()
            items = []
            for d in docs:
                x = d.to_dict() or {}
                items.append({"id": d.id, "title": x.get("title"), "grade": x.get("grade"),
                              "subject": x.get("subject"), "sourceType": x.get("sourceType"),
                              "sourceUrl": x.get("sourceUrl"), "chunkCount": x.get("chunkCount", 0),
                              "active": x.get("active", True)})
            return jsonify({"materials": items})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post('/api/teacher/ai-materials')
    def create_ai_material():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            teacher = _teacher(db, uid)
            if not teacher:
                return jsonify({"error": "Approved teacher access required."}), 403

            title = _clean(request.form.get('title') or (request.get_json(silent=True) or {}).get('title'), 180)
            grade = _clean(request.form.get('grade') or (request.get_json(silent=True) or {}).get('grade'), 10)
            subject = _clean(request.form.get('subject') or (request.get_json(silent=True) or {}).get('subject'), 80)
            source_url = _clean(request.form.get('sourceUrl') or (request.get_json(silent=True) or {}).get('sourceUrl'), 2000)
            text = _clean(request.form.get('text') or (request.get_json(silent=True) or {}).get('text'), MAX_TEXT_CHARS)
            upload = request.files.get('file')

            if not title or grade not in ALLOWED_GRADES or not subject:
                return jsonify({"error": "Title, valid grade and subject are required."}), 400
            source_type = 'text'
            gcs_result = None
            if upload:
                raw = upload.read(MAX_PDF_BYTES + 1)
                if len(raw) > MAX_PDF_BYTES:
                    return jsonify({"error": "PDF is too large. Maximum is 12 MB."}), 413
                if not raw:
                    return jsonify({"error": "Uploaded file is empty."}), 400
                text = _extract_pdf(raw)
                source_type = 'pdf'
                try:
                    from gcs_storage import upload_document, signed_url
                    gcs_result = upload_document(raw, upload.filename, upload.content_type or 'application/pdf', uid, folder='ai-materials')
                    source_url = signed_url(gcs_result['path'])
                except Exception as exc:
                    app.logger.exception('AI material GCS storage failed')
                    return jsonify({"error": f"Could not store the original PDF: {exc}"}), 502
            elif not text:
                return jsonify({"error": "Provide lesson text or upload a PDF."}), 400

            text = text[:MAX_TEXT_CHARS]
            parts = _chunks(text)
            if not parts:
                return jsonify({"error": "No readable text was found. Scanned PDFs need OCR before ingestion."}), 400

            now = datetime.now(timezone.utc)
            material_ref = db.collection('aiMaterials').document()
            material_ref.set({
                'title': title, 'grade': grade, 'subject': subject,
                'sourceType': source_type, 'sourceUrl': source_url,
                'storageProvider': gcs_result.get('provider') if gcs_result else None,
                'storagePath': gcs_result.get('path') if gcs_result else None,
                'storageBucket': gcs_result.get('bucket') if gcs_result else None,
                'teacherUid': uid, 'active': True, 'chunkCount': len(parts),
                'createdAt': now, 'updatedAt': now,
            })

            # Generate embeddings once at ingestion time. If embeddings are not
            # configured, keep ingestion working; retrieval will use lexical fallback.
            embeddings = []
            try:
                from embedding_service import embed_texts
                embeddings = embed_texts([f'{title}\n{content}' for content in parts], task_type='RETRIEVAL_DOCUMENT')
            except Exception as exc:
                app.logger.warning('Embedding generation skipped: %s', exc)

            batch = db.batch()
            for i, content in enumerate(parts):
                ref = db.collection('aiMaterialChunks').document()
                batch.set(ref, {
                    'materialId': material_ref.id, 'teacherUid': uid,
                    'title': title, 'grade': grade, 'subject': subject,
                    'chunkIndex': i, 'content': content, 'active': True,
                    'createdAt': now,
                    'embedding': embeddings[i] if i < len(embeddings) else [],
                    'embeddingModel': __import__('os').getenv('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001') if i < len(embeddings) else None,
                })
                if (i + 1) % 400 == 0:
                    batch.commit(); batch = db.batch()
            batch.commit()
            return jsonify({"success": True, "materialId": material_ref.id,
                            "chunks": len(parts), "sourceType": source_type}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception as exc:
            app.logger.exception("AI material ingestion failed")
            return jsonify({"error": str(exc)}), 500

    @app.delete('/api/teacher/ai-materials/<material_id>')
    def delete_ai_material(material_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            ref = db.collection('aiMaterials').document(material_id)
            snap = ref.get()
            if not snap.exists or (snap.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Material not found."}), 404
            chunks = db.collection('aiMaterialChunks').where('materialId', '==', material_id).stream()
            batch = db.batch(); count = 0
            for d in chunks:
                batch.delete(d.reference); count += 1
                if count % 400 == 0:
                    batch.commit(); batch = db.batch()
            batch.commit(); ref.delete()
            return jsonify({"success": True, "deletedChunks": count})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
