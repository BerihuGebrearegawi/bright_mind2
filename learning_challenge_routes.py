"""BMT Academic Challenge server-authoritative routes.

B owns Question Bank, Practice, Academic/Timed Challenge, grading,
qualification, leaderboard, awards, certificates, and Telegram Challenge.
Digital Library hierarchy and Reader APIs are owned by Cloud-B and are not
duplicated or accessed through parallel Firestore collections here.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import re
import random

from flask import jsonify, request

MAX_TEXT = 12000
MAX_OPTIONS = 8
MAX_QUESTIONS_PER_CHALLENGE = 100
ALLOWED_ROLES = {"admin", "teacher", "student"}


def _clean(value, limit=500):
    return str(value or "").strip()[:limit]


def _safe_key(value):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(value or ""))[:150]


def _utc_now():
    return datetime.now(timezone.utc)


def _as_dt(value):
    if hasattr(value, "timestamp"):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _role(db, uid, detail):
    user = {}
    snap = db.collection("users").document(uid).get()
    if snap.exists:
        user = snap.to_dict() or {}
    role = str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()
    if role not in ALLOWED_ROLES:
        role = "student"
    return role, user


def _teacher_approved(db, uid):
    snap = db.collection("teachers").document(uid).get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    return bool(data.get("approved") or data.get("status") == "approved")


def _can_manage(role, approved_teacher=False):
    return role == "admin" or (role == "teacher" and approved_teacher)


def _question_public(q):
    return {
        "id": q.get("id"),
        "question": q.get("question", ""),
        "type": q.get("type", "mcq"),
        "options": q.get("options") or {},
        "points": q.get("points", 1),
        "subject": q.get("subject", ""),
        "grade": q.get("grade", ""),
        "domain": q.get("domain", ""),
        "difficulty": q.get("difficulty", "medium"),
        "bookId": q.get("bookId", ""),
        "chapterId": q.get("chapterId", ""),
        "subchapterId": q.get("subchapterId", ""),
        "sourceRef": q.get("sourceRef", ""),
    }


def _question_payload(body, uid):
    question = _clean(body.get("question"), 4000)
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    options = {str(k)[:8]: _clean(v, 1000) for k, v in list(options.items())[:MAX_OPTIONS] if _clean(v, 1000)}
    answer = _clean(body.get("correctAnswer"), 20).upper()
    if not question or len(options) < 2 or not answer or answer not in {str(k).upper() for k in options}:
        raise ValueError("Question, at least two options, and a valid correctAnswer are required.")
    points = max(0.1, min(100.0, float(body.get("points", 1) or 1)))
    return {
        "question": question,
        "type": _clean(body.get("type") or "mcq", 30),
        "options": options,
        "correctAnswer": answer,
        "points": points,
        "subject": _clean(body.get("subject"), 80),
        "grade": _clean(body.get("grade"), 20),
        "domain": _clean(body.get("domain"), 100),
        "difficulty": _clean(body.get("difficulty") or "medium", 20).lower(),
        "bookId": _clean(body.get("bookId"), 150),
        "chapterId": _clean(body.get("chapterId"), 150),
        "subchapterId": _clean(body.get("subchapterId"), 150),
        "sourceRef": _clean(body.get("sourceRef"), 300),
        "createdBy": uid,
    }


def _challenge_public(challenge):
    return {
        "id": challenge.get("id"),
        "title": challenge.get("title", "Academic Challenge"),
        "grade": challenge.get("grade", ""),
        "subject": challenge.get("subject", ""),
        "domain": challenge.get("domain", ""),
        "durationMinutes": challenge.get("durationMinutes", 20),
        "questionCount": len(challenge.get("questionIds") or []),
        "status": challenge.get("status", "draft"),
        "startsAt": challenge.get("startsAt"),
        "endsAt": challenge.get("endsAt"),
        "season": challenge.get("season", ""),
        "roundNumber": int(challenge.get("roundNumber", 1) or 1),
        "qualificationCount": int(challenge.get("qualificationCount", 0) or 0),
    }


def _attempt_id(uid, challenge_id):
    raw = f"{uid}:{challenge_id}:{_utc_now().timestamp()}".encode()
    return hashlib.sha256(raw).hexdigest()[:40]


def register_learning_challenge_routes(app, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _identity():
        ok, detail = require_user()
        if not ok:
            return None, detail
        return detail, None

    # ------------------------- Chapter Practice -------------------------
    @app.post("/api/practice/start")
    def start_chapter_practice():
        """Create a short practice set from one verified chapter/subchapter.

        Practice is deliberately separate from competitive attempts: it uses only
        approved questions, never sends answer keys, and stores the selected
        question IDs server-side so the client cannot swap in unrelated items.
        """
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]
            role, _ = _role(db, uid, detail)
            if role != "student":
                return jsonify({"error": "Only student accounts can start practice."}), 403
            body = request.get_json(silent=True) or {}
            book_id = _safe_key(body.get("bookId")); chapter_id = _safe_key(body.get("chapterId"))
            subchapter_id = _safe_key(body.get("subchapterId"))
            subject = _clean(body.get("subject"), 80).lower()
            difficulty = _clean(body.get("difficulty"), 20).lower()
            try:
                count = max(1, min(30, int(body.get("count", 10) or 10)))
            except (TypeError, ValueError):
                return jsonify({"error": "count must be an integer."}), 400
            if not book_id or not chapter_id:
                return jsonify({"error": "bookId and chapterId are required."}), 400
            candidates = []
            for snap in db.collection("questionBank").where("chapterId", "==", chapter_id).limit(300).stream():
                q = snap.to_dict() or {}
                if q.get("status") != "approved":
                    continue
                if str(q.get("bookId") or "") != book_id:
                    continue
                if subchapter_id and str(q.get("subchapterId") or "") != subchapter_id:
                    continue
                if subject and subject != str(q.get("subject") or "").lower():
                    continue
                if difficulty and difficulty != str(q.get("difficulty") or "").lower():
                    continue
                q["id"] = snap.id
                candidates.append(q)
            if not candidates:
                return jsonify({"error": "No approved practice questions are available for this location."}), 404
            random.shuffle(candidates)
            selected = candidates[:count]
            session_id = hashlib.sha256(f"practice:{uid}:{chapter_id}:{_utc_now().timestamp()}".encode()).hexdigest()[:40]
            now = _utc_now()
            db.collection("practiceSessions").document(session_id).set({
                "userId": uid, "bookId": book_id, "chapterId": chapter_id,
                "subchapterId": subchapter_id, "questionIds": [q["id"] for q in selected],
                "status": "started", "startedAt": now, "updatedAt": now
            })
            questions = [_question_public(q) for q in selected]
            return jsonify({"success": True, "sessionId": session_id, "bookId": book_id,
                            "chapterId": chapter_id, "subchapterId": subchapter_id,
                            "questionCount": len(questions), "questions": questions}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Chapter practice start failed")
            return jsonify({"error": "Unable to start chapter practice."}), 500

    @app.post("/api/practice/submit")
    def submit_chapter_practice():
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]
            role, _ = _role(db, uid, detail)
            if role != "student":
                return jsonify({"error": "Only student accounts can submit practice."}), 403
            body = request.get_json(silent=True) or {}
            session_id = _safe_key(body.get("sessionId"))
            answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
            if not session_id or len(answers) > 30:
                return jsonify({"error": "sessionId and valid answers are required."}), 400
            ref = db.collection("practiceSessions").document(session_id); snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Practice session not found."}), 404
            session = snap.to_dict() or {}
            if session.get("userId") != uid:
                return jsonify({"error": "This practice session does not belong to you."}), 403
            if session.get("status") != "started":
                return jsonify({"error": "Practice session is already submitted."}), 409
            safe = {str(k)[:150]: _clean(v, 30).upper() for k, v in answers.items()}
            score = 0.0; total = 0.0; correct = 0; results = []
            for qid in session.get("questionIds") or []:
                qsnap = db.collection("questionBank").document(_safe_key(qid)).get()
                if not qsnap.exists: continue
                q = qsnap.to_dict() or {}; points = max(0.0, float(q.get("points", 1) or 1)); total += points
                chosen = str(safe.get(qid, "")).upper(); key = str(q.get("correctAnswer", "")).upper()
                ok = chosen == key
                if ok: score += points; correct += 1
                results.append({"questionId": qid, "selectedAnswer": chosen, "correct": ok,
                                "correctAnswer": key})
            percentage = round((score / total) * 100, 1) if total else 0.0
            ref.update({"status": "submitted", "answers": safe, "score": score, "totalPoints": total,
                        "percentage": percentage, "correctCount": correct,
                        "questionCount": len(results), "submittedAt": _utc_now(), "updatedAt": _utc_now()})
            return jsonify({"success": True, "score": score, "totalPoints": total,
                            "percentage": percentage, "correctCount": correct,
                            "questionCount": len(results), "results": results})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Chapter practice submission failed")
            return jsonify({"error": "Unable to submit chapter practice."}), 500

    # ------------------------- Question Bank -------------------------
    @app.get("/api/question-bank")
    def question_bank():
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]
            role, _ = _role(db, uid, detail)
            approved = _teacher_approved(db, uid)
            if not _can_manage(role, approved):
                return jsonify({"error": "Teacher or admin access required."}), 403
            subject = _clean(request.args.get("subject"), 80).lower()
            grade = _clean(request.args.get("grade"), 20)
            domain = _clean(request.args.get("domain"), 100).lower()
            status = _clean(request.args.get("status") or "approved", 30).lower()
            rows = []
            for snap in db.collection("questionBank").limit(500).stream():
                q = snap.to_dict() or {}; q["id"] = snap.id
                if subject and subject != str(q.get("subject", "")).lower(): continue
                if grade and grade != str(q.get("grade", "")): continue
                if domain and domain not in str(q.get("domain", "")).lower(): continue
                if status and status != str(q.get("status", "draft")).lower(): continue
                rows.append(_question_public(q))
            return jsonify({"questions": rows})
        except Exception:
            app.logger.exception("Question bank lookup failed")
            return jsonify({"error": "Unable to load question bank."}), 500

    @app.post("/api/question-bank")
    def create_question():
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]
            role, _ = _role(db, uid, detail); approved = _teacher_approved(db, uid)
            if not _can_manage(role, approved):
                return jsonify({"error": "Approved teacher or admin access required."}), 403
            body = request.get_json(silent=True) or {}
            q = _question_payload(body, uid)
            now = _utc_now(); q.update({"status": "draft", "createdAt": now, "updatedAt": now})
            ref = db.collection("questionBank").document(); ref.set(q)
            return jsonify({"success": True, "questionId": ref.id, "status": "draft"}), 201
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid question data."}), 400
        except Exception:
            app.logger.exception("Question creation failed")
            return jsonify({"error": "Unable to create question."}), 500

    @app.post("/api/question-bank/<question_id>/approve")
    def approve_question(question_id):
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]
            role, _ = _role(db, uid, detail); approved = _teacher_approved(db, uid)
            if not _can_manage(role, approved):
                return jsonify({"error": "Approved teacher or admin access required."}), 403
            ref = db.collection("questionBank").document(_safe_key(question_id)); snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Question not found."}), 404
            now = _utc_now(); ref.update({"status": "approved", "approvedBy": uid, "approvedAt": now, "updatedAt": now})
            return jsonify({"success": True, "questionId": ref.id, "status": "approved"})
        except Exception:
            app.logger.exception("Question approval failed")
            return jsonify({"error": "Unable to approve question."}), 500

    # ------------------------- Academic Challenge -------------------------
    @app.get("/api/challenges")
    def available_challenges():
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]; role, user = _role(db, uid, detail)
            if role != "student":
                return jsonify({"error": "Only student accounts can view challenges."}), 403
            grade = str(user.get("className") or user.get("class") or user.get("grade") or "").strip()
            now = _utc_now(); rows = []
            for snap in db.collection("academicChallenges").where("status", "==", "published").limit(100).stream():
                c = snap.to_dict() or {}; c["id"] = snap.id
                if c.get("grade") and str(c.get("grade")) != grade: continue
                start = _as_dt(c.get("startsAt")); end = _as_dt(c.get("endsAt"))
                if start and now < start: continue
                if end and now > end: continue
                rows.append(_challenge_public(c))
            rows.sort(key=lambda x: str(x.get("title", "")).lower())
            return jsonify({"challenges": rows, "grade": grade})
        except Exception:
            app.logger.exception("Challenge listing failed")
            return jsonify({"error": "Unable to load challenges."}), 500

    @app.post("/api/teacher/challenges")
    def create_challenge():
        detail, error = _identity()
        if error: return error
        try:
            db = _db(); uid = detail["uid"]; role, _ = _role(db, uid, detail); approved = _teacher_approved(db, uid)
            if not _can_manage(role, approved):
                return jsonify({"error": "Approved teacher or admin access required."}), 403
            body = request.get_json(silent=True) or {}
            ids = body.get("questionIds") if isinstance(body.get("questionIds"), list) else []
            ids = [_safe_key(x) for x in ids][:MAX_QUESTIONS_PER_CHALLENGE]
            ids = [x for x in ids if x]
            if not ids:
                return jsonify({"error": "At least one questionId is required."}), 400
            if len(set(ids)) != len(ids):
                return jsonify({"error": "Duplicate questionIds are not allowed."}), 409
            # Verify every question exists and is approved before publication.
            verified = []
            for qid in ids:
                snap = db.collection("questionBank").document(qid).get()
                if snap.exists and (snap.to_dict() or {}).get("status") == "approved": verified.append(qid)
            if len(verified) != len(ids):
                return jsonify({"error": "Every challenge question must exist and be approved."}), 409
            duration = max(1, min(240, int(body.get("durationMinutes", 20) or 20)))
            round_number = max(1, min(7, int(body.get("roundNumber", 1) or 1)))
            qualification_count = max(0, min(100000, int(body.get("qualificationCount", 0) or 0)))
            season = _clean(body.get("season"), 60)
            starts_at = _as_dt(body.get("startsAt")) if body.get("startsAt") else None
            ends_at = _as_dt(body.get("endsAt")) if body.get("endsAt") else None
            if body.get("startsAt") and (not starts_at or starts_at.tzinfo is None or starts_at.utcoffset() is None):
                return jsonify({"error": "startsAt must be a timezone-aware ISO timestamp."}), 400
            if body.get("endsAt") and (not ends_at or ends_at.tzinfo is None or ends_at.utcoffset() is None):
                return jsonify({"error": "endsAt must be a timezone-aware ISO timestamp."}), 400
            if starts_at and ends_at and ends_at <= starts_at:
                return jsonify({"error": "endsAt must be later than startsAt."}), 400
            now = _utc_now()
            c = {"title": _clean(body.get("title") or "Academic Challenge", 200), "grade": _clean(body.get("grade"), 20), "subject": _clean(body.get("subject"), 80), "domain": _clean(body.get("domain"), 100), "durationMinutes": duration, "questionIds": verified, "status": "draft", "startsAt": starts_at, "endsAt": ends_at, "season": season, "roundNumber": round_number, "qualificationCount": qualification_count, "createdBy": uid, "createdAt": now, "updatedAt": now}
            ref = db.collection("academicChallenges").document(); ref.set(c)
            return jsonify({"success": True, "challengeId": ref.id, "status": "draft"}), 201
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid challenge data."}), 400
        except Exception:
            app.logger.exception("Challenge creation failed")
            return jsonify({"error": "Unable to create challenge."}), 500

    @app.post("/api/teacher/challenges/<challenge_id>/publish")
    def publish_challenge(challenge_id):
        detail, error = _identity()
        if error: return error
        try:
            db = _db(); uid = detail["uid"]; role, _ = _role(db, uid, detail); approved = _teacher_approved(db, uid)
            if not _can_manage(role, approved): return jsonify({"error": "Approved teacher or admin access required."}), 403
            ref = db.collection("academicChallenges").document(_safe_key(challenge_id)); snap = ref.get()
            if not snap.exists: return jsonify({"error": "Challenge not found."}), 404
            c = snap.to_dict() or {}; ids = c.get("questionIds") or []
            if c.get("status") != "draft":
                return jsonify({"error": "Only DRAFT challenges can be published."}), 409
            if not ids: return jsonify({"error": "Challenge has no questions."}), 409
            for qid in ids:
                q = db.collection("questionBank").document(_safe_key(qid)).get()
                if not q.exists or (q.to_dict() or {}).get("status") != "approved":
                    return jsonify({"error": "Challenge contains an unapproved question."}), 409
            now = _utc_now(); ref.update({"status": "published", "publishedBy": uid, "publishedAt": now, "updatedAt": now})
            return jsonify({"success": True, "challengeId": ref.id, "status": "published"})
        except Exception:
            app.logger.exception("Challenge publication failed")
            return jsonify({"error": "Unable to publish challenge."}), 500

    @app.post("/api/challenges/start")
    def start_challenge():
        detail, error = _identity()
        if error: return error
        try:
            db = _db(); uid = detail["uid"]; role, user = _role(db, uid, detail)
            if role != "student": return jsonify({"error": "Only student accounts can take challenges."}), 403
            body = request.get_json(silent=True) or {}; challenge_id = _safe_key(body.get("challengeId"))
            ref = db.collection("academicChallenges").document(challenge_id); snap = ref.get()
            if not snap.exists: return jsonify({"error": "Challenge not found."}), 404
            c = snap.to_dict() or {}
            c["id"] = snap.id
            if c.get("status") not in ("published", "active"): return jsonify({"error": "Challenge is not available."}), 403
            grade = str(user.get("className") or user.get("class") or user.get("grade") or "").strip()
            if c.get("grade") and str(c.get("grade")) != grade: return jsonify({"error": "This challenge is not assigned to your class."}), 403
            now = _utc_now(); start = _as_dt(c.get("startsAt")); end = _as_dt(c.get("endsAt"))
            if start and now < start: return jsonify({"error": "Challenge is not open yet."}), 403
            if end and now > end:
                # The challenge lifecycle is server-authoritative: once the
                # configured end time passes, a published/active challenge is
                # closed and can no longer accept starts.
                if c.get("status") != "closed":
                    ref.update({"status": "closed", "closedAt": now, "closedReason": "schedule_expired", "updatedAt": now})
                return jsonify({"error": "Challenge is closed."}), 403
            if c.get("status") == "published":
                # A challenge becomes ACTIVE only when its scheduled window is
                # actually entered by the server, never by a client status field.
                ref.update({"status": "active", "activatedAt": now, "updatedAt": now})
                c["status"] = "active"

            # Paid challenges require a server-verified entry. The client cannot
            # bypass payment by calling /start directly. Free challenges remain
            # available without an entry record.
            entry_fee = c.get("entryFee", 0)
            try:
                entry_fee = float(entry_fee or 0)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid challenge entry fee configuration."}), 500
            if entry_fee < 0 or entry_fee > 1000000:
                return jsonify({"error": "Invalid challenge entry fee configuration."}), 500
            if entry_fee > 0:
                verified = db.collection("challengeEntries").where("challengeId", "==", challenge_id).where("userId", "==", uid).where("status", "==", "verified").limit(1).stream()
                if next(iter(verified), None) is None:
                    return jsonify({"error": "Verified challenge entry is required before starting."}), 402

            # Deterministic attempt identity closes the start-replay race: the
            # same student/challenge can never create multiple attempts by
            # repeatedly calling /start.
            attempt_id = hashlib.sha256(f"{uid}:{challenge_id}".encode("utf-8")).hexdigest()[:40]
            aref = db.collection("challengeAttempts").document(attempt_id)
            asnap = aref.get()
            if asnap.exists:
                a = asnap.to_dict() or {}
                if a.get("userId") != uid or a.get("challengeId") != challenge_id:
                    return jsonify({"error": "Attempt identity conflict."}), 409
                if a.get("status") == "started":
                    return _challenge_start_response(db, c, attempt_id, a.get("startedAt"), a.get("deadlineAt"),
                                                     questions=_public_questions_from_snapshot(a.get("questionSnapshot") or []))
                return jsonify({"error": "This challenge attempt has already been submitted."}), 409

            duration = max(1, min(240, int(c.get("durationMinutes", 20) or 20))); deadline = now + timedelta(minutes=duration)
            # Capture the exact published question set at start. A later edit/archive
            # of a question must never change an in-progress or finalized attempt.
            configured_ids = [_safe_key(x) for x in (c.get("questionIds") or []) if _safe_key(x)]
            if not configured_ids or len(set(configured_ids)) != len(configured_ids):
                return jsonify({"error": "Challenge question set is invalid."}), 409
            questions = []
            question_snapshot = []
            for qid in configured_ids:
                qsnap = db.collection("questionBank").document(qid).get()
                if not qsnap.exists:
                    return jsonify({"error": "Challenge question data is unavailable."}), 409
                q = qsnap.to_dict() or {}; q["id"] = qsnap.id
                if q.get("status") != "approved":
                    return jsonify({"error": "A published challenge question is no longer approved."}), 409
                public_q = _question_public(q)
                questions.append(public_q)
                question_snapshot.append({
                    "id": qid,
                    "question": public_q.get("question", ""),
                    "type": public_q.get("type", "mcq"),
                    "options": public_q.get("options") or {},
                    "points": max(0.0, float(q.get("points", 1) or 1)),
                    "correctAnswer": str(q.get("correctAnswer", "")).upper(),
                    "subject": public_q.get("subject", ""),
                    "grade": public_q.get("grade", ""),
                    "domain": public_q.get("domain", ""),
                    "difficulty": public_q.get("difficulty", "medium"),
                    "bookId": public_q.get("bookId", ""),
                    "chapterId": public_q.get("chapterId", ""),
                    "subchapterId": public_q.get("subchapterId", ""),
                })
            # Atomic creation closes the final start race: two concurrent start
            # requests cannot both create/replace the same deterministic attempt.
            from firebase_admin import firestore as _firestore
            attempt_payload = {"challengeId": challenge_id, "userId": uid, "status": "started",
                               "startedAt": now, "deadlineAt": deadline, "answers": {},
                               "updatedAt": now, "questionIds": configured_ids,
                               "questionSnapshot": question_snapshot,
                               "challengeVersion": str(c.get("version") or "1")}
            tx = db.transaction()
            @ _firestore.transactional
            def _create_once(transaction):
                current = aref.get(transaction=transaction)
                if current.exists:
                    existing = current.to_dict() or {}
                    if existing.get("userId") != uid or existing.get("challengeId") != challenge_id:
                        raise PermissionError("Attempt identity conflict.")
                    return False, existing
                transaction.create(aref, attempt_payload)
                return True, attempt_payload
            created, stored = _create_once(tx)
            if not created:
                if stored.get("status") == "started":
                    return _challenge_start_response(db, c, attempt_id, stored.get("startedAt"), stored.get("deadlineAt"),
                                                     questions=_public_questions_from_snapshot(stored.get("questionSnapshot") or []))
                return jsonify({"error": "This challenge attempt has already been submitted."}), 409
            return _challenge_start_response(db, c, attempt_id, now, deadline, questions), 201
        except Exception:
            app.logger.exception("Challenge start failed")
            return jsonify({"error": "Unable to start challenge."}), 500

    @app.post("/api/challenges/save")
    def save_challenge_answers():
        detail, error = _identity()
        if error: return error
        try:
            db = _db(); uid = detail["uid"]; role, _ = _role(db, uid, detail)
            if role != "student": return jsonify({"error": "Only student accounts can save challenge answers."}), 403
            body = request.get_json(silent=True) or {}
            challenge_id = _safe_key(body.get("challengeId")); attempt_id = _safe_key(body.get("attemptId"))
            answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
            if not challenge_id or not attempt_id or len(answers) > MAX_QUESTIONS_PER_CHALLENGE:
                return jsonify({"error": "challengeId, attemptId and valid answers are required."}), 400
            ref = db.collection("challengeAttempts").document(attempt_id); snap = ref.get()
            if not snap.exists: return jsonify({"error": "Attempt not found."}), 404
            attempt = snap.to_dict() or {}
            if attempt.get("userId") != uid or attempt.get("challengeId") != challenge_id:
                return jsonify({"error": "This attempt does not belong to you."}), 403
            if attempt.get("status") != "started": return jsonify({"error": "Attempt is not open."}), 409
            deadline = _as_dt(attempt.get("deadlineAt"))
            if deadline and _utc_now().timestamp() > deadline.timestamp() + 5:
                return jsonify({"error": "Challenge time has expired."}), 409
            allowed_ids = {str(x) for x in (attempt.get("questionIds") or [])}
            safe_answers = {str(k)[:150]: _clean(v, 30).upper() for k, v in answers.items() if str(k) in allowed_ids}
            from firebase_admin import firestore as _firestore
            @_firestore.transactional
            def _save(tx):
                current = ref.get(transaction=tx)
                if not current.exists:
                    raise ValueError("Attempt not found.")
                latest = current.to_dict() or {}
                if latest.get("userId") != uid or latest.get("challengeId") != challenge_id:
                    raise PermissionError("Attempt ownership mismatch.")
                if latest.get("status") != "started":
                    raise RuntimeError("Attempt is not open.")
                latest_deadline = _as_dt(latest.get("deadlineAt"))
                if latest_deadline and _utc_now().timestamp() > latest_deadline.timestamp() + 5:
                    raise RuntimeError("Challenge time has expired.")
                tx.update(ref, {"answers": safe_answers, "updatedAt": _utc_now()})
            _save(db.transaction())
            return jsonify({"success": True, "saved": len(safe_answers)})
        except Exception:
            app.logger.exception("Challenge answer save failed")
            return jsonify({"error": "Unable to save challenge answers."}), 500

    @app.post("/api/challenges/submit")
    def submit_challenge():
        """Atomically finalize one challenge attempt.

        The attempt document is the single write authority. A Firestore
        transaction prevents two concurrent submissions from both grading and
        finalizing the same attempt. Client-supplied score/rank/prize fields are
        never accepted.
        """
        detail, error = _identity()
        if error:
            return error
        try:
            db = _db(); uid = detail["uid"]; role, _ = _role(db, uid, detail)
            if role != "student":
                return jsonify({"error": "Only student accounts can submit challenges."}), 403
            body = request.get_json(silent=True) or {}
            challenge_id = _safe_key(body.get("challengeId"))
            attempt_id = _safe_key(body.get("attemptId"))
            answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
            if not challenge_id or not attempt_id or len(answers) > MAX_QUESTIONS_PER_CHALLENGE:
                return jsonify({"error": "challengeId, attemptId and valid answers are required."}), 400

            from firebase_admin import firestore as _firestore
            attempt_ref = db.collection("challengeAttempts").document(attempt_id)
            challenge_ref = db.collection("academicChallenges").document(challenge_id)
            now = _utc_now()

            tx = db.transaction()
            @_firestore.transactional
            def _finalize(transaction):
                attempt_snap = attempt_ref.get(transaction=transaction)
                if not attempt_snap.exists:
                    return {"kind": "missing"}
                attempt = attempt_snap.to_dict() or {}
                if attempt.get("userId") != uid or attempt.get("challengeId") != challenge_id:
                    return {"kind": "forbidden"}
                if attempt.get("status") == "submitted":
                    return {"kind": "already", "attempt": attempt}
                if attempt.get("status") != "started":
                    return {"kind": "closed"}

                challenge_snap = challenge_ref.get(transaction=transaction)
                challenge = challenge_snap.to_dict() or {} if challenge_snap.exists else {}
                if challenge.get("awardsFinalizedAt"):
                    return {"kind": "closed"}
                challenge_status = str(challenge.get("status") or "").lower()
                challenge_end = _as_dt(challenge.get("endsAt"))
                challenge_expired = bool(challenge_end and now.timestamp() > challenge_end.timestamp() + 5)
                deadline = _as_dt(attempt.get("deadlineAt"))
                expired = bool(deadline and now.timestamp() > deadline.timestamp() + 5)
                # A manually CLOSED challenge must not accept a late submission.
                # A schedule-expired CLOSED challenge may still finalize attempts
                # that were already started, using only their durably saved answers.
                if challenge_status not in ("active", "closed"):
                    return {"kind": "closed"}
                if challenge_status == "closed" and not (challenge_expired or expired):
                    return {"kind": "closed"}
                if challenge_status == "active" and challenge_expired:
                    expired = True

                question_ids = [str(x) for x in (attempt.get("questionIds") or [])][:MAX_QUESTIONS_PER_CHALLENGE]
                snapshot = [x for x in (attempt.get("questionSnapshot") or []) if isinstance(x, dict)]
                snapshot_by_id = {str(x.get("id")): x for x in snapshot if x.get("id")}

                # Once the deadline has passed, only answers durably saved on
                # the attempt are trusted. Before expiry, submitted answers are
                # filtered to the immutable question set captured at start.
                raw_answers = attempt.get("answers") or {} if expired else answers
                allowed = set(question_ids)
                safe_answers = {
                    str(k)[:150]: _clean(v, 30).upper()
                    for k, v in raw_answers.items()
                    if str(k) in allowed
                }

                score = 0.0; total = 0.0; correct_count = 0; total_count = 0
                for qid in question_ids:
                    q = snapshot_by_id.get(qid)
                    if q is None:
                        # Legacy attempt without a snapshot: only use the current
                        # question record as a compatibility fallback. New attempts
                        # are always graded from their immutable snapshot.
                        qsnap = db.collection("questionBank").document(_safe_key(qid)).get(transaction=transaction)
                        if not qsnap.exists:
                            continue
                        q = qsnap.to_dict() or {}
                    points = max(0.0, float(q.get("points", 1) or 1))
                    total += points
                    total_count += 1
                    chosen = str(safe_answers.get(qid, "")).upper()
                    key = str(q.get("correctAnswer", "")).upper()
                    if chosen == key:
                        score += points
                        correct_count += 1

                percentage = round((score / total) * 100, 1) if total else 0.0
                status_result = "PASS" if percentage >= max(0.0, min(100.0, float(attempt.get("passMark", challenge.get("passMark", 50)) or 50))) else "FAIL"
                transaction.update(attempt_ref, {
                    "status": "submitted", "answers": safe_answers, "score": score,
                    "totalPoints": total, "percentage": percentage,
                    "correctCount": correct_count, "questionCount": total_count,
                    "expiredSubmission": expired, "submittedAt": now,
                    "statusResult": status_result,
                    "updatedAt": now, "gradingVersion": "V31.55"
                })
                return {"kind": "finalized", "attempt": attempt, "challenge": challenge,
                        "score": score, "total": total, "percentage": percentage,
                        "correct": correct_count, "count": total_count, "expired": expired,
                        "status": status_result}

            result = _finalize(tx)
            kind = result.get("kind")
            if kind == "missing":
                return jsonify({"error": "Attempt not found."}), 404
            if kind == "forbidden":
                return jsonify({"error": "This attempt does not belong to you."}), 403
            if kind == "closed":
                return jsonify({"error": "Attempt is not open."}), 409
            if kind == "already":
                a = result.get("attempt") or {}
                return jsonify({"score": a.get("score", 0), "totalPoints": a.get("totalPoints", 0),
                                "percentage": a.get("percentage", 0), "correctCount": a.get("correctCount", 0),
                                "questionCount": a.get("questionCount", 0), "alreadySubmitted": True,
                                "status": a.get("statusResult", "FAIL")})

            return jsonify({"success": True, "score": result["score"], "totalPoints": result["total"],
                            "percentage": result["percentage"], "correctCount": result["correct"],
                            "questionCount": result["count"], "expiredSubmission": result["expired"],
                            "challengeTitle": (result.get("challenge") or {}).get("title", "Academic Challenge"),
                            "status": result["status"]})
        except Exception:
            app.logger.exception("Challenge submission failed")
            return jsonify({"error": "Unable to submit challenge."}), 500

    @app.get("/api/challenges/<challenge_id>/leaderboard")
    def challenge_leaderboard(challenge_id):
        detail, error = _identity()
        if error: return error
        try:
            db = _db(); uid = detail["uid"]; role, _ = _role(db, uid, detail)
            if role not in {"student", "teacher", "admin"}: return jsonify({"error": "Authenticated access required."}), 403
            if role == "teacher" and not _teacher_approved(db, uid):
                return jsonify({"error": "Approved teacher access required."}), 403
            # Build one authoritative leaderboard row per student. Legacy data may
            # contain duplicate submitted attempts, so the best result wins; ties
            # are resolved deterministically by earliest submission, then user ID.
            by_user = {}
            for snap in db.collection("challengeAttempts").where("challengeId", "==", _safe_key(challenge_id)).where("status", "==", "submitted").limit(500).stream():
                x = snap.to_dict() or {}
                uid_value = _safe_key(x.get("userId"))
                if not uid_value:
                    continue
                row = {"userId": uid_value, "percentage": float(x.get("percentage", 0) or 0), "score": float(x.get("score", 0) or 0), "submittedAt": x.get("submittedAt")}
                prior = by_user.get(uid_value)
                if prior is None or (row["percentage"], row["score"], -_submission_epoch(row.get("submittedAt"))) > (prior["percentage"], prior["score"], -_submission_epoch(prior.get("submittedAt"))):
                    by_user[uid_value] = row
            rows = list(by_user.values())
            rows.sort(key=lambda x: (-x["percentage"], -x["score"], _submission_epoch(x.get("submittedAt")), x["userId"]))
            challenge_snap = db.collection("academicChallenges").document(_safe_key(challenge_id)).get()
            challenge = challenge_snap.to_dict() or {}
            qualification_count = max(0, int(challenge.get("qualificationCount", 0) or 0))
            entry_fee = float(challenge.get("entryFee", 0) or 0)
            if entry_fee > 0:
                verified = {
                    _safe_key((s.to_dict() or {}).get("userId"))
                    for s in db.collection("challengeEntries").where("challengeId", "==", _safe_key(challenge_id)).where("status", "==", "verified").limit(5000).stream()
                }
                rows = [r for r in rows if r["userId"] in verified]
                rows.sort(key=lambda x: (-x["percentage"], -x["score"], _submission_epoch(x.get("submittedAt")), x["userId"]))
            if role != "student" and qualification_count:
                for index, row in enumerate(rows, 1):
                    row["qualified"] = index <= qualification_count
            # Do not expose identity fields beyond the authenticated user's own ID
            # unless caller is an approved teacher/admin.
            if role == "student":
                for r in rows: r["isMe"] = r.get("userId") == uid; r.pop("userId", None)
            else:
                for index, r in enumerate(rows, 1): r["rank"] = index
            return jsonify({"challengeId": _safe_key(challenge_id), "leaderboard": rows[:100]})
        except Exception:
            app.logger.exception("Challenge leaderboard failed")
            return jsonify({"error": "Unable to load leaderboard."}), 500


def _public_questions_from_snapshot(snapshot):
    """Strip server-only answer keys from an immutable attempt question snapshot."""
    public = []
    for item in snapshot or []:
        if not isinstance(item, dict):
            continue
        public.append({
            "id": item.get("id"),
            "question": item.get("question", ""),
            "type": item.get("type", "mcq"),
            "options": item.get("options") or {},
            "points": item.get("points", 1),
            "subject": item.get("subject", ""),
            "grade": item.get("grade", ""),
            "domain": item.get("domain", ""),
            "difficulty": item.get("difficulty", "medium"),
            "bookId": item.get("bookId", ""),
            "chapterId": item.get("chapterId", ""),
            "subchapterId": item.get("subchapterId", ""),
        })
    return public


def _challenge_start_response(db, challenge, attempt_id, started_at, deadline_at, questions=None):
    if questions is None:
        questions = []
        for qid in challenge.get("questionIds") or []:
            snap = db.collection("questionBank").document(_safe_key(qid)).get()
            if not snap.exists:
                continue
            q = snap.to_dict() or {}; q["id"] = snap.id
            if q.get("status") == "approved":
                questions.append(_question_public(q))
    return jsonify({"attemptId": attempt_id, "challengeId": challenge.get("id"), "title": challenge.get("title", "Academic Challenge"), "durationMinutes": int(challenge.get("durationMinutes", 20) or 20), "startedAt": _as_dt(started_at).isoformat() if _as_dt(started_at) else started_at, "deadlineAt": _as_dt(deadline_at).isoformat() if _as_dt(deadline_at) else deadline_at, "questions": questions})
