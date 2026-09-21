"""Server-authoritative Teacher Portal APIs.

Teacher writes go through Flask/Admin SDK so a client cannot impersonate an
approved teacher or write an arbitrary teacherUid. Firestore rules remain a
second defensive layer for direct client access.
"""
from datetime import datetime, timezone
from flask import request, jsonify

from targeting_access import content_visible_or_untargeted
import lesson_blocks as _lb

# V31.108 Competition Center Admin Completion pass added
# /api/admin/teachers/<uid>/assignment (admin sets a teacher's authorized
# subjects/classes) and creation-time enforcement of it; this is the same
# allow-list /api/teacher/apply already validated applications against, now
# shared so both places can never drift apart.
ALLOWED_TEACHER_SUBJECTS = {
    'Mathematics', 'Physics', 'Chemistry', 'Biology', 'English',
    'Amharic', 'History', 'Geography', 'Economics'
}
ALLOWED_TEACHER_CLASSES = {str(x) for x in range(3, 13)}


def register_teacher_routes(app, require_user, require_admin, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def _entitlement(db, uid):
        """Same best-effort entitlement lookup already used everywhere else
        content_target_allowed()/content_visible_or_untargeted() is called
        (learning_challenge_routes.py, student_course_routes.py) - missing
        or unreadable entitlement never blocks the request, it just means
        the student is treated as non-premium."""
        try:
            snap = db.collection('entitlements').document(uid).get()
            return snap.to_dict() or {} if snap.exists else {}
        except Exception:
            return {}

    def _teacher(db, uid):
        snap = db.collection("teachers").document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if not data.get("approved") and data.get("status") != "approved":
            return None
        return data

    @app.post('/api/admin/teachers/approve')
    def admin_approve_teacher():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            request_id = str(body.get('requestId', '')).strip()
            if not request_id:
                return jsonify({'error': 'requestId is required.'}), 400
            db = _db()
            req_ref = db.collection('teacherRequests').document(request_id)
            snap = req_ref.get()
            if not snap.exists:
                return jsonify({'error': 'Teacher request not found.'}), 404
            data = snap.to_dict() or {}
            uid = str(data.get('uid', '')).strip()
            if not uid:
                return jsonify({'error': 'Teacher request has no user ID.'}), 400
            status = str(data.get('status', 'pending')).lower()
            if status == 'approved':
                return jsonify({'success': True, 'alreadyApproved': True, 'uid': uid}), 200
            if status not in {'pending', 'submitted'}:
                return jsonify({'error': f'Cannot approve request from status: {status}.'}), 409
            from firebase_admin import firestore
            now = datetime.now(timezone.utc)
            teacher_ref = db.collection('teachers').document(uid)
            teacher_data = dict(data)
            teacher_data.update({
                'uid': uid, 'approved': True, 'status': 'approved',
                'approvedAt': now, 'approvedBy': detail.get('uid', 'admin'),
                'updatedAt': now,
                'permissions': {
                    'createQuiz': True, 'createLiveClass': True,
                    'viewReports': True, 'approvePayments': False
                }
            })
            # Approval is a single atomic batch so the request and teacher profile
            # cannot diverge if one write fails.
            batch = db.batch()
            batch.update(req_ref, {'status': 'approved', 'approvedAt': now, 'approvedBy': detail.get('uid', 'admin'), 'updatedAt': now})
            batch.set(teacher_ref, teacher_data, merge=True)
            batch.commit()
            return jsonify({'success': True, 'uid': uid, 'status': 'approved'}), 200
        except Exception as exc:
            app.logger.exception('Admin teacher approval failed')
            return jsonify({'error': 'Teacher approval failed.'}), 500

    @app.get('/api/admin/teachers')
    def admin_teacher_roster():
        """Admin-facing teacher roster: merges the 'teachers' collection (subjects,
        classes, approval info - already captured at /api/teacher/apply) with the
        'users' collection (phone, last login) and a live count of each teacher's
        courses/assignments/students, so nothing here is duplicated data entry."""
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            db = _db()
            teacher_docs = list(db.collection('teachers').stream())
            rows = []
            for snap in teacher_docs:
                t = snap.to_dict() or {}
                uid = snap.id
                if not t.get('approved') and t.get('status') != 'approved':
                    continue
                user_snap = db.collection('users').document(uid).get()
                u = (user_snap.to_dict() or {}) if user_snap.exists else {}
                grades = set()
                course_count = 0
                for d in db.collection('courses').where('teacherUid', '==', uid).limit(500).stream():
                    course_count += 1
                    x = d.to_dict() or {}
                    g = str(x.get('className') or x.get('grade') or '').strip()
                    if g: grades.add(g)
                assignment_count = 0
                for d in db.collection('assignments').where('teacherUid', '==', uid).limit(500).stream():
                    assignment_count += 1
                    x = d.to_dict() or {}
                    g = str(x.get('className') or x.get('grade') or '').strip()
                    if g: grades.add(g)
                student_count = 0
                if grades:
                    for s in db.collection('users').limit(5000).stream():
                        x = s.to_dict() or {}
                        role = str(x.get('role') or x.get('userType') or x.get('accountType') or '').lower()
                        if role not in {'student', 'learner', ''} or x.get('isAdmin'): continue
                        grade = str(x.get('className') or x.get('class') or x.get('grade') or '').strip()
                        if grade in grades: student_count += 1
                rows.append({
                    'uid': uid,
                    'name': t.get('name') or u.get('displayName') or u.get('name') or 'Teacher',
                    'email': t.get('email') or u.get('email') or '',
                    'phone': u.get('phone') or u.get('phoneNumber') or '',
                    'subjects': t.get('subjects') or [],
                    'classes': sorted(t.get('classes') or list(grades)),
                    'registeredAt': iso(t.get('createdAt')),
                    'approvedAt': iso(t.get('approvedAt')),
                    'lastLoginAt': iso(u.get('lastLoginAt') or u.get('lastSeenAt')),
                    'courseCount': course_count,
                    'assignmentCount': assignment_count,
                    'studentCount': student_count,
                    'status': 'active' if (course_count or assignment_count) else 'approved-inactive',
                    'contractStatus': t.get('contractStatus') or 'active',
                    'contractStartAt': iso(t.get('contractStartAt')),
                    'contractEndAt': iso(t.get('contractEndAt')),
                })
            rows.sort(key=lambda r: str(r.get('name') or '').lower())
            return jsonify({'success': True, 'teachers': rows, 'count': len(rows)}), 200
        except Exception:
            app.logger.exception('Admin teacher roster failed')
            return jsonify({'error': 'Unable to load the teacher roster.'}), 500

    # ---- V31.108 Competition Center Admin Completion: teacher assignment
    # and contract management -----------------------------------------
    # Confirmed missing: subjects/classes were self-declared once at
    # /api/teacher/apply and never enforced against anything a teacher
    # actually created (course/live-class/assignment subject+grade were
    # completely free-text). Admin also had no "contract" concept at all -
    # only a generic, non-teacher-specific suspend/unsuspend
    # (student_profile_routes.py) that immediately cuts all API access.
    # Per explicit decision: contract end date is informational/manual only
    # (no auto-suspend at expiry), and narrowing a teacher's subjects/
    # classes never affects content they already created - enforcement
    # below only runs at creation time, on the three endpoints that
    # actually let a teacher "teach" (course, live class, assignment).
    @app.patch('/api/admin/teachers/<uid>/assignment')
    def admin_set_teacher_assignment(uid):
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            db = _db()
            ref = db.collection('teachers').document(uid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Teacher not found.'}), 404
            existing = snap.to_dict() or {}
            if not existing.get('approved') and existing.get('status') != 'approved':
                return jsonify({'error': 'Teacher is not an approved teacher.'}), 404
            body = request.get_json(silent=True) or {}
            if 'subjects' not in body or 'classes' not in body:
                return jsonify({'error': 'subjects and classes are both required.'}), 400
            subjects = body.get('subjects')
            classes = body.get('classes')
            if not isinstance(subjects, list) or not isinstance(classes, list):
                return jsonify({'error': 'subjects and classes must both be lists.'}), 400
            subjects = [str(x).strip() for x in subjects if str(x).strip()]
            invalid_subjects = [x for x in subjects if x not in ALLOWED_TEACHER_SUBJECTS]
            if invalid_subjects:
                return jsonify({'error': 'One or more selected subjects are not supported.'}), 400
            classes = [str(x).strip() for x in classes if str(x).strip()]
            if any(x not in ALLOWED_TEACHER_CLASSES for x in classes):
                return jsonify({'error': 'Grades must be between 3 and 12.'}), 400
            subjects = list(dict.fromkeys(subjects))[:10]
            classes = list(dict.fromkeys(classes))[:15]
            now = datetime.now(timezone.utc)
            ref.update({
                'subjects': subjects, 'classes': classes,
                'assignedBy': detail.get('uid', 'admin'), 'assignedAt': now, 'updatedAt': now,
            })
            return jsonify({'success': True, 'uid': uid, 'subjects': subjects, 'classes': classes}), 200
        except Exception:
            app.logger.exception('Admin teacher assignment update failed')
            return jsonify({'error': 'Unable to update teacher assignment.'}), 500

    @app.post('/api/admin/teachers/<uid>/contract/extend')
    def admin_extend_teacher_contract(uid):
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            db = _db()
            ref = db.collection('teachers').document(uid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Teacher not found.'}), 404
            existing = snap.to_dict() or {}
            if not existing.get('approved') and existing.get('status') != 'approved':
                return jsonify({'error': 'Teacher is not an approved teacher.'}), 404
            body = request.get_json(silent=True) or {}
            contract_end_at = str(body.get('contractEndAt', '')).strip()
            if not contract_end_at:
                return jsonify({'error': 'contractEndAt is required.'}), 400
            try:
                parsed_end = datetime.fromisoformat(contract_end_at.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'contractEndAt must be an ISO 8601 date/time.'}), 400
            now = datetime.now(timezone.utc)
            update = {
                'contractEndAt': parsed_end, 'contractStatus': 'active',
                'contractUpdatedBy': detail.get('uid', 'admin'), 'updatedAt': now,
            }
            if not existing.get('contractStartAt'):
                update['contractStartAt'] = now
            ref.update(update)
            return jsonify({'success': True, 'uid': uid, 'contractStatus': 'active', 'contractEndAt': iso(parsed_end)}), 200
        except Exception:
            app.logger.exception('Admin contract extension failed')
            return jsonify({'error': 'Unable to extend teacher contract.'}), 500

    @app.post('/api/admin/teachers/<uid>/contract/terminate')
    def admin_terminate_teacher_contract(uid):
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            db = _db()
            ref = db.collection('teachers').document(uid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Teacher not found.'}), 404
            existing = snap.to_dict() or {}
            if not existing.get('approved') and existing.get('status') != 'approved':
                return jsonify({'error': 'Teacher is not an approved teacher.'}), 404
            now = datetime.now(timezone.utc)
            ref.update({
                'contractStatus': 'terminated', 'contractTerminatedAt': now,
                'contractUpdatedBy': detail.get('uid', 'admin'), 'updatedAt': now,
            })
            # Termination must actually cut access, not just be a label - reuse
            # the same, already-tested suspend flag every other part of the
            # platform already checks (_check_not_suspended), rather than
            # inventing a second, parallel access-control mechanism.
            db.collection('users').document(uid).set({
                'isSuspended': True, 'suspendedAt': now, 'suspendedBy': detail.get('uid', 'admin'),
                'suspendReason': 'Teacher contract terminated by admin.',
            }, merge=True)
            return jsonify({'success': True, 'uid': uid, 'contractStatus': 'terminated'}), 200
        except Exception:
            app.logger.exception('Admin contract termination failed')
            return jsonify({'error': 'Unable to terminate teacher contract.'}), 500

    @app.post('/api/admin/teachers/reject')
    def admin_reject_teacher():
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            request_id = str(body.get('requestId', '')).strip()
            reason = str(body.get('reason', '')).strip()[:500]
            if not request_id:
                return jsonify({'error': 'requestId is required.'}), 400
            db = _db()
            req_ref = db.collection('teacherRequests').document(request_id)
            snap = req_ref.get()
            if not snap.exists:
                return jsonify({'error': 'Teacher request not found.'}), 404
            data = snap.to_dict() or {}
            status = str(data.get('status', 'pending')).lower()
            if status == 'rejected':
                return jsonify({'success': True, 'alreadyRejected': True}), 200
            if status == 'approved':
                return jsonify({'error': 'An approved teacher request cannot be rejected.'}), 409
            now = datetime.now(timezone.utc)
            req_ref.update({'status': 'rejected', 'rejectedAt': now, 'rejectedBy': detail.get('uid', 'admin'), 'rejectionReason': reason, 'updatedAt': now})
            return jsonify({'success': True, 'status': 'rejected'}), 200
        except Exception:
            app.logger.exception('Admin teacher rejection failed')
            return jsonify({'error': 'Teacher rejection failed.'}), 500

    @app.get('/api/student/assignments')
    def student_assignments():
        ok, detail=require_user()
        if not ok: return detail
        try:
            db=_db(); uid=detail['uid']
            user=db.collection('users').document(uid).get().to_dict() or {}
            grade=str(user.get('className') or user.get('grade') or user.get('class') or '').strip()
            entitlement=_entitlement(db, uid)
            # V31.108 Distance Assignment Tracking: batch-load this student's
            # own submissions once, keyed by assignmentId, so each assignment
            # in the list can carry submission/status/score/feedback inline.
            # Before this, a student (Distance students especially, who have
            # no other cue like a classroom to tell them what's done) had to
            # call /api/student/assignments/<id>/submission once per
            # assignment just to learn whether they'd already submitted it.
            my_subs={}
            for sd in db.collection('assignmentSubmissions').where('studentUid','==',uid).stream():
                s=sd.to_dict() or {}
                aid=str(s.get('assignmentId') or '')
                if aid:
                    my_subs[aid]={'id':sd.id,**s}
            docs=db.collection('assignments').where('status','==','published').stream()
            items=[]
            for d in docs:
                a={'id':d.id,**(d.to_dict() or {})}
                if grade and str(a.get('className','')).strip()!=grade: continue
                # Learning Mode / Target Audience targeting: an assignment
                # with no learningModes/audiences set (every assignment
                # created before this feature) stays visible to everyone,
                # exactly as before - only an assignment a teacher
                # explicitly targets (e.g. Distance-only) is restricted.
                # Same rule already used for the Digital Library legacy
                # catalog, via the same shared helper.
                if not content_visible_or_untargeted(user, a, entitlement): continue
                sub=my_subs.get(d.id)
                a['submission']=sub
                a['submissionStatus']=(sub or {}).get('status') or 'not_submitted'
                a['completed']=a['submissionStatus'] in ('submitted','graded')
                a['score']=(sub or {}).get('score')
                a['maxScore']=(sub or {}).get('maxScore')
                a['percentage']=(sub or {}).get('percentage')
                a['feedback']=(sub or {}).get('feedback')
                items.append(a)
            return jsonify({'assignments':items}),200
        except Exception:
            app.logger.exception('Student assignments load failed')
            return jsonify({'error':'Unable to load assignments.'}),500


    @app.post('/api/student/assignments/<assignment_id>/submit')
    def student_submit_assignment(assignment_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            body = request.get_json(silent=True) or {}
            text = str(body.get('text', '')).strip()[:10000]
            link = str(body.get('link', '')).strip()[:1000]
            if not text and not link:
                return jsonify({'error': 'Write an answer or provide a submission link.'}), 400
            ref = db.collection('assignments').document(assignment_id)
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Assignment not found.'}), 404
            assignment = snap.to_dict() or {}
            if assignment.get('status') != 'published':
                return jsonify({'error': 'This assignment is not accepting submissions.'}), 409
            user = db.collection('users').document(uid).get().to_dict() or {}
            student_grade = str(user.get('className') or user.get('grade') or user.get('class') or '').strip()
            if student_grade and str(assignment.get('className', '')).strip() != student_grade:
                return jsonify({'error': 'This assignment is not assigned to your grade.'}), 403
            # Distance Assignment Tracking: submission must respect the same
            # Learning Mode / Target Audience targeting as visibility - a
            # student who was never shown this assignment (wrong Learning
            # Mode or audience) cannot submit to it by hitting the endpoint
            # directly. Untargeted assignments (no learningModes/audiences
            # set) are unaffected, so this changes nothing for a Regular
            # classroom assignment created the old way.
            if not content_visible_or_untargeted(user, assignment, _entitlement(db, uid)):
                return jsonify({'error': 'This assignment is not available to your Learning Mode or audience.'}), 403
            due_raw = str(assignment.get('dueAt') or '').strip()
            if due_raw:
                try:
                    due = datetime.fromisoformat(due_raw.replace('Z', '+00:00'))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) > due:
                        return jsonify({'error': 'The submission deadline has passed.'}), 409
                except ValueError:
                    pass
            existing_q = db.collection('assignmentSubmissions').where('assignmentId','==',assignment_id).stream()
            existing = next((d for d in existing_q if (d.to_dict() or {}).get('studentUid') == uid), None)
            now = datetime.now(timezone.utc)
            data = {'assignmentId': assignment_id, 'studentUid': uid, 'studentEmail': detail.get('email',''),
                    'text': text, 'link': link, 'status': 'submitted', 'submittedAt': now, 'updatedAt': now}
            if existing:
                old = existing.to_dict() or {}
                if old.get('status') == 'graded':
                    return jsonify({'error': 'This submission has already been graded.'}), 409
                existing.reference.update(data)
                sid = existing.id
            else:
                sid = db.collection('assignmentSubmissions').document().id
                db.collection('assignmentSubmissions').document(sid).set(data)
            return jsonify({'success': True, 'submissionId': sid, 'status': 'submitted'}), 200
        except Exception:
            app.logger.exception('Student assignment submission failed')
            return jsonify({'error': 'Unable to submit assignment.'}), 500

    @app.get('/api/student/assignments/<assignment_id>/submission')
    def student_assignment_submission(assignment_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            snap = db.collection('assignments').document(assignment_id).get()
            if not snap.exists:
                return jsonify({'error':'Assignment not found.'}), 404
            assignment = snap.to_dict() or {}
            # Distance Assignment Tracking: same grade + Learning Mode /
            # Audience checks used by the list and submit endpoints, so a
            # student can't probe an assignment outside their grade or
            # targeting by calling this endpoint directly. This never
            # exposes another student's data either way - the query below
            # only ever returns the caller's own submission - but the
            # authorization should still be consistent across all three
            # student-facing assignment endpoints.
            user = db.collection('users').document(uid).get().to_dict() or {}
            student_grade = str(user.get('className') or user.get('grade') or user.get('class') or '').strip()
            if student_grade and str(assignment.get('className', '')).strip() != student_grade:
                return jsonify({'error': 'This assignment is not assigned to your grade.'}), 403
            if not content_visible_or_untargeted(user, assignment, _entitlement(db, uid)):
                return jsonify({'error': 'This assignment is not available to your Learning Mode or audience.'}), 403
            q = db.collection('assignmentSubmissions').where('assignmentId','==',assignment_id).stream()
            sub = next((d for d in q if (d.to_dict() or {}).get('studentUid') == uid), None)
            return jsonify({'submission': ({'id':sub.id, **(sub.to_dict() or {})} if sub else None)}), 200
        except Exception:
            app.logger.exception('Student assignment submission load failed')
            return jsonify({'error':'Unable to load submission.'}), 500

    @app.get('/api/teacher/assignments/<assignment_id>/submissions')
    def teacher_assignment_submissions(assignment_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error':'Approved teacher access required.'}), 403
            assignment = db.collection('assignments').document(assignment_id).get()
            if not assignment.exists or (assignment.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({'error':'Assignment not found or not owned by this teacher.'}), 403
            docs = db.collection('assignmentSubmissions').where('assignmentId','==',assignment_id).stream()
            rows=[]
            for d in docs:
                row={'id':d.id, **(d.to_dict() or {})}
                user=db.collection('users').document(str(row.get('studentUid',''))).get().to_dict() or {}
                row['studentName']=user.get('name') or user.get('displayName') or row.get('studentEmail','Student')
                rows.append(row)
            rows.sort(key=lambda x: str(x.get('submittedAt','')), reverse=True)
            return jsonify({'submissions':rows}), 200
        except Exception:
            app.logger.exception('Teacher assignment submissions load failed')
            return jsonify({'error':'Unable to load submissions.'}), 500

    @app.post('/api/teacher/submissions/<submission_id>/grade')
    def teacher_grade_assignment_submission(submission_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error':'Approved teacher access required.'}), 403
            body=request.get_json(silent=True) or {}
            try:
                score=float(body.get('score'))
                max_score=float(body.get('maxScore',100))
            except (TypeError, ValueError):
                return jsonify({'error':'Score and maximum score must be numeric.'}),400
            if max_score <= 0 or score < 0 or score > max_score:
                return jsonify({'error':'Score must be between 0 and the maximum score.'}),400
            feedback=str(body.get('feedback','')).strip()[:4000]
            ref=db.collection('assignmentSubmissions').document(submission_id)
            snap=ref.get()
            if not snap.exists:
                return jsonify({'error':'Submission not found.'}),404
            sub=snap.to_dict() or {}
            assignment=db.collection('assignments').document(str(sub.get('assignmentId',''))).get()
            if not assignment.exists or (assignment.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({'error':'You cannot grade this submission.'}),403
            now=datetime.now(timezone.utc)
            ref.update({'score':score,'maxScore':max_score,'percentage':round(score/max_score*100,2),'feedback':feedback,'status':'graded','gradedBy':uid,'gradedAt':now,'updatedAt':now})
            return jsonify({'success':True,'status':'graded','score':score,'maxScore':max_score,'percentage':round(score/max_score*100,2)}),200
        except Exception:
            app.logger.exception('Teacher assignment grading failed')
            return jsonify({'error':'Unable to grade submission.'}),500

    @app.get('/api/teacher/profile')
    def teacher_profile():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            uid = detail['uid']
            profile = _teacher(db, uid)
            if not profile or profile.get('approved') is not True:
                return jsonify({"error": "Approved teacher access required."}), 403
            profile.pop('permissions', None) if isinstance(profile.get('permissions'), dict) else None
            return jsonify({"approved": True, "profile": profile}), 200
        except RuntimeError as exc:
            app.logger.error('Teacher profile service unavailable: %s', exc)
            return jsonify({"error": "Teacher profile service is temporarily unavailable.", "requestId": getattr(__import__('flask').g, 'request_id', '')}), 503
        except Exception:
            app.logger.exception('Teacher profile lookup failed')
            return jsonify({"error": "Unable to load teacher profile.", "requestId": getattr(__import__('flask').g, 'request_id', '')}), 500

    @app.post('/api/teacher/apply')
    def teacher_apply():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            body = request.get_json(silent=True) or {}
            name = str(body.get('name', '')).strip()[:120]
            bio = str(body.get('bio', '')).strip()[:1500]
            education_level = str(body.get('educationLevel', '')).strip()[:120]
            institution = str(body.get('institution', '')).strip()[:160]
            experience = str(body.get('experience', '')).strip()[:1500]
            raw_experience_years = body.get('experienceYears', '')
            experience_years = str(raw_experience_years).strip()[:20]
            certifications = str(body.get('certifications', '')).strip()[:1000]
            subjects = body.get('subjects', [])
            classes = body.get('classes', [])
            if not name or not education_level:
                return jsonify({"error": "Full name and education level are required."}), 400
            if not isinstance(subjects, list) or not subjects:
                return jsonify({"error": "Name and at least one subject are required."}), 400
            if not isinstance(classes, list) or not classes:
                return jsonify({"error": "Select at least one grade."}), 400
            subjects = [str(x).strip() for x in subjects if str(x).strip()]
            invalid_subjects = [x for x in subjects if x not in ALLOWED_TEACHER_SUBJECTS]
            if invalid_subjects:
                return jsonify({"error": "One or more selected subjects are not supported."}), 400
            subjects = subjects[:10]
            classes = [str(x).strip() for x in classes if str(x).strip()]
            if any(x not in ALLOWED_TEACHER_CLASSES for x in classes):
                return jsonify({"error": "Grades must be between 3 and 12."}), 400
            classes = classes[:15]
            try:
                experience_years_num = float(raw_experience_years) if str(raw_experience_years).strip() else 0
            except (TypeError, ValueError):
                return jsonify({"error": "Years of experience must be a number."}), 400
            if not 0 <= experience_years_num <= 60:
                return jsonify({"error": "Years of experience must be between 0 and 60."}), 400
            experience_years = str(int(experience_years_num)) if experience_years_num.is_integer() else str(experience_years_num)
            db = _db()
            uid = detail['uid']
            existing = db.collection('teachers').document(uid).get()
            if existing.exists and (existing.to_dict() or {}).get('approved'):
                return jsonify({"error": "You are already an approved teacher."}), 409
            pending = db.collection('teacherRequests').where('uid', '==', uid).stream()
            if any((d.to_dict() or {}).get('status') == 'pending' for d in pending):
                return jsonify({"error": "Your teacher application is already pending."}), 409
            now = datetime.now(timezone.utc)
            ref = db.collection('teacherRequests').document()
            ref.set({
                'uid': uid, 'email': detail.get('email', ''), 'name': name,
                'bio': bio, 'educationLevel': education_level, 'institution': institution,
                'experience': experience, 'experienceYears': experience_years,
                'certifications': certifications, 'subjects': subjects, 'classes': classes,
                'status': 'pending', 'createdAt': now, 'updatedAt': now
            })
            return jsonify({"success": True, "requestId": ref.id, "status": "pending"}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get('/api/teacher/students')
    def teacher_students():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            grades = set()
            for d in db.collection('courses').where('teacherUid', '==', uid).limit(200).stream():
                x = d.to_dict() or {}; g = str(x.get('className') or x.get('grade') or '').strip()
                if g: grades.add(g)
            for d in db.collection('assignments').where('teacherUid', '==', uid).limit(200).stream():
                x = d.to_dict() or {}; g = str(x.get('className') or x.get('grade') or '').strip()
                if g: grades.add(g)
            if not grades:
                return jsonify({'success': True, 'students': [], 'grades': []}), 200
            rows = []
            for snap in db.collection('users').limit(5000).stream():
                x = snap.to_dict() or {}
                role = str(x.get('role') or x.get('userType') or x.get('accountType') or '').lower()
                if role not in {'student', 'learner', ''} or x.get('isAdmin'): continue
                grade = str(x.get('className') or x.get('class') or x.get('grade') or '').strip()
                if grade not in grades: continue
                rows.append({'uid': snap.id, 'name': x.get('displayName') or x.get('name') or x.get('fullName') or 'Student', 'email': x.get('email') or '', 'phone': x.get('phone') or x.get('phoneNumber') or '', 'grade': grade, 'isPaid': bool(x.get('isPaid'))})
            rows.sort(key=lambda r: (r['grade'], str(r['name']).lower()))
            return jsonify({'success': True, 'students': rows, 'grades': sorted(grades)}), 200
        except Exception:
            app.logger.exception('Teacher student list failed')
            return jsonify({'error': 'Unable to load your students.'}), 500

    @app.get('/api/teacher/courses')
    def teacher_courses():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            docs = db.collection('courses').where('teacherUid', '==', uid).stream()
            items = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
            return jsonify({"courses": items}), 200
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post('/api/teacher/courses')
    def teacher_create_course():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            teacher_data = _teacher(db, uid)
            if not teacher_data:
                return jsonify({"error": "Approved teacher access required."}), 403
            body = request.get_json(silent=True) or {}
            title = str(body.get('title', '')).strip()[:160]
            class_name = str(body.get('className', '')).strip()[:20]
            description = str(body.get('description', '')).strip()[:2000]
            subject = str(body.get('subject', '')).strip()[:80]
            allowed = {str(i) for i in range(3, 13)}
            if not title or class_name not in allowed:
                return jsonify({"error": "Valid course title and grade are required."}), 400
            # V31.108 Competition Center Admin Completion: a teacher may only
            # create a course for a grade admin has assigned them to, and -
            # when a subject is given (it's optional here, unlike Live
            # Class below, matching this field's existing free-text/optional
            # behavior) - only for a subject admin has assigned them too.
            # `classes`/`subjects` being entirely absent from the teacher
            # doc (not merely an empty list) means this teacher predates the
            # assignment feature or was approved outside the normal
            # /api/teacher/apply flow - treated as unrestricted rather than
            # locked out, so this never blocks a teacher an admin simply
            # hasn't gotten around to assigning yet. An admin who explicitly
            # sets an empty list via /api/admin/teachers/<uid>/assignment IS
            # revoking new-content creation, on purpose. Narrowing a
            # teacher's assignment later never affects a course already
            # created; this only runs at creation time.
            assigned_classes = teacher_data.get('classes')
            if assigned_classes is not None and class_name not in set(assigned_classes):
                return jsonify({"error": "You are not assigned to teach this grade. Ask an admin to update your subject/grade assignment."}), 403
            assigned_subjects = teacher_data.get('subjects')
            if subject and assigned_subjects is not None and subject not in set(assigned_subjects):
                return jsonify({"error": "You are not assigned to teach this subject. Ask an admin to update your subject/grade assignment."}), 403
            # V31.108 Phase C: Learning Mode / Target Audience, same validation
            # rule as learning_challenge_routes.py's _targeting(), so a course
            # can be reached by targeting_access.content_target_allowed()
            # exactly like Question Bank / Challenge content already is.
            allowed_modes = {"Regular", "Distance"}
            allowed_audiences = {"Free Regular", "Paid Regular", "Distance", "Scholarship", "Competition"}
            modes = body.get("learningModes")
            audiences = body.get("audiences")
            modes = [str(x).strip() for x in modes] if isinstance(modes, list) else ["Regular"]
            audiences = [str(x).strip() for x in audiences] if isinstance(audiences, list) else ["Free Regular", "Paid Regular"]
            modes = list(dict.fromkeys(x for x in modes if x in allowed_modes)) or ["Regular"]
            audiences = list(dict.fromkeys(x for x in audiences if x in allowed_audiences)) or ["Free Regular", "Paid Regular"]
            now = datetime.now(timezone.utc)
            ref = db.collection('courses').document()
            ref.set({
                'title': title, 'className': class_name, 'description': description,
                'subject': subject, 'learningModes': modes, 'audiences': audiences,
                'teacherUid': uid, 'teacherEmail': detail.get('email', ''),
                'status': 'published', 'createdAt': now, 'updatedAt': now
            })
            return jsonify({"success": True, "course": {"id": ref.id, "title": title, "className": class_name, "description": description, "subject": subject, "learningModes": modes, "audiences": audiences}}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post('/api/teacher/lessons')
    def teacher_create_lesson():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            body = request.get_json(silent=True) or {}
            course_id = str(body.get('courseId', '')).strip()
            # V31.108 Course Structure Completion: optional unitId, so a
            # lesson can be grouped under a Unit within its course. Left
            # unset by default (None) - every existing caller that never
            # sends unitId keeps creating exactly the same ungrouped lesson
            # as before, and every existing lesson document (which has no
            # unitId field at all) reads back as unitId=None identically.
            unit_id = str(body.get('unitId', '')).strip()
            title = str(body.get('title', '')).strip()[:160]
            content_type = str(body.get('contentType', 'video')).strip().lower()
            url = str(body.get('url', '')).strip()[:2000]
            description = str(body.get('description', '')).strip()[:2000]
            try:
                order = int(body.get('order', 0) or 0)
            except (TypeError, ValueError):
                order = 0
            if not course_id or not title or not url:
                return jsonify({"error": "Course, lesson title and content URL are required."}), 400
            # V31.108 audit fix: the URL is later rendered as a student-facing
            # link. Only plain http(s) web links are accepted (javascript:,
            # data:, vbscript:, file: ... are rejected).
            if not _lb.safe_web_link(url):
                return jsonify({"error": "Lesson content URL must be a web link starting with http:// or https://."}), 400
            course = db.collection('courses').document(course_id).get()
            if not course.exists or (course.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            if unit_id:
                # Same ownership + same-course check already proven for
                # lessonId/courseId pairs on /api/teacher/assignments and
                # /api/teacher/lesson-plans above.
                u = db.collection('units').document(unit_id).get()
                udata = u.to_dict() or {}
                if not u.exists or udata.get('teacherUid') != uid or udata.get('courseId') != course_id:
                    return jsonify({"error": "Unit not found, not owned by this teacher, or does not belong to the given course."}), 403
            if content_type not in {'youtube', 'telegram', 'pdf', 'drive', 'cloudinary', 'link'}:
                return jsonify({"error": "Unsupported lesson content type."}), 400
            # V31.108 Course/Unit/Lesson Foundation: optional initial status
            # so a lesson can be prepared as a draft. Absent (every existing
            # caller) keeps the historical behavior: published immediately.
            status = str(body.get('status') or 'published').strip().lower()
            if status not in {'published', 'draft'}:
                return jsonify({"error": "Status must be one of: published, draft."}), 400
            now = datetime.now(timezone.utc)
            ref = db.collection('lessons').document()
            ref.set({
                'courseId': course_id, 'unitId': unit_id or None, 'teacherUid': uid, 'title': title,
                'contentType': content_type, 'url': url, 'description': description,
                'order': order, 'status': status, 'createdAt': now, 'updatedAt': now
            })
            return jsonify({"success": True, "lessonId": ref.id, "unitId": unit_id or None, "status": status}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ---- V31.108 Course Structure Completion: Unit layer -----------------
    # Audit confirmed the real hierarchy was flat Course -> Lesson, with no
    # Unit collection or unitId field anywhere in the codebase (see
    # HANDOFF_NOTES / the V31.108 lesson-plan pass, which stored "unit" as a
    # free-text bookkeeping field on lessonPlans for exactly this reason).
    # This adds the minimum needed to make Unit real: a `units` collection
    # (courseId, teacherUid, title, description, order, status) plus an
    # optional `unitId` on `lessons` (added to /api/teacher/lessons above).
    # Existing courses/lessons/progress/certificate logic is untouched -
    # lesson counting for progress/completion still iterates every published
    # lesson in the course regardless of unit, so a lesson with no unitId
    # (i.e. every lesson created before this change) behaves identically to
    # before.
    _unit_allowed_status = {"published", "draft"}

    @app.get('/api/teacher/courses/<course_id>/units')
    def teacher_list_units(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            course = db.collection('courses').document(course_id).get()
            if not course.exists or (course.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            docs = db.collection('units').where('courseId', '==', course_id).stream()
            items = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
            items.sort(key=lambda x: (x.get('order', 0), str(x.get('title') or '')))
            return jsonify({"units": items}), 200
        except Exception:
            app.logger.exception('Teacher unit list failed')
            return jsonify({"error": "Unable to load units."}), 500

    @app.post('/api/teacher/courses/<course_id>/units')
    def teacher_create_unit(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            course = db.collection('courses').document(course_id).get()
            if not course.exists or (course.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            body = request.get_json(silent=True) or {}
            title = str(body.get('title', '')).strip()[:160]
            if not title:
                return jsonify({"error": "Unit title is required."}), 400
            description = str(body.get('description', '')).strip()[:2000]
            try:
                order = int(body.get('order', 0) or 0)
            except (TypeError, ValueError):
                order = 0
            now = datetime.now(timezone.utc)
            ref = db.collection('units').document()
            ref.set({
                'courseId': course_id, 'teacherUid': uid, 'title': title,
                'description': description, 'order': order, 'status': 'published',
                'createdAt': now, 'updatedAt': now,
            })
            return jsonify({"success": True, "unit": {
                "id": ref.id, "courseId": course_id, "title": title,
                "description": description, "order": order, "status": "published",
            }}), 201
        except Exception:
            app.logger.exception('Teacher unit creation failed')
            return jsonify({"error": "Unable to create unit."}), 500

    @app.patch('/api/teacher/units/<unit_id>')
    def teacher_update_unit(unit_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            ref = db.collection('units').document(unit_id)
            snap = ref.get()
            if not snap.exists or (snap.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Unit not found or not owned by this teacher."}), 404
            body = request.get_json(silent=True) or {}
            patch = {}
            if 'title' in body:
                title = str(body.get('title', '')).strip()[:160]
                if not title:
                    return jsonify({"error": "Unit title cannot be empty."}), 400
                patch['title'] = title
            if 'description' in body:
                patch['description'] = str(body.get('description', '') or '').strip()[:2000]
            if 'order' in body:
                try:
                    patch['order'] = int(body.get('order', 0) or 0)
                except (TypeError, ValueError):
                    return jsonify({"error": "Order must be a number."}), 400
            if 'status' in body:
                status = str(body.get('status', '')).strip().lower()
                if status not in _unit_allowed_status:
                    return jsonify({"error": "Status must be one of: published, draft."}), 400
                patch['status'] = status
            if not patch:
                return jsonify({"error": "No valid fields to update."}), 400
            patch['updatedAt'] = datetime.now(timezone.utc)
            ref.update(patch)
            return jsonify({"success": True, "id": unit_id}), 200
        except Exception:
            app.logger.exception('Teacher unit update failed')
            return jsonify({"error": "Unable to update unit."}), 500

    # ---- V31.108 Course/Unit/Lesson Foundation: lesson management --------
    # Audit of the baseline found teachers could CREATE a lesson but had no
    # route to list a course's lessons (drafts included), edit a lesson,
    # move it between units, publish/unpublish it, or reorder anything in
    # bulk. Existing orders are mostly ties at 0 (the create form defaults to
    # it), so reordering by swapping two `order` values cannot work; the
    # bulk routes below renumber a whole sibling list 0..n-1 in one batch.
    # Everything reuses the same _teacher() gate and the same
    # ownership/same-course rules as the unit and lesson-create routes
    # above; no existing route's contract changes.
    _lesson_allowed_status = {"published", "draft"}
    # Must stay identical to the allow-list in teacher_create_lesson().
    _lesson_content_types = {'youtube', 'telegram', 'pdf', 'drive', 'cloudinary', 'link'}
    _REORDER_MAX = 400  # a Firestore batch holds at most 500 writes

    def _ord(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _owns_course(db, uid, course_id):
        snap = db.collection('courses').document(course_id).get()
        return bool(snap.exists and (snap.to_dict() or {}).get('teacherUid') == uid)

    def _owned_unit_in_course(db, uid, unit_id, course_id):
        if not unit_id or '/' in unit_id:
            return False
        snap = db.collection('units').document(unit_id).get()
        data = snap.to_dict() or {}
        return bool(snap.exists and data.get('teacherUid') == uid and data.get('courseId') == course_id)

    def _clean_id_list(body):
        ids = body.get('order')
        if not isinstance(ids, list) or not ids or len(ids) > _REORDER_MAX:
            return None
        if not all(isinstance(x, str) and x.strip() for x in ids):
            return None
        ids = [x.strip() for x in ids]
        return ids if len(set(ids)) == len(ids) else None

    def _write_order(db, collection, ids):
        now = datetime.now(timezone.utc)
        batch = db.batch()
        for index, doc_id in enumerate(ids):
            batch.update(db.collection(collection).document(doc_id), {'order': index, 'updatedAt': now})
        batch.commit()

    @app.get('/api/teacher/courses/<course_id>/lessons')
    def teacher_list_course_lessons(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            if not _owns_course(db, uid, course_id):
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            docs = db.collection('lessons').where('courseId', '==', course_id).stream()
            items = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
            items.sort(key=lambda x: (_ord(x.get('order')), str(x.get('title') or '')))
            return jsonify({"lessons": items}), 200
        except Exception:
            app.logger.exception('Teacher lesson list failed')
            return jsonify({"error": "Unable to load lessons."}), 500

    @app.patch('/api/teacher/lessons/<lesson_id>')
    def teacher_update_lesson(lesson_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            ref = db.collection('lessons').document(lesson_id)
            snap = ref.get()
            lesson = snap.to_dict() or {}
            if not snap.exists or lesson.get('teacherUid') != uid:
                return jsonify({"error": "Lesson not found or not owned by this teacher."}), 404
            body = request.get_json(silent=True) or {}
            patch = {}
            if 'title' in body:
                title = str(body.get('title', '')).strip()[:160]
                if not title:
                    return jsonify({"error": "Lesson title cannot be empty."}), 400
                patch['title'] = title
            if 'description' in body:
                patch['description'] = str(body.get('description', '') or '').strip()[:2000]
            if 'url' in body:
                url = str(body.get('url', '') or '').strip()[:2000]
                if not url:
                    return jsonify({"error": "Lesson content URL cannot be empty."}), 400
                if not _lb.safe_web_link(url):
                    return jsonify({"error": "Lesson content URL must be a web link starting with http:// or https://."}), 400
                patch['url'] = url
            if 'contentType' in body:
                content_type = str(body.get('contentType', '')).strip().lower()
                if content_type not in _lesson_content_types:
                    return jsonify({"error": "Unsupported lesson content type."}), 400
                patch['contentType'] = content_type
            if 'order' in body:
                try:
                    patch['order'] = int(body.get('order', 0) or 0)
                except (TypeError, ValueError):
                    return jsonify({"error": "Order must be a number."}), 400
            if 'status' in body:
                status = str(body.get('status', '')).strip().lower()
                if status not in _lesson_allowed_status:
                    return jsonify({"error": "Status must be one of: published, draft."}), 400
                patch['status'] = status
            if 'unitId' in body:
                # Same ownership + same-course rule as teacher_create_lesson().
                # An empty/null unitId moves the lesson out of its unit.
                new_unit = str(body.get('unitId') or '').strip()
                if new_unit:
                    if not _owned_unit_in_course(db, uid, new_unit, lesson.get('courseId')):
                        return jsonify({"error": "Unit not found, not owned by this teacher, or does not belong to the given course."}), 403
                    patch['unitId'] = new_unit
                else:
                    patch['unitId'] = None
            if not patch:
                return jsonify({"error": "No valid fields to update."}), 400
            patch['updatedAt'] = datetime.now(timezone.utc)
            ref.update(patch)
            return jsonify({"success": True, "id": lesson_id}), 200
        except Exception:
            app.logger.exception('Teacher lesson update failed')
            return jsonify({"error": "Unable to update lesson."}), 500

    @app.post('/api/teacher/courses/<course_id>/units/reorder')
    def teacher_reorder_units(course_id):
        """Body: {"order": [unitId, ...]} - every unit of the course, in the
        new order. Renumbers them 0..n-1 atomically."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            if not _owns_course(db, uid, course_id):
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            ids = _clean_id_list(request.get_json(silent=True) or {})
            if ids is None:
                return jsonify({"error": "order must be a non-empty list of unique unit ids."}), 400
            siblings = {d.id for d in db.collection('units').where('courseId', '==', course_id).stream()
                        if (d.to_dict() or {}).get('teacherUid') == uid}
            if set(ids) != siblings:
                return jsonify({"error": "The unit list has changed. Reload and try again."}), 409
            _write_order(db, 'units', ids)
            return jsonify({"success": True, "order": ids}), 200
        except Exception:
            app.logger.exception('Teacher unit reorder failed')
            return jsonify({"error": "Unable to reorder units."}), 500

    @app.post('/api/teacher/courses/<course_id>/lessons/reorder')
    def teacher_reorder_lessons(course_id):
        """Body: {"order": [lessonId, ...], "unitId": "<unit id>" | null}.
        Lists every lesson in ONE scope - the given unit, or (unitId omitted/
        null) the lessons that belong to no unit, which for a legacy course
        with no units is every lesson - in the new order. Renumbers them
        0..n-1 atomically."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            if not _owns_course(db, uid, course_id):
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            body = request.get_json(silent=True) or {}
            ids = _clean_id_list(body)
            if ids is None:
                return jsonify({"error": "order must be a non-empty list of unique lesson ids."}), 400
            scope_unit = str(body.get('unitId') or '').strip()
            if scope_unit and not _owned_unit_in_course(db, uid, scope_unit, course_id):
                return jsonify({"error": "Unit not found, not owned by this teacher, or does not belong to the given course."}), 403
            siblings = set()
            for d in db.collection('lessons').where('courseId', '==', course_id).stream():
                data = d.to_dict() or {}
                if data.get('teacherUid') == uid and (data.get('unitId') or '') == scope_unit:
                    siblings.add(d.id)
            if set(ids) != siblings:
                return jsonify({"error": "The lesson list has changed. Reload and try again."}), 409
            _write_order(db, 'lessons', ids)
            return jsonify({"success": True, "order": ids}), 200
        except Exception:
            app.logger.exception('Teacher lesson reorder failed')
            return jsonify({"error": "Unable to reorder lessons."}), 500


    @app.post('/api/teacher/assignments')
    def teacher_create_assignment():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            teacher_data = _teacher(db, uid)
            if not teacher_data:
                return jsonify({'error':'Approved teacher access required.'}), 403
            body=request.get_json(silent=True) or {}
            title=str(body.get('title','')).strip()[:160]
            description=str(body.get('description','')).strip()[:4000]
            grade=str(body.get('className','')).strip()[:20]
            due=str(body.get('dueAt','')).strip()[:80]
            course_id=str(body.get('courseId','')).strip()
            lesson_id=str(body.get('lessonId','')).strip()
            if not title or grade not in {str(i) for i in range(3,13)}:
                return jsonify({'error':'Assignment title and valid grade are required.'}),400
            # V31.108 Competition Center Admin Completion: same
            # assigned-grade enforcement as /api/teacher/courses above,
            # same absent-vs-empty-list backward-compatibility rule.
            # Assignments have no subject field, so only grade is checked.
            assigned_classes = teacher_data.get('classes')
            if assigned_classes is not None and grade not in set(assigned_classes):
                return jsonify({'error': 'You are not assigned to teach this grade. Ask an admin to update your subject/grade assignment.'}), 403
            if course_id:
                c=db.collection('courses').document(course_id).get()
                if not c.exists or (c.to_dict() or {}).get('teacherUid') != uid:
                    return jsonify({'error':'Course not found or not owned by this teacher.'}),403
            if lesson_id:
                # Distance Assignment Tracking: optional course/lesson
                # relationship, so a Distance course's lesson can carry its
                # own assignment. Same ownership + (if courseId was also
                # given) same-course check as /api/teacher/lessons itself.
                l=db.collection('lessons').document(lesson_id).get()
                ldata=l.to_dict() or {}
                if not l.exists or ldata.get('teacherUid') != uid or (course_id and ldata.get('courseId') != course_id):
                    return jsonify({'error':'Lesson not found, not owned by this teacher, or does not belong to the given course.'}),403
            # Distance Assignment Tracking: optional Learning Mode / Target
            # Audience targeting, same validation rule already used for
            # courses (see /api/teacher/courses above) and Question Bank /
            # Challenge content. Left unset by default so any existing
            # caller that never sends these fields keeps creating exactly
            # the same untargeted assignment as before - visible to every
            # grade-matched student, Regular and Distance alike, via
            # content_visible_or_untargeted().
            modes=body.get('learningModes')
            audiences=body.get('audiences')
            if modes is not None or audiences is not None:
                allowed_modes={"Regular","Distance"}
                allowed_audiences={"Free Regular","Paid Regular","Distance","Scholarship","Competition"}
                modes=[str(x).strip() for x in modes] if isinstance(modes,list) else []
                audiences=[str(x).strip() for x in audiences] if isinstance(audiences,list) else []
                modes=list(dict.fromkeys(x for x in modes if x in allowed_modes))
                audiences=list(dict.fromkeys(x for x in audiences if x in allowed_audiences))
                if not modes or not audiences:
                    return jsonify({'error':'At least one valid Learning Mode and Target Audience is required.'}),400
            else:
                modes=None; audiences=None
            now=datetime.now(timezone.utc)
            ref=db.collection('assignments').document()
            payload={'title':title,'description':description,'className':grade,'courseId':course_id or None,'lessonId':lesson_id or None,'teacherUid':uid,'teacherEmail':detail.get('email',''),'dueAt':due or None,'status':'published','createdAt':now,'updatedAt':now}
            if modes is not None:
                payload['learningModes']=modes; payload['audiences']=audiences
            ref.set(payload)
            resp={'id':ref.id,'title':title,'className':grade,'dueAt':due or None,'courseId':course_id or None,'lessonId':lesson_id or None}
            if modes is not None:
                resp['learningModes']=modes; resp['audiences']=audiences
            return jsonify({'success':True,'assignment':resp}),201
        except Exception as exc:
            app.logger.exception('Teacher assignment creation failed')
            return jsonify({'error':'Unable to create assignment.'}),500

    @app.get('/api/teacher/assignments')
    def teacher_assignments():
        ok, detail=require_user()
        if not ok:
            return detail
        try:
            db=_db(); uid=detail['uid']
            if not _teacher(db,uid): return jsonify({'error':'Approved teacher access required.'}),403
            docs=db.collection('assignments').where('teacherUid','==',uid).stream()
            items=[{'id':d.id,**(d.to_dict() or {})} for d in docs]
            items.sort(key=lambda x:str(x.get('createdAt','')), reverse=True)
            return jsonify({'assignments':items}),200
        except Exception:
            app.logger.exception('Teacher assignments load failed')
            return jsonify({'error':'Unable to load assignments.'}),500

    # ---- V31.108 Teacher Lesson Plan ------------------------------------
    # Confirmed missing before this pass: the only prior "lesson plan"
    # reference anywhere in the project was the AI Copilot's ephemeral
    # generated text (see docs/history/V30.71_RELEASE_NOTES.md) - nothing
    # stored, nothing a teacher could list/edit/track. This adds exactly
    # that, as its own `lessonPlans` collection - a planning document,
    # distinct from `lessons` (the student-facing content record).
    #
    # There is no Unit collection anywhere in this codebase (Course maps
    # directly to Lesson, not through a Unit layer), so "Unit" is stored
    # here as a plain free-text field rather than inventing a Unit
    # collection/relationship the rest of the app has no concept of.
    # courseId/lessonId/assignmentId are optional links into the systems
    # that already exist, re-using the exact ownership (+ same-course)
    # checks already proven in /api/teacher/assignments above.
    _lp_allowed_modes = {"Regular", "Distance"}
    _lp_allowed_audiences = {"Free Regular", "Paid Regular", "Distance", "Scholarship", "Competition"}
    _lp_allowed_status = {"planned", "in_progress", "completed"}
    _lp_grades = {str(i) for i in range(3, 13)}

    def _clean_list(raw, max_items=20, max_len=500):
        if not isinstance(raw, list):
            return []
        out = [str(x).strip()[:max_len] for x in raw if str(x).strip()]
        return out[:max_items]

    def _lesson_plan_links(db, uid, body, existing=None):
        """Validates the optional courseId/lessonId/assignmentId links a
        lesson plan may carry. Returns (fields_dict, error_response_or_None).
        `existing` is the current doc (for PATCH, to fall back to already-
        stored links when one is being kept rather than changed)."""
        existing = existing or {}
        course_id = body.get('courseId', existing.get('courseId')) or ''
        course_id = str(course_id).strip()
        if course_id:
            c = db.collection('courses').document(course_id).get()
            if not c.exists or (c.to_dict() or {}).get('teacherUid') != uid:
                return None, (jsonify({'error': 'Course not found or not owned by this teacher.'}), 403)

        lesson_id = body.get('lessonId', existing.get('lessonId')) or ''
        lesson_id = str(lesson_id).strip()
        if lesson_id:
            l = db.collection('lessons').document(lesson_id).get()
            ldata = l.to_dict() or {}
            if not l.exists or ldata.get('teacherUid') != uid or (course_id and ldata.get('courseId') != course_id):
                return None, (jsonify({'error': 'Lesson not found, not owned by this teacher, or does not belong to the given course.'}), 403)

        assignment_id = body.get('assignmentId', existing.get('assignmentId')) or ''
        assignment_id = str(assignment_id).strip()
        if assignment_id:
            a = db.collection('assignments').document(assignment_id).get()
            adata = a.to_dict() or {}
            if not a.exists or adata.get('teacherUid') != uid or (course_id and adata.get('courseId') != course_id):
                return None, (jsonify({'error': 'Assignment not found, not owned by this teacher, or does not belong to the given course.'}), 403)

        return {'courseId': course_id or None, 'lessonId': lesson_id or None, 'assignmentId': assignment_id or None}, None

    @app.post('/api/teacher/lesson-plans')
    def teacher_create_lesson_plan():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            body = request.get_json(silent=True) or {}
            grade = str(body.get('className', '')).strip()[:20]
            if grade not in _lp_grades:
                return jsonify({'error': 'A valid grade (3-12) is required.'}), 400
            title = str(body.get('title', '')).strip()[:160]
            subject = str(body.get('subject', '')).strip()[:80]
            unit = str(body.get('unit', '')).strip()[:160]
            week_of = str(body.get('weekOf', '')).strip()[:40]
            objectives = _clean_list(body.get('objectives'))
            activities = _clean_list(body.get('activities'))
            materials = _clean_list(body.get('materials'))
            assessment = str(body.get('assessment', '')).strip()[:2000]

            links, err = _lesson_plan_links(db, uid, body)
            if err:
                return err

            # Learning Mode / Target Audience: optional, same validation as
            # /api/teacher/assignments above - which cohort's version of
            # this plan it is, for a teacher running Regular and Distance
            # sections of the same grade/subject differently. Left unset by
            # default; nothing student-facing reads this (lesson plans are
            # a teacher-only tool, no student endpoint exists), so there is
            # no visibility behavior to preserve either way - this is
            # bookkeeping only, respected here because the same content
            # could plausibly need it later without a schema change.
            modes = body.get('learningModes')
            audiences = body.get('audiences')
            if modes is not None or audiences is not None:
                modes = [str(x).strip() for x in modes] if isinstance(modes, list) else []
                audiences = [str(x).strip() for x in audiences] if isinstance(audiences, list) else []
                modes = list(dict.fromkeys(x for x in modes if x in _lp_allowed_modes))
                audiences = list(dict.fromkeys(x for x in audiences if x in _lp_allowed_audiences))
                if not modes or not audiences:
                    return jsonify({'error': 'At least one valid Learning Mode and Target Audience is required.'}), 400
            else:
                modes = None; audiences = None

            status = str(body.get('status', 'planned')).strip().lower()
            if status not in _lp_allowed_status:
                status = 'planned'

            now = datetime.now(timezone.utc)
            payload = {
                'title': title, 'className': grade, 'subject': subject, 'unit': unit,
                'weekOf': week_of, 'objectives': objectives, 'activities': activities,
                'materials': materials, 'assessment': assessment, 'status': status,
                'teacherUid': uid, 'teacherEmail': detail.get('email', ''),
                'createdAt': now, 'updatedAt': now,
                **links,
            }
            if modes is not None:
                payload['learningModes'] = modes; payload['audiences'] = audiences
            ref = db.collection('lessonPlans').document()
            ref.set(payload)
            return jsonify({'success': True, 'lessonPlan': {'id': ref.id, **payload, 'createdAt': iso(now), 'updatedAt': iso(now)}}), 201
        except Exception:
            app.logger.exception('Teacher lesson plan creation failed')
            return jsonify({'error': 'Unable to create lesson plan.'}), 500

    @app.get('/api/teacher/lesson-plans')
    def teacher_lesson_plans():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            docs = db.collection('lessonPlans').where('teacherUid', '==', uid).stream()
            items = [{'id': d.id, **(d.to_dict() or {})} for d in docs]
            items.sort(key=lambda x: str(x.get('createdAt', '')), reverse=True)
            return jsonify({'lessonPlans': items}), 200
        except Exception:
            app.logger.exception('Teacher lesson plans load failed')
            return jsonify({'error': 'Unable to load lesson plans.'}), 500

    @app.patch('/api/teacher/lesson-plans/<plan_id>')
    def teacher_update_lesson_plan(plan_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            ref = db.collection('lessonPlans').document(plan_id)
            snap = ref.get()
            if not snap.exists or (snap.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({'error': 'Lesson plan not found or not owned by this teacher.'}), 404
            existing = snap.to_dict() or {}
            body = request.get_json(silent=True) or {}
            patch = {}
            if 'className' in body:
                grade = str(body.get('className', '')).strip()[:20]
                if grade not in _lp_grades:
                    return jsonify({'error': 'A valid grade (3-12) is required.'}), 400
                patch['className'] = grade
            for field, limit in [('title', 160), ('subject', 80), ('unit', 160), ('weekOf', 40), ('assessment', 2000)]:
                if field in body:
                    patch[field] = str(body.get(field, '') or '').strip()[:limit]
            for field in ('objectives', 'activities', 'materials'):
                if field in body:
                    patch[field] = _clean_list(body.get(field))
            if 'status' in body:
                status = str(body.get('status', '')).strip().lower()
                if status not in _lp_allowed_status:
                    return jsonify({'error': 'Status must be one of: planned, in_progress, completed.'}), 400
                patch['status'] = status
            if any(k in body for k in ('courseId', 'lessonId', 'assignmentId')):
                links, err = _lesson_plan_links(db, uid, body, existing=existing)
                if err:
                    return err
                patch.update(links)
            if 'learningModes' in body or 'audiences' in body:
                modes = body.get('learningModes', existing.get('learningModes'))
                audiences = body.get('audiences', existing.get('audiences'))
                modes = [str(x).strip() for x in modes] if isinstance(modes, list) else []
                audiences = [str(x).strip() for x in audiences] if isinstance(audiences, list) else []
                modes = list(dict.fromkeys(x for x in modes if x in _lp_allowed_modes))
                audiences = list(dict.fromkeys(x for x in audiences if x in _lp_allowed_audiences))
                if not modes or not audiences:
                    return jsonify({'error': 'At least one valid Learning Mode and Target Audience is required.'}), 400
                patch['learningModes'] = modes; patch['audiences'] = audiences
            if not patch:
                return jsonify({'error': 'No valid fields to update.'}), 400
            patch['updatedAt'] = datetime.now(timezone.utc)
            ref.update(patch)
            return jsonify({'success': True, 'id': plan_id}), 200
        except Exception:
            app.logger.exception('Teacher lesson plan update failed')
            return jsonify({'error': 'Unable to update lesson plan.'}), 500

    @app.get('/api/teacher/exams')
    def teacher_exams():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            docs = db.collection('exams').where('teacherUid', '==', uid).stream()
            rows = []
            for d in docs:
                x = d.to_dict() or {}
                attempts = db.collection('examAttempts').where('examId', '==', d.id).stream()
                submitted = []
                for a in attempts:
                    av = a.to_dict() or {}
                    if av.get('status') != 'submitted':
                        continue
                    submitted.append({
                        'attemptId': a.id,
                        'studentUid': av.get('userId'),
                        'score': av.get('score', 0),
                        'totalPoints': av.get('totalPoints', 0),
                        'percentage': av.get('percentage', 0),
                        'weakTopics': av.get('weakTopics') or [],
                        'submittedAt': av.get('submittedAt').isoformat() if hasattr(av.get('submittedAt'), 'isoformat') else None
                    })
                rows.append({
                    'id': d.id, 'title': x.get('title', 'Exam'), 'className': x.get('className', ''),
                    'durationMinutes': x.get('durationMinutes', 30),
                    'questionCount': len(x.get('questions') or []),
                    'submittedCount': len(submitted), 'attempts': submitted
                })
            rows.sort(key=lambda x: str(x.get('title', '')).lower())

            # Aggregate analytics from the same server-authoritative submitted attempts.
            all_attempts = [a for row in rows for a in row.get('attempts', [])]
            percentages = []
            pass_count = 0
            fail_count = 0
            weak_counts = {}
            student_stats = {}
            for attempt in all_attempts:
                try:
                    pct = float(attempt.get('percentage', 0) or 0)
                    percentages.append(pct)
                    if pct >= 50:
                        pass_count += 1
                    else:
                        fail_count += 1
                except (TypeError, ValueError):
                    pass
                for topic in attempt.get('weakTopics') or []:
                    topic = str(topic).strip()
                    if topic:
                        weak_counts[topic] = weak_counts.get(topic, 0) + 1
                uid_key = str(attempt.get('studentUid') or 'Student')
                item = student_stats.setdefault(uid_key, {'sum': 0.0, 'attempts': 0})
                try:
                    item['sum'] += float(attempt.get('percentage', 0) or 0)
                    item['attempts'] += 1
                except (TypeError, ValueError):
                    pass

            student_performance = []
            for student_uid, item in student_stats.items():
                if item['attempts']:
                    student_performance.append({
                        'studentUid': student_uid,
                        'averagePercentage': round(item['sum'] / item['attempts'], 1),
                        'attempts': item['attempts']
                    })
            student_performance.sort(key=lambda x: x['averagePercentage'], reverse=True)
            analytics = {
                'totalExams': len(rows),
                'totalSubmissions': len(all_attempts),
                'averagePercentage': round(sum(percentages) / len(percentages), 1) if percentages else None,
                'passRate': round(pass_count / len(all_attempts) * 100, 1) if all_attempts else None,
                'passCount': pass_count,
                'failCount': fail_count,
                'topWeakTopics': [
                    {'topic': topic, 'count': count}
                    for topic, count in sorted(weak_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
                ],
                'studentPerformance': student_performance[:50]
            }
            return jsonify({'exams': rows, 'analytics': analytics}), 200
        except Exception:
            app.logger.exception('Teacher exams load failed')
            return jsonify({'error': 'Unable to load exam results.'}), 500

    @app.post('/api/teacher/exams/<exam_id>/publish')
    def teacher_publish_exam(exam_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error': 'Approved teacher access required.'}), 403
            ref = db.collection('exams').document(str(exam_id)[:150])
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Exam not found.'}), 404
            data = snap.to_dict() or {}
            if data.get('teacherUid') != uid:
                return jsonify({'error': 'You cannot publish this exam.'}), 403
            if not db.collection('examKeys').document(ref.id).get().exists:
                return jsonify({'error': 'Exam answer key is missing.'}), 409
            ref.update({'status': 'published', 'updatedAt': datetime.now(timezone.utc)})
            return jsonify({'success': True, 'examId': ref.id, 'status': 'published'}), 200
        except Exception:
            app.logger.exception('Teacher exam publish failed')
            return jsonify({'error': 'Unable to publish exam.'}), 500

    @app.post('/api/teacher/exams')
    def teacher_create_exam():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            body = request.get_json(silent=True) or {}
            title = str(body.get('title', '')).strip()[:160]
            questions = body.get('questions', [])
            pass_mark = max(0, min(100, float(body.get('passMark', 50))))
            max_attempts = max(1, min(20, int(body.get('maxAttempts', 1))))
            if not title or not isinstance(questions, list) or not questions or len(questions) > 100:
                return jsonify({"error": "Exam title and 1–100 questions are required."}), 400
            clean_questions, answers, points, topics = [], {}, {}, {}
            for i, original in enumerate(questions):
                if not isinstance(original, dict):
                    return jsonify({"error": f"Question {i+1} is invalid."}), 400
                question = str(original.get('question', '')).strip()[:2000]
                answer = str(original.get('answer', '')).strip()[:20]
                qtype = str(original.get('type', 'mcq')).strip().lower()
                if qtype not in ('mcq', 'true_false'):
                    return jsonify({"error": f"Question {i+1} type must be mcq or true_false."}), 400
                opts = original.get('options', {})
                if qtype == 'true_false':
                    opts = {'A': 'True', 'B': 'False'}
                    if answer.upper() in ('TRUE', 'T'): answer = 'A'
                    elif answer.upper() in ('FALSE', 'F'): answer = 'B'
                if not question or not answer or not isinstance(opts, dict) or not opts:
                    return jsonify({"error": f"Question {i+1} is incomplete."}), 400
                if answer not in opts:
                    return jsonify({"error": f"Question {i+1} has an invalid correct answer."}), 400
                pts = max(1, min(100, int(original.get('points', 1))))
                topic = str(original.get('topic', 'General')).strip()[:80] or 'General'
                clean_questions.append({"question": question, "type": qtype, "topic": topic, "options": {str(k): str(v)[:500] for k,v in opts.items()}, "points": pts})
                answers[str(i)] = answer; points[str(i)] = pts; topics[str(i)] = topic
            now = datetime.now(timezone.utc)
            total_points = sum(int(q.get('points', 1)) for q in clean_questions)
            exam_ref = db.collection('exams').document()
            exam_ref.set({
                'title': title, 'className': str(body.get('className', '')).strip()[:20],
                'durationMinutes': max(1, min(240, int(body.get('durationMinutes', 30)))),
                'passMark': pass_mark, 'maxAttempts': max_attempts, 'totalPoints': total_points,
                'questions': clean_questions, 'teacherUid': uid, 'status': 'draft', 'createdAt': now, 'updatedAt': now
            })
            db.collection('examKeys').document(exam_ref.id).set({'examId': exam_ref.id, 'teacherUid': uid, 'answers': answers, 'points': points, 'topics': topics, 'passMark': pass_mark, 'createdAt': now})
            return jsonify({"success": True, "examId": exam_ref.id}), 201
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid numeric exam value."}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
