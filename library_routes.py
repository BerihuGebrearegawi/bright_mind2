"""BMT Digital Library — Reader APIs (V31.35).

Extends the EXISTING flat book storage (collections: books, bookLibrary,
teacherGuides, referenceBooks, psychologyBooks — see app.py) with an
in-app chapter/subchapter reading structure, per-student reading
position, and bookmarks.

Deliberately reuses the existing book records instead of creating a
parallel library system:
  - No new top-level "library" collection is introduced.
  - Chapters live in a subcollection under the EXISTING book document:
        {collection}/{bookId}/chapters/{chapterId}
        {collection}/{bookId}/chapters/{chapterId}/subchapters/{subchapterId}
  - Reading position is stored on the existing per-user progress map
    (users/{uid}.progress.books), matching the existing
    progress.videos / progress.quizzes convention already used
    elsewhere in this codebase (see progress_routes.py).
  - Bookmarks reuse the existing bookmarks/{uid} document and items map.

A book that has no chapters yet behaves exactly as before (flat
document view/download) — hasChapters defaults to false, so this is a
backward-compatible addition, not a breaking change.

Authorization mirrors the existing rule used for GCS book downloads in
app.py (_can_access_gcs_book): admin, the uploading/owning teacher, or
a student whose className matches the book's className.

Chapter/subchapter management (create/update/delete) is restricted to
admins and approved teachers who own the book, matching the existing
/api/storage/document upload permission in app.py.
"""
from datetime import datetime, timezone
from flask import request, jsonify

# The five existing book-ish collections this feature is allowed to touch.
# Keep in sync with app.py's `sources` list in the digital-library search
# route. Never add a new collection here without updating both places.
ALLOWED_BOOK_COLLECTIONS = {
    'books', 'bookLibrary', 'teacherGuides', 'referenceBooks', 'psychologyBooks'
}


