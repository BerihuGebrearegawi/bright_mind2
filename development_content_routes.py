"""V31.85: Admin-manageable media (video/book) for the Psychology & Child
Development lessons shown on the Student dashboard (/api/student/development,
defined in parent_routes.py's DEVELOPMENT_CATALOG) and the Parent dashboard
(PARENT_DEV_MODULES in templates/parent.html).

Additive and isolated: it does not replace or duplicate the existing
text-based lesson content. It stores a small {videoUrl, bookUrl} record per
lesson id in a new 'developmentMedia' Firestore collection, so admin can
attach a video link (e.g. an uploaded Cloudinary URL or a YouTube link) and a
book/PDF (uploaded via the existing Cloudinary storage - no Google Cloud
billing needed) to any lesson without touching code.
"""
from datetime import datetime, timezone
from flask import request, jsonify

# Labels only, for the admin picker - the authoritative lesson text lives in
# parent_routes.py (student catalog) and templates/parent.html (parent
# modules). Kept in sync manually since both dashboards' catalogs are static.
_STUDENT_LESSON_LABELS = [
    ('🌟 Character & Ethics', [
        ('truth-trust', 'Truth, Trust & Responsibility'),
        ('respect-empathy', 'Respect, Empathy & Helping Others'),
        ('fairness-patience', 'Fairness, Patience & Self-Control'),
        ('time-community', 'Punctuality & Everyday Ethics'),
    ]),
    ('🚀 Self Development', [
        ('self-awareness-goals', 'Self-Awareness & Goal Setting'),
        ('time-study', 'Time Management & Study Skills'),
        ('focus-discipline-confidence', 'Concentration, Discipline & Confidence'),
        ('problem-solving-leadership', 'Problem Solving, Decisions, Communication, Leadership & Creativity'),
    ]),
    ('❤️ Emotional & Social Development', [
        ('anger-regulation', 'Understanding Anger & Self-Regulation'),
        ('stress-resilience', 'Managing Stress & Building Resilience'),
        ('conflict-bullying', 'Conflict Resolution & Bullying Safety'),
        ('empathy-teamwork-relationships', 'Empathy, Teamwork & Healthy Relationships'),
    ]),
    ('🎯 Life Skills', [
        ('financial-literacy', 'Financial Literacy'),
        ('digital-online-safety', 'Digital Literacy & Online Safety'),
        ('critical-media-literacy', 'Critical Thinking & Media Literacy'),
        ('communication-responsibility-career', 'Communication, Responsibility & Future Careers'),
    ]),
]

_PARENT_MODULE_LABELS = [
    ('child-ethics', 'Teaching Ethics to Your Child (ስነምግባር)'),
    ('positive-parenting', 'Positive Parenting'),
    ('emotional-growth', 'Social & Emotional Growth'),
    ('learning-at-home', 'Supporting Learning at Home'),
    ('reading-habits', 'Reading Habits'),
    ('digital-safety', 'Digital Safety'),
    ('communication', 'Parent–Child Communication'),
    ('healthy-routine', 'Healthy Daily Routine'),
    ('confidence', 'Confidence & Independence'),
]


def register_development_content_routes(app, require_admin, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _clean(v, limit=2000):
        return str(v or '').strip()[:limit]

    @app.get('/api/development/media')
    def development_media_public():
        """Any signed-in user (student/parent) reads current attachments so
        both dashboards can show the same admin-uploaded video/book."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            out = {}
            for snap in db.collection('developmentMedia').stream():
                x = snap.to_dict() or {}
                out[snap.id] = {
                    'videoUrl': x.get('videoUrl') or '',
                    'bookUrl': x.get('bookUrl') or '',
                    'bookName': x.get('bookName') or '',
                }
            return jsonify({'success': True, 'media': out}), 200
        except Exception:
            app.logger.exception('Development media load failed')
            return jsonify({'error': 'Unable to load development media.'}), 500

    @app.get('/api/admin/development/catalog')
    def development_catalog_for_admin():
        """Flat picker list (both dashboards' lesson ids + titles) merged with
        whatever media is already attached, so admin doesn't need to know
        internal lesson ids."""
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            db = _db()
            media = {}
            for snap in db.collection('developmentMedia').stream():
                x = snap.to_dict() or {}
                media[snap.id] = {
                    'videoUrl': x.get('videoUrl') or '',
                    'bookUrl': x.get('bookUrl') or '',
                    'bookName': x.get('bookName') or '',
                }
        except Exception:
            media = {}
        items = []
        for group_title, lessons in _STUDENT_LESSON_LABELS:
            for lesson_id, title in lessons:
                items.append({'id': lesson_id, 'title': title, 'group': f'Student · {group_title}',
                              **media.get(lesson_id, {'videoUrl': '', 'bookUrl': '', 'bookName': ''})})
        for lesson_id, title in _PARENT_MODULE_LABELS:
            items.append({'id': lesson_id, 'title': title, 'group': 'Parent Center',
                          **media.get(lesson_id, {'videoUrl': '', 'bookUrl': '', 'bookName': ''})})
        return jsonify({'success': True, 'items': items}), 200

    @app.post('/api/admin/development/media')
    def development_media_set():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            lesson_id = _clean(body.get('lessonId'), 120)
            if not lesson_id:
                return jsonify({'error': 'lessonId is required.'}), 400
            video_url = _clean(body.get('videoUrl'), 500)
            book_url = _clean(body.get('bookUrl'), 500)
            book_name = _clean(body.get('bookName'), 200)
            if video_url and not video_url.startswith('https://'):
                return jsonify({'error': 'Video URL must be an HTTPS link.'}), 400
            if book_url and not book_url.startswith('https://'):
                return jsonify({'error': 'Book URL must be an HTTPS link.'}), 400
            db = _db()
            db.collection('developmentMedia').document(lesson_id).set({
                'videoUrl': video_url, 'bookUrl': book_url, 'bookName': book_name,
                'updatedAt': datetime.now(timezone.utc), 'updatedBy': detail.get('uid', 'admin'),
            }, merge=True)
            return jsonify({'success': True, 'lessonId': lesson_id, 'videoUrl': video_url,
                             'bookUrl': book_url, 'bookName': book_name}), 200
        except Exception:
            app.logger.exception('Development media save failed')
            return jsonify({'error': 'Unable to save development media.'}), 500

    @app.post('/api/admin/development/upload')
    def development_media_upload():
        """Upload a video or book/PDF file for a development lesson through
        the existing Cloudinary-backed storage (no Google Cloud billing
        needed - same provider already used for documents/books)."""
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            if 'file' not in request.files:
                return jsonify({'error': 'No file uploaded.'}), 400
            file = request.files['file']
            if not file or not file.filename:
                return jsonify({'error': 'No file selected.'}), 400
            raw = file.read()
            max_bytes = 60 * 1024 * 1024
            if len(raw) > max_bytes:
                return jsonify({'error': 'File is too large. Maximum size is 60MB.'}), 413
            content_type = file.content_type or 'application/octet-stream'
            from cloudinary_storage import upload_document
            uid = detail.get('uid', 'admin')
            # This media is intentionally open to any logged-in student/parent
            # once an admin publishes it (see module docstring) - not a
            # per-request-authorized document like /api/storage/document, so
            # it opts into the simpler permanent-URL access mode.
            result = upload_document(raw, file.filename, content_type, uid, folder='development-media', access='public')
            return jsonify({'success': True, 'url': result.get('_cloudinaryUrl') or '',
                             'fileName': result.get('fileName')}), 201
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Development media upload failed')
            return jsonify({'error': 'Unable to upload the file.'}), 500
