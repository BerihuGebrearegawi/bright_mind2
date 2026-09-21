"""V31.87: Student profile completion (age, school, guardian/parent contact,
education level, address) and the admin-facing per-student detail/history
view. Additive - the existing name/class/phone/region fields (set at signup
and via /api/student/region) are untouched; this only adds the remaining
fields and a single endpoint that lets admin see one student's full picture
without piecing it together from multiple collections manually.
"""
from datetime import datetime, timezone
from flask import request, jsonify

EDUCATION_LEVELS = ["Pre-primary", "Primary (1-8)", "Secondary (9-10)", "Preparatory (11-12)", "TVET/College", "University"]


def register_student_profile_routes(app, require_user, require_admin, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _clean(v, limit=300):
        return str(v or "").strip()[:limit]

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    @app.get('/api/student/profile-details')
    def student_profile_details_get():
        ok, detail = require_user()
        if not ok: return detail
        try:
            db = _db(); uid = detail.get('uid')
            snap = db.collection('users').document(uid).get()
            u = (snap.to_dict() or {}) if snap.exists else {}
            return jsonify({'success': True, 'profile': {
                'name': u.get('name') or u.get('displayName') or '',
                'age': u.get('age') or '',
                'className': u.get('class') or u.get('className') or '',
                'schoolName': u.get('schoolName') or '',
                'educationLevel': u.get('educationLevel') or '',
                'guardianName': u.get('guardianName') or '',
                'guardianPhone': u.get('guardianPhone') or '',
                'address': u.get('address') or '',
                'region': u.get('region') or '',
                'zone': u.get('zone') or '',
                'woreda': u.get('woreda') or '',
                'kebele': u.get('kebele') or '',
                'learningMode': u.get('learningMode') or 'Regular',
            }, 'educationLevels': EDUCATION_LEVELS}), 200
        except Exception:
            app.logger.exception('Student profile details load failed')
            return jsonify({'error': 'Unable to load your profile.'}), 500

    @app.post('/api/student/profile-details')
    def student_profile_details_save():
        ok, detail = require_user()
        if not ok: return detail
        try:
            body = request.get_json(silent=True) or {}
            age_raw = body.get('age')
            age = None
            if age_raw not in (None, ''):
                try:
                    age = max(3, min(30, int(age_raw)))
                except (ValueError, TypeError):
                    return jsonify({'error': 'Age must be a whole number.'}), 400
            education_level = _clean(body.get('educationLevel'), 40)
            if education_level and education_level not in EDUCATION_LEVELS:
                return jsonify({'error': 'Choose a valid education level.', 'educationLevels': EDUCATION_LEVELS}), 400
            update = {
                'schoolName': _clean(body.get('schoolName'), 200),
                'educationLevel': education_level,
                'guardianName': _clean(body.get('guardianName'), 150),
                'guardianPhone': _clean(body.get('guardianPhone'), 30),
                'address': _clean(body.get('address'), 300),
                'zone': _clean(body.get('zone'), 100),
                'woreda': _clean(body.get('woreda'), 100),
                'kebele': _clean(body.get('kebele'), 100),
                'updatedAt': datetime.now(timezone.utc),
            }
            if age is not None:
                update['age'] = age
            db = _db(); uid = detail.get('uid')
            db.collection('users').document(uid).set(update, merge=True)
            return jsonify({'success': True}), 200
        except Exception:
            app.logger.exception('Student profile details save failed')
            return jsonify({'error': 'Unable to save your profile.'}), 500

    @app.delete('/api/admin/users/<user_uid>')
    def admin_delete_user(user_uid):
        """Remove an unneeded/unwanted account (student, teacher, or parent):
        the Firebase Auth account plus their Firestore profile and teacher
        application record if any. Requires admin. Irreversible."""
        ok, detail = require_admin()
        if not ok: return detail
        try:
            uid = _clean(user_uid, 128)
            if not uid:
                return jsonify({'error': 'A user id is required.'}), 400
            if uid == detail.get('uid'):
                return jsonify({'error': 'You cannot delete your own admin account from here.'}), 400
            db = _db()
            snap = db.collection('users').document(uid).get()
            if not snap.exists:
                return jsonify({'error': 'User not found.'}), 404
            user_data = snap.to_dict() or {}
            if user_data.get('isAdmin'):
                return jsonify({'error': 'Admin accounts cannot be deleted from this panel.'}), 400
            try:
                from firebase_admin import auth as firebase_auth
                firebase_auth.delete_user(uid)
            except Exception:
                pass  # already gone from Auth, or Auth is unreachable - Firestore cleanup still proceeds
            db.collection('users').document(uid).delete()
            db.collection('teachers').document(uid).delete()
            db.collection('teacherRequests').document(uid).delete()
            return jsonify({'success': True, 'uid': uid}), 200
        except Exception:
            app.logger.exception('Admin user deletion failed')
            return jsonify({'error': 'Unable to delete this user.'}), 500

    @app.post('/api/admin/users/<user_uid>/suspend')
    def admin_suspend_user(user_uid):
        """Temporarily block a student/teacher/parent account from using the
        service WITHOUT deleting anything - reversible, unlike the delete
        endpoint above. Body: {"suspended": true/false, "reason": "..."}.

        The actual enforcement lives in app.py's _require_user_bearer() (via
        _check_not_suspended), which every route module in the app shares -
        so flipping this flag here blocks every authenticated API call
        anywhere in BMT, not just one page. This endpoint only flips the
        flag and writes an audit trail; it does no enforcement itself.
        """
        ok, detail = require_admin()
        if not ok: return detail
        try:
            body = request.get_json(silent=True) or {}
            suspended = bool(body.get('suspended', True))
            reason = _clean(body.get('reason'), 300)
            uid = _clean(user_uid, 128)
            if not uid:
                return jsonify({'error': 'A user id is required.'}), 400
            if uid == detail.get('uid'):
                return jsonify({'error': 'You cannot suspend your own admin account.'}), 400
            db = _db()
            snap = db.collection('users').document(uid).get()
            if not snap.exists:
                return jsonify({'error': 'User not found.'}), 404
            user_data = snap.to_dict() or {}
            if user_data.get('isAdmin'):
                return jsonify({'error': 'Admin accounts cannot be suspended from this panel.'}), 400
            update = {
                'isSuspended': suspended,
                'suspendedAt': datetime.now(timezone.utc) if suspended else None,
                'suspendedBy': detail.get('uid') if suspended else None,
                'suspendReason': reason if suspended else '',
            }
            db.collection('users').document(uid).set(update, merge=True)
            if suspended:
                try:
                    # Best-effort only: invalidates any ID token the user
                    # already has in hand right away, instead of waiting for
                    # it to naturally expire (~1hr) before the isSuspended
                    # check above ever gets a chance to run. The Firestore
                    # flag above is the actual, authoritative gate either way.
                    from firebase_admin import auth as firebase_auth
                    firebase_auth.revoke_refresh_tokens(uid)
                except Exception:
                    pass
            return jsonify({'success': True, 'uid': uid, 'isSuspended': suspended}), 200
        except Exception:
            app.logger.exception('Admin user suspend/unsuspend failed')
            return jsonify({'error': 'Unable to update this account.'}), 500

    @app.get('/api/admin/students/<student_uid>')
    def admin_student_detail(student_uid):
        """Full one-student picture for the admin dashboard: profile fields +
        payment history + challenge attempts + awards + support tickets +
        linked parent - so admin isn't piecing it together across pages."""
        ok, detail = require_admin()
        if not ok: return detail
        try:
            db = _db(); uid = _clean(student_uid, 128)
            snap = db.collection('users').document(uid).get()
            if not snap.exists:
                return jsonify({'error': 'Student not found.'}), 404
            u = snap.to_dict() or {}

            payments = []
            for d in db.collection('payments').where('uid', '==', uid).limit(200).stream():
                x = d.to_dict() or {}
                payments.append({'id': d.id, 'plan': x.get('plan'), 'amount': x.get('amount'), 'status': x.get('status'), 'provider': x.get('provider'), 'createdAt': _iso(x.get('createdAt'))})

            attempts = []
            for d in db.collection('challengeAttempts').where('userId', '==', uid).limit(200).stream():
                x = d.to_dict() or {}
                attempts.append({'id': d.id, 'challengeId': x.get('challengeId'), 'status': x.get('status'), 'score': x.get('score'), 'percentage': x.get('percentage'), 'submittedAt': _iso(x.get('submittedAt'))})

            awards = []
            for d in db.collection('awardLedger').where('userId', '==', uid).limit(100).stream():
                x = d.to_dict() or {}
                awards.append({'id': d.id, 'challengeId': x.get('challengeId'), 'rank': x.get('rank'), 'prizeAmount': x.get('prizeAmount'), 'isScholarship': bool(x.get('isScholarship')), 'scholarshipLabel': x.get('scholarshipLabel'), 'finalizedAt': _iso(x.get('finalizedAt'))})

            tickets = []
            for d in db.collection('supportTickets').where('studentUid', '==', uid).limit(100).stream():
                x = d.to_dict() or {}
                tickets.append({'id': d.id, 'subject': x.get('subject'), 'status': x.get('status'), 'createdAt': _iso(x.get('createdAt'))})

            parent_links = []
            for d in db.collection('parentChildLinks').where('childUid', '==', uid).limit(20).stream():
                x = d.to_dict() or {}
                parent_links.append({'parentUid': x.get('parentUid'), 'linkedAt': _iso(x.get('linkedAt') or x.get('createdAt'))})

            profile = {
                'uid': uid,
                'name': u.get('name') or u.get('displayName') or '',
                'email': u.get('email') or '',
                'phone': u.get('phone') or u.get('phoneNumber') or '',
                'age': u.get('age') or '',
                'className': u.get('class') or u.get('className') or '',
                'schoolName': u.get('schoolName') or '',
                'educationLevel': u.get('educationLevel') or '',
                'region': u.get('region') or '',
                'zone': u.get('zone') or '',
                'woreda': u.get('woreda') or '',
                'kebele': u.get('kebele') or '',
                'guardianName': u.get('guardianName') or '',
                'guardianPhone': u.get('guardianPhone') or '',
                'address': u.get('address') or '',
                'isPaid': bool(u.get('isPaid')),
                'learningMode': u.get('learningMode') or 'Regular',
                'registeredAt': _iso(u.get('registeredAt')),
                'lastLoginAt': _iso(u.get('lastLoginAt') or u.get('lastSeenAt')),
                'progress': u.get('progress') or {},
            }
            return jsonify({'success': True, 'profile': profile, 'payments': payments,
                             'challengeAttempts': attempts, 'awards': awards,
                             'supportTickets': tickets, 'parentLinks': parent_links}), 200
        except Exception:
            app.logger.exception('Admin student detail failed')
            return jsonify({'error': 'Unable to load this student.'}), 500

    @app.post('/api/admin/students/<student_uid>/learning-mode')
    def admin_set_student_learning_mode(student_uid):
        """V31.108 Phase B: the only place a student's learningMode is ever
        written. Before this endpoint existed, targeting_access.py could read
        user['learningMode'] but nothing could ever set it to 'Distance', so
        Distance targeting was unreachable in practice. Additive - does not
        touch any other profile field.
        """
        ok, detail = require_admin()
        if not ok: return detail
        try:
            body = request.get_json(silent=True) or {}
            mode = str(body.get('learningMode') or '').strip()
            if mode not in {'Regular', 'Distance'}:
                return jsonify({'error': "learningMode must be 'Regular' or 'Distance'."}), 400
            db = _db(); uid = _clean(student_uid, 128)
            ref = db.collection('users').document(uid)
            if not ref.get().exists:
                return jsonify({'error': 'Student not found.'}), 404
            ref.set({'learningMode': mode, 'updatedAt': datetime.now(timezone.utc)}, merge=True)
            return jsonify({'success': True, 'uid': uid, 'learningMode': mode}), 200
        except Exception:
            app.logger.exception('Admin set student learning mode failed')
            return jsonify({'error': 'Unable to update learning mode.'}), 500
