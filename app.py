import os
import json
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
import time
import re
import base64
from pathlib import Path
from decimal import Decimal, InvalidOperation
from collections import defaultdict, deque
from flask import Flask, render_template, request, jsonify, g, send_from_directory
from flask_cors import CORS
import requests

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# V30.67 reliability/security hardening. This lightweight limiter is deliberately
# process-local and protects expensive endpoints from accidental bursts. For
# multi-instance production deployments, put the same policy at the edge/gateway.
_RATE_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_RATE_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))
_rate_buckets = defaultdict(deque)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")

def _client_key():
    # Only trust X-Forwarded-For when explicitly enabled by the deployment.
    # Otherwise clients could spoof the header and evade the limiter.
    if os.getenv("TRUST_PROXY_HEADERS", "0").lower() in {"1", "true", "yes"}:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.remote_addr or "unknown"

def _rate_limited(path=None):
    target = path or request.path
    # Only protect API endpoints. Health checks remain cheap and probe-friendly.
    if not target.startswith("/api/"):
        return False
    now = time.monotonic()
    key = f"{_client_key()}:{target}"
    bucket = _rate_buckets[key]
    cutoff = now - max(1, _RATE_WINDOW_SECONDS)
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= max(1, _RATE_MAX_REQUESTS):
        return True
    bucket.append(now)
    # Bound process-local memory by pruning inactive buckets periodically.
    if len(_rate_buckets) > 10000:
        stale = [k for k, q in _rate_buckets.items() if not q or q[-1] <= cutoff]
        for k in stale[:2000]:
            _rate_buckets.pop(k, None)
    return False

# V30.32+ production hardening: never silently broaden API CORS.
_allowed_origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
_allow_permissive_cors = os.getenv("ALLOW_PERMISSIVE_CORS", "0").lower() in {"1", "true", "yes"}
if _allowed_origins:
    CORS(app, resources={r"/api/*": {"origins": _allowed_origins}})
elif _allow_permissive_cors:
    CORS(app, resources={r"/api/*": {"origins": "*"}})

# Keep oversized JSON/multipart requests from consuming excessive memory.
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", str(100 * 1024 * 1024)))

@app.before_request
def _attach_request_id():
    import secrets
    incoming = request.headers.get("X-Request-ID", "").strip()
    g.request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else secrets.token_hex(12)
    g.request_started_at = time.perf_counter()
    g.rate_limited = _rate_limited()

@app.before_request
def _enforce_api_rate_limit():
    if getattr(g, "rate_limited", False):
        response = jsonify({"error": "Too many requests. Please try again shortly.", "requestId": getattr(g, "request_id", "")})
        response.status_code = 429
        response.headers["Retry-After"] = str(max(1, _RATE_WINDOW_SECONDS))
        return response

@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
    response.headers.setdefault("X-DNS-Prefetch-Control", "off")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.is_secure or os.getenv("FORCE_HSTS", "0").lower() in {"1", "true", "yes"}:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
    started = getattr(g, "request_started_at", None)
    if started is not None:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
        response.headers.setdefault("X-Response-Time-Ms", f"{duration_ms:.1f}")
    if request.path.startswith("/api/") or request.path in {"/healthz", "/readyz"}:
        response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.errorhandler(400)
def _bad_request(_error):
    return jsonify({"error": "Bad request.", "requestId": getattr(g, "request_id", "")}), 400

@app.errorhandler(413)
def _request_too_large(_error):
    return jsonify({"error": "Request is too large.", "requestId": getattr(g, "request_id", "")}), 413

@app.errorhandler(500)
def _internal_error(_error):
    app.logger.exception("Unhandled server error")
    return jsonify({"error": "Internal server error.", "requestId": getattr(g, "request_id", "")}), 500

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/service-worker.js')
def service_worker():
    # Root-scoped PWA service worker. It caches only the stable app shell and
    # never caches API/Firebase/Cloudinary responses.
    return send_from_directory(app.root_path, 'service-worker.js', mimetype='application/javascript')

# V31.07 — secure admin dashboard preview tokens.
def _preview_secret():
    return (os.getenv('ADMIN_PREVIEW_SECRET') or os.getenv('SECRET_KEY') or '').strip()

def _make_preview_token(role, ttl_seconds=300):
    secret = _preview_secret()
    if not secret:
        raise RuntimeError('ADMIN_PREVIEW_SECRET is not configured.')
    import secrets as _secrets
    payload = {'role': role, 'exp': int(time.time()) + int(ttl_seconds), 'nonce': _secrets.token_hex(8)}
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f'{body}.{sig}'

