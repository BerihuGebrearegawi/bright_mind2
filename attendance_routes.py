"""V31.108 — Student Attendance (Regular classroom attendance only).

Confirmed gap (see ATTENDANCE_MARKS_AUDIT.md): before this file, the only
"attendance" concept anywhere in the backend was liveClassAttendance in
live_routes.py, which just stamps joinedAt/lastSeenAt for whichever student
clicks a Live Class link - there was no teacher-recorded Present/Absent/Late
concept, no per-date/per-class record, and no student-facing history. The
parent dashboard already renders an "attendanceRate" field
(studentAnalytics.attendanceRate) but nothing in the codebase ever writes
that collection, so it silently shows "-" today; this file does not touch
that pre-existing, unrelated gap (out of scope for this task).

Design constraints enforced here, matching the task's student-separation
rule:
  - Only students whose profile learningMode is 'Regular' (the existing
    default from student_profile_routes.py / app.py signup - see
    targeting_access.py's own `str(user.get("learningMode") or "Regular")`
    pattern, reused verbatim below) can have classroom attendance recorded
    against them. A Distance-mode student is skipped server-side even if a
    UID is submitted for them - this is enforced in the write path, not
    just left to the frontend to avoid showing them in a class roster.
  - Competition/Scholarship participation lives entirely in
    learning_challenge_routes.py / challenge_entry_routes.py and is never
    read or written here.
  - A teacher may only mark/view attendance for a grade they actually
    teach, using the same "grades this teacher owns" derivation already
    used by GET /api/teacher/students in teacher_routes.py (courses +
    assignments where teacherUid == this teacher). That logic is small
    enough to duplicate locally rather than importing/modifying
    teacher_routes.py.
"""
import re
from datetime import datetime, timezone, timedelta
from flask import jsonify, request

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_ALLOWED_GRADES = {str(i) for i in range(3, 13)}
_ALLOWED_STATUS = {'present', 'absent', 'late'}


