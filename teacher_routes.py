"""Server-authoritative Teacher Portal APIs.

Teacher writes go through Flask/Admin SDK so a client cannot impersonate an
approved teacher or write an arbitrary teacherUid. Firestore rules remain a
second defensive layer for direct client access.
"""
from datetime import datetime, timezone
from flask import request, jsonify


def register_teacher_routes(app, require_user, require_admin, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

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
            docs=db.collection('assignments').where('status','==','published').stream()
            items=[]
            for d in docs:
                a={'id':d.id,**(d.to_dict() or {})}
                if not grade or str(a.get('className','')).strip()==grade: items.append(a)
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
            if not profile:
                return jsonify({"approved": False}), 200
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
            allowed_subjects = {
                'Mathematics', 'Physics', 'Chemistry', 'Biology', 'English',
                'Amharic', 'History', 'Geography', 'Economics'
            }
            subjects = [str(x).strip() for x in subjects if str(x).strip()]
            invalid_subjects = [x for x in subjects if x not in allowed_subjects]
            if invalid_subjects:
                return jsonify({"error": "One or more selected subjects are not supported."}), 400
            subjects = subjects[:10]
            classes = [str(x).strip() for x in classes if str(x).strip()]
            allowed_classes = {str(x) for x in range(3, 13)}
            if any(x not in allowed_classes for x in classes):
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
            if not _teacher(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            body = request.get_json(silent=True) or {}
            title = str(body.get('title', '')).strip()[:160]
            class_name = str(body.get('className', '')).strip()[:20]
            description = str(body.get('description', '')).strip()[:2000]
            allowed = {str(i) for i in range(3, 13)}
            if not title or class_name not in allowed:
                return jsonify({"error": "Valid course title and grade are required."}), 400
            now = datetime.now(timezone.utc)
            ref = db.collection('courses').document()
            ref.set({
                'title': title, 'className': class_name, 'description': description,
                'teacherUid': uid, 'teacherEmail': detail.get('email', ''),
                'status': 'published', 'createdAt': now, 'updatedAt': now
            })
            return jsonify({"success": True, "course": {"id": ref.id, "title": title, "className": class_name, "description": description}}), 201
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
            title = str(body.get('title', '')).strip()[:160]
            content_type = str(body.get('contentType', 'video')).strip().lower()
            url = str(body.get('url', '')).strip()[:2000]
            description = str(body.get('description', '')).strip()[:2000]
            if not course_id or not title or not url:
                return jsonify({"error": "Course, lesson title and content URL are required."}), 400
            course = db.collection('courses').document(course_id).get()
            if not course.exists or (course.to_dict() or {}).get('teacherUid') != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            if content_type not in {'youtube', 'telegram', 'pdf', 'drive', 'cloudinary', 'link'}:
                return jsonify({"error": "Unsupported lesson content type."}), 400
            now = datetime.now(timezone.utc)
            ref = db.collection('lessons').document()
            ref.set({
                'courseId': course_id, 'teacherUid': uid, 'title': title,
                'contentType': content_type, 'url': url, 'description': description,
                'status': 'published', 'createdAt': now, 'updatedAt': now
            })
            return jsonify({"success": True, "lessonId": ref.id}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.post('/api/teacher/assignments')
    def teacher_create_assignment():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail['uid']
            if not _teacher(db, uid):
                return jsonify({'error':'Approved teacher access required.'}), 403
            body=request.get_json(silent=True) or {}
            title=str(body.get('title','')).strip()[:160]
            description=str(body.get('description','')).strip()[:4000]
            grade=str(body.get('className','')).strip()[:20]
            due=str(body.get('dueAt','')).strip()[:80]
            course_id=str(body.get('courseId','')).strip()
            if not title or grade not in {str(i) for i in range(3,13)}:
                return jsonify({'error':'Assignment title and valid grade are required.'}),400
            if course_id:
                c=db.collection('courses').document(course_id).get()
                if not c.exists or (c.to_dict() or {}).get('teacherUid') != uid:
                    return jsonify({'error':'Course not found or not owned by this teacher.'}),403
            now=datetime.now(timezone.utc)
            ref=db.collection('assignments').document()
            ref.set({'title':title,'description':description,'className':grade,'courseId':course_id or None,'teacherUid':uid,'teacherEmail':detail.get('email',''),'dueAt':due or None,'status':'published','createdAt':now,'updatedAt':now})
            return jsonify({'success':True,'assignment':{'id':ref.id,'title':title,'className':grade,'dueAt':due or None}}),201
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
