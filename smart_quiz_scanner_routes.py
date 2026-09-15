"""Smart Quiz Scanner owned by ChatGPT B.

Flow: image/PDF -> OCR/question extraction -> server-stored editable draft ->
review/approval -> import into the existing quizzes collection.

This module deliberately does not create a Library/Reader/Scanner architecture
owned by Cloud-B. Library metadata is carried as integration fields only.
"""
import base64
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone

import requests
from flask import request, jsonify

MAX_SCAN_BYTES = 10 * 1024 * 1024
MAX_PAGES = 8
MAX_QUESTIONS = 40
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_PDF_TYPES = {"application/pdf"}
ALLOWED_BOOK_COLLECTIONS = {'books', 'bookLibrary', 'teacherGuides', 'referenceBooks', 'psychologyBooks'}

LIBRARY_FIELDS = (
    "collection", "bookId", "chapterId", "subchapterId",
    "learningObjective", "pageNumber", "sourceRef", "sourceScanId"
)


def _clean(value, limit=500):
    return str(value or "").strip()[:limit]


def _now():
    return datetime.now(timezone.utc)


def _db(firebase_admin_factory):
    fb = firebase_admin_factory()
    if not fb:
        raise RuntimeError("Firebase server credentials are not configured.")
    from firebase_admin import firestore
    return firestore.client()


def _approved_teacher(db, uid):
    snap = db.collection("teachers").document(uid).get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    return bool(data.get("approved") or data.get("status") == "approved")


def _staff_detail(detail, db):
    if detail.get("admin") is True or detail.get("role") == "admin":
        return "admin"
    uid = _clean(detail.get("uid"), 160)
    if uid and _approved_teacher(db, uid):
        return "teacher"
    return None


def _parse_json_object(text):
    text = str(text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("AI returned no JSON object.")
        return json.loads(match.group(0))


def _normalize_question(raw):
    if not isinstance(raw, dict):
        return None
    question = _clean(raw.get("question"), 4000)
    options = raw.get("options")
    if isinstance(options, list):
        # A short list (e.g. only 2-3 options extracted) must still produce
        # a full A-D dict with the missing letters blank, not a partial dict
        # - the ABCD lookup below assumes all four keys exist and otherwise
        # raises KeyError, turning one malformed question into a 500 for the
        # entire request instead of that question being safely dropped.
        options = {chr(65 + i): "" for i in range(4)}
        options.update({chr(65 + i): _clean(v, 1000) for i, v in enumerate(raw.get("options")[:4])})
    elif isinstance(options, dict):
        options = {k: _clean(options.get(k), 1000) for k in "ABCD"}
    else:
        options = {k: "" for k in "ABCD"}
    if not question or any(not options[k] for k in "ABCD"):
        return None
    answer = _clean(raw.get("correctAnswer"), 1).upper()
    if answer not in {"A", "B", "C", "D"}:
        answer = ""
    try:
        page = int(raw.get("pageNumber")) if raw.get("pageNumber") is not None else None
    except (TypeError, ValueError):
        page = None
    return {
        "question": question,
        "options": options,
        "correctAnswer": answer,
        "pageNumber": page,
        "sourceRef": _clean(raw.get("sourceRef"), 1500),
        "learningObjective": _clean(raw.get("learningObjective"), 1000),
    }


def _extract_with_gemini(images, source_name):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OCR extraction is not configured. Add GEMINI_API_KEY to the server environment.")
    model = os.getenv("GEMINI_VISION_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    parts = [{"text": (
        "You are the BMT Smart Quiz Scanner. Extract only clearly visible multiple-choice "
        "questions from the supplied page images. Return ONLY JSON with key questions. "
        "Each item must contain question, options (exactly four A-D strings), correctAnswer "
        "(A-D only when an answer is explicitly visible; otherwise empty string), pageNumber, "
        "sourceRef, and learningObjective. Never invent missing text, answers, objectives, "
        "or metadata. "
        "MATH AND PHYSICS NOTATION: write every formula, equation, fraction, exponent, "
        "subscript, square root, unit, and Greek letter as LaTeX. Wrap inline math in single "
        "dollar signs, e.g. $x^2 + 3x = 0$, $\\frac{1}{2}mv^2$, $v = u + at$, $\\theta$. Use "
        "double dollar signs for a standalone displayed equation, e.g. $$F = ma$$. Do not use "
        "plain-text approximations like x^2 or 1/2 outside of LaTeX — always wrap them in $...$. "
        "DIAGRAMS AND GRAPHS: if a question depends on a diagram, circuit, graph, or figure "
        "that cannot be represented in text, briefly describe the diagram in words inside the "
        "question text (e.g. '[Diagram: a 5kg block on a 30-degree incline]') so the question "
        "still makes sense on its own; never invent values that aren't visible in the figure. "
        f"Source file: {source_name}."
    )}]
    for page_number, mime, raw in images:
        parts.append({"text": f"Page number: {page_number}"})
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    timeout = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))
    response = requests.post(url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=payload, timeout=timeout)
    if not response.ok:
        raise RuntimeError("OCR extraction service rejected the request.")
    data = response.json()
    text_parts = []
    for candidate in data.get("candidates", []) or []:
        for part in candidate.get("content", {}).get("parts", []) or []:
            if isinstance(part, dict) and part.get("text"):
                text_parts.append(str(part["text"]))
    parsed = _parse_json_object("\n".join(text_parts))
    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list):
        raise RuntimeError("OCR extraction returned an invalid question list.")
    normalized = []
    for raw in questions[:MAX_QUESTIONS]:
        item = _normalize_question(raw)
        if item:
            normalized.append(item)
    return normalized, model