def _verify_preview_token(token, role):
    secret = _preview_secret()
    if not secret or not token or not role:
        return False
    try:
        body, sig = str(token).split('.', 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        padded = body + '=' * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return payload.get('role') == role and int(payload.get('exp', 0)) >= int(time.time())
    except Exception:
        return False

@app.post('/api/admin/preview-token')
def admin_preview_token():
    ok, detail = _require_admin_bearer()
    if not ok:
        return detail
    role = str((request.get_json(silent=True) or {}).get('role') or '').strip().lower()
    if role not in {'student', 'teacher', 'parent'}:
        return jsonify({'error': 'Invalid preview role.'}), 400
    try:
        return jsonify({'success': True, 'role': role, 'token': _make_preview_token(role), 'expiresIn': 300}), 200
    except RuntimeError as exc:
        return jsonify({'error': str(exc), 'requestId': getattr(g, 'request_id', '')}), 503

@app.get('/api/admin/preview-verify')
def verify_admin_preview():
    role = str(request.args.get('role') or '').strip().lower()
    token = str(request.args.get('token') or '').strip()
    if role not in {'student', 'teacher', 'parent'} or not _verify_preview_token(token, role):
        return jsonify({'valid': False}), 401
    return jsonify({'valid': True, 'role': role}), 200

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/teacher')
def teacher():
    return render_template('teacher.html')

@app.route('/student')
def student():
    return render_template('student.html', fcm_vapid_key=os.getenv('FCM_VAPID_KEY', ''))

@app.route('/parent')
def parent():
    return render_template('parent.html')

@app.post('/api/storage/image')
def upload_image_proxy():
    """Deprecated compatibility endpoint. New image uploads use Cloudinary directly."""
    return jsonify({
        "error": "Image storage has moved to Cloudinary. Please use the current storage service.",
        "provider": "cloudinary",
        "requestId": getattr(g, "request_id", "")
    }), 410


@app.post('/api/storage/document')
def upload_document_proxy():
    """Upload a book/document only for admins or approved teachers."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        db = __import__('firebase_admin').firestore.client()
        is_admin = detail.get('admin') is True or detail.get('role') == 'admin'
        if not is_admin:
            uid = str(detail.get('uid') or '').strip()
            teacher = db.collection('teachers').document(uid).get().to_dict() or {}
            if not bool(teacher.get('approved')):
                return jsonify({"error": "Admin or approved teacher access required for document uploads."}), 403
    except Exception:
        app.logger.exception('Document upload authorization check failed')
        return jsonify({"error": "Could not verify document upload permissions."}), 503
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"error": "Document file is required."}), 400
    allowed = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    content_type = (file.content_type or '').lower().strip()
    if content_type not in allowed:
        return jsonify({"error": "Only PDF, DOC and DOCX files are allowed."}), 415
    max_bytes = int(os.getenv('GCS_DOCUMENT_MAX_BYTES', str(100 * 1024 * 1024)))
    if request.content_length and request.content_length > max_bytes + 1024 * 1024:
        return jsonify({"error": "Document is too large."}), 413
    try:
        raw = file.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return jsonify({"error": f"Document is too large. Maximum is {max_bytes // (1024 * 1024)} MB."}), 413
        from gcs_storage import upload_document, signed_url
        result = upload_document(raw, file.filename, content_type, str(detail.get('uid', 'unknown')), folder='books')
        url = signed_url(result['path'])
        return jsonify({"success": True, **result, "url": url, "previewUrl": url, "downloadUrl": url}), 201
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception:
        app.logger.exception('GCS document upload failed')
        return jsonify({"error": "Document upload failed."}), 502


def _can_access_gcs_book(db, path, detail):
    """Authorize access to a GCS book before minting a signed URL.

    A signed URL is a bearer credential, so possession of a storage path must
    never be sufficient to obtain one. Access is tied to the Firestore book
    record and the authenticated user's role/class.
    """
    uid = str(detail.get('uid') or '').strip()
    is_admin = detail.get('admin') is True or detail.get('role') == 'admin'
    if is_admin:
        return True
    if not path.startswith('bmt/books/'):
        return False

    # Exact match prevents path guessing and limits this endpoint to records
    # that the application actually knows about.
    matches = []
    for field in ('storagePath', 'fileId'):
        try:
            snap = db.collection('books').where(field, '==', path).limit(5).stream()
            matches.extend(list(snap))
        except Exception:
            # Some older Firestore indexes/data may not support the optional
            # field. Continue with the other exact-match field.
            continue
    seen = set()
    for snap in matches:
        if snap.id in seen:
            continue
        seen.add(snap.id)
        book = snap.to_dict() or {}
        if book.get('storageProvider') != 'gcs':
            continue
        if str(book.get('storagePath') or book.get('fileId') or '').strip() != path:
            continue
        # The normal student path is class-scoped. Teachers may access their
        # own uploaded content; admins were handled above.
        user = db.collection('users').document(uid).get().to_dict() or {}
        if str(book.get('className') or '').strip() and str(book.get('className')).strip() == str(user.get('class') or '').strip():
            return True
        if str(book.get('teacherUid') or book.get('uploaderUid') or '').strip() == uid:
            return True
    return False


@app.post('/api/storage/document-url')
def document_access_url():
    """Return a short-lived signed URL only for an authorized GCS book."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    body = request.get_json(silent=True) or {}
    path = str(body.get('storagePath') or body.get('fileId') or '').strip()
    if not path or len(path) > 500:
        return jsonify({"error": "A valid GCS storage path is required."}), 400
    try:
        from firebase_admin import firestore
        db = firestore.client()
        if not _can_access_gcs_book(db, path, detail):
            return jsonify({"error": "You are not authorized to access this document."}), 403
        from gcs_storage import signed_url
        return jsonify({"success": True, "url": signed_url(path)})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception:
        app.logger.exception('GCS signed URL generation failed')
        return jsonify({"error": "Could not create a document access URL."}), 502



@app.post('/api/auth/forgot-password')
def auth_forgot_password():
    """Trigger Firebase's password-reset email without revealing account existence.

    Configure FIREBASE_WEB_API_KEY with the Firebase Web API key. The endpoint
    always returns the same success message for normal user-input failures so
    attackers cannot enumerate registered email addresses.
    """
    body = request.get_json(silent=True) or {}
    email = str(body.get('email') or '').strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return jsonify({'error':'Enter a valid email address.','requestId':getattr(g,'request_id','')}),400
    api_key = os.getenv('FIREBASE_WEB_API_KEY','').strip()
    if not api_key:
        return jsonify({'error':'Password recovery is not configured on the server.','requestId':getattr(g,'request_id','')}),503
    continue_url = os.getenv('PASSWORD_RESET_CONTINUE_URL','').strip() or request.host_url.rstrip('/') + '/student'
    try:
        r = requests.post(
            f'https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}',
            json={'requestType':'PASSWORD_RESET','email':email,'continueUrl':continue_url},
            timeout=15,
        )
        payload = r.json() if r.content else {}
        if r.ok:
            return jsonify({'success':True,'message':'If an account exists for that email, a password-reset email has been sent.'})
        err = ((payload.get('error') or {}).get('message') or '') if isinstance(payload,dict) else ''
        # User/account errors are intentionally normalized.
        if err in {'EMAIL_NOT_FOUND','INVALID_EMAIL','USER_DISABLED'}:
            return jsonify({'success':True,'message':'If an account exists for that email, a password-reset email has been sent.'})
        if r.status_code in {400,401,403}:
            app.logger.warning('Firebase password reset configuration/request failure: %s', err or r.status_code)
            return jsonify({'error':'Password recovery service is temporarily unavailable.','requestId':getattr(g,'request_id','')}),503
        app.logger.error('Firebase password reset HTTP %s: %s', r.status_code, err or 'unknown')
        return jsonify({'error':'Password recovery service is temporarily unavailable.','requestId':getattr(g,'request_id','')}),502
    except requests.RequestException:
        app.logger.exception('Firebase password reset request failed')
        return jsonify({'error':'Password recovery service is temporarily unavailable.','requestId':getattr(g,'request_id','')}),503

@app.route('/healthz')
def healthz():
    return {"status": "ok", "version": "31.17", "requestId": getattr(g, "request_id", "")}, 200

@app.route('/readyz')
def readyz():
    """Deployment readiness check without exposing secrets or provider details."""
    firebase_ready = False
    try:
        firebase_ready = _firebase_admin_from_env() is not None
    except Exception:
        app.logger.exception("Firebase readiness check failed")
    ai_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    telebirr_webhook_configured = bool(os.getenv("TELEBIRR_WEBHOOK_SECRET", "").strip())
    cbe_webhook_configured = bool(os.getenv("CBE_WEBHOOK_SECRET", "").strip())
    payment_webhook_configured = telebirr_webhook_configured or cbe_webhook_configured
    production_mode = os.getenv("FLASK_ENV", "").strip().lower() == "production" or os.getenv("BMT_ENV", "").strip().lower() == "production"
    allow_webhook_without_amount = os.getenv("ALLOW_WEBHOOK_WITHOUT_AMOUNT", "0").lower() in {"1", "true", "yes"}
    payment_currency_configured = bool(os.getenv("PAYMENT_CURRENCY", "").strip())
    production_safety = not (production_mode and allow_webhook_without_amount) and (not production_mode or payment_currency_configured)
    checks = {
        "firebase": firebase_ready,
        "ai_configured": ai_configured,
        "telebirr_webhook_configured": telebirr_webhook_configured,
        "cbe_webhook_configured": cbe_webhook_configured,
        "payment_webhook_configured": payment_webhook_configured,
        "payment_currency_configured": payment_currency_configured,
        "production_safety": production_safety,
    }
    status = "ready" if all((firebase_ready, ai_configured, payment_webhook_configured, production_safety)) else "not_ready"
    return jsonify({
        "status": status,
        "version": "31.17",
        "checks": checks,
        "requestId": getattr(g, "request_id", ""),
    }), (200 if (firebase_ready and ai_configured and payment_webhook_configured and production_safety) else 503)

@app.get('/api/admin/config-status')
def admin_config_status():
    """Non-secret production diagnostics for authenticated admins.

    Returns only whether required integrations are configured and whether
    Firebase credentials can actually be parsed. Never returns secret values.
    """
    diagnostic_key = os.getenv("DIAGNOSTIC_KEY", "").strip()
    supplied_key = request.headers.get("X-Diagnostic-Key", "").strip()
    key_authorized = bool(diagnostic_key and supplied_key and hmac.compare_digest(supplied_key, diagnostic_key))
    if not key_authorized:
        ok, detail = _require_admin_bearer()
        if not ok:
            return detail
    firebase_ok = False
    try:
        firebase_ok = _firebase_admin_from_env() is not None
    except Exception:
        app.logger.exception("Admin config diagnostic failed")
    checks = {
        "firebase": firebase_ok,
        "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "imgbb": bool(os.getenv("IMGBB_API_KEY", "").strip()),
        "fcmVapid": bool(os.getenv("FCM_VAPID_KEY", "").strip()),
        "telebirrWebhook": bool(os.getenv("TELEBIRR_WEBHOOK_SECRET", "").strip()),
        "cbeWebhook": bool(os.getenv("CBE_WEBHOOK_SECRET", "").strip()),
        "paymentCurrency": bool(os.getenv("PAYMENT_CURRENCY", "").strip()),
        "allowedOrigins": bool(os.getenv("ALLOWED_ORIGINS", "").strip()),
        "gcsBucket": bool(os.getenv("GCS_BUCKET_NAME", "").strip()),
        "cloudinary": bool(os.getenv("CLOUDINARY_CLOUD_NAME", "").strip() and os.getenv("CLOUDINARY_UPLOAD_PRESET", "").strip()),
    }
    return jsonify({"success": True, "checks": checks, "environment": os.getenv("BMT_ENV", os.getenv("FLASK_ENV", "development"))})





@app.get('/api/library')
def library_list():
    ok, detail = _require_user_bearer()
    if not ok: return detail
    grade=str(request.args.get('className') or '').strip()[:20]
    category=str(request.args.get('category') or '').strip().lower()[:40]
    subject=str(request.args.get('subject') or '').strip().lower()[:80]
    labels={'student_textbook':'Student Textbook','teacher_guide':'Teacher Guide','reference':'Reference Book (መጣቐስቲ)','psychology':'Psychology & General Development'}
    sources=[('books','student_textbook'),('bookLibrary','student_textbook'),('teacherGuides','teacher_guide'),('referenceBooks','reference'),('psychologyBooks','psychology')]
    try:
        from firebase_admin import firestore
        db=firestore.client(); rows=[]; seen=set()
        for collection_name, default_category in sources:
            for snap in db.collection(collection_name).limit(250).stream():
                x=snap.to_dict() or {}
                g=str(x.get('className') or x.get('grade') or '').strip()
                if grade and g!=grade: continue
                c=str(x.get('category') or default_category).strip().lower()
                if category and c!=category: continue
                sub=str(x.get('subject') or '').strip()
                title=str(x.get('title') or x.get('name') or 'Library item').strip()
                if subject and subject not in sub.lower() and subject not in title.lower(): continue
                key=f'{collection_name}:{snap.id}'
                if key in seen: continue
                seen.add(key)
                rows.append({'id':snap.id,'sourceCollection':collection_name,'title':title,'grade':g,'subject':sub,'category':c,'categoryLabel':labels.get(c,c.replace('_',' ').title()),'description':x.get('description') or '','url':x.get('previewUrl') or x.get('link') or x.get('fileUrl') or x.get('downloadUrl') or '','storageProvider':x.get('storageProvider') or '','storagePath':x.get('storagePath') or x.get('fileId') or ''})
        rows.sort(key=lambda r:(int(r['grade']) if str(r['grade']).isdigit() else 99,r['categoryLabel'],r['subject'],r['title']))
        return jsonify({'success':True,'items':rows[:500]})
    except RuntimeError:
        return jsonify({'error':'Library service is temporarily unavailable.'}),503
    except Exception:
        app.logger.exception('Library list failed')
        return jsonify({'error':'Could not load digital library.'}),500

@app.post('/api/ai/translate')
def ai_translate():
    """Translate library text through the existing Gemini gateway."""
    ok, detail = _require_user_bearer()
    if not ok: return detail
    body=request.get_json(silent=True) or {}
    text=str(body.get('text') or '').strip()
    target=str(body.get('targetLanguage') or '').strip()[:60]
    source=str(body.get('sourceLanguage') or 'auto').strip()[:60]
    if not text or not target: return jsonify({'error':'Text and targetLanguage are required.'}),400
    if len(text)>12000: return jsonify({'error':'Text is too long. Maximum is 12,000 characters per request.'}),413
    api_key=os.getenv('GEMINI_API_KEY','').strip()
    if not api_key: return jsonify({'error':'AI translation is not configured.'}),503
    prompt=(f'Translate the following educational text from {source} to {target}. Preserve mathematical notation, formulas, units, names, and structure. Do not add commentary.\n\n{text}')
    model=os.getenv('GEMINI_MODEL','gemini-1.5-flash').strip()
    try:
        r=requests.post(GEMINI_API_URL.format(model=model)+f'?key={api_key}',json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'temperature':0.1,'maxOutputTokens':4096}},timeout=45)
        data=r.json() if r.content else {}
        if not r.ok: return jsonify({'error':'AI translation service is temporarily unavailable.'}),502
        parts=((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
        translated=''.join(str(p.get('text','')) for p in parts).strip()
        if not translated: return jsonify({'error':'No translation was returned.'}),502
        return jsonify({'success':True,'translation':translated,'targetLanguage':target,'sourceLanguage':source})
    except requests.RequestException:
        app.logger.exception('AI translation request failed')
        return jsonify({'error':'AI translation service is temporarily unavailable.'}),503

@app.post('/api/ai/tutor')
def ai_tutor():
    """Authenticated AI Tutor gateway with optional BMT material retrieval."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400
    if len(message) > 8000:
        return jsonify({"error": "Message is too long."}), 413

    history = payload.get("history", [])
    if not isinstance(history, list):
        history = []
    grade = str(payload.get("grade", "")).strip()[:20]
    subject = str(payload.get("subject", "")).strip()[:80]
    uid = str(detail.get("uid", "")).strip()

    try:
        from ai_tutor_service import check_and_record_quota, retrieve_material_context, ask_gemini
        db_getter = None
        try:
            fb = _firebase_admin_from_env()
            if fb:
                from firebase_admin import firestore
                db = firestore.client()
                db_getter = lambda: db
        except Exception:
            db_getter = None

        premium = False
        if db_getter:
            try:
                snap = db_getter().collection("entitlements").document(uid).get()
                ent = snap.to_dict() or {}
                expires = ent.get("expiresAt")
                premium = bool(ent.get("premium")) and (not expires or expires > datetime.now(timezone.utc))
            except Exception:
                premium = False

        allowed, remaining = check_and_record_quota(db_getter, uid, premium)
        if not allowed:
            return jsonify({"error": "Daily AI Tutor limit reached. Please try again tomorrow or unlock premium access.", "remaining": 0}), 429

        materials = retrieve_material_context(db_getter, grade, subject, message)
        answer, model = ask_gemini(message, history, grade, subject, materials)
        return jsonify({
            "answer": answer,
            "model": model,
            "remainingToday": remaining,
            "sources": [{"id": m.get("id"), "title": m["title"], "grade": m.get("grade"), "subject": m.get("subject")} for m in materials],
        })
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception:
        app.logger.exception("AI Tutor failure")
        return jsonify({"error": "AI Tutor is temporarily unavailable. Please try again."}), 502


@app.post('/api/ai/homework-coach')
def ai_homework_coach():
    """Low-cost text homework coach using the existing Gemini quota/key.

    It teaches step-by-step rather than returning a bare final answer. Images can
    still be handled by the normal upload features; this endpoint intentionally
    stays text-only so it requires no new provider or storage configuration.
    """
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    payload = request.get_json(silent=True) or {}
    question = str(payload.get('question', '')).strip()[:8000]
    grade = str(payload.get('grade', '')).strip()[:20]
    subject = str(payload.get('subject', '')).strip()[:80]
    if not question:
        return jsonify({'error': 'Homework question is required.'}), 400
    try:
        from ai_tutor_service import check_and_record_quota, retrieve_material_context, ask_gemini
        fb = _firebase_admin_from_env()
        db_getter = None
        if fb:
            from firebase_admin import firestore
            database = firestore.client()
            db_getter = lambda: database
        uid = str(detail.get('uid', ''))
        premium = False
        if db_getter:
            ent = db_getter().collection('entitlements').document(uid).get().to_dict() or {}
            expires = ent.get('expiresAt')
            premium = bool(ent.get('premium')) and (not expires or expires > datetime.now(timezone.utc))
        allowed, remaining = check_and_record_quota(db_getter, uid, premium)
        if not allowed:
            return jsonify({'error': 'Daily AI Tutor limit reached.', 'remaining': 0}), 429
        materials = retrieve_material_context(db_getter, grade, subject, question, limit=6)
        prompt = (
            'Act as a patient homework coach. Solve the following problem step-by-step. '
            'First identify what is being asked, then list known information, choose the '
            'method, show each calculation clearly, verify the result, and finish with a '
            'one-line final answer. Do not skip reasoning. If the problem is ambiguous, '
            'state the ambiguity instead of inventing data. After solving, give one short '
            'similar practice question without its answer.\n\nHomework:\n' + question
        )
        answer, model = ask_gemini(prompt, [], grade, subject, materials)
        return jsonify({'answer': answer, 'model': model, 'remainingToday': remaining,
                        'sources': [{'id': m.get('id'), 'title': m['title']} for m in materials]})
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 503
    except Exception:
        app.logger.exception('AI homework coach failure')
        return jsonify({'error': 'AI homework coach is temporarily unavailable.'}), 502


@app.post('/api/ai/homework-image')
def ai_homework_image():
    """Multimodal homework coach using the existing Gemini API key.

    Accepts a small image upload plus an optional question. No new storage
    provider is required because the image is sent directly to Gemini and is
    not persisted by BMT.
    """
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    upload = request.files.get('image')
    question = str(request.form.get('question', '')).strip()[:3000]
    grade = str(request.form.get('grade', '')).strip()[:20]
    subject = str(request.form.get('subject', '')).strip()[:80]
    if not upload:
        return jsonify({'error': 'Homework image is required.'}), 400
    allowed = {'image/jpeg', 'image/png', 'image/webp'}
    mime = (upload.mimetype or '').lower()
    if mime not in allowed:
        return jsonify({'error': 'Use a JPG, PNG or WebP image.'}), 400
    raw = upload.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        return jsonify({'error': 'Image is too large. Maximum is 8 MB.'}), 413
    if not raw:
        return jsonify({'error': 'Uploaded image is empty.'}), 400
    try:
        from ai_tutor_service import check_and_record_quota
        import requests as _requests
        import os as _os
        fb = _firebase_admin_from_env()
        db_getter = None
        if fb:
            from firebase_admin import firestore
            database = firestore.client()
            db_getter = lambda: database
        uid = str(detail.get('uid', ''))
        premium = False
        if db_getter:
            ent = db_getter().collection('entitlements').document(uid).get().to_dict() or {}
            expires = ent.get('expiresAt')
            premium = bool(ent.get('premium')) and (not expires or expires > datetime.now(timezone.utc))
        allowed_quota, remaining = check_and_record_quota(db_getter, uid, premium)
        if not allowed_quota:
            return jsonify({'error': 'Daily AI Tutor limit reached.', 'remaining': 0}), 429

        api_key = _os.getenv('GEMINI_API_KEY')
        if not api_key:
            return jsonify({'error': 'AI Tutor is not configured. Add GEMINI_API_KEY to the server environment.'}), 503
        model = _os.getenv('GEMINI_VISION_MODEL') or _os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')
        prompt = (
            'You are the Bright Mind Tutor multimodal homework coach. Read the uploaded '
            'homework image carefully. Transcribe only the relevant problem, explain what '
            'is being asked, identify known information, solve it step-by-step, verify the '
            'result, and finish with a clear final answer. If handwriting or the image is '
            'unclear, say exactly what part is unclear and ask for a clearer photo. Do not '
            'invent missing values. Give one short similar practice question without its answer. '
            f'Grade: {grade or "school"}. Subject: {subject or "general"}. '
            + (f'Additional student note: {question}' if question else '')
        )
        endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        body = {'contents': [{'role': 'user', 'parts': [
            {'text': prompt},
            {'inlineData': {'mimeType': mime, 'data': base64.b64encode(raw).decode('ascii')}}
        ]}], 'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 1800}}
        r = _requests.post(endpoint, params={'key': api_key}, json=body, timeout=75)
        if r.status_code == 429:
            return jsonify({'error': 'Gemini quota is temporarily unavailable.', 'remaining': remaining}), 429
        if not r.ok:
            app.logger.warning('Gemini vision request failed: %s %s', r.status_code, r.text[:500])
            return jsonify({'error': 'Gemini could not process the homework image right now.'}), 502
        data = r.json()
        parts = (((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or [])
        answer = '\n'.join(str(x.get('text', '')).strip() for x in parts if x.get('text')).strip()
        if not answer:
            return jsonify({'error': 'Gemini returned an empty homework solution.'}), 502
        return jsonify({'answer': answer, 'model': model, 'remainingToday': remaining})
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 503
    except Exception:
        app.logger.exception('AI multimodal homework failure')
        return jsonify({'error': 'AI homework image processing is temporarily unavailable.'}), 502

@app.post('/api/ai/study-plan')
def ai_study_plan():
    """Create a personalized study plan from the student's current inputs."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    payload = request.get_json(silent=True) or {}
    grade = str(payload.get('grade', '')).strip()[:20]
    subject = str(payload.get('subject', '')).strip()[:80]
    goal = str(payload.get('goal', '')).strip()[:1000]
    weak_topics = str(payload.get('weakTopics', '')).strip()[:2000]
    days = max(1, min(int(payload.get('days', 7) or 7), 30))
    if not goal and not weak_topics:
        return jsonify({'error': 'Tell the tutor your goal or weak topics.'}), 400
    try:
        from ai_tutor_service import check_and_record_quota, ask_gemini
        fb = _firebase_admin_from_env()
        db_getter = None
        if fb:
            from firebase_admin import firestore
            database = firestore.client()
            db_getter = lambda: database
        uid = str(detail.get('uid', ''))
        premium = False
        if db_getter:
            ent = db_getter().collection('entitlements').document(uid).get().to_dict() or {}
            expires = ent.get('expiresAt')
            premium = bool(ent.get('premium')) and (not expires or expires > datetime.now(timezone.utc))
        allowed, remaining = check_and_record_quota(db_getter, uid, premium)
        if not allowed:
            return jsonify({'error': 'Daily AI Tutor limit reached.', 'remaining': 0}), 429
        prompt = (
            f'Create a realistic {days}-day personalized study plan for a Grade {grade or "school"} '
            f'student studying {subject or "multiple subjects"}. Goal: {goal or "improve weak areas"}. '
            f'Weak topics: {weak_topics or "not specified"}. Give a day-by-day table-like plan, '
            'daily study time, short active-recall tasks, practice questions, review days, and a '
            'simple mastery check. Keep it age-appropriate and practical for an Ethiopian student. '
            'Do not claim to know performance data that was not supplied.'
        )
        answer, model = ask_gemini(prompt, [], grade, subject, [])
        return jsonify({'plan': answer, 'model': model, 'remainingToday': remaining})
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid study-plan duration.'}), 400
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 503
    except Exception:
        app.logger.exception('AI study plan failure')
        return jsonify({'error': 'AI study plan is temporarily unavailable.'}), 502

@app.post('/api/ai/practice-quiz')
def ai_practice_quiz():
    """Generate a short BMT-grounded practice quiz for the authenticated student."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("topic") or payload.get("message") or "").strip()[:500]
    grade = str(payload.get("grade", "")).strip()[:20]
    subject = str(payload.get("subject", "")).strip()[:80]
    try:
        count = max(1, min(int(payload.get("count", 5)), 10))
    except (TypeError, ValueError):
        count = 5
    if not message:
        return jsonify({"error": "Topic is required."}), 400
    try:
        from ai_tutor_service import check_and_record_quota, retrieve_material_context, generate_practice_quiz
        db_getter = None
        fb = _firebase_admin_from_env()
        if fb:
            from firebase_admin import firestore
            database = firestore.client()
            db_getter = lambda: database
        uid = str(detail.get("uid", ""))
        premium = False
        if db_getter:
            ent = db_getter().collection("entitlements").document(uid).get().to_dict() or {}
            expires = ent.get("expiresAt")
            premium = bool(ent.get("premium")) and (not expires or expires > datetime.now(timezone.utc))
        allowed, remaining = check_and_record_quota(db_getter, uid, premium)
        if not allowed:
            return jsonify({"error": "Daily AI Tutor limit reached.", "remaining": 0}), 429
        materials = retrieve_material_context(db_getter, grade, subject, message, limit=6)
        if not materials:
            return jsonify({"error": "No matching BMT study material was found. Please choose a topic with an uploaded teacher material."}), 404
        questions, model = generate_practice_quiz(message, grade, subject, materials, count)
        return jsonify({"questions": questions, "model": model, "remainingToday": remaining,
                        "sources": [{"id": m.get("id"), "title": m["title"]} for m in materials]})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception:
        app.logger.exception("AI practice quiz failure")
        return jsonify({"error": "AI practice quiz is temporarily unavailable."}), 502


def _firebase_admin_from_env():
    """Load Firebase Admin credentials robustly from Render environment variables.

    Supports raw JSON, JSON with escaped newlines, base64 JSON, or a mounted
    credential file. This prevents harmless Render formatting differences from
    appearing as the misleading "credentials are not configured" error.
    """
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    b64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_B64", "").strip()
    file_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
    # Fallback for Render setups that store the service-account fields
    # separately. This avoids a false "credentials are not configured"
    # response when the JSON secret is not available as one variable.
    individual = {
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
    if not raw and individual.get("project_id") and individual.get("private_key") and individual.get("client_email"):
        raw = json.dumps({k: v for k, v in individual.items() if v})
    if not raw and b64:
        try:
            raw = base64.b64decode(b64).decode("utf-8").strip()
        except Exception:
            app.logger.exception("Invalid FIREBASE_SERVICE_ACCOUNT_JSON_B64")
            return None
    if not raw and file_path:
        try:
            raw = Path(file_path).read_text(encoding="utf-8").strip()
        except Exception:
            app.logger.exception("Could not read FIREBASE_SERVICE_ACCOUNT_FILE")
            return None
    if not raw:
        return None
    import firebase_admin
    from firebase_admin import credentials
    try:
        # Handle Render values pasted with surrounding quotes or literal \n.
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            raw = raw[1:-1]
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if not isinstance(parsed, dict) or not parsed.get("project_id") or not parsed.get("private_key"):
            raise ValueError("service-account JSON is missing project_id/private_key")
        # Render users sometimes paste JSON with a double-escaped private key.
        # Normalize both literal \n and actual newlines before Firebase consumes it.
        private_key = str(parsed.get("private_key", ""))
        parsed["private_key"] = private_key.replace("\\n", "\n")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(parsed))
        return firebase_admin
    except Exception as exc:
        app.logger.error("Invalid Firebase service-account configuration: %s", exc)
        return None

def _auth_error(message, status):
    return jsonify({"error": message, "requestId": getattr(g, "request_id", "")}), status

def _require_admin_bearer():
    fb = _firebase_admin_from_env()
    if not fb:
        return False, _auth_error("Firebase server credentials are not configured.", 503)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or not auth_header.split(" ", 1)[1].strip():
        return False, _auth_error("Admin authentication required.", 401)
    try:
        from firebase_admin import auth as firebase_auth
        decoded = firebase_auth.verify_id_token(auth_header.split(" ", 1)[1].strip(), check_revoked=True)
        if not (decoded.get("admin") is True or decoded.get("role") == "admin"):
            return False, _auth_error("Admin access required.", 403)
        return True, decoded
    except Exception:
        return False, _auth_error("Invalid authentication token.", 401)



PAYMENT_PLANS = {
    "monthly": {"amount": 9.99, "duration_days": 30},
    "yearly": {"amount": 79.99, "duration_days": 365},
}
PAYMENT_PROVIDERS = {"telebirr", "cbe", "manual"}
PAYMENT_VERIFIED_STATUSES = {"verified", "success", "successful", "paid", "completed"}

def _webhook_secret(provider):
    return os.getenv(f"{provider.upper()}_WEBHOOK_SECRET", "").strip()

def _constant_time_signature_ok(provider, raw_body, signature):
    """Verify a generic HMAC-SHA256 gateway webhook signature.

    The exact Telebirr/CBE webhook adapter can map its native signature to this
    endpoint. No gateway secret is ever stored in Firestore or sent to clients.
    """
    secret = _webhook_secret(provider)
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    supplied = signature.strip().lower().replace("sha256=", "")
    return hmac.compare_digest(expected, supplied)


def _payment_id(provider, transaction_id):
    """Stable ID prevents the same gateway transaction from being submitted twice."""
    raw = f"{provider.lower()}:{transaction_id.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def _server_now():
    return datetime.now(timezone.utc)




def _record_admin_audit(db, admin_detail, action, target=""):
    try:
        db.collection("adminAuditLogs").document().set({
            "action": str(action)[:100],
            "target": str(target)[:200],
            "adminUid": str((admin_detail or {}).get("uid", ""))[:160],
            "adminEmail": str((admin_detail or {}).get("email", ""))[:200],
            "createdAt": _server_now(),
        })
    except Exception:
        app.logger.exception("Admin audit logging failed")


def _approve_payment_transaction(db, payment_id, admin_uid, verification_source="admin"):
    from firebase_admin import firestore

    payment_ref = db.collection("payments").document(payment_id)
    transaction = db.transaction()
    payment_snap = transaction.get(payment_ref)
    if not payment_snap.exists:
        raise ValueError("Payment record not found.")
    payment = payment_snap.to_dict() or {}
    uid = str(payment.get("uid", "")).strip()
    if not uid:
        raise ValueError("Payment is not linked to a student account.")

    user_ref = db.collection("users").document(uid)
    entitlement_ref = db.collection("entitlements").document(uid)
    user_snap = transaction.get(user_ref)
    if not user_snap.exists:
        raise ValueError("Student account was not found.")

    status = str(payment.get("status", "Pending"))
    if status in {"Approved", "Verified"}:
        entitlement_snap = transaction.get(entitlement_ref)
        entitlement = entitlement_snap.to_dict() if entitlement_snap.exists else {}
        return {"already_approved": True, "uid": uid, "entitlement": entitlement}
    if status not in {"Pending", "Submitted"}:
        raise ValueError(f"Payment cannot be approved from status: {status}.")

    plan_name = str(payment.get("plan", "monthly"))
    plan = PAYMENT_PLANS.get(plan_name)
    if not plan:
        raise ValueError("Invalid payment plan.")

    now = _server_now()
    entitlement_snap = transaction.get(entitlement_ref)
    existing_entitlement = entitlement_snap.to_dict() if entitlement_snap.exists else {}
    existing_expiry = existing_entitlement.get("expiresAt")
    if isinstance(existing_expiry, datetime) and existing_expiry > now:
        starts = existing_entitlement.get("startsAt") or now
        expires = existing_expiry + timedelta(days=plan["duration_days"])
    else:
        starts = now
        expires = now + timedelta(days=plan["duration_days"])

    entitlement = {
        "uid": uid,
        "premium": True,
        "plan": plan_name,
        "amount": plan["amount"],
        "startsAt": starts,
        "expiresAt": expires,
        "updatedAt": now,
        "sourcePaymentId": payment_id,
        "updatedBy": admin_uid,
        "verificationSource": verification_source,
    }

    transaction.update(payment_ref, {
        "status": "Verified" if verification_source != "admin" else "Approved",
        "approvedAt": now,
        "approvedBy": admin_uid,
        "verifiedAt": now,
        "verificationSource": verification_source,
        "updatedAt": now,
    })
    transaction.set(entitlement_ref, entitlement, merge=True)
    transaction.update(user_ref, {
        "isPaid": True,
        "freeTrial.isActive": False,
        "subscription": {
            "plan": plan_name,
            "amount": plan["amount"],
            "startedAt": starts,
            "expiresAt": expires,
            "paymentId": payment_id,
        },
        "unlockedAt": now,
        "updatedAt": now,
    })
    return {"already_approved": False, "uid": uid, "entitlement": entitlement}


@app.post('/api/payments/submit')
def submit_payment():
    """Create a pending payment using server-controlled plan/amount values."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        provider = str(body.get("provider", "manual")).strip().lower()
        transaction_id = str(body.get("transactionId", "")).strip()
        plan_name = str(body.get("plan", "monthly")).strip().lower()
        student_name = str(body.get("studentName", "")).strip()[:120]
        class_name = str(body.get("className", "")).strip()[:20]
        uid = detail.get("uid")

        if provider not in PAYMENT_PROVIDERS:
            return jsonify({"error": "Unsupported payment provider."}), 400
        if not transaction_id or len(transaction_id) > 160:
            return jsonify({"error": "A valid transaction ID is required."}), 400
        if plan_name not in PAYMENT_PLANS:
            return jsonify({"error": "Invalid payment plan."}), 400

        db = firestore.client()
        payment_id = _payment_id(provider, transaction_id)
        ref = db.collection("payments").document(payment_id)
        existing = ref.get()
        if existing.exists:
            old = existing.to_dict() or {}
            if old.get("uid") != uid:
                return jsonify({"error": "This transaction ID has already been submitted."}), 409
            return jsonify({"success": True, "paymentId": payment_id, "status": old.get("status", "Pending")}), 200

        now = _server_now()
        ref.set({
            "uid": uid,
            "studentName": student_name,
            "className": class_name,
            "provider": provider,
            "transactionId": transaction_id,
            "plan": plan_name,
            "amount": PAYMENT_PLANS[plan_name]["amount"],
            "timestamp": now,
            "status": "Pending",
        })
        return jsonify({"success": True, "paymentId": payment_id, "status": "Pending"}), 201
    except Exception as exc:
        return jsonify({"error": "Request could not be completed.", "requestId": getattr(g, "request_id", "")}), 500


@app.post('/api/payments/approve')
def approve_payment():
    """Atomically approve payment + create entitlement + unlock account."""
    ok, detail = _require_admin_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        payment_id = str(body.get("paymentId", "")).strip()
        if not payment_id:
            return jsonify({"error": "paymentId is required."}), 400
        db = firestore.client()
        result = _approve_payment_transaction(db, payment_id, detail.get("uid", "admin"))
        if not result.get('already_approved'):
            _record_admin_audit(db, detail, 'payment_approved', payment_id)
            _create_notification(db, result.get('uid'), 'Premium access unlocked 🎉', 'Your payment has been verified. Premium courses and exam archives are now available.', 'payment', {'paymentId': payment_id})
        return jsonify({"success": True, **result}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "Request could not be completed.", "requestId": getattr(g, "request_id", "")}), 500


@app.post('/api/payments/reject')
def reject_payment():
    ok, detail = _require_admin_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        payment_id = str(body.get("paymentId", "")).strip()
        if not payment_id:
            return jsonify({"error": "paymentId is required."}), 400
        db = firestore.client()
        ref = db.collection("payments").document(payment_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({"error": "Payment record not found."}), 404
        payment = snap.to_dict() or {}
        if payment.get("status") == "Approved":
            return jsonify({"error": "An approved payment cannot be rejected."}), 409
        ref.update({"status": "Rejected", "rejectedAt": _server_now(), "rejectedBy": detail.get("uid", "admin")})
        _record_admin_audit(db, detail, 'payment_rejected', payment_id)
        return jsonify({"success": True, "status": "Rejected"})
    except Exception as exc:
        return jsonify({"error": "Request could not be completed.", "requestId": getattr(g, "request_id", "")}), 500


@app.post('/api/payments/webhook/<provider>')
def payment_gateway_webhook(provider):
    """Gateway callback: verify signature, match the existing payment, then unlock atomically.

    Configure the real Telebirr/CBE gateway webhook to call this endpoint and
    set TELEBIRR_WEBHOOK_SECRET / CBE_WEBHOOK_SECRET on the server. The generic
    adapter expects JSON containing transactionId, status and amount.
    """
    provider = str(provider).strip().lower()
    if provider not in {"telebirr", "cbe"}:
        return jsonify({"error": "Unsupported payment gateway."}), 404
    raw = request.get_data(cache=True)
    signature = request.headers.get("X-BMT-Signature") or request.headers.get("X-Signature", "")
    if not _constant_time_signature_ok(provider, raw, signature):
        return jsonify({"error": "Invalid webhook signature."}), 401
    try:
        from firebase_admin import firestore
        payload = request.get_json(silent=True) or {}
        transaction_id = str(payload.get("transactionId") or payload.get("transaction_id") or payload.get("reference") or "").strip()
        gateway_status = str(payload.get("status") or payload.get("paymentStatus") or "").strip().lower()
        if not transaction_id or gateway_status not in PAYMENT_VERIFIED_STATUSES:
            return jsonify({"success": True, "ignored": True}), 200

        db = firestore.client()
        payment_id = _payment_id(provider, transaction_id)
        payment_ref = db.collection("payments").document(payment_id)
        snap = payment_ref.get()
        if not snap.exists:
            return jsonify({"error": "Payment reference not found."}), 404
        payment = snap.to_dict() or {}
        expected_amount_raw = payment.get("amount", 0)
        received_amount = payload.get("amount")
        # V30.44: a verified-status webhook must carry an amount. Otherwise a
        # signed transaction-id/status pair could unlock a payment without
        # proving that the gateway amount matches the server-side order.
        try:
            expected_amount = Decimal(str(expected_amount_raw))
        except (InvalidOperation, ValueError, TypeError):
            return jsonify({"error": "Invalid stored payment amount."}), 500
        if expected_amount <= 0:
            return jsonify({"error": "Invalid stored payment amount."}), 500
        if received_amount is None:
            if os.getenv("ALLOW_WEBHOOK_WITHOUT_AMOUNT", "0").lower() not in {"1", "true", "yes"}:
                return jsonify({"error": "Payment amount is required for gateway verification."}), 400
        else:
            try:
                received_amount_decimal = Decimal(str(received_amount))
                if abs(received_amount_decimal - expected_amount) > Decimal("0.01"):
                    return jsonify({"error": "Payment amount mismatch."}), 409
            except (InvalidOperation, ValueError, TypeError):
                return jsonify({"error": "Invalid payment amount."}), 400

        configured_currency = os.getenv("PAYMENT_CURRENCY", "").strip().upper()
        received_currency = str(payload.get("currency") or payload.get("currencyCode") or "").strip().upper()
        if configured_currency and not received_currency:
            return jsonify({"error": "Payment currency is required for gateway verification."}), 400
        if configured_currency and received_currency != configured_currency:
            return jsonify({"error": "Payment currency mismatch."}), 409

        result = _approve_payment_transaction(db, payment_id, "gateway", provider)
        if not result.get("already_approved"):
            _create_notification(db, result.get("uid"), "Payment verified 🎉", "Your payment was verified automatically. Premium access is now unlocked.", "payment", {"paymentId": payment_id, "provider": provider})
        return jsonify({"success": True, "paymentId": payment_id, "unlocked": True, "alreadyProcessed": result.get("already_approved", False)}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Payment gateway webhook failure")
        return jsonify({"error": "Webhook processing failed."}), 500


@app.get('/api/quizzes')
def get_student_quizzes():
    """Return sanitized quiz questions without exposing answer keys."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        class_name = str(request.args.get('className', '')).strip()[:20]
        if not class_name:
            return jsonify({'error': 'className is required.'}), 400
        db = firestore.client()
        rows = []
        query = db.collection('quizzes').where('className', '==', class_name)
        for snap in query.stream():
            quiz = snap.to_dict() or {}
            rows.append({
                'id': snap.id,
                'title': quiz.get('title', 'Quiz'),
                'question': quiz.get('question', ''),
                'options': quiz.get('options', {}) if isinstance(quiz.get('options', {}), dict) else {},
                'imageUrl': quiz.get('imageUrl', ''),
                'className': quiz.get('className', class_name),
            })
        return jsonify({'quizzes': rows}), 200
    except Exception:
        app.logger.exception('Quiz content request failed')
        return jsonify({'error': 'Unable to load quizzes.'}), 500


@app.get('/api/videos')
def get_student_videos():
    """Return only video metadata/URLs the authenticated user is entitled to view."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        class_name = str(request.args.get('className', '')).strip()[:20]
        if not class_name:
            return jsonify({'error': 'className is required.'}), 400
        db = firestore.client()
        uid = str(detail.get('uid', '')).strip()
        is_admin = detail.get('admin') is True or detail.get('role') == 'admin'
        user = db.collection('users').document(uid).get().to_dict() or {}
        entitlement = db.collection('entitlements').document(uid).get().to_dict() or {}
        now = _server_now()
        expires = entitlement.get('expiresAt')
        premium = bool(entitlement.get('premium')) and (not isinstance(expires, datetime) or expires > now)
        trial = user.get('freeTrial') or {}
        trial_expires = trial.get('expiresAt')
        trial_active = bool(trial.get('isActive'))
        if isinstance(trial_expires, str):
            try:
                trial_expires = datetime.fromisoformat(trial_expires.replace('Z', '+00:00'))
            except ValueError:
                trial_expires = None
        if isinstance(trial_expires, datetime) and trial_expires.tzinfo is None:
            trial_expires = trial_expires.replace(tzinfo=timezone.utc)
        trial_active = trial_active and (not isinstance(trial_expires, datetime) or trial_expires > now)

        rows = []
        for snap in db.collection('videos').where('className', '==', class_name).stream():
            video = snap.to_dict() or {}
            paid = bool(video.get('isPaid'))
            allowed = is_admin or not paid or premium or (trial_active and video.get('isFreeTrialAccessible') is not False)
            item = {
                'id': snap.id,
                'title': video.get('title', 'Video'),
                'description': video.get('description', ''),
                'className': video.get('className', class_name),
                'isPaid': paid,
                'isFreeTrialAccessible': video.get('isFreeTrialAccessible', True),
                'provider': video.get('provider', ''),
                'duration': video.get('duration', ''),
                'thumbnail': video.get('thumbnail', ''),
                'accessible': allowed,
            }
            if allowed:
                item['url'] = video.get('url', '')
            rows.append(item)
        return jsonify({'videos': rows, 'premium': premium, 'freeTrialActive': trial_active}), 200
    except Exception:
        app.logger.exception('Video content request failed')
        return jsonify({'error': 'Unable to load videos.'}), 500


@app.get('/api/payments/entitlement')
def get_entitlement():
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        uid = detail.get("uid")
        snap = firestore.client().collection("entitlements").document(uid).get()
        if not snap.exists:
            return jsonify({"premium": False}), 200
        data = snap.to_dict() or {}
        expires = data.get("expiresAt")
        if isinstance(expires, datetime) and expires <= _server_now():
            return jsonify({"premium": False, "expired": True}), 200
        return jsonify({
            "premium": bool(data.get("premium")),
            "plan": data.get("plan"),
            "expiresAt": expires.isoformat() if isinstance(expires, datetime) else None,
        }), 200
    except Exception as exc:
        return jsonify({"error": "Request could not be completed.", "requestId": getattr(g, "request_id", "")}), 500

def _require_user_bearer():
    fb = _firebase_admin_from_env()
    if not fb:
        return False, _auth_error('Server Firebase credentials are not configured. Exam grading is unavailable until FIREBASE_SERVICE_ACCOUNT_JSON is added.', 503)
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or not auth_header.split(' ', 1)[1].strip():
        return False, _auth_error('Authentication required.', 401)
    try:
        from firebase_admin import auth as firebase_auth
        return True, firebase_auth.verify_id_token(auth_header.split(' ', 1)[1].strip(), check_revoked=True)
    except Exception:
        return False, _auth_error('Invalid authentication token.', 401)


@app.post('/api/quiz-results')
def submit_legacy_quiz_result():
    """Server-authoritative result submission for the legacy one-question quiz UI."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        quiz_id = str(body.get('quizId', '')).strip()
        selected = str(body.get('selectedAnswer', '')).strip().upper()
        if not quiz_id or not selected:
            return jsonify({'error': 'quizId and selectedAnswer are required.'}), 400
        if selected not in {'A', 'B', 'C', 'D'}:
            return jsonify({'error': 'Invalid answer option.'}), 400

        db = firestore.client()
        snap = db.collection('quizzes').document(quiz_id).get()
        if not snap.exists:
            return jsonify({'error': 'Quiz not found.'}), 404
        quiz = snap.to_dict() or {}
        correct = str(quiz.get('correctAnswer', '')).strip().upper()
        if correct not in {'A', 'B', 'C', 'D'}:
            return jsonify({'error': 'This quiz has no valid answer key.'}), 409

        score = 1 if selected == correct else 0
        total = 1
        uid = str(detail.get('uid', '')).strip()
        now = datetime.now(timezone.utc)
        db.collection('quizResults').document().set({
            'quizId': quiz_id,
            'studentUid': uid,
            'studentEmail': detail.get('email', ''),
            'studentName': detail.get('name', detail.get('email', 'Student')),
            'score': score,
            'total': total,
            'percentage': score * 100,
            'submittedAt': now,
            'selectedAnswer': selected,
        })
        return jsonify({'success': True, 'score': score, 'total': total, 'percentage': score * 100}), 201
    except Exception:
        app.logger.exception('Legacy quiz result submission failed')
        return jsonify({'error': 'Unable to submit quiz result.'}), 500


@app.get('/api/exams/available')
def available_exams():
    """Return only exams the authenticated student is allowed to see.

    Exam documents are no longer read directly by the browser for discovery.
    Correct answers remain server-side in examKeys.
    """
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        db = firestore.client()
        uid = detail.get('uid')
        user_snap = db.collection('users').document(uid).get()
        user = user_snap.to_dict() or {} if user_snap.exists else {}
        role = str(user.get('accountType') or user.get('role') or detail.get('role') or '').lower()
        if role != 'student':
            return jsonify({'error': 'Only student accounts can view exams.'}), 403
        student_class = str(user.get('className') or user.get('class') or user.get('grade') or '').strip()
        if not student_class:
            return jsonify({'exams': [], 'warning': 'Your student class is not configured.'}), 200
        rows = []
        docs = db.collection('exams').where('status', '==', 'published').limit(100).stream()
        now = _server_now()
        for d in docs:
            e = d.to_dict() or {}
            exam_class = str(e.get('className') or e.get('grade') or '').strip()
            if exam_class != student_class:
                continue
            start_at, end_at = e.get('startAt'), e.get('endAt')
            if hasattr(start_at, 'timestamp') and now.timestamp() < start_at.timestamp():
                continue
            if hasattr(end_at, 'timestamp') and now.timestamp() > end_at.timestamp():
                continue
            rows.append({
                'id': d.id, 'title': e.get('title', 'Exam'),
                'className': exam_class,
                'durationMinutes': int(e.get('durationMinutes', 30) or 30),
                'totalPoints': e.get('totalPoints', 0),
                'questionCount': len(e.get('questions') or []),
                'passMark': float(e.get('passMark', 50) or 50),
                'maxAttempts': int(e.get('maxAttempts', 1) or 1)
            })
        rows.sort(key=lambda x: str(x.get('title', '')).lower())
        return jsonify({'exams': rows, 'className': student_class}), 200
    except Exception:
        app.logger.exception('Available exams lookup failed')
        return jsonify({'error': 'Unable to load available exams.'}), 500


@app.post('/api/exams/start')
def start_exam():
    """Create a server-authoritative exam attempt and deadline."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        exam_id = str(body.get('examId', '')).strip()
        if not exam_id:
            return jsonify({'error': 'examId is required.'}), 400
        db = firestore.client()
        exam_snap = db.collection('exams').document(exam_id).get()
        if not exam_snap.exists:
            return jsonify({'error': 'Exam not found.'}), 404
        exam = exam_snap.to_dict() or {}
        if exam.get('status') != 'published':
            return jsonify({'error': 'This exam is not available.'}), 403
        uid = detail.get('uid')
        # Exam policy is server-controlled. Require a real student account and
        # enforce the student's grade/class against the published exam.
        user_snap = db.collection('users').document(uid).get()
        user = user_snap.to_dict() or {} if user_snap.exists else {}
        user_role = str(user.get('accountType') or user.get('role') or detail.get('role') or '').lower()
        if user_role != 'student':
            return jsonify({'error': 'Only student accounts can take exams.'}), 403
        student_class = str(user.get('className') or user.get('class') or user.get('grade') or '').strip()
        exam_class = str(exam.get('className') or exam.get('grade') or '').strip()
        if exam_class and student_class and exam_class != student_class:
            return jsonify({'error': 'This exam is not assigned to your class.'}), 403
        if exam_class and not student_class:
            return jsonify({'error': 'Your student class is not configured.'}), 409
        # Optional publication window is enforced by the server.
        now = _server_now()
        start_at = exam.get('startAt')
        end_at = exam.get('endAt')
        if hasattr(start_at, 'timestamp') and now.timestamp() < start_at.timestamp():
            return jsonify({'error': 'This exam is not open yet.'}), 403
        if hasattr(end_at, 'timestamp') and now.timestamp() > end_at.timestamp():
            return jsonify({'error': 'This exam is closed.'}), 403
        # Enforce the exam's maximum-attempt policy server-side.
        max_attempts = max(1, min(20, int(exam.get('maxAttempts', 1))))
        submitted_count = 0
        for attempt_doc in db.collection('examAttempts').where('examId', '==', exam_id).where('userId', '==', uid).where('status', '==', 'submitted').stream():
            submitted_count += 1
        if submitted_count >= max_attempts:
            return jsonify({'error': 'Maximum attempts reached for this exam.', 'maxAttempts': max_attempts}), 409
        # One active attempt per student/exam.
        active = db.collection('examAttempts').where('examId', '==', exam_id).where('userId', '==', uid).where('status', '==', 'started').limit(1).stream()
        existing = next(iter(active), None)
        if existing:
            data = existing.to_dict() or {}
            return jsonify({'attemptId': existing.id, 'startedAt': data.get('startedAt').isoformat(), 'deadlineAt': data.get('deadlineAt').isoformat()}), 200
        now = _server_now()
        duration = max(1, min(240, int(exam.get('durationMinutes', 30))))
        deadline = now + timedelta(minutes=duration)
        ref = db.collection('examAttempts').document()
        ref.set({'examId': exam_id, 'examTitle': exam.get('title', 'Exam'), 'userId': uid,
                 'className': exam.get('className', ''), 'status': 'started', 'answers': {},
                 'passMark': float(exam.get('passMark', 50) or 50),
                 'maxAttempts': max_attempts, 'startedAt': now, 'deadlineAt': deadline, 'updatedAt': now})
        safe_questions = [{
            'question': q.get('question', ''), 'type': q.get('type', 'mcq'),
            'options': q.get('options') or {}, 'points': q.get('points', 1),
            'topic': q.get('topic', 'General')
        } for q in (exam.get('questions') or [])]
        return jsonify({'attemptId': ref.id, 'startedAt': now.isoformat(),
                        'deadlineAt': deadline.isoformat(), 'examTitle': exam.get('title', 'Exam'),
                        'className': exam.get('className', ''), 'durationMinutes': duration,
                        'passMark': float(exam.get('passMark', 50) or 50),
                        'questions': safe_questions}), 201
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

@app.get('/api/exams/active')
def active_exam_attempt():
    """Resume the student's active exam attempt after refresh/reconnect. Never exposes answer keys."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        uid = detail.get('uid')
        db = firestore.client()
        active = db.collection('examAttempts').where('userId', '==', uid).where('status', '==', 'started').order_by('updatedAt', direction=firestore.Query.DESCENDING).limit(5).stream()
        for attempt_doc in active:
            attempt = attempt_doc.to_dict() or {}
            deadline = attempt.get('deadlineAt')
            if deadline and hasattr(deadline, 'timestamp') and _server_now().timestamp() >= deadline.timestamp():
                continue
            exam_id = str(attempt.get('examId', ''))
            exam_snap = db.collection('exams').document(exam_id).get()
            if not exam_snap.exists:
                continue
            exam = exam_snap.to_dict() or {}
            if exam.get('status') != 'published':
                continue
            # Only send question content, never examKeys/correct answers.
            safe_questions = []
            for q in (exam.get('questions') or []):
                safe_questions.append({
                    'question': q.get('question', ''),
                    'type': q.get('type', 'mcq'),
                    'options': q.get('options') or {},
                    'points': q.get('points', 1),
                    'topic': q.get('topic', 'General')
                })
            return jsonify({'active': True, 'examId': exam_id, 'attemptId': attempt_doc.id,
                            'examTitle': exam.get('title', 'Exam'), 'className': exam.get('className', ''),
                            'durationMinutes': exam.get('durationMinutes', 30),
                            'passMark': float(attempt.get('passMark', exam.get('passMark', 50)) or 50),
                            'startedAt': attempt.get('startedAt').isoformat() if hasattr(attempt.get('startedAt'), 'isoformat') else None,
                            'deadlineAt': deadline.isoformat() if hasattr(deadline, 'isoformat') else None,
                            'answers': attempt.get('answers') or {}, 'questions': safe_questions})
        return jsonify({'active': False})
    except Exception as exc:
        return jsonify({'error': 'Unable to resume exam attempt.'}), 500

@app.get('/api/exams/history')
def exam_history():
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        uid = detail.get('uid')
        snap = firestore.client().collection('examAttempts').where('userId', '==', uid).where('status', '==', 'submitted').order_by('submittedAt', direction=firestore.Query.DESCENDING).limit(30).stream()
        rows = []
        for d in snap:
            x = d.to_dict() or {}
            rows.append({'attemptId': d.id, 'examId': x.get('examId'), 'examTitle': x.get('examTitle', 'Exam'),
                         'score': x.get('score', 0), 'totalPoints': x.get('totalPoints', 0), 'percentage': x.get('percentage', 0),
                         'submittedAt': x.get('submittedAt').isoformat() if hasattr(x.get('submittedAt'), 'isoformat') else None})
        return jsonify({'attempts': rows})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/exams/save')
def save_exam_answers():
    """Durably autosave answers while an attempt is still open. Never accepts grading data."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        exam_id = str(body.get('examId', '')).strip()
        attempt_id = str(body.get('attemptId', '')).strip()
        answers = body.get('answers') or {}
        if not exam_id or not attempt_id or not isinstance(answers, dict):
            return jsonify({'error': 'examId, attemptId and answers are required.'}), 400
        if len(answers) > 300:
            return jsonify({'error': 'Too many answers.'}), 400
        db = firestore.client(); uid = detail.get('uid')
        ref = db.collection('examAttempts').document(attempt_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({'error': 'Exam attempt not found.'}), 404
        attempt = snap.to_dict() or {}
        if attempt.get('userId') != uid or attempt.get('examId') != exam_id:
            return jsonify({'error': 'This attempt does not belong to you.'}), 403
        if attempt.get('status') != 'started':
            return jsonify({'saved': False, 'status': attempt.get('status')}), 409
        deadline = attempt.get('deadlineAt')
        now = _server_now()
        if deadline and hasattr(deadline, 'timestamp') and now.timestamp() > deadline.timestamp():
            return jsonify({'saved': False, 'expired': True}), 409
        # Only simple answer values are persisted; grading remains server-side.
        clean = {str(k)[:8]: str(v)[:20] for k, v in answers.items()}
        ref.update({'answers': clean, 'updatedAt': now})
        return jsonify({'saved': True, 'savedAt': now.isoformat()})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

@app.post('/api/exams/grade')
def grade_exam():
    """Atomically grade one attempt using the server-only answer key."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        exam_id = str(body.get('examId', '')).strip()[:150]
        attempt_id = str(body.get('attemptId', '')).strip()[:150]
        submitted_answers = body.get('answers') or {}
        if not exam_id or not attempt_id or not isinstance(submitted_answers, dict):
            return jsonify({'error': 'examId, attemptId and answers are required.'}), 400
        if len(submitted_answers) > 300:
            return jsonify({'error': 'Too many answers.'}), 400

        # Normalize only simple answer values. Unknown question IDs are ignored
        # during grading, so the browser can never add points or alter the key.
        submitted_answers = {
            str(k)[:20]: str(v)[:100]
            for k, v in submitted_answers.items()
            if k is not None and v is not None
        }
        db = firestore.client()
        uid = detail.get('uid')
        attempt_ref = db.collection('examAttempts').document(attempt_id)
        exam_ref = db.collection('exams').document(exam_id)
        key_ref = db.collection('examKeys').document(exam_id)
        transaction = db.transaction()

        result = {}
        should_notify_parent = False

        # A Firestore transaction makes the submitted transition atomic. Two
        # concurrent requests can therefore not both grade the same attempt.
        attempt_snap = transaction.get(attempt_ref)
        if not attempt_snap.exists:
            return jsonify({'error': 'Exam attempt not found.'}), 404
        attempt = attempt_snap.to_dict() or {}
        if attempt.get('userId') != uid or attempt.get('examId') != exam_id:
            return jsonify({'error': 'This attempt does not belong to you.'}), 403
        if attempt.get('status') == 'submitted':
            result = {
                'score': attempt.get('score', 0),
                'totalPoints': attempt.get('totalPoints', 0),
                'percentage': attempt.get('percentage', 0),
                'passMark': attempt.get('passMark', 50),
                'status': attempt.get('statusResult', 'FAIL'),
                'alreadySubmitted': True,
            }
            return jsonify(result), 200
        if attempt.get('status') != 'started':
            return jsonify({'error': 'This attempt is not open.'}), 409

        exam_snap = transaction.get(exam_ref)
        key_snap = transaction.get(key_ref)
        if not exam_snap.exists or not key_snap.exists:
            return jsonify({'error': 'Exam grading data is unavailable.'}), 404
        exam = exam_snap.to_dict() or {}
        key = key_snap.to_dict() or {}
        if exam.get('status') != 'published':
            return jsonify({'error': 'This exam is no longer available.'}), 409

        deadline = attempt.get('deadlineAt')
        now = _server_now()
        expired = bool(hasattr(deadline, 'timestamp') and now.timestamp() > deadline.timestamp() + 5)
        # If the deadline has passed, use the last server-autosaved answers and
        # do not trust a late browser payload.
        answers = attempt.get('answers') or {}
        if not expired:
            answers = submitted_answers

        correct = key.get('answers') or {}
        points_map = key.get('points') or {}
        topics_map = key.get('topics') or {}
        pass_mark = max(0.0, min(100.0, float(attempt.get('passMark', exam.get('passMark', 50)) or 50)))
        score = 0.0
        total = 0.0
        topic_stats = {}
        for idx, expected in correct.items():
            idx = str(idx)
            points = max(0.0, float(points_map.get(idx, 1) or 1))
            total += points
            topic = str(topics_map.get(idx, 'General'))[:80] or 'General'
            stat = topic_stats.setdefault(topic, {'score': 0, 'total': 0, 'correct': 0, 'questions': 0})
            stat['total'] += points
            stat['questions'] += 1
            if str(answers.get(idx, '')).strip().upper() == str(expected).strip().upper():
                score += points
                stat['score'] += points
                stat['correct'] += 1

        percentage = round((score / total) * 100, 1) if total else 0.0
        weak_topics = [
            topic for topic, stat in topic_stats.items()
            if stat['total'] and (stat['score'] / stat['total'] * 100) < 60
        ]
        status_result = 'PASS' if percentage >= pass_mark else 'FAIL'
        transaction.update(attempt_ref, {
            'status': 'submitted',
            'answers': answers,
            'score': score,
            'totalPoints': total,
            'percentage': percentage,
            'passMark': pass_mark,
            'statusResult': status_result,
            'topicStats': topic_stats,
            'weakTopics': weak_topics,
            'submittedAt': now,
            'updatedAt': now,
            'gradingVersion': 'V31.11',
        })
        transaction.commit()

        result = {
            'score': score,
            'totalPoints': total,
            'percentage': percentage,
            'passMark': pass_mark,
            'status': status_result,
            'topicStats': topic_stats,
            'weakTopics': weak_topics,
            'expiredSubmission': expired,
        }
        should_notify_parent = True

        # Notify every verified parent linked to this student. This is deliberately
        # outside the grading transaction so notification failure can never roll
        # back a valid exam result.
        if should_notify_parent:
            try:
                links = db.collection('parentChildLinks').where('childUid', '==', uid).where('status', '==', 'verified').limit(100).stream()
                for link in links:
                    parent_uid = str((link.to_dict() or {}).get('parentUid', '')).strip()
                    if parent_uid:
                        _create_notification(
                            db, parent_uid,
                            'Exam result available',
                            f"{exam.get('title', 'Exam')}: {percentage}% ({status_result}).",
                            kind='exam_result',
                            data={'examId': exam_id, 'attemptId': attempt_id, 'childUid': uid, 'percentage': percentage, 'status': status_result},
                        )
            except Exception:
                app.logger.exception('Exam parent notification failed')

        return jsonify(result), 200
    except Exception:
        app.logger.exception('Server-side exam grading failed')
        return jsonify({'error': 'Unable to grade exam result.'}), 500


@app.get('/api/exams/result/<attempt_id>')
def exam_result(attempt_id):
    """Return the student's own completed result, including topic-level analysis."""
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        uid = detail.get('uid')
        snap = firestore.client().collection('examAttempts').document(str(attempt_id)[:150]).get()
        if not snap.exists:
            return jsonify({'error': 'Result not found.'}), 404
        x = snap.to_dict() or {}
        if x.get('userId') != uid:
            return jsonify({'error': 'Forbidden.'}), 403
        if x.get('status') != 'submitted':
            return jsonify({'error': 'This exam attempt has not been submitted yet.'}), 409
        exam_snap = firestore.client().collection('exams').document(str(x.get('examId', ''))).get()
        key_snap = firestore.client().collection('examKeys').document(str(x.get('examId', ''))).get()
        questions = (exam_snap.to_dict() or {}).get('questions') if exam_snap.exists else []
        key = key_snap.to_dict() or {} if key_snap.exists else {}
        correct = key.get('answers') or {}
        answers = x.get('answers') or {}
        review = []
        for idx, q in enumerate(questions or []):
            expected = str(correct.get(str(idx), ''))
            selected = str(answers.get(str(idx), ''))
            review.append({
                'index': idx, 'question': q.get('question', ''), 'type': q.get('type', 'mcq'),
                'options': q.get('options') or {}, 'selectedAnswer': selected,
                'correctAnswer': expected, 'isCorrect': bool(selected and expected and selected.upper() == expected.upper()),
                'points': q.get('points', 1), 'topic': q.get('topic', 'General')
            })
        percentage = float(x.get('percentage', 0) or 0)
        pass_mark = float(x.get('passMark', 50) or 50)
        return jsonify({
            'attemptId': snap.id, 'examId': x.get('examId'), 'examTitle': x.get('examTitle', 'Exam'),
            'score': x.get('score', 0), 'totalPoints': x.get('totalPoints', 0),
            'percentage': percentage, 'passMark': pass_mark, 'status': 'PASS' if percentage >= pass_mark else 'FAIL',
            'topicStats': x.get('topicStats') or {}, 'weakTopics': x.get('weakTopics') or [],
            'answers': answers, 'review': review,
            'startedAt': x.get('startedAt').isoformat() if hasattr(x.get('startedAt'), 'isoformat') else None,
            'submittedAt': x.get('submittedAt').isoformat() if hasattr(x.get('submittedAt'), 'isoformat') else None
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500



def _create_notification(db, target_uid, title, message, kind='general', data=None):
    """Create an in-app notification. Firestore is the durable source of truth."""
    from firebase_admin import firestore
    ref = db.collection('notifications').document()
    payload = {
        'targetUid': target_uid,
        'title': str(title)[:120],
        'message': str(message)[:600],
        'kind': str(kind)[:40],
        'read': False,
        'createdAt': firestore.SERVER_TIMESTAMP,
    }
    if isinstance(data, dict):
        payload['data'] = {str(k): str(v)[:300] for k, v in data.items() if v is not None}
        if data.get('actionUrl'):
            payload['actionUrl'] = str(data.get('actionUrl'))[:300]
    ref.set(payload)
    return ref.id


@app.get('/api/notifications')
def list_notifications():
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        uid = detail.get('uid')
        rows = []
        query = (firestore.client().collection('notifications')
                 .where('targetUid', 'in', [uid, 'all'])
                 .order_by('createdAt', direction=firestore.Query.DESCENDING)
                 .limit(50))
        for snap in query.stream():
            x = snap.to_dict() or {}
            created = x.get('createdAt')
            rows.append({
                'id': snap.id,
                'title': x.get('title', 'Bright Mind Tutor'),
                'message': x.get('message', ''),
                'kind': x.get('kind', 'general'),
                'read': bool(x.get('read', False)),
                'createdAt': created.isoformat() if hasattr(created, 'isoformat') else None,
                'data': x.get('data') or {},
            })
        unread = sum(1 for x in rows if not x['read'])
        return jsonify({'notifications': rows, 'unread': unread})
    except Exception as exc:
        # If a composite index is not deployed yet, return a useful error instead of breaking the dashboard.
        app.logger.exception('Notification list failed')
        return jsonify({'error': str(exc)}), 500


@app.post('/api/notifications/<notification_id>/read')
def mark_notification_read(notification_id):
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        ref = firestore.client().collection('notifications').document(notification_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({'error': 'Notification not found.'}), 404
        x = snap.to_dict() or {}
        if x.get('targetUid') not in {detail.get('uid'), 'all'}:
            return jsonify({'error': 'Not authorized.'}), 403
        ref.update({'read': True, 'readAt': firestore.SERVER_TIMESTAMP})
        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/notifications/read-all')
def mark_all_notifications_read():
    ok, detail = _require_user_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        db = firestore.client(); uid = detail.get('uid')
        docs = list(db.collection('notifications').where('targetUid', 'in', [uid, 'all']).where('read', '==', False).limit(100).stream())
        batch = db.batch()
        for snap in docs:
            batch.update(snap.reference, {'read': True, 'readAt': firestore.SERVER_TIMESTAMP})
        if docs:
            batch.commit()
        return jsonify({'success': True, 'updated': len(docs)})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/admin/announcements')
def create_announcement():
    ok, detail = _require_admin_bearer()
    if not ok:
        return detail
    try:
        from firebase_admin import firestore
        body = request.get_json(silent=True) or {}
        title = str(body.get('title', '')).strip()
        message = str(body.get('message', '')).strip()
        kind = str(body.get('kind', 'announcement')).strip().lower()
        target_uid = str(body.get('targetUid', 'all')).strip() or 'all'
        if not title or not message:
            return jsonify({'error': 'Title and message are required.'}), 400
        if len(title) > 120 or len(message) > 600:
            return jsonify({'error': 'Title or message is too long.'}), 400
        db = firestore.client()
        nid = _create_notification(db, target_uid, title, message, kind, {'announcement': 'true'})
        _record_admin_audit(db, detail, 'announcement_created', nid)
        # Optional browser push for an all-student announcement. In-app notification remains authoritative.
        push = {'sent': 0, 'failed': 0}
        try:
            from firebase_admin import messaging
            if os.getenv('ENABLE_FCM_PUSH', '0') == '1':
                token_docs = db.collection('fcmTokens').stream()
                tokens = []
                for token_doc in token_docs:
                    token_data = token_doc.to_dict() or {}
                    token = str(token_data.get('token') or '').strip()
                    uid = str(token_data.get('uid') or '').strip()
                    if not token or not uid:
                        continue
                    pref = db.collection('notificationPreferences').document(uid).get()
                    pref_data = pref.to_dict() or {} if pref.exists else {}
                    if pref_data.get('push', True) is False or pref_data.get('announcements', True) is False:
                        continue
                    tokens.append(token)
                if tokens:
                    result = messaging.send_each([messaging.Message(notification=messaging.Notification(title=title, body=message), token=t) for t in tokens])
                    push = {'sent': result.success_count, 'failed': result.failure_count}
        except Exception:
            app.logger.exception('Optional FCM announcement failed')
        return jsonify({'success': True, 'notificationId': nid, 'push': push}), 201
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

@app.post('/api/notifications/send')
def send_notification():
    """Optional FCM sender. Requires Firebase service-account JSON on the server."""
    ok, detail = _require_admin_bearer()
    if not ok:
        return detail
    try:
        import firebase_admin
        from firebase_admin import messaging
        body = request.get_json(silent=True) or {}
        tokens = body.get("tokens") or []
        title = str(body.get("title", "Bright Mind Tutor"))[:100]
        message = str(body.get("message", ""))[:500]
        target = str(body.get("target", "devices")).strip().lower()
        if target == "all" and not tokens:
            token_docs = firestore.client().collection('fcmTokens').limit(500).stream()
            tokens = [str((d.to_dict() or {}).get('token') or '').strip() for d in token_docs]
        if not isinstance(tokens, list) or not tokens or len(tokens) > 500 or not message:
            return jsonify({"error": "Provide a message and up to 500 registered devices."}), 400
        tokens = [str(t).strip() for t in tokens if str(t).strip()]
        if not tokens:
            return jsonify({"error": "At least one valid token is required."}), 400
        messages = [
            messaging.Message(
                notification=messaging.Notification(title=title, body=message),
                token=t,
            )
            for t in tokens
        ]
        result = messaging.send_each(messages)

        # Return actionable, non-secret diagnostics. This is especially useful
        # for distinguishing an invalid/unregistered browser token from a
        # Firebase credential, project, or permission problem.
        error_codes = {}
        invalid_tokens = []
        for token, response in zip(tokens, result.responses):
            if response.success:
                continue
            exc = response.exception
            code = getattr(exc, "code", None) or getattr(exc, "__class__", type(exc)).__name__
            code = str(code)[:120]
            error_codes[code] = error_codes.get(code, 0) + 1
            text = str(exc).lower()
            if any(marker in text for marker in (
                "unregistered", "registration-token-not-registered",
                "invalid-registration-token", "not a valid fcm registration token",
            )):
                invalid_tokens.append(token)

        # Remove tokens that Firebase explicitly reports as no longer valid.
        # This keeps future sends clean without exposing token values to the client.
        if invalid_tokens:
            database = firestore.client()
            for token in invalid_tokens:
                token_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
                ref = database.collection("fcmTokens").document(token_id)
                snap = ref.get()
                if snap.exists:
                    ref.delete()

        payload = {
            "success": result.failure_count == 0,
            "sent": result.success_count,
            "failed": result.failure_count,
            "errorCodes": error_codes,
        }
        if result.failure_count:
            payload["diagnostic"] = (
                "Some FCM sends failed. Check errorCodes. Invalid/unregistered "
                "tokens are removed automatically; permission/configuration errors "
                "must be fixed in Firebase/Render."
            )
        return jsonify(payload), 200 if result.success_count else 502
    except Exception as exc:
        # Preserve the request ID and expose a safe category, never credentials.
        text = str(exc).lower()
        if "permission" in text or "credential" in text or "unauthorized" in text:
            category = "FCM_CONFIGURATION_OR_PERMISSION_ERROR"
        elif "token" in text:
            category = "FCM_TOKEN_ERROR"
        else:
            category = "FCM_SERVER_ERROR"
        app.logger.exception("FCM send failed [%s] requestId=%s", category, getattr(g, "request_id", ""))
        return jsonify({
            "error": category,
            "requestId": getattr(g, "request_id", ""),
        }), 502

from teacher_routes import register_teacher_routes
register_teacher_routes(app, _require_user_bearer, _require_admin_bearer, _firebase_admin_from_env)

from archive_routes import register_archive_routes
register_archive_routes(app, _require_user_bearer, _require_admin_bearer, _firebase_admin_from_env)
from ai_material_routes import register_ai_material_routes
register_ai_material_routes(app, _require_user_bearer, _firebase_admin_from_env)
from live_routes import register_live_routes
register_live_routes(app, _require_user_bearer, _firebase_admin_from_env, _create_notification)
from progress_routes import register_progress_routes
register_progress_routes(app, _require_user_bearer, _firebase_admin_from_env)
from parent_routes import register_parent_routes
register_parent_routes(app, _require_user_bearer, _firebase_admin_from_env, _require_admin_bearer)
from notification_routes import register_notification_routes
register_notification_routes(app, _require_user_bearer, _require_admin_bearer, _firebase_admin_from_env, _create_notification)
from communication_routes import register_communication_routes
register_communication_routes(app, _require_user_bearer, _require_admin_bearer, _firebase_admin_from_env, _create_notification)
from admin_control_routes import register_admin_control_routes
register_admin_control_routes(app, _require_admin_bearer, _firebase_admin_from_env)
from admin_features_routes import register_admin_feature_routes
register_admin_feature_routes(app, _require_admin_bearer, _firebase_admin_from_env, _require_user_bearer)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)

