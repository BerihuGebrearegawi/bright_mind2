"""Live class scheduling, teacher presence, and class Q&A APIs.
Server-authoritative: teachers can only manage their own classes; students can only
see classes matching their grade. Presence expires automatically after 2 minutes.
"""
from datetime import datetime, timezone, timedelta
from flask import request, jsonify

from targeting_access import content_visible_or_untargeted


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

    def teacher_data_or_none(database, uid):
        """Same shape/contract as teacher_routes.py's _teacher() - returns
        the teacher doc when approved, else None. Added alongside teacher_ok
        (left untouched, still used elsewhere in this file) rather than
        changing its return type, so this is additive only."""
        snap = database.collection('teachers').document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if not (data.get('approved') or data.get('status') == 'approved'):
            return None
        return data

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
            # V31.108 Live Learning Integration: a live class can now be
            # optionally restricted to a Learning Mode/Audience, using the
            # same content_visible_or_untargeted() rule already used for the
            # Digital Library/Videos - a class with no explicit targeting
            # stays visible to everyone (Regular and Distance alike), exactly
            # as it was before this change. Fetched once per request, not
            # per class.
            profile_snap = database.collection('users').document(uid).get()
            student_profile = profile_snap.to_dict() or {} if profile_snap.exists else {}
            try:
                ent_snap = database.collection('entitlements').document(uid).get()
                entitlement = ent_snap.to_dict() or {} if ent_snap.exists else {}
            except Exception:
                entitlement = {}
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
                if not content_visible_or_untargeted(student_profile, x, entitlement):
                    continue
                start = x.get('startAt')
                if hasattr(start, 'timestamp') and start.timestamp() < now.timestamp() - 7200:
                    continue
                row = serialize(snap)
                # Never expose the meeting URL in the public class listing.
                # It is returned only by the enrollment/authorized join path.
                row.pop('meetingUrl', None)
                row.pop('meeting_url', None)
                rows.append(row)
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
            teacher_data = teacher_data_or_none(database, uid)
            if not teacher_data:
                return jsonify({'error': 'Approved teacher access required.'}), 403
            body = request.get_json(silent=True) or {}
            title = clean_text(body.get('title'), 160)
            grade = clean_text(body.get('grade'), 10)
            subject = clean_text(body.get('subject'), 80)
            meeting_url = clean_text(body.get('meetingUrl'), 2000)
            description = clean_text(body.get('description'), 1200)
            class_group_id = clean_text(body.get('classGroupId'), 80)
            time_zone = clean_text(body.get('timeZone') or 'Africa/Addis_Ababa', 80)
            # V31.108 Live Learning Integration: optional Learning Mode /
            # Target Audience, same allowed values and validation used
            # everywhere else this model appears (books, videos, courses,
            # challenges, materials). Optional Course/Lesson link so a live
            # class can be connected to the existing Course->Lesson model
            # where a teacher chooses to (Unit does not exist anywhere in
            # this codebase, so there is nothing to link at that level).
            allowed_modes = {"Regular", "Distance"}
            allowed_audiences = {"Free Regular", "Paid Regular", "Distance", "Scholarship", "Competition"}
            raw_modes = body.get('learningModes')
            raw_audiences = body.get('audiences')
            raw_modes = [str(x).strip() for x in raw_modes] if isinstance(raw_modes, list) else []
            raw_audiences = [str(x).strip() for x in raw_audiences] if isinstance(raw_audiences, list) else []
            learning_modes = list(dict.fromkeys(x for x in raw_modes if x in allowed_modes))
            audiences = list(dict.fromkeys(x for x in raw_audiences if x in allowed_audiences))
            course_id = clean_text(body.get('courseId'), 150)
            lesson_id = clean_text(body.get('lessonId'), 150)
            if course_id:
                course_snap = database.collection('courses').document(course_id).get()
                if not course_snap.exists or (course_snap.to_dict() or {}).get('teacherUid') != uid:
                    return jsonify({'error': 'Course not found or not owned by this teacher.'}), 403
            if lesson_id:
                lesson_snap = database.collection('lessons').document(lesson_id).get()
                if not lesson_snap.exists or (lesson_snap.to_dict() or {}).get('teacherUid') != uid:
                    return jsonify({'error': 'Lesson not found or not owned by this teacher.'}), 403
            if not title or grade not in {str(i) for i in range(3, 13)} or not subject or not meeting_url:
                return jsonify({'error': 'Title, Grade 3–12, subject and meeting URL are required.'}), 400
            # V31.108 Competition Center Admin Completion: same
            # assigned-grade/subject enforcement as /api/teacher/courses
            # (teacher_routes.py) - absent field on the teacher doc means
            # unrestricted (pre-dates the assignment feature), an explicit
            # empty list means admin revoked new assignments on purpose.
            # Subject is already mandatory for a live class (unlike course
            # above), so it is always checked when assigned_subjects is set.
            assigned_classes = teacher_data.get('classes')
            if assigned_classes is not None and grade not in set(assigned_classes):
                return jsonify({'error': 'You are not assigned to teach this grade. Ask an admin to update your subject/grade assignment.'}), 403
            assigned_subjects = teacher_data.get('subjects')
            if assigned_subjects is not None and subject not in set(assigned_subjects):
                return jsonify({'error': 'You are not assigned to teach this subject. Ask an admin to update your subject/grade assignment.'}), 403
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
            doc = {'title': title, 'grade': grade, 'subject': subject, 'meetingUrl': meeting_url,
                   'description': description, 'teacherUid': uid, 'teacherEmail': detail.get('email', ''),
                   'classGroupId': class_group_id, 'timeZone': time_zone,
                   'status': 'scheduled', 'startAt': start, 'endAt': end,
                   'createdAt': now, 'updatedAt': now}
            if learning_modes: doc['learningModes'] = learning_modes
            if audiences: doc['audiences'] = audiences
            if course_id: doc['courseId'] = course_id
            if lesson_id: doc['lessonId'] = lesson_id
            ref.set(doc)
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
            if 'subject' in patch:
                # V31.108 Competition Center Admin Completion: closes the
                # same enforcement gap on the one field a teacher could
                # otherwise use to bypass the create-time subject check
                # above - grade itself is not patchable here, so no
                # equivalent grade re-check is needed.
                teacher_data = teacher_data_or_none(database, uid)
                assigned_subjects = (teacher_data or {}).get('subjects')
                if assigned_subjects is not None and patch['subject'] and patch['subject'] not in set(assigned_subjects):
                    return jsonify({'error': 'You are not assigned to teach this subject. Ask an admin to update your subject/grade assignment.'}), 403
            if 'status' in body and body['status'] in ('scheduled','live','cancelled','completed'):
                patch['status'] = body['status']
            if 'classGroupId' in body:
                patch['classGroupId'] = clean_text(body.get('classGroupId'), 80)
            if 'timeZone' in body:
                patch['timeZone'] = clean_text(body.get('timeZone') or 'Africa/Addis_Ababa', 80)
            if 'learningModes' in body or 'audiences' in body:
                allowed_modes = {"Regular", "Distance"}
                allowed_audiences = {"Free Regular", "Paid Regular", "Distance", "Scholarship", "Competition"}
                if 'learningModes' in body:
                    raw = body.get('learningModes')
                    raw = [str(x).strip() for x in raw] if isinstance(raw, list) else []
                    patch['learningModes'] = list(dict.fromkeys(x for x in raw if x in allowed_modes))
                if 'audiences' in body:
                    raw = body.get('audiences')
                    raw = [str(x).strip() for x in raw] if isinstance(raw, list) else []
                    patch['audiences'] = list(dict.fromkeys(x for x in raw if x in allowed_audiences))
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
            student_grade = str(pdata.get('class') or pdata.get('className') or pdata.get('grade') or '')
            if student_grade and student_grade != grade:
                return jsonify({'error': 'This class is for a different grade.'}), 403
            # V31.108 Live Learning Integration: re-checked here, not just in
            # the GET /api/live/classes listing - a student who already had
            # a class id (a shared link, a cached response) could otherwise
            # enroll in a class not targeted to their Learning Mode/Audience
            # even if the listing had hidden it from them.
            try:
                ent_snap = database.collection('entitlements').document(uid).get()
                entitlement = ent_snap.to_dict() or {} if ent_snap.exists else {}
            except Exception:
                entitlement = {}
            if not content_visible_or_untargeted(pdata, c, entitlement):
                return jsonify({'error': 'This class is not assigned to your learning mode or audience.'}), 403
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
            c = snap.to_dict() or {}
            enrollment = database.collection('liveClassEnrollments').document(f'{class_id}_{uid}').get()
            if not enrollment.exists:
                return jsonify({'error': 'Enroll before joining the class.'}), 403
            # V31.108 Live Learning Integration: same targeting re-check as
            # enroll - a class's targeting could change after a student
            # already enrolled, so this is the actual "join" gate, not just
            # a formality.
            profile = database.collection('users').document(uid).get()
            pdata = profile.to_dict() if profile.exists else {}
            try:
                ent_snap = database.collection('entitlements').document(uid).get()
                entitlement = ent_snap.to_dict() or {} if ent_snap.exists else {}
            except Exception:
                entitlement = {}
            if not content_visible_or_untargeted(pdata, c, entitlement):
                return jsonify({'error': 'This class is not assigned to your learning mode or audience.'}), 403
            meeting_url = str(c.get('meetingUrl') or '').strip()
            if not meeting_url:
                return jsonify({'error': 'This live class does not have a meeting link yet.'}), 409
            now = datetime.now(timezone.utc)
            database.collection('liveClassAttendance').document(f'{class_id}_{uid}').set({
                'classId': class_id, 'studentUid': uid, 'joinedAt': now, 'lastSeenAt': now
            }, merge=True)
            # Return the meeting URL only after the authenticated student has
            # passed enrollment, grade, learning-mode/audience checks. The
            # public class-list endpoint deliberately strips this field.
            return jsonify({'success': True, 'meetingUrl': meeting_url})
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
