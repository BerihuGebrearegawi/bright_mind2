"""Notification preferences and secure FCM token registration for BMT V24."""
from datetime import datetime, timezone
from flask import request, jsonify
import hashlib


def register_notification_routes(app, require_user, require_admin, firebase_admin_factory, create_notification):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def clean(value, n):
        return str(value or '').strip()[:n]

    @app.get('/api/notification-preferences')
    def get_preferences():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            snap = database.collection('notificationPreferences').document(uid).get()
            defaults = {
                'inApp': True,
                'push': True,
                'payments': True,
                'liveClasses': True,
                'exams': True,
                'announcements': True,
                'messages': True,
            }
            if snap.exists:
                defaults.update(snap.to_dict() or {})
            return jsonify(defaults)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.patch('/api/notification-preferences')
    def update_preferences():
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        allowed = {'inApp','push','payments','liveClasses','exams','announcements','messages'}
        updates = {k: bool(body[k]) for k in allowed if k in body}
        if not updates:
            return jsonify({'error': 'No valid preferences supplied.'}), 400
        try:
            from firebase_admin import firestore
            database = db(); uid = detail['uid']
            updates['updatedAt'] = firestore.SERVER_TIMESTAMP
            database.collection('notificationPreferences').document(uid).set(updates, merge=True)
            return jsonify({'success': True, 'preferences': {k:v for k,v in updates.items() if k != 'updatedAt'}})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/notifications/register-device')
    def register_device():
        """Store FCM token server-side; clients never write fcmTokens directly."""
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        token = clean(body.get('token'), 4096)
        if not token:
            return jsonify({'error': 'FCM token is required.'}), 400
        if len(token) < 20:
            return jsonify({'error': 'Invalid FCM token.'}), 400
        try:
            from firebase_admin import firestore
            database = db(); uid = detail['uid']
            token_id = hashlib.sha256(token.encode('utf-8')).hexdigest()
            database.collection('fcmTokens').document(token_id).set({
                'uid': uid,
                'token': token,
                'platform': clean(body.get('platform'), 30) or 'web',
                'updatedAt': firestore.SERVER_TIMESTAMP,
            }, merge=True)
            return jsonify({'success': True, 'tokenId': token_id})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.delete('/api/notifications/register-device')
    def unregister_device():
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        token = clean(body.get('token'), 4096)
        if not token:
            return jsonify({'error': 'FCM token is required.'}), 400
        try:
            database = db(); uid = detail['uid']
            token_id = hashlib.sha256(token.encode('utf-8')).hexdigest()
            ref = database.collection('fcmTokens').document(token_id)
            snap = ref.get()
            if snap.exists and (snap.to_dict() or {}).get('uid') == uid:
                ref.delete()
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/admin/announcements/targeted')
    def targeted_announcement():
        """Admin announcement to all students of a grade or a single user."""
        ok, detail = require_admin()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        title = clean(body.get('title'), 120)
        message = clean(body.get('message'), 600)
        grade = clean(body.get('grade'), 20)
        target_uid = clean(body.get('targetUid'), 128)
        if not title or not message:
            return jsonify({'error': 'Title and message are required.'}), 400
        if not grade and not target_uid:
            return jsonify({'error': 'Provide targetUid or grade.'}), 400
        try:
            database = db()
            created = 0
            if target_uid:
                create_notification(database, target_uid, title, message, 'announcement', {'announcement':'true','actionUrl': clean(body.get('actionUrl'),300)})
                created = 1
            else:
                # Student profiles are read by the server only. Exact field name supports the current BMT model.
                # BMT student profiles have existed with className, grade, or class
                # across older releases. Match all supported fields and de-duplicate UIDs.
                matched = {}
                for field in ('grade', 'className', 'class'):
                    for snap in database.collection('users').where(field, '==', grade).stream():
                        matched[snap.id] = snap
                for uid, snap in matched.items():
                    create_notification(database, uid, title, message, 'announcement', {'announcement':'true','grade':grade,'actionUrl': clean(body.get('actionUrl'),300)})
                    created += 1
            return jsonify({'success': True, 'created': created}), 201
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
