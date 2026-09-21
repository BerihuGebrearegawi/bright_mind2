"""V31.108 — Student Mark / Grade List (read-only aggregation).

Confirmed audit finding (see ATTENDANCE_MARKS_AUDIT.md): the existing
codebase already grades three separate things -
  - Assignments  -> assignmentSubmissions (score/maxScore/percentage,
                    set by POST /api/teacher/submissions/<id>/grade)
  - Legacy Quiz  -> quizResults (score/total/percentage, set by
                    POST /api/quiz-results)
  - Exams        -> examAttempts (score/totalPoints/percentage, set by
                    the exam auto-grading path)
but nothing anywhere combines them into a single "Mark List" view for a
teacher's class or for a student's own record - a teacher currently has to
open each assignment's submission list and the exam results page
separately, and a student only sees assignment feedback and exam history
as two unrelated screens.

Everything in this file is read-only aggregation of data that already
exists. It does not grade anything, does not change how any of the three
collections above are written, and does not invent a letter-grade scale or
an academic "Term" concept - neither exists anywhere in the current data
model, so adding one here would be inventing grading policy rather than
implementing what the structure already supports. Only Assignment, Quiz,
Exam, Total and Average are produced, per the task's own "only if already
supported by the existing structure" qualifier.
"""
from flask import jsonify, request

_ALLOWED_GRADES = {str(i) for i in range(3, 13)}


def register_marklist_routes(app, require_user, firebase_admin_factory):
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

    def _avg(values):
        vals = [v for v in values if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _roster(db, grade):
        rows = []
        for snap in db.collection('users').limit(5000).stream():
            x = snap.to_dict() or {}
            role = str(x.get('role') or x.get('userType') or x.get('accountType') or '').lower()
            if role not in {'student', 'learner', ''} or x.get('isAdmin'):
                continue
            g = str(x.get('className') or x.get('class') or x.get('grade') or '').strip()
            if g != grade:
                continue
            rows.append({'uid': snap.id, 'name': x.get('displayName') or x.get('name') or x.get('fullName') or 'Student'})
        rows.sort(key=lambda r: str(r['name']).lower())
        return rows

    # ------------------------------------------------------------------
    # Teacher: mark list for a grade they teach
    # ------------------------------------------------------------------
    @app.get('/api/teacher/marklist')
    def teacher_marklist():
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

            assignment_ids = [d.id for d in db.collection('assignments')
                              .where('teacherUid', '==', uid).where('className', '==', grade).stream()]
            exam_ids = [d.id for d in db.collection('exams')
                        .where('teacherUid', '==', uid).where('className', '==', grade).stream()]
            if not assignment_ids and not exam_ids:
                return jsonify({'error': 'You do not teach this grade, or have not created any graded work for it yet.'}), 403

            per_student = {}

            def _bucket(su):
                return per_student.setdefault(su, {'assignment': [], 'exam': []})

            for aid in assignment_ids:
                for d in db.collection('assignmentSubmissions').where('assignmentId', '==', aid).stream():
                    x = d.to_dict() or {}
                    if x.get('status') != 'graded':
                        continue
                    su = x.get('studentUid')
                    if not su:
                        continue
                    _bucket(su)['assignment'].append(x.get('percentage'))

            for eid in exam_ids:
                for d in db.collection('examAttempts').where('examId', '==', eid).stream():
                    x = d.to_dict() or {}
                    if x.get('status') != 'submitted':
                        continue
                    su = x.get('userId')
                    if not su:
                        continue
                    _bucket(su)['exam'].append(x.get('percentage'))

            names = {s['uid']: s['name'] for s in _roster(db, grade)}
            rows = []
            for su, buckets in per_student.items():
                assignment_avg = _avg(buckets['assignment'])
                exam_avg = _avg(buckets['exam'])
                category_avgs = [v for v in (assignment_avg, exam_avg) if v is not None]
                rows.append({
                    'studentUid': su, 'name': names.get(su, 'Student'),
                    'assignmentAverage': assignment_avg, 'assignmentCount': len(buckets['assignment']),
                    'examAverage': exam_avg, 'examCount': len(buckets['exam']),
                    'overallAverage': _avg(category_avgs) if category_avgs else None,
                })
            rows.sort(key=lambda r: str(r['name']).lower())

            return jsonify({'grade': grade, 'students': rows}), 200
        except Exception:
            app.logger.exception('Teacher mark list failed')
            return jsonify({'error': 'Unable to build the mark list.'}), 500

    # ------------------------------------------------------------------
    # Student: own consolidated mark list across Assignment/Quiz/Exam
    # ------------------------------------------------------------------
    @app.get('/api/student/marklist')
    def student_marklist():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']

            assignments = []
            for d in db.collection('assignmentSubmissions').where('studentUid', '==', uid).stream():
                x = d.to_dict() or {}
                if x.get('status') != 'graded':
                    continue
                a = db.collection('assignments').document(str(x.get('assignmentId', ''))).get()
                title = (a.to_dict() or {}).get('title', 'Assignment') if a.exists else 'Assignment'
                assignments.append({
                    'title': title, 'score': x.get('score'), 'maxScore': x.get('maxScore'),
                    'percentage': x.get('percentage'), 'gradedAt': _iso(x.get('gradedAt')),
                })

            exams = []
            for d in db.collection('examAttempts').where('userId', '==', uid).stream():
                x = d.to_dict() or {}
                if x.get('status') != 'submitted':
                    continue
                e = db.collection('exams').document(str(x.get('examId', ''))).get()
                title = (e.to_dict() or {}).get('title', 'Exam') if e.exists else 'Exam'
                exams.append({
                    'title': title, 'score': x.get('score'), 'totalPoints': x.get('totalPoints'),
                    'percentage': x.get('percentage'), 'submittedAt': _iso(x.get('submittedAt')),
                })

            quizzes = []
            for d in db.collection('quizResults').where('studentUid', '==', uid).stream():
                x = d.to_dict() or {}
                q = db.collection('quizzes').document(str(x.get('quizId', ''))).get()
                title = (q.to_dict() or {}).get('title', 'Quiz') if q.exists else 'Quiz'
                quizzes.append({
                    'title': title, 'score': x.get('score'), 'total': x.get('total'),
                    'percentage': x.get('percentage'), 'submittedAt': _iso(x.get('submittedAt')),
                })

            assignment_avg = _avg([a['percentage'] for a in assignments])
            exam_avg = _avg([e['percentage'] for e in exams])
            quiz_avg = _avg([q['percentage'] for q in quizzes])
            category_avgs = [v for v in (assignment_avg, exam_avg, quiz_avg) if v is not None]

            return jsonify({
                'assignments': assignments, 'exams': exams, 'quizzes': quizzes,
                'summary': {
                    'assignmentAverage': assignment_avg, 'examAverage': exam_avg, 'quizAverage': quiz_avg,
                    'overallAverage': _avg(category_avgs) if category_avgs else None,
                },
            }), 200
        except Exception:
            app.logger.exception('Student mark list failed')
            return jsonify({'error': 'Unable to load your marks.'}), 500