def register_attendance_routes(app, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _teacher(db, uid):
        snap = db.collection("teachers").document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if not data.get("approved") and data.get("status") != "approved":
            return None
        return data

    def _teacher_grades(db, uid):
        """Same derivation as GET /api/teacher/students in teacher_routes.py:
        a teacher 'owns' whichever grades appear on their own courses or
        assignments. Duplicated locally on purpose - see module docstring."""
        grades = set()
        for d in db.collection('courses').where('teacherUid', '==', uid).limit(200).stream():
            x = d.to_dict() or {}; g = str(x.get('className') or x.get('grade') or '').strip()
            if g:
                grades.add(g)
        for d in db.collection('assignments').where('teacherUid', '==', uid).limit(200).stream():
            x = d.to_dict() or {}; g = str(x.get('className') or x.get('grade') or '').strip()
            if g:
                grades.add(g)
        return grades

    def _valid_date(s):
        s = str(s or '').strip()
        if not _DATE_RE.match(s):
            return None
        try:
            datetime.strptime(s, '%Y-%m-%d')
        except ValueError:
            return None
        return s

    def _today_str():
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')

    def _learning_mode(profile):
        """Same default rule used everywhere else in the codebase: a
        missing/blank learningMode means 'Regular', never 'Distance'."""
        return str((profile or {}).get('learningMode') or 'Regular').strip() or 'Regular'

    def _regular_roster(db, grade):
        """Students in this grade who are eligible for classroom attendance,
        i.e. role student/learner (not admin) and learningMode == 'Regular'.
        Mirrors the role-detection already used by GET /api/teacher/students."""
        rows = []
        for snap in db.collection('users').limit(5000).stream():
            x = snap.to_dict() or {}
            role = str(x.get('role') or x.get('userType') or x.get('accountType') or '').lower()
            if role not in {'student', 'learner', ''} or x.get('isAdmin'):
                continue
            g = str(x.get('className') or x.get('class') or x.get('grade') or '').strip()
            if g != grade:
                continue
            if _learning_mode(x) != 'Regular':
                continue
            rows.append({
                'uid': snap.id,
                'name': x.get('displayName') or x.get('name') or x.get('fullName') or 'Student',
            })
        rows.sort(key=lambda r: str(r['name']).lower())
        return rows

    # ------------------------------------------------------------------
    # Teacher: mark attendance for one class/date
    # ------------------------------------------------------------------
    @app.post('/api/teacher/attendance')
    def teacher_mark_attendance():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403

            body = request.get_json(silent=True) or {}
            grade = str(body.get('className') or body.get('grade') or '').strip()
            subject = str(body.get('subject') or '').strip()[:80]
            date_str = _valid_date(body.get('date'))
            records = body.get('records')

            if grade not in _ALLOWED_GRADES:
                return jsonify({'error': 'A valid grade is required.'}), 400
            if not date_str:
                return jsonify({'error': "date must be 'YYYY-MM-DD'."}), 400
            if date_str > _today_str():
                return jsonify({'error': 'Attendance cannot be recorded for a future date.'}), 400
            if not isinstance(records, list) or not records or len(records) > 200:
                return jsonify({'error': 'records must be a non-empty list (max 200).'}), 400

            owned_grades = _teacher_grades(db, uid)
            if grade not in owned_grades:
                return jsonify({'error': 'You do not teach this grade.'}), 403

            marked, skipped = [], []
            from firebase_admin import firestore
            batch = db.batch()
            pending = 0
            now = datetime.now(timezone.utc)

            for i, rec in enumerate(records):
                if not isinstance(rec, dict):
                    skipped.append({'studentUid': None, 'reason': f'Record {i+1} is invalid.'})
                    continue
                student_uid = str(rec.get('studentUid') or '').strip()
                status = str(rec.get('status') or '').strip().lower()
                if not student_uid or status not in _ALLOWED_STATUS:
                    skipped.append({'studentUid': student_uid or None, 'reason': 'Missing studentUid or invalid status.'})
                    continue
                profile_snap = db.collection('users').document(student_uid).get()
                if not profile_snap.exists:
                    skipped.append({'studentUid': student_uid, 'reason': 'Student profile not found.'})
                    continue
                profile = profile_snap.to_dict() or {}
                role = str(profile.get('role') or profile.get('userType') or profile.get('accountType') or '').lower()
                if role not in {'student', 'learner', ''} or profile.get('isAdmin'):
                    skipped.append({'studentUid': student_uid, 'reason': 'Not a student account.'})
                    continue
                student_grade = str(profile.get('className') or profile.get('class') or profile.get('grade') or '').strip()
                if student_grade != grade:
                    skipped.append({'studentUid': student_uid, 'reason': 'Student is not in this grade.'})
                    continue
                if _learning_mode(profile) != 'Regular':
                    # This is the explicit separation rule: Distance students
                    # never get a classroom attendance record, even if a
                    # teacher's client mistakenly submits their UID.
                    skipped.append({'studentUid': student_uid, 'reason': 'Distance-mode students are not tracked by classroom attendance.'})
                    continue

                doc_id = f"{grade}_{subject or 'general'}_{date_str}_{student_uid}"
                ref = db.collection('attendanceRecords').document(doc_id)
                batch.set(ref, {
                    'studentUid': student_uid, 'grade': grade, 'subject': subject,
                    'date': date_str, 'status': status,
                    'teacherUid': uid, 'updatedAt': now,
                }, merge=True)
                pending += 1
                marked.append({'studentUid': student_uid, 'status': status})
                if pending >= 450:
                    batch.commit(); batch = db.batch(); pending = 0

            if pending:
                batch.commit()

            return jsonify({
                'success': True, 'grade': grade, 'subject': subject, 'date': date_str,
                'markedCount': len(marked), 'marked': marked, 'skipped': skipped,
            }), 200
        except Exception:
            app.logger.exception('Teacher attendance marking failed')
            return jsonify({'error': 'Unable to record attendance.'}), 500

    # ------------------------------------------------------------------
    # Teacher: view one date's roster + statuses for a grade
    # ------------------------------------------------------------------
    @app.get('/api/teacher/attendance')
    def teacher_view_attendance():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403

            grade = str(request.args.get('className') or request.args.get('grade') or '').strip()
            subject = str(request.args.get('subject') or '').strip()[:80]
            date_str = _valid_date(request.args.get('date')) or _today_str()

            if grade not in _ALLOWED_GRADES:
                return jsonify({'error': 'A valid grade is required.'}), 400
            owned_grades = _teacher_grades(db, uid)
            if grade not in owned_grades:
                return jsonify({'error': 'You do not teach this grade.'}), 403

            roster = _regular_roster(db, grade)
            query = db.collection('attendanceRecords').where('grade', '==', grade).where('date', '==', date_str)
            statuses = {}
            for d in query.stream():
                x = d.to_dict() or {}
                if subject and str(x.get('subject') or '') != subject:
                    continue
                statuses[x.get('studentUid')] = x.get('status')

            rows = [{'uid': s['uid'], 'name': s['name'], 'status': statuses.get(s['uid'], 'unmarked')} for s in roster]
            counts = {'present': 0, 'absent': 0, 'late': 0, 'unmarked': 0}
            for r in rows:
                counts[r['status']] = counts.get(r['status'], 0) + 1

            return jsonify({
                'grade': grade, 'subject': subject, 'date': date_str,
                'students': rows, 'counts': counts,
            }), 200
        except Exception:
            app.logger.exception('Teacher attendance view failed')
            return jsonify({'error': 'Unable to load attendance.'}), 500

    # ------------------------------------------------------------------
    # Teacher: aggregated report over a date range
    # ------------------------------------------------------------------
    @app.get('/api/teacher/attendance/report')
    def teacher_attendance_report():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403

            grade = str(request.args.get('className') or request.args.get('grade') or '').strip()
            if grade not in _ALLOWED_GRADES:
                return jsonify({'error': 'A valid grade is required.'}), 400
            owned_grades = _teacher_grades(db, uid)
            if grade not in owned_grades:
                return jsonify({'error': 'You do not teach this grade.'}), 403

            today = _today_str()
            from_str = _valid_date(request.args.get('from')) or (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).strftime('%Y-%m-%d')
            to_str = _valid_date(request.args.get('to')) or today
            if from_str > to_str:
                from_str, to_str = to_str, from_str

            query = (db.collection('attendanceRecords')
                     .where('grade', '==', grade)
                     .where('date', '>=', from_str)
                     .where('date', '<=', to_str))
            per_student = {}
            for d in query.stream():
                x = d.to_dict() or {}
                su = x.get('studentUid')
                if not su:
                    continue
                item = per_student.setdefault(su, {'present': 0, 'absent': 0, 'late': 0})
                status = x.get('status')
                if status in item:
                    item[status] += 1

            names = {s['uid']: s['name'] for s in _regular_roster(db, grade)}
            rows = []
            for su, item in per_student.items():
                total = item['present'] + item['absent'] + item['late']
                rate = round(item['present'] / total * 100, 1) if total else None
                rows.append({
                    'studentUid': su, 'name': names.get(su, 'Student'),
                    'present': item['present'], 'absent': item['absent'], 'late': item['late'],
                    'attendanceRate': rate,
                })
            rows.sort(key=lambda r: str(r['name']).lower())

            return jsonify({
                'grade': grade, 'from': from_str, 'to': to_str, 'students': rows,
            }), 200
        except Exception:
            app.logger.exception('Teacher attendance report failed')
            return jsonify({'error': 'Unable to build attendance report.'}), 500

    # ------------------------------------------------------------------
    # Student: own attendance history
    # ------------------------------------------------------------------
    @app.get('/api/student/attendance')
    def student_attendance_history():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            profile_snap = db.collection('users').document(uid).get()
            profile = profile_snap.to_dict() or {} if profile_snap.exists else {}
            mode = _learning_mode(profile)

            if mode != 'Regular':
                # Distance students are never given a classroom attendance
                # system - point the frontend at course/lesson progress
                # instead, which already exists via /api/student/courses.
                return jsonify({
                    'mode': mode, 'records': [], 'summary': None,
                    'note': 'Classroom attendance does not apply to Distance-mode students; see course/lesson progress instead.',
                }), 200

            from_str = _valid_date(request.args.get('from'))
            to_str = _valid_date(request.args.get('to'))
            query = db.collection('attendanceRecords').where('studentUid', '==', uid)
            rows = []
            for d in query.stream():
                x = d.to_dict() or {}
                date_str = x.get('date')
                if from_str and (not date_str or date_str < from_str):
                    continue
                if to_str and (not date_str or date_str > to_str):
                    continue
                rows.append({
                    'date': date_str, 'status': x.get('status'),
                    'grade': x.get('grade'), 'subject': x.get('subject') or '',
                    'updatedAt': _iso(x.get('updatedAt')),
                })
            rows.sort(key=lambda r: r.get('date') or '', reverse=True)

            present = sum(1 for r in rows if r['status'] == 'present')
            absent = sum(1 for r in rows if r['status'] == 'absent')
            late = sum(1 for r in rows if r['status'] == 'late')
            total = present + absent + late
            summary = {
                'present': present, 'absent': absent, 'late': late,
                'attendanceRate': round(present / total * 100, 1) if total else None,
            }

            return jsonify({'mode': mode, 'records': rows, 'summary': summary}), 200
        except Exception:
            app.logger.exception('Student attendance history failed')
            return jsonify({'error': 'Unable to load your attendance.'}), 500
