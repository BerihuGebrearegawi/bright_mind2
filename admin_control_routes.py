"""V26 - Admin Control Center APIs.

All reads are server-authoritative and restricted to admins. The endpoint is
intentionally aggregation-oriented so the admin dashboard does not need to
read every platform collection directly from the browser.
"""
from datetime import datetime, timezone
from flask import request, jsonify


def register_admin_control_routes(app, require_admin, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else None

    def count(database, collection_name, limit=5000):
        return sum(1 for _ in database.collection(collection_name).limit(limit).stream())

    def recent(database, collection_name, limit=8):
        rows = []
        for snap in database.collection(collection_name).limit(limit).stream():
            data = snap.to_dict() or {}
            rows.append({
                "id": snap.id,
                "collection": collection_name,
                "title": data.get("title") or data.get("subject") or data.get("name") or data.get("studentName") or "Record",
                "status": data.get("status"),
                "updatedAt": iso(data.get("updatedAt") or data.get("createdAt") or data.get("timestamp")),
            })
        rows.sort(key=lambda x: x.get("updatedAt") or "", reverse=True)
        return rows

    @app.get('/api/admin/control-center')
    def admin_control_center():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            database = db()
            collections = {
                "students": "users",
                "teachers": "teachers",
                "teacherRequests": "teacherRequests",
                "courses": "courses",
                "lessons": "lessons",
                "exams": "exams",
                "examArchives": "examArchives",
                "liveClasses": "liveClasses",
                "supportTickets": "supportTickets",
                "payments": "payments",
                "entitlements": "entitlements",
                "notifications": "notifications",
                "aiUsage": "aiUsage",
            }
            counts = {key: count(database, collection) for key, collection in collections.items()}

            payment_rows = list(database.collection('payments').limit(5000).stream())
            payment_data = [x.to_dict() or {} for x in payment_rows]
            payments = {
                "total": len(payment_data),
                "pending": sum(1 for x in payment_data if x.get("status") in {"Pending", "Submitted"}),
                "approved": sum(1 for x in payment_data if x.get("status") in {"Approved", "Verified"}),
                "rejected": sum(1 for x in payment_data if x.get("status") == "Rejected"),
            }

            ticket_rows = list(database.collection('supportTickets').limit(5000).stream())
            ticket_data = [x.to_dict() or {} for x in ticket_rows]
            support = {
                "total": len(ticket_data),
                "open": sum(1 for x in ticket_data if x.get("status") == "open"),
                "closed": sum(1 for x in ticket_data if x.get("status") == "closed"),
                "unassigned": sum(1 for x in ticket_data if not x.get("assignedTeacherUid")),
            }

            request_rows = list(database.collection('teacherRequests').limit(5000).stream())
            request_data = [x.to_dict() or {} for x in request_rows]
            teacher_requests = {
                "total": len(request_data),
                "pending": sum(1 for x in request_data if x.get("status") == "pending"),
                "approved": sum(1 for x in request_data if x.get("status") in {"approved", "accepted"}),
                "rejected": sum(1 for x in request_data if x.get("status") == "rejected"),
            }

            activities = []
            for collection_name in ('payments', 'teacherRequests', 'supportTickets', 'liveClasses', 'courses', 'exams', 'announcements'):
                try:
                    activities.extend(recent(database, collection_name, 8))
                except Exception:
                    app.logger.exception('Admin activity read failed for %s', collection_name)
            activities.sort(key=lambda x: x.get("updatedAt") or "", reverse=True)

            audit = []
            for snap in database.collection('adminAuditLogs').limit(50).stream():
                x = snap.to_dict() or {}
                audit.append({
                    "id": snap.id,
                    "action": x.get("action", "admin_action"),
                    "target": x.get("target", ""),
                    "adminUid": x.get("adminUid", ""),
                    "adminEmail": x.get("adminEmail", ""),
                    "createdAt": iso(x.get("createdAt")),
                })
            audit.sort(key=lambda x: x.get("createdAt") or "", reverse=True)

            return jsonify({
                "success": True,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "counts": counts,
                "payments": payments,
                "support": support,
                "teacherRequests": teacher_requests,
                "activity": activities[:20],
                "audit": audit[:20],
            })
        except Exception as exc:
            app.logger.exception('Admin control center failed')
            if isinstance(exc, RuntimeError) and 'credentials' in str(exc).lower():
                return jsonify({"error": "Admin control center is temporarily unavailable because Firebase server credentials are not configured."}), 503
            return jsonify({"error": "Could not load admin control center."}), 500

    @app.get('/api/admin/analytics/overview')
    def admin_analytics_overview():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            database = db()
            now = datetime.now(timezone.utc)
            users = list(database.collection('users').limit(5000).stream())
            students = []
            teachers = []
            for snap in users:
                x = snap.to_dict() or {}
                role = str(x.get('role') or x.get('userType') or '').lower()
                if role == 'teacher':
                    teachers.append(x)
                elif role in {'student', 'learner', ''}:
                    students.append(x)

            def dt_value(x):
                for key in ('lastLoginAt', 'lastSeenAt', 'updatedAt', 'createdAt'):
                    value = x.get(key)
                    if hasattr(value, 'timestamp'):
                        return value
                    if isinstance(value, str):
                        try:
                            return datetime.fromisoformat(value.replace('Z', '+00:00'))
                        except Exception:
                            pass
                return None

            def active(rows, days):
                cutoff = now.timestamp() - days * 86400
                return sum(1 for x in rows if (v := dt_value(x)) and v.timestamp() >= cutoff)

            progress_rows = []
            for snap in users:
                x = snap.to_dict() or {}
                progress = x.get('progress') or {}
                videos = progress.get('videos') or {}
                quizzes = progress.get('quizzes') or {}
                watched = sum(1 for v in videos.values() if isinstance(v, dict) and (v.get('watched') or float(v.get('progress', 0) or 0) >= 95))
                submitted_quizzes = sum(1 for v in quizzes.values() if isinstance(v, dict) and (v.get('submitted') or v.get('score') is not None))
                progress_rows.append((watched, submitted_quizzes))

            content = {}
            for key, collection_name in (
                ('courses', 'courses'), ('lessons', 'lessons'), ('quizzes', 'quizzes'),
                ('examArchives', 'examArchives'), ('liveClasses', 'liveClasses'),
            ):
                try:
                    content[key] = count(database, collection_name)
                except Exception:
                    content[key] = 0

            provider_counts = {'cloudinary': 0, 'youtube': 0, 'google_drive': 0, 'other': 0}
            for collection_name in ('videos', 'videoLibrary', 'media', 'books', 'bookLibrary', 'examArchives'):
                try:
                    for snap in database.collection(collection_name).limit(5000).stream():
                        x = snap.to_dict() or {}
                        provider = str(x.get('storageProvider') or x.get('provider') or x.get('source') or '').lower()
                        if 'youtube' in provider:
                            provider_counts['youtube'] += 1
                        elif 'cloudinary' in provider:
                            provider_counts['cloudinary'] += 1
                        elif 'drive' in provider:
                            provider_counts['google_drive'] += 1
                        elif provider:
                            provider_counts['other'] += 1
                except Exception:
                    continue

            return jsonify({
                'success': True,
                'generatedAt': now.isoformat(),
                'users': {
                    'students': len(students), 'teachers': len(teachers),
                    'activeStudents7d': active(students, 7),
                    'activeStudents30d': active(students, 30),
                    'activeTeachers30d': active(teachers, 30),
                },
                'learning': {
                    'studentsWithProgress': sum(1 for w, q in progress_rows if w or q),
                    'videosWatched': sum(w for w, _ in progress_rows),
                    'quizSubmissions': sum(q for _, q in progress_rows),
                },
                'content': content,
                'storageProviders': provider_counts,
            })
        except Exception:
            app.logger.exception('Admin analytics overview failed')
            return jsonify({'error': 'Unable to load platform analytics.'}), 500

    @app.post('/api/admin/audit')
    def admin_audit():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            action = str(body.get('action', '')).strip()[:100]
            target = str(body.get('target', '')).strip()[:200]
            if not action:
                return jsonify({"error": "action is required."}), 400
            database = db()
            ref = database.collection('adminAuditLogs').document()
            ref.set({
                "action": action,
                "target": target,
                "adminUid": detail.get('uid', ''),
                "adminEmail": detail.get('email', ''),
                "createdAt": datetime.now(timezone.utc),
            })
            return jsonify({"success": True, "id": ref.id}), 201
        except Exception as exc:
            if isinstance(exc, RuntimeError) and 'credentials' in str(exc).lower():
                return jsonify({"error": "Admin control center is temporarily unavailable because Firebase server credentials are not configured."}), 503
            return jsonify({"error": "Could not load admin control center."}), 500
