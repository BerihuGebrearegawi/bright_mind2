"""Live class scheduling, teacher presence, and class Q&A APIs.
Server-authoritative: teachers can only manage their own classes; students can only
see classes matching their grade. Presence expires automatically after 2 minutes.
"""
from datetime import datetime, timezone, timedelta
from flask import request, jsonify


def register_live_routes(app, require_user, firebase_admin_factory, create_notification):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def teacher_ok(database, uid):
        snap = database.collection('teachers').document(uid).get()
        if not snap.exists:
            return False
        data = snap.to_dict() or {}
        return bool(data.get('approved') or data.get('status') == 'approved')

    def clean_text(value, max_len):
        return str(value or '').strip()[:max_len]

    def parse_start(value):
        raw = clean_text(value, 40).replace('Z', '+00:00')
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def serialize(snap):
        x = snap.to_dict() or {}
        for key in ('startAt', 'endAt', 'createdAt', 'updatedAt', 'presenceAt'):
            value = x.get(key)
            if hasattr(value, 'isoformat'):
                x[key] = value.isoformat()
        x['id'] = snap.id
        return x

    @app.get('/api/live/classes')
    def list_live_classes():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db()
            uid = detail['uid']
            grade = clean_text(request.args.get('grade'), 10)
            subject = clean_text(request.args.get('subject'), 80)
            class_group_id = clean_text(request.args.get('classGroupId'), 80)
            now = datetime.now(timezone.utc)
            docs = database.collection('liveClasses').stream()
            rows = []
            for snap in docs:
                x = snap.to_dict() or {}
                if x.get('status', 'scheduled') not in ('scheduled', 'live'):
                    continue
                if grade and str(x.get('grade', '')) != grade:
                    continue
                if subject and str(x.get('subject', '')).lower() != subject.lower():
                    continue
                if class_group_id and str(x.get('classGroupId', '')) != class_group_id:
                    continue
                start = x.get('startAt')
                if hasattr(start, 'timestamp') and start.timestamp() < now.timestamp() - 7200:
                    continue
                rows.append(serialize(snap))
            rows.sort(key=lambda x: x.get('startAt') or '')
            for row in rows:
                teacher_uid = row.get('teacherUid')
                p = database.collection('teacherPresence').document(str(teacher_uid)).get()
                pdata = p.to_dict() if p.exists else {}
                ts = pdata.get('updatedAt')
                online = False
                if hasattr(ts, 'timestamp'):
                    online = (now - ts).total_seconds() <= 120
                row['teacherOnline'] = online
                row['isOwner'] = teacher_uid == uid
            return jsonify({'classes': rows[:50]})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/teacher/live/classes')
    def create_live_class():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            if not teacher_ok(database, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            body = request.get_json(silent=True) or {}
            title = clean_text(body.get('title'), 160)
            grade = clean_text(body.get('grade'), 10)
            subject = clean_text(body.get('subject'), 80)
            meeting_url = clean_text(body.get('meetingUrl'), 2000)
            description = clean_text(body.get('description'), 1200)
            class_group_id = clean_text(body.get('classGroupId'), 80)
            time_zone = clean_text(body.get('timeZone') or 'Africa/Addis_Ababa', 80)
            if not title or grade not in {str(i) for i in range(3, 13)} or not subject or not meeting_url:
                return jsonify({'error': 'Title, Grade 3–12, subject and meeting URL are required.'}), 400
            try:
                start = parse_start(body.get('startAt'))
            except Exception:
                return jsonify({'error': 'Invalid startAt. Use an ISO date/time.'}), 400
            duration = max(15, min(240, int(body.get('durationMinutes', 60))))
            end = start + timedelta(minutes=duration)
            now_utc = datetime.now(timezone.utc)
            if start < now_utc - timedelta(minutes=5):
                return jsonify({'error': 'Class start time must be in the future.'}), 400

            # Prevent double-booking the same teacher.  We deliberately scan the
            # teacher's classes here instead of requiring a composite Firestore
            # index, keeping this feature deployable on the existing project.
            requested_end = end
            # A teacher cannot host overlapping classes.
            for existing in database.collection('liveClasses').where('teacherUid', '==', uid).stream():
                ex = existing.to_dict() or {}
                if ex.get('status') == 'cancelled':
                    continue
                ex_start, ex_end = ex.get('startAt'), ex.get('endAt')
                if hasattr(ex_start, 'timestamp') and hasattr(ex_end, 'timestamp'):
                    if start < ex_end and requested_end > ex_start:
                        return jsonify({'error': 'You already have another class during this time.'}), 409

            # If a section/group is supplied, students in that group cannot be
            # scheduled into two simultaneous live classes, even with different teachers.
            if class_group_id:
                for existing in database.collection('liveClasses').where('classGroupId', '==', class_group_id).stream():
                    ex = existing.to_dict() or {}
                    if ex.get('status') == 'cancelled':
                        continue
                    ex_start, ex_end = ex.get('startAt'), ex.get('endAt')
                    if hasattr(ex_start, 'timestamp') and hasattr(ex_end, 'timestamp'):
                        if start < ex_end and requested_end > ex_start:
                            return jsonify({'error': 'This class group already has another class during this time.'}), 409

            ref = database.collection('liveClasses').document()
            now = datetime.now(timezone.utc)
            ref.set({'title': title, 'grade': grade, 'subject': subject, 'meetingUrl': meeting_url,
                     'description': description, 'teacherUid': uid, 'teacherEmail': detail.get('email', ''),
                     'classGroupId': class_group_id, 'timeZone': time_zone,
                     'status': 'scheduled', 'startAt': start, 'endAt': end,
                     'createdAt': now, 'updatedAt': now})
            create_notification(database, 'all', f'📅 New {subject} live class',
                                f'{title} for Grade {grade} is scheduled. Check Live Classes for the time and link.',
                                'live_class', {'classId': ref.id, 'grade': grade})
            return jsonify({'success': True, 'class': serialize(ref.get())}), 201
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid class duration or date/time.'}), 400
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.patch('/api/teacher/live/classes/<class_id>')
    def update_live_class(class_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            ref = database.collection('liveClasses').document(class_id); snap = ref.get()
            if not snap.exists or (snap.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({'error': 'Class not found or not owned by this teacher.'}), 404
            body = request.get_json(silent=True) or {}
            patch = {}
            for field, limit in [('title',160),('subject',80),('meetingUrl',2000),('description',1200)]:
                if field in body: patch[field] = clean_text(body[field], limit)
            if 'status' in body and body['status'] in ('scheduled','live','cancelled','completed'):
                patch['status'] = body['status']
            if 'classGroupId' in body:
                patch['classGroupId'] = clean_text(body.get('classGroupId'), 80)
            if 'timeZone' in body:
                patch['timeZone'] = clean_text(body.get('timeZone') or 'Africa/Addis_Ababa', 80)
            if 'startAt' in body:
                patch['startAt'] = parse_start(body['startAt'])
                duration = max(15, min(240, int(body.get('durationMinutes', 60))))
                patch['endAt'] = patch['startAt'] + timedelta(minutes=duration)

            # Re-check conflicts whenever time, teacher, or group scheduling data changes.
            if 'startAt' in patch or 'endAt' in patch or 'classGroupId' in patch:
                current = snap.to_dict() or {}
                new_start = patch.get('startAt', current.get('startAt'))
                new_end = patch.get('endAt', current.get('endAt'))
                new_group = patch.get('classGroupId', current.get('classGroupId', ''))
                if hasattr(new_start, 'timestamp') and hasattr(new_end, 'timestamp') and patch.get('status', current.get('status')) != 'cancelled':
                    for existing in database.collection('liveClasses').where('teacherUid', '==', uid).stream():
                        if existing.id == class_id:
                            continue
                        ex = existing.to_dict() or {}
                        if ex.get('status') == 'cancelled':
                            continue
                        ex_start, ex_end = ex.get('startAt'), ex.get('endAt')
                        if hasattr(ex_start, 'timestamp') and hasattr(ex_end, 'timestamp') and new_start < ex_end and new_end > ex_start:
                            return jsonify({'error': 'You already have another class during this time.'}), 409
                    if new_group:
                        for existing in database.collection('liveClasses').where('classGroupId', '==', new_group).stream():
                            if existing.id == class_id:
                                continue
                            ex = existing.to_dict() or {}
                            if ex.get('status') == 'cancelled':
                                continue
                            ex_start, ex_end = ex.get('startAt'), ex.get('endAt')
                            if hasattr(ex_start, 'timestamp') and hasattr(ex_end, 'timestamp') and new_start < ex_end and new_end > ex_start:
                                return jsonify({'error': 'This class group already has another class during this time.'}), 409

            patch['updatedAt'] = datetime.now(timezone.utc)
            ref.update(patch)
            if patch.get('status') == 'cancelled':
                create_notification(database, 'all', '⚠️ Live class cancelled',
                                    f"{(snap.to_dict() or {}).get('title','A live class')} has been cancelled.",
                                    'live_class_cancelled', {'classId': class_id})
            return jsonify({'success': True, 'class': serialize(ref.get())})
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid date/time or duration.'}), 400
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/teacher/presence')
    def teacher_presence():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            if not teacher_ok(database, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            database.collection('teacherPresence').document(uid).set({
                'teacherUid': uid, 'online': True, 'updatedAt': datetime.now(timezone.utc)
            }, merge=True)
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/live/classes/<class_id>/enroll')
    def enroll_live_class(class_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            class_ref = database.collection('liveClasses').document(class_id)
            snap = class_ref.get()
            if not snap.exists:
                return jsonify({'error': 'Live class not found.'}), 404
            c = snap.to_dict() or {}
            if c.get('status') not in ('scheduled', 'live'):
                return jsonify({'error': 'This class is not open for enrollment.'}), 400
            grade = clean_text(c.get('grade'), 10)
            profile = database.collection('users').document(uid).get()
            pdata = profile.to_dict() if profile.exists else {}
            student_grade = str(pdata.get('class') or pdata.get('className') or '')
            if student_grade and student_grade != grade:
                return jsonify({'error': 'This class is for a different grade.'}), 403
            class_group_id = str(c.get('classGroupId') or '')
            student_group = str(pdata.get('classGroupId') or pdata.get('section') or '')
            if class_group_id and student_group and student_group != class_group_id:
                return jsonify({'error': 'This class is for a different class group.'}), 403
            ref = database.collection('liveClassEnrollments').document(f'{class_id}_{uid}')
            now = datetime.now(timezone.utc)
            ref.set({'classId': class_id, 'studentUid': uid, 'createdAt': now, 'updatedAt': now}, merge=True)
            return jsonify({'success': True, 'enrolled': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/live/classes/<class_id>/attendance')
    def mark_live_attendance(class_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            class_ref = database.collection('liveClasses').document(class_id); snap = class_ref.get()
            if not snap.exists:
                return jsonify({'error': 'Live class not found.'}), 404
            enrollment = database.collection('liveClassEnrollments').document(f'{class_id}_{uid}').get()
            if not enrollment.exists:
                return jsonify({'error': 'Enroll before joining the class.'}), 403
            now = datetime.now(timezone.utc)
            database.collection('liveClassAttendance').document(f'{class_id}_{uid}').set({
                'classId': class_id, 'studentUid': uid, 'joinedAt': now, 'lastSeenAt': now
            }, merge=True)
            return jsonify({'success': True})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.get('/api/teacher/live/classes/<class_id>/enrollments')
    def live_class_enrollments(class_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            snap = database.collection('liveClasses').document(class_id).get()
            if not snap.exists or (snap.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({'error': 'Class not found or not owned by this teacher.'}), 404
            rows = []
            for e in database.collection('liveClassEnrollments').where('classId', '==', class_id).stream():
                x=e.to_dict() or {}; x['id']=e.id; rows.append(x)
            return jsonify({'enrollments': rows, 'count': len(rows)})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.post('/api/live/classes/<class_id>/qna')
    def class_qna(class_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            database = db(); uid = detail['uid']
            class_ref = database.collection('liveClasses').document(class_id); class_snap = class_ref.get()
            if not class_snap.exists:
                return jsonify({'error': 'Live class not found.'}), 404
            c = class_snap.to_dict() or {}
            message = clean_text((request.get_json(silent=True) or {}).get('message'), 1000)
            if not message:
                return jsonify({'error': 'Question is required.'}), 400
            ref = database.collection('liveClassQna').document()
            now = datetime.now(timezone.utc)
            ref.set({'classId': class_id, 'studentUid': uid, 'message': message,
                     'status': 'open', 'createdAt': now, 'updatedAt': now})
            create_notification(database, c.get('teacherUid'), '💬 New class question',
                                f'A student asked a question in {c.get("title", "your class")}.',
                                'class_qna', {'classId': class_id, 'qnaId': ref.id})
            return jsonify({'success': True, 'qnaId': ref.id}), 201
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
