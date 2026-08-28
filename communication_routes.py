"""BMT V25 - Student/Teacher Communication & Support Center."""
from datetime import datetime, timezone
from flask import request, jsonify


def register_communication_routes(app, require_user, require_admin, firebase_admin_factory, create_notification):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def clean(value, limit=1000):
        return str(value or '').strip()[:limit]

    def approved_teacher(database, uid):
        snap = database.collection('teachers').document(uid).get()
        return snap.exists and bool((snap.to_dict() or {}).get('approved'))

    def serialize(snap):
        data = snap.to_dict() or {}
        data['id'] = snap.id
        for key in ('createdAt', 'updatedAt', 'lastMessageAt', 'closedAt'):
            value = data.get(key)
            if hasattr(value, 'isoformat'):
                data[key] = value.isoformat()
        return data

    def ticket_ref(database, ticket_id):
        return database.collection('supportTickets').document(ticket_id)

    def user_can_access_ticket(database, ticket_id, uid, admin=False):
        snap = ticket_ref(database, ticket_id).get()
        if not snap.exists:
            return None, (jsonify({'error': 'Ticket not found.'}), 404)
        ticket = snap.to_dict() or {}
        if admin or ticket.get('studentUid') == uid or ticket.get('assignedTeacherUid') == uid:
            return snap, None
        return None, (jsonify({'error': 'You are not allowed to access this ticket.'}), 403)

    @app.post('/api/support/tickets')
    def create_ticket():
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        subject = clean(body.get('subject'), 160)
        message = clean(body.get('message'), 3000)
        category = clean(body.get('category'), 40).lower() or 'general'
        teacher_uid = clean(body.get('teacherUid'), 128)
        allowed_categories = {'general', 'course', 'exam', 'payment', 'live_class', 'technical', 'academic'}
        if not subject or not message:
            return jsonify({'error': 'Subject and message are required.'}), 400
        if category not in allowed_categories:
            return jsonify({'error': 'Invalid support category.'}), 400
        try:
            database = db(); uid = detail['uid']
            if teacher_uid and not approved_teacher(database, teacher_uid):
                return jsonify({'error': 'Selected teacher is not available.'}), 400
            now = datetime.now(timezone.utc)
            ref = database.collection('supportTickets').document()
            ref.set({
                'studentUid': uid,
                'studentEmail': detail.get('email', ''),
                'assignedTeacherUid': teacher_uid or None,
                'subject': subject,
                'category': category,
                'status': 'open',
                'createdAt': now,
                'updatedAt': now,
                'lastMessageAt': now,
            })
            database.collection('supportMessages').document().set({
                'ticketId': ref.id,
                'senderUid': uid,
                'senderRole': 'student',
                'message': message,
                'createdAt': now,
            })
            if teacher_uid:
                create_notification(database, teacher_uid, '💬 New student question', subject, 'support_message', {'ticketId': ref.id})
            return jsonify({'success': True, 'ticket': serialize(ref.get())}), 201
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/support/tickets')
    def list_student_tickets():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            rows = []
            for snap in database.collection('supportTickets').where('studentUid', '==', uid).stream():
                rows.append(serialize(snap))
            rows.sort(key=lambda x: x.get('updatedAt', ''), reverse=True)
            return jsonify({'tickets': rows})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/support/tickets/<ticket_id>/messages')
    def list_ticket_messages(ticket_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            snap, error = user_can_access_ticket(database, ticket_id, uid)
            if error:
                return error
            rows = []
            for item in database.collection('supportMessages').where('ticketId', '==', ticket_id).stream():
                rows.append(serialize(item))
            rows.sort(key=lambda x: x.get('createdAt', ''))
            return jsonify({'messages': rows})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/support/tickets/<ticket_id>/messages')
    def send_ticket_message(ticket_id):
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        message = clean(body.get('message'), 3000)
        if not message:
            return jsonify({'error': 'Message is required.'}), 400
        try:
            database = db(); uid = detail['uid']
            snap, error = user_can_access_ticket(database, ticket_id, uid)
            if error:
                return error
            ticket = snap.to_dict() or {}
            if ticket.get('status') == 'closed':
                return jsonify({'error': 'This ticket is closed.'}), 400
            role = 'teacher' if ticket.get('assignedTeacherUid') == uid and approved_teacher(database, uid) else 'student'
            now = datetime.now(timezone.utc)
            database.collection('supportMessages').document().set({
                'ticketId': ticket_id,
                'senderUid': uid,
                'senderRole': role,
                'message': message,
                'createdAt': now,
            })
            ticket_ref(database, ticket_id).update({'updatedAt': now, 'lastMessageAt': now, 'status': 'open'})
            recipient = ticket.get('studentUid') if role == 'teacher' else ticket.get('assignedTeacherUid')
            if recipient:
                create_notification(database, recipient, '💬 New support message', message[:140], 'support_message', {'ticketId': ticket_id})
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/support/tickets/<ticket_id>/close')
    def close_ticket(ticket_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            snap, error = user_can_access_ticket(database, ticket_id, uid)
            if error:
                return error
            ticket = snap.to_dict() or {}
            if ticket.get('studentUid') != uid and ticket.get('assignedTeacherUid') != uid:
                return jsonify({'error': 'Not allowed.'}), 403
            now = datetime.now(timezone.utc)
            ticket_ref(database, ticket_id).update({'status': 'closed', 'closedAt': now, 'updatedAt': now})
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/teacher/support/tickets')
    def teacher_tickets():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            if not approved_teacher(database, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            rows = []
            # Assigned tickets plus unassigned academic/general tickets.
            for snap in database.collection('supportTickets').where('assignedTeacherUid', '==', uid).stream():
                rows.append(serialize(snap))
            return jsonify({'tickets': rows})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/teacher/support/tickets/<ticket_id>/claim')
    def claim_ticket(ticket_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            if not approved_teacher(database, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            ref = ticket_ref(database, ticket_id); snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Ticket not found.'}), 404
            ticket = snap.to_dict() or {}
            if ticket.get('assignedTeacherUid') and ticket.get('assignedTeacherUid') != uid:
                return jsonify({'error': 'This ticket is assigned to another teacher.'}), 409
            ref.update({'assignedTeacherUid': uid, 'updatedAt': datetime.now(timezone.utc)})
            create_notification(database, ticket.get('studentUid'), '👨‍🏫 Teacher assigned', 'A teacher is now handling your support request.', 'support_assignment', {'ticketId': ticket_id})
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/feedback')
    def submit_feedback():
        ok, detail = require_user()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        rating = body.get('rating')
        message = clean(body.get('message'), 2000)
        category = clean(body.get('category'), 50).lower() or 'general'
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = 0
        if rating < 1 or rating > 5:
            return jsonify({'error': 'Rating must be between 1 and 5.'}), 400
        if not message:
            return jsonify({'error': 'Feedback message is required.'}), 400
        try:
            database = db(); uid = detail['uid']; now = datetime.now(timezone.utc)
            ref = database.collection('feedback').document()
            ref.set({'uid': uid, 'email': detail.get('email', ''), 'rating': rating, 'category': category, 'message': message, 'status': 'new', 'createdAt': now})
            return jsonify({'success': True, 'feedbackId': ref.id}), 201
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/admin/support/tickets')
    def admin_support_tickets():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            database = db(); rows = []
            for snap in database.collection('supportTickets').stream():
                rows.append(serialize(snap))
            rows.sort(key=lambda x: x.get('updatedAt', ''), reverse=True)
            return jsonify({'tickets': rows})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/admin/feedback')
    def admin_feedback():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            database = db(); rows = []
            for snap in database.collection('feedback').stream():
                rows.append(serialize(snap))
            rows.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
            return jsonify({'feedback': rows})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