def _pdf_pages(raw):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Scanned PDF OCR requires PyMuPDF on the server.") from exc
    doc = fitz.open(stream=raw, filetype="pdf")
    images = []
    # 2.5x zoom (~180 DPI) instead of 1.5x (~108 DPI): fine details in math/physics
    # notation — fraction bars, exponents, subscripts, small symbols — need higher
    # resolution than plain paragraph text to be read correctly by the OCR model.
    for idx, page in enumerate(doc[:MAX_PAGES], start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
        images.append((idx, "image/png", pix.tobytes("png")))
    doc.close()
    return images


def _text_pdf(raw):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF text extraction requires pypdf on the server.") from exc
    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for idx, page in enumerate(reader.pages[:MAX_PAGES], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((idx, text))
    return pages


def register_smart_quiz_scanner_routes(app, require_user, require_admin, firebase_admin_factory):
    @app.post("/api/scanner/scan")
    def scanner_scan():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = _db(firebase_admin_factory)
            role = _staff_detail(detail, database)
            if role not in {"admin", "teacher"}:
                return jsonify({"error": "Admin or approved teacher access required."}), 403
            upload = request.files.get("file")
            if not upload or not upload.filename:
                return jsonify({"error": "Image or PDF is required."}), 400
            content_type = (upload.content_type or "").lower()
            if content_type not in ALLOWED_IMAGE_TYPES and content_type not in ALLOWED_PDF_TYPES:
                return jsonify({"error": "Only JPG, PNG, WEBP, GIF or PDF scans are supported."}), 415
            raw = upload.read(MAX_SCAN_BYTES + 1)
            if len(raw) > MAX_SCAN_BYTES:
                return jsonify({"error": "Scan is too large. Maximum size is 10 MB."}), 413
            if not raw:
                return jsonify({"error": "Uploaded scan is empty."}), 400

            source_scan_id = uuid.uuid4().hex
            metadata = {field: _clean(request.form.get(field), 1500) for field in LIBRARY_FIELDS}
            metadata["sourceScanId"] = source_scan_id
            collection = metadata.get("collection", "")
            book_id = metadata.get("bookId", "")
            chapter_id = metadata.get("chapterId", "")
            subchapter_id = metadata.get("subchapterId", "")
            if any([book_id, chapter_id, subchapter_id]) and not collection:
                return jsonify({"error": "collection is required when Library location is supplied."}), 400
            if collection and collection not in ALLOWED_BOOK_COLLECTIONS:
                return jsonify({"error": "Unknown Library collection."}), 400
            if chapter_id and not book_id:
                return jsonify({"error": "bookId is required when chapterId is supplied."}), 400
            if subchapter_id and not chapter_id:
                return jsonify({"error": "chapterId is required when subchapterId is supplied."}), 400
            if collection and book_id:
                book_snap = database.collection(collection).document(book_id).get()
                if not book_snap.exists:
                    return jsonify({"error": "Selected Library book was not found."}), 404
                if chapter_id:
                    chapter_snap = (database.collection(collection).document(book_id)
                                     .collection("chapters").document(chapter_id).get())
                    if not chapter_snap.exists:
                        return jsonify({"error": "Selected Library chapter was not found."}), 404
                    if subchapter_id:
                        sub_snap = (database.collection(collection).document(book_id)
                                    .collection("chapters").document(chapter_id)
                                    .collection("subchapters").document(subchapter_id).get())
                        if not sub_snap.exists:
                            return jsonify({"error": "Selected Library subchapter was not found."}), 404
            try:
                page_number = int(request.form.get("pageNumber") or 1)
                metadata["pageNumber"] = max(1, page_number)
            except ValueError:
                metadata["pageNumber"] = 1
            metadata["sourceRef"] = f"scanner:{source_scan_id}:page:{metadata['pageNumber']}"

            storage = {}
            try:
                from cloudinary_storage import upload_document, signed_url
                stored = upload_document(raw, upload.filename, content_type, str(detail.get("uid")), folder="smart-quiz-scans")
                storage = {"provider": stored.get("provider"), "path": stored.get("path"), "url": signed_url(stored.get("path"))}
            except Exception:
                # OCR/review remains usable even if optional original-file storage is unavailable.
                app.logger.exception("Smart Quiz Scanner source storage failed")

            images = []
            if content_type in ALLOWED_IMAGE_TYPES:
                images = [(metadata["pageNumber"], content_type, raw)]
            else:
                text_pages = _text_pdf(raw)
                if text_pages and not any(len(t) > 20 for _, t in text_pages):
                    text_pages = []
                if text_pages:
                    # Feed text PDFs through Gemini as a synthetic image-independent prompt.
                    joined = "\n\n".join(f"PAGE {p}\n{text}" for p, text in text_pages)
                    api_key = os.getenv("GEMINI_API_KEY", "").strip()
                    if not api_key:
                        raise RuntimeError("OCR extraction is not configured.")
                    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
                    prompt = (
                        "Extract clearly visible multiple-choice questions from this PDF text. "
                        "Return ONLY JSON {questions:[...]}; each item requires question, options A-D, "
                        "correctAnswer only if explicitly present, pageNumber, sourceRef, learningObjective. "
                        "Do not invent missing answers or metadata. Write every formula, fraction, "
                        "exponent, subscript, and Greek letter as LaTeX wrapped in single dollar signs, "
                        "e.g. $x^2 + 3x = 0$, $\\frac{1}{2}mv^2$; use $$...$$ for a standalone displayed "
                        "equation.\n\n" + joined[:100000]
                    )
                    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                    response = requests.post(url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=payload, timeout=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60")))
                    if not response.ok:
                        raise RuntimeError("OCR extraction service rejected the request.")
                    data = response.json()
                    text = "\n".join(str(p.get("text")) for c in data.get("candidates", []) for p in c.get("content", {}).get("parts", []) if isinstance(p, dict) and p.get("text"))
                    parsed = _parse_json_object(text)
                    questions = [_normalize_question(q) for q in (parsed.get("questions") or [])[:MAX_QUESTIONS]]
                    questions = [q for q in questions if q]
                    model_used = model
                else:
                    images = _pdf_pages(raw)
                    questions, model_used = _extract_with_gemini(images, upload.filename)

            if images:
                questions, model_used = _extract_with_gemini(images, upload.filename)
            elif 'questions' not in locals():
                questions, model_used = [], None

            # Provenance is server-controlled. Never persist a client- or model-supplied sourceRef as authoritative.
            for q in questions:
                page = q.get("pageNumber") or metadata.get("pageNumber") or 1
                q["sourceRef"] = f"scanner:{source_scan_id}:page:{page}"
                if not q.get("learningObjective"):
                    q["learningObjective"] = metadata.get("learningObjective", "")
            now = _now()
            scan_ref = database.collection("smartQuizScans").document(source_scan_id)
            scan_ref.set({
                "sourceScanId": source_scan_id,
                "ownerUid": str(detail.get("uid", "")),
                "ownerRole": role,
                "fileName": _clean(upload.filename, 180),
                "contentType": content_type,
                "status": "EXTRACTED",
                "metadata": metadata,
                "storage": storage,
                "questions": questions,
                "questionCount": len(questions),
                "ocrModel": model_used,
                "createdAt": now,
                "updatedAt": now,
            })
            return jsonify({"success": True, "sourceScanId": source_scan_id, "status": "EXTRACTED", "questions": questions, "questionCount": len(questions)}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Smart Quiz Scanner extraction failed")
            return jsonify({"error": "Unable to extract questions from this scan."}), 500

    @app.get("/api/scanner/scan/<scan_id>")
    def scanner_get(scan_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = _db(firebase_admin_factory)
            role = _staff_detail(detail, database)
            snap = database.collection("smartQuizScans").document(_clean(scan_id, 80)).get()
            if not snap.exists:
                return jsonify({"error": "Scan not found."}), 404
            data = snap.to_dict() or {}
            uid = str(detail.get("uid", ""))
            if role != "admin" and data.get("ownerUid") != uid:
                return jsonify({"error": "Access denied."}), 403
            storage = data.get("storage") or {}
            if storage.get("path"):
                # storage.url was a short-lived Cloudinary signed link minted
                # at extraction time - mint a fresh one now rather than
                # returning what is very likely an already-expired URL.
                try:
                    from cloudinary_storage import signed_url
                    data = {**data, "storage": {**storage, "url": signed_url(storage["path"])}}
                except Exception:
                    app.logger.exception("Could not refresh Smart Quiz Scanner source URL for %s", snap.id)
            return jsonify({"success": True, "scan": {"id": snap.id, **data}}), 200
        except Exception:
            return jsonify({"error": "Unable to load scan."}), 500

    @app.put("/api/scanner/scan/<scan_id>/review")
    def scanner_review(scan_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = _db(firebase_admin_factory)
            role = _staff_detail(detail, database)
            snap = database.collection("smartQuizScans").document(_clean(scan_id, 80)).get()
            if not snap.exists:
                return jsonify({"error": "Scan not found."}), 404
            data = snap.to_dict() or {}
            if role != "admin" and data.get("ownerUid") != str(detail.get("uid", "")):
                return jsonify({"error": "Access denied."}), 403
            if data.get("status") == "IMPORTED":
                return jsonify({"error": "Imported scans are immutable."}), 409
            body = request.get_json(silent=True) or {}
            incoming = body.get("questions")
            if not isinstance(incoming, list) or not incoming or len(incoming) > MAX_QUESTIONS:
                return jsonify({"error": "A non-empty question list is required."}), 400
            normalized = [_normalize_question(q) for q in incoming]
            if any(q is None for q in normalized):
                return jsonify({"error": "Every question needs exactly four non-empty options."}), 400
            database.collection("smartQuizScans").document(snap.id).update({"questions": normalized, "questionCount": len(normalized), "status": "REVIEWED", "updatedAt": _now()})
            return jsonify({"success": True, "status": "REVIEWED", "questions": normalized}), 200
        except Exception:
            return jsonify({"error": "Unable to save scanner review."}), 500

    @app.post("/api/scanner/scan/<scan_id>/import")
    def scanner_import(scan_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = _db(firebase_admin_factory)
            role = _staff_detail(detail, database)
            snap = database.collection("smartQuizScans").document(_clean(scan_id, 80)).get()
            if not snap.exists:
                return jsonify({"error": "Scan not found."}), 404
            data = snap.to_dict() or {}
            uid = str(detail.get("uid", ""))
            if role != "admin" and data.get("ownerUid") != uid:
                return jsonify({"error": "Access denied."}), 403
            if data.get("status") == "IMPORTED":
                ids = data.get("importedQuizIds") if isinstance(data.get("importedQuizIds"), list) else []
                return jsonify({"success": True, "status": "IMPORTED", "quizCount": len(ids), "quizIds": ids, "idempotent": True}), 200
            if data.get("status") != "REVIEWED":
                return jsonify({"error": "Review the extracted questions before import."}), 409
            body = request.get_json(silent=True) or {}
            destination = _clean(body.get("destination") or "classwork", 20).lower()
            if destination not in {"classwork", "questionbank"}:
                destination = "classwork"
            title_prefix = _clean(body.get("titlePrefix") or data.get("fileName") or "Scanned Quiz", 160)
            class_name = ""
            if destination == "classwork":
                class_name = _clean(body.get("className"), 40)
                allowed_classes = {str(i) for i in range(1, 13)} | {"KG", "Nursery", "LKG", "UKG"}
                if class_name not in allowed_classes:
                    return jsonify({"error": "Invalid class selection."}), 400
            qb_subject = _clean(body.get("subject"), 80)
            qb_grade = _clean(body.get("grade"), 20)
            qb_domain = _clean(body.get("domain"), 200)
            questions = data.get("questions") if isinstance(data.get("questions"), list) else []
            if not questions:
                return jsonify({"error": "No reviewed questions are available."}), 409
            missing = [i + 1 for i, q in enumerate(questions) if str(q.get("correctAnswer", "")).upper() not in {"A", "B", "C", "D"}]
            if missing:
                return jsonify({"error": f"Set a valid correct answer for question(s): {', '.join(map(str, missing))}."}), 400
            # Import is one Firestore transaction. This closes the race where
            # two concurrent requests could both observe REVIEWED and create
            # duplicate quiz documents before marking the scan IMPORTED.
            from firebase_admin import firestore
            scan_ref = database.collection("smartQuizScans").document(snap.id)
            transaction = database.transaction()

            @firestore.transactional
            def _import_transaction(tx):
                current = tx.get(scan_ref)
                if not current.exists:
                    raise ValueError("Scan not found.")
                current_data = current.to_dict() or {}
                if current_data.get("status") == "IMPORTED":
                    ids = current_data.get("importedQuizIds") if isinstance(current_data.get("importedQuizIds"), list) else []
                    return {"status": "IMPORTED", "ids": ids, "idempotent": True}
                if current_data.get("status") != "REVIEWED":
                    raise ValueError("Review the extracted questions before import.")
                # Use the transaction-read draft, not the pre-transaction snapshot.
                # A concurrent review update will therefore either be observed here
                # or cause Firestore to retry the transaction with the latest scan.
                tx_questions = current_data.get("questions") if isinstance(current_data.get("questions"), list) else []
                if not tx_questions:
                    raise ValueError("No reviewed questions are available.")
                if len(tx_questions) > MAX_QUESTIONS:
                    raise ValueError("Too many reviewed questions.")
                missing_tx = [i + 1 for i, q in enumerate(tx_questions) if not isinstance(q, dict) or str(q.get("correctAnswer", "")).upper() not in {"A", "B", "C", "D"}]
                if missing_tx:
                    raise ValueError(f"Set a valid correct answer for question(s): {', '.join(map(str, missing_tx))}.")

                meta = current_data.get("metadata") if isinstance(current_data.get("metadata"), dict) else {}
                refs = []
                payloads = []
                for idx, q in enumerate(tx_questions, start=1):
                    if destination == "questionbank":
                        ref = database.collection("questionBank").document()
                        item = {
                            "question": q["question"],
                            "options": q["options"],
                            "correctAnswer": q["correctAnswer"],
                            "subject": qb_subject,
                            "grade": qb_grade,
                            "domain": qb_domain,
                            "difficulty": "medium",
                            "points": 1,
                            # Auto-scraped questions still go through the same
                            # draft -> approved review step as manual/AI-drafted
                            # ones before they can reach Practice/Challenge/
                            # classwork/Telegram - OCR is not error-free.
                            "status": "draft",
                            "source": "scanner",
                            "createdAt": _now(),
                            "createdBy": uid,
                            "sourceScanId": current_data.get("sourceScanId"),
                            "sourceRef": q.get("sourceRef") or meta.get("sourceRef", ""),
                            "pageNumber": q.get("pageNumber") or meta.get("pageNumber"),
                            "bookId": meta.get("bookId", ""),
                            "chapterId": meta.get("chapterId", ""),
                            "subchapterId": meta.get("subchapterId", ""),
                        }
                    else:
                        ref = database.collection("quizzes").document()
                        item = {
                            "className": class_name,
                            "title": f"{title_prefix} — Q{idx}",
                            "question": q["question"],
                            "imageUrl": "",
                            "options": q["options"],
                            "correctAnswer": q["correctAnswer"],
                            "createdAt": _now(),
                            "createdBy": uid,
                            "sourceScanId": current_data.get("sourceScanId"),
                            "sourceRef": q.get("sourceRef") or meta.get("sourceRef", ""),
                            "pageNumber": q.get("pageNumber") or meta.get("pageNumber"),
                            "collection": meta.get("collection", ""),
                            "bookId": meta.get("bookId", ""),
                            "chapterId": meta.get("chapterId", ""),
                            "subchapterId": meta.get("subchapterId", ""),
                            "learningObjective": q.get("learningObjective") or meta.get("learningObjective", ""),
                        }
                    tx.set(ref, item)
                    refs.append(ref.id)
                    payloads.append({"id": ref.id, **item})

                tx.update(scan_ref, {
                    "status": "IMPORTED",
                    "importedQuizIds": refs,
                    "importDestination": destination,
                    "updatedAt": _now(),
                })
                return {"status": "IMPORTED", "ids": refs, "payloads": payloads, "idempotent": False}

            result = _import_transaction(transaction)
            ids = result["ids"]
            return jsonify({
                "success": True,
                "status": "IMPORTED",
                "destination": destination,
                "quizCount": len(ids),
                "quizIds": ids,
                "idempotent": result.get("idempotent", False),
            }), (200 if result.get("idempotent") else 201)
        except Exception:
            app.logger.exception("Smart Quiz Scanner import failed")
            return jsonify({"error": "Unable to import reviewed questions into the existing quiz system."}), 500