def register_library_routes(app, require_user, require_admin, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _is_admin(detail):
        return detail.get('admin') is True or detail.get('role') == 'admin'

    def _book_ref(db, collection, book_id):
        if collection not in ALLOWED_BOOK_COLLECTIONS:
            return None
        return db.collection(collection).document(book_id)

    def _can_read_book(db, book, detail):
        """Same authorization shape as _can_access_gcs_book in app.py,
        generalized to work from an already-fetched book record instead
        of a storage path."""
        if _is_admin(detail):
            return True
        uid = str(detail.get('uid') or '').strip()
        if str(book.get('teacherUid') or book.get('uploaderUid') or '').strip() == uid:
            return True
        user = db.collection('users').document(uid).get().to_dict() or {}
        # V31.108 security fix: the chapter/reader endpoint must enforce the
        # same Learning Mode/Audience targeting as /api/library. Otherwise a
        # student who knows a book id could bypass the filtered library list.
        try:
            entitlement = db.collection('entitlements').document(uid).get().to_dict() or {}
        except Exception:
            entitlement = {}
        from targeting_access import content_visible_or_untargeted
        if not content_visible_or_untargeted(user, book, entitlement):
            return False
        book_class = str(book.get('className') or '').strip()
        if book_class and book_class == str(user.get('class') or '').strip():
            return True
        # A book with no className set is treated as open to all
        # signed-in students, matching the existing library search
        # behaviour (grade filter is optional there too).
        if not book_class:
            return True
        return False

    def _can_manage_book(db, book, detail):
        """Chapter/subchapter writes: admin, or the approved teacher who
        owns this book. Matches /api/storage/document's upload rule."""
        if _is_admin(detail):
            return True
        uid = str(detail.get('uid') or '').strip()
        if str(book.get('teacherUid') or book.get('uploaderUid') or '').strip() != uid:
            return False
        teacher = db.collection('teachers').document(uid).get().to_dict() or {}
        return bool(teacher.get('approved'))

    def _serialize_subchapter(snap):
        d = snap.to_dict() or {}
        return {
            'id': snap.id,
            'title': d.get('title') or '',
            'order': d.get('order') or 0,
            'pageStart': d.get('pageStart'),
            'pageEnd': d.get('pageEnd'),
            'content': d.get('content') or '',
            'learningObjective': d.get('learningObjective') or '',
        }

    def _serialize_chapter(db, book_ref, snap, include_subchapters=True):
        d = snap.to_dict() or {}
        out = {
            'id': snap.id,
            'title': d.get('title') or '',
            'order': d.get('order') or 0,
        }
        if include_subchapters:
            subs = (book_ref.collection('chapters').document(snap.id)
                    .collection('subchapters').order_by('order').stream())
            out['subchapters'] = [_serialize_subchapter(s) for s in subs]
        return out

    # ------------------------------------------------------------------
    # Book listing — admin only. Existing admin UI can create books but
    # has no way to browse them; needed here so the chapter manager has
    # something to select from.
    # ------------------------------------------------------------------
    @app.get('/api/library/books')
    def library_list_books():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            collection = request.args.get('collection', 'books')
            if collection not in ALLOWED_BOOK_COLLECTIONS:
                return jsonify({"error": "Unknown library collection."}), 400
            db = _db()
            docs = db.collection(collection).order_by(
                'createdAt', direction='DESCENDING'
            ).limit(200).stream()
            out = []
            for d in docs:
                x = d.to_dict() or {}
                out.append({
                    'id': d.id,
                    'title': x.get('title') or x.get('fileName') or '(untitled)',
                    'className': x.get('className') or '',
                    'hasChapters': bool(x.get('hasChapters')),
                })
            return jsonify({"success": True, "collection": collection, "books": out}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_list_books failed')
            return jsonify({"error": "Could not list books."}), 502

    # ------------------------------------------------------------------
    # Structure — read
    # ------------------------------------------------------------------
    @app.get('/api/library/books/<collection>/<book_id>/chapters')
    def library_get_structure(collection, book_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            snap = book_ref.get()
            if not snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = snap.to_dict() or {}
            if not _can_read_book(db, book, detail):
                return jsonify({"error": "You do not have access to this book."}), 403
            chapters = book_ref.collection('chapters').order_by('order').stream()
            return jsonify({
                "success": True,
                "bookId": book_id,
                "collection": collection,
                "title": book.get('title') or book.get('fileName') or '',
                "hasChapters": bool(book.get('hasChapters')),
                "chapters": [_serialize_chapter(db, book_ref, c) for c in chapters],
            }), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_get_structure failed')
            return jsonify({"error": "Could not load book structure."}), 502

    # ------------------------------------------------------------------
    # Structure — write (admin / owning approved teacher)
    # ------------------------------------------------------------------
    @app.post('/api/library/chapters')
    def library_create_chapter():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            collection = str(body.get('collection', '')).strip()
            book_id = str(body.get('bookId', '')).strip()
            title = str(body.get('title', '')).strip()
            order = body.get('order', 0)
            if not title:
                return jsonify({"error": "Chapter title is required."}), 400
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            snap = book_ref.get()
            if not snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = snap.to_dict() or {}
            if not _can_manage_book(db, book, detail):
                return jsonify({"error": "Admin or the owning approved teacher can manage chapters."}), 403
            now = datetime.now(timezone.utc)
            chap_ref = book_ref.collection('chapters').document()
            chap_ref.set({
                'title': title, 'order': int(order) if str(order).lstrip('-').isdigit() else 0,
                'createdAt': now, 'createdBy': detail.get('uid'),
            })
            if not book.get('hasChapters'):
                book_ref.update({'hasChapters': True})
            return jsonify({"success": True, "chapterId": chap_ref.id}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_create_chapter failed')
            return jsonify({"error": "Could not create chapter."}), 502

    @app.put('/api/library/chapters/<collection>/<book_id>/<chapter_id>')
    def library_update_chapter(collection, book_id, chapter_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            book_snap = book_ref.get()
            if not book_snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = book_snap.to_dict() or {}
            if not _can_manage_book(db, book, detail):
                return jsonify({"error": "Admin or the owning approved teacher can manage chapters."}), 403
            chap_ref = book_ref.collection('chapters').document(chapter_id)
            if not chap_ref.get().exists:
                return jsonify({"error": "Chapter not found."}), 404
            updates = {}
            if 'title' in body:
                updates['title'] = str(body['title']).strip()
            if 'order' in body:
                updates['order'] = body['order']
            if updates:
                chap_ref.update(updates)
            return jsonify({"success": True}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_update_chapter failed')
            return jsonify({"error": "Could not update chapter."}), 502

    @app.delete('/api/library/chapters/<collection>/<book_id>/<chapter_id>')
    def library_delete_chapter(collection, book_id, chapter_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            book_snap = book_ref.get()
            if not book_snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = book_snap.to_dict() or {}
            if not _can_manage_book(db, book, detail):
                return jsonify({"error": "Admin or the owning approved teacher can manage chapters."}), 403
            chap_ref = book_ref.collection('chapters').document(chapter_id)
            # Cascade-delete subchapters first (Firestore doesn't do this
            # automatically for subcollections).
            for sub in chap_ref.collection('subchapters').stream():
                sub.reference.delete()
            chap_ref.delete()
            return jsonify({"success": True}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_delete_chapter failed')
            return jsonify({"error": "Could not delete chapter."}), 502

    @app.post('/api/library/subchapters')
    def library_create_subchapter():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            collection = str(body.get('collection', '')).strip()
            book_id = str(body.get('bookId', '')).strip()
            chapter_id = str(body.get('chapterId', '')).strip()
            title = str(body.get('title', '')).strip()
            if not title:
                return jsonify({"error": "Subchapter title is required."}), 400
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            book_snap = book_ref.get()
            if not book_snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = book_snap.to_dict() or {}
            if not _can_manage_book(db, book, detail):
                return jsonify({"error": "Admin or the owning approved teacher can manage chapters."}), 403
            chap_ref = book_ref.collection('chapters').document(chapter_id)
            if not chap_ref.get().exists:
                return jsonify({"error": "Chapter not found."}), 404
            now = datetime.now(timezone.utc)
            sub_ref = chap_ref.collection('subchapters').document()
            sub_ref.set({
                'title': title,
                'order': body.get('order', 0),
                'pageStart': body.get('pageStart'),
                'pageEnd': body.get('pageEnd'),
                'content': str(body.get('content', ''))[:20000],
                'learningObjective': str(body.get('learningObjective', ''))[:500],
                'createdAt': now, 'createdBy': detail.get('uid'),
            })
            return jsonify({"success": True, "subchapterId": sub_ref.id}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_create_subchapter failed')
            return jsonify({"error": "Could not create subchapter."}), 502

    @app.delete('/api/library/subchapters/<collection>/<book_id>/<chapter_id>/<subchapter_id>')
    def library_delete_subchapter(collection, book_id, chapter_id, subchapter_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            if book_ref is None:
                return jsonify({"error": "Unknown library collection."}), 400
            book_snap = book_ref.get()
            if not book_snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = book_snap.to_dict() or {}
            if not _can_manage_book(db, book, detail):
                return jsonify({"error": "Admin or the owning approved teacher can manage chapters."}), 403
            (book_ref.collection('chapters').document(chapter_id)
             .collection('subchapters').document(subchapter_id).delete())
            return jsonify({"success": True}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_delete_subchapter failed')
            return jsonify({"error": "Could not delete subchapter."}), 502

    # ------------------------------------------------------------------
    # Reading progress — students only, stored on users/{uid}.progress.books
    # ------------------------------------------------------------------
    @app.get('/api/library/progress/<collection>/<book_id>')
    def library_get_progress(collection, book_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            uid = detail['uid']
            user = db.collection('users').document(uid).get().to_dict() or {}
            books_progress = ((user.get('progress') or {}).get('books') or {})
            key = f"{collection}:{book_id}"
            entry = books_progress.get(key) or {}
            return jsonify({"success": True, "progress": entry}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_get_progress failed')
            return jsonify({"error": "Could not load reading progress."}), 502

    @app.post('/api/library/progress')
    def library_save_progress():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            collection = str(body.get('collection', '')).strip()
            book_id = str(body.get('bookId', '')).strip()
            if collection not in ALLOWED_BOOK_COLLECTIONS or not book_id:
                return jsonify({"error": "collection and bookId are required."}), 400
            db = _db()
            book_ref = _book_ref(db, collection, book_id)
            book_snap = book_ref.get()
            if not book_snap.exists:
                return jsonify({"error": "Book not found."}), 404
            book = book_snap.to_dict() or {}
            if not _can_read_book(db, book, detail):
                return jsonify({"error": "You do not have access to this book."}), 403
            uid = detail['uid']
            key = f"{collection}:{book_id}"
            now = datetime.now(timezone.utc)
            entry = {
                'chapterId': body.get('chapterId'),
                'subchapterId': body.get('subchapterId'),
                'page': body.get('page'),
                'percentComplete': max(0, min(100, float(body.get('percentComplete', 0) or 0))),
                'updatedAt': now,
            }
            db.collection('users').document(uid).set(
                {'progress': {'books': {key: entry}}}, merge=True
            )
            return jsonify({"success": True}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception('library_save_progress failed')
            return jsonify({"error": "Could not save reading progress."}), 502

    # ------------------------------------------------------------------
    # Bookmarks — INTENTIONALLY NOT IMPLEMENTED HERE.
    #
    # BMT already has a working bookmark/"save" system: a single
    # `bookmarks/{uid}` document with an `items` map, written directly
    # from the client via student.js's toggleBookmark(id, title, type,
    # url, className). Reader "bookmark this spot" calls that SAME
    # function (exposed as window.toggleBookmark) with
    # id = `${collection}:${bookId}:${subchapterId}` and
    # type = 'book_position', so it shows up in the existing "My Saved"
    # list alongside book/video saves. A parallel bookmark subcollection
    # was drafted here initially and removed after review — one BMT
    # Core, no duplicated systems (Master Agreement §2.3).
